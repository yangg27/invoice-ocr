"""OCR 引擎封装：RapidOCR(PP-OCR ONNX)，纯本地 CPU 推理，无网络。

文档/截图统一返回 [(bbox, text, score)]，bbox=(x0,y0,x1,y1)。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

_lock = threading.Lock()
_engine: Any = None
_backend: str = ""


@dataclass(slots=True)
class OcrItem:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float


def _load_engine() -> Any:
    global _engine, _backend
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        errors: list[str] = []
        try:
            from rapidocr_onnxruntime import RapidOCR  # 老包名

            _engine = RapidOCR()
            _backend = "rapidocr_onnxruntime"
            return _engine
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rapidocr_onnxruntime: {type(exc).__name__}: {exc}")
        try:
            from rapidocr import RapidOCR  # 新包名

            _engine = RapidOCR()
            _backend = "rapidocr"
            return _engine
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rapidocr: {type(exc).__name__}: {exc}")
        raise RuntimeError(
            "OCR 引擎未安装或加载失败。离线部署时请确认 rapidocr_onnxruntime 目录"
            "（含子模块与 models）随程序一起分发。详情：" + "；".join(errors)
        )


def backend_name() -> str:
    return _backend or "unloaded"


def _run(img: Any) -> list[OcrItem]:
    eng = _load_engine()
    with _lock:
        out = eng(img)
    # 兼容两种返回：(result, elapse) 或 直接 result
    result = out[0] if isinstance(out, tuple) else out
    items: list[OcrItem] = []
    if not result:
        return items
    for row in result:
        try:
            box, text, score = row[0], row[1], row[2]
        except Exception:
            continue
        if not text or not str(text).strip():
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        items.append(OcrItem(min(xs), min(ys), max(xs), max(ys), str(text).strip(), float(score)))
    return items


def ocr_image(path: str) -> tuple[list[OcrItem], int, int]:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
    items = _run(path)
    return items, w, h


def ocr_ndarray(arr: Any) -> list[OcrItem]:
    import numpy as np  # noqa: F401

    return _run(arr)