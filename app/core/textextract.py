"""输入层：PDF（文本层优先）/ 图片 / 剪贴板 → PageLayout。

设计要点
- PDF 文本层给出「按字」坐标，再按空隙聚成 token，避免整行数字粘连；
- 没有文本层的 PDF 页（扫描件）自动渲染成位图走 OCR；
- 图片与截图直接走 OCR，坐标按图片像素给出；
- 所有来源最终统一成 PageLayout，解析器无需区分。
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Iterable

from . import ocr as ocr_mod
from .layout import PageLayout, Word

PDF_EXTS = {".pdf"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
SUPPORTED_EXTS = PDF_EXTS | IMG_EXTS

_OCR_DPI = 260
_MIN_TEXT_CHARS = 12


def work_dir() -> str:
    """临时文件目录：优先用程序数据目录下的 _tmp，避免受系统 TEMP 策略影响。"""
    forced = os.environ.get("INVOICE_TMP_DIR")
    if forced:
        try:
            os.makedirs(forced, exist_ok=True)
            return forced
        except OSError:
            pass
    return tempfile.gettempdir()


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


_LABEL_VALUE_RE = re.compile(r"^(.*?[:：])(\s*\S.*)$")
_PAREN_VALUE_RE = re.compile(r"^(.*?[）)])(\s*[¥￥0-9].*)$")
_MONEY_RE = re.compile(r"[¥￥]\s?-?[0-9][0-9,]*(?:\.[0-9]{1,2})?")


def _split_token(w: Word) -> list[Word]:
    """OCR 常把「标签+值」或「两个金额」识别成同一个框，按比例切成两个 token。"""
    t = w.text
    pieces: list[str] = []

    m = _LABEL_VALUE_RE.match(t) or _PAREN_VALUE_RE.match(t)
    if m and m.group(2).strip():
        pieces = [m.group(1), m.group(2).strip()]
    else:
        money = _MONEY_RE.findall(t)
        if len(money) >= 2:
            parts = re.split(r"(?=[¥￥])", t)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 2:
                pieces = parts
    if not pieces:
        return [w]

    total = sum(len(p) for p in pieces) or 1
    out: list[Word] = []
    x = w.x0
    scale = w.w / total
    for p in pieces:
        piece_w = scale * len(p)
        out.append(Word(p, x, w.y0, min(x + piece_w, w.x1), w.y1, w.size, w.page))
        x += piece_w
    return out


def _normalize_tokens(words: list[Word]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        out.extend(_split_token(w))
    return out


def _cjk_digit_count(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff" or ch.isdigit())


_SYMBOL_NOISE = re.compile(r"[<>*/+]{2,}")


def _sanitize(words: list[Word], width: float) -> list[Word]:
    """去掉两类噪声，避免污染版式：

    1. 右边缘的「下载次数/水印」字符（数电票 PDF 常见，x 贴近页面右边界）；
    2. 老版发票密码区的逐字符乱码（连续符号、无中文）。
    """
    out: list[Word] = []
    edge = width * 0.975
    for w in words:
        t = w.text.strip()
        if not t:
            continue
        if w.x0 >= edge and len(t) <= 14:
            continue
        if _SYMBOL_NOISE.search(t) and not re.search(r"[\u4e00-\u9fff]", t):
            continue
        out.append(w)
    return out


def _words_from_rawdict(page, page_no: int) -> list[Word]:
    words: list[Word] = []
    data = page.get_text("rawdict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size", 7.0) or 7.0)
                chars = span.get("chars")
                if not chars:
                    txt = (span.get("text") or "").strip()
                    if txt:
                        x0, y0, x1, y1 = span["bbox"]
                        words.append(Word(txt, x0, y0, x1, y1, size, page_no))
                    continue
                cur: list[dict] = []

                def flush() -> None:
                    if not cur:
                        return
                    txt = "".join(c["c"] for c in cur)
                    if txt.strip():
                        x0 = min(c["bbox"][0] for c in cur)
                        y0 = min(c["bbox"][1] for c in cur)
                        x1 = max(c["bbox"][2] for c in cur)
                        y1 = max(c["bbox"][3] for c in cur)
                        words.append(Word(txt, x0, y0, x1, y1, size, page_no))
                    cur.clear()

                for ch in chars:
                    c = ch.get("c", "")
                    if not c.strip():
                        flush()
                        continue
                    bb = ch.get("bbox")
                    if not bb:
                        continue
                    if cur:
                        prev = cur[-1]
                        gap = bb[0] - prev["bbox"][2]
                        dy = abs(bb[1] - prev["bbox"][1])
                        if gap > max(0.55 * size, 0.7) or dy > 0.35 * size:
                            flush()
                    cur.append(ch)
                flush()
    return words


def _pdf_pages_raw(path: str) -> list[tuple[int, float, float, list[Word]]]:
    import fitz  # PyMuPDF

    out: list[tuple[int, float, float, list[Word]]] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            w = float(page.rect.width)
            h = float(page.rect.height)
            words = _sanitize(_words_from_rawdict(page, i), w)
            out.append((i, w, h, words))
    return out


def _render_page_png(doc, page_no: int, dpi: int = _OCR_DPI) -> str:
    page = doc[page_no]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz_matrix(zoom), alpha=False)
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="inv_page_", dir=work_dir())
    os.close(fd)
    pix.save(tmp)
    return tmp


def fitz_matrix(zoom: float):
    import fitz

    return fitz.Matrix(zoom, zoom)


def extract_pdf(path: str, *, allow_ocr: bool = True) -> list[PageLayout]:
    """解析 PDF，返回每页版式；文本层不可用的页回落到 OCR。"""
    import fitz

    layouts: list[PageLayout] = []
    raw = _pdf_pages_raw(path)
    need_ocr: list[int] = []
    for page_no, w, h, words in raw:
        text = "".join(wd.text for wd in words)
        if _cjk_digit_count(text) < _MIN_TEXT_CHARS or not re.search(r"发票|税额|金额|价税", text):
            need_ocr.append(page_no)
        layouts.append(PageLayout(width=w, height=h, words=words, page_no=page_no, source="pdf"))
    if need_ocr and allow_ocr:
        with fitz.open(path) as doc:
            for page_no in need_ocr:
                tmp = _render_page_png(doc, page_no)
                try:
                    items, iw, ih = ocr_mod.ocr_image(tmp)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                layout = layouts[page_no]
                layout.words = [
                    Word(it.text, it.x0, it.y0, it.x1, it.y1, max(it.y1 - it.y0, 1.0), page_no)
                    for it in items
                ]
                layout.source = "pdf-ocr"
                # 用渲染图尺寸重标定坐标，保持与 PDF 点坐标同尺度
                if iw and ih and layout.width:
                    scale = layout.width / iw
                    for wd in layout.words:
                        wd.x0 *= scale
                        wd.x1 *= scale
                        wd.y0 *= scale
                        wd.y1 *= scale
                        wd.size *= scale
    layouts.sort(key=lambda p: p.page_no)
    return layouts


def extract_image(path: str) -> list[PageLayout]:
    items, w, h = ocr_mod.ocr_image(path)
    words = [
        Word(it.text, it.x0, it.y0, it.x1, it.y1, max(it.y1 - it.y0, 1.0), 0)
        for it in items
    ]
    words = _sanitize(_normalize_tokens(words), float(w))
    return [PageLayout(width=float(w), height=float(h), words=words, page_no=0, source="image")]


def extract_any(path: str) -> list[PageLayout]:
    ext = os.path.splitext(path)[1].lower()
    if ext in PDF_EXTS:
        return extract_pdf(path)
    if ext in IMG_EXTS:
        return extract_image(path)
    raise ValueError(f"不支持的文件类型: {ext}")


def extract_clipboard_image(qimage) -> list[PageLayout]:
    """把 QImage 保存为临时 PNG 后 OCR。"""
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="inv_clip_", dir=work_dir())
    os.close(fd)
    try:
        if not qimage.save(tmp, "PNG"):
            raise RuntimeError("剪贴板图片保存失败")
        return extract_image(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def iter_supported_files(paths: Iterable[str]) -> list[str]:
    """展开目录，过滤扩展名，保持稳定顺序。"""
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    if is_supported(fp):
                        out.append(fp)
        elif is_supported(p):
            out.append(p)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq