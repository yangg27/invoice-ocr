"""识别引擎：文件 → 票面解析 → 费用科目归类 → 统一记录结构。

与 UI 无关，方便命令行批量导入与单元验证。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from . import parser as parser_mod
from .db import file_sha1, now_str
from .expense import ExpenseClassifier
from .textextract import extract_any


@dataclass
class Recognized:
    record: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    template: str = ""
    confidence: float = 0.0


def _classify_subject(cls: ExpenseClassifier, item_name: str, seller: str, remark: str) -> str:
    subject = cls.classify(item_name)
    if subject != "其他":
        return subject
    subject = cls.classify(seller)
    if subject != "其他":
        return subject
    return cls.classify(remark) if remark else "其他"


def recognize_file(path: str, classifier: ExpenseClassifier) -> list[Recognized]:
    """识别一个文件；PDF 多页 = 多张发票，逐页返回。"""
    layouts = extract_any(path)
    src_hash = file_sha1(path)
    base = os.path.basename(path)
    out: list[Recognized] = []
    for layout in layouts:
        res = parser_mod.parse_layout(layout, base)
        f = res.fields
        subject = _classify_subject(
            classifier,
            str(f.get("item_name") or ""),
            str(f.get("seller_name") or ""),
            str(f.get("remark") or ""),
        )
        rec: dict[str, Any] = {
            "invoice_type": f.get("invoice_type") or "",
            "invoice_code": f.get("invoice_code") or "",
            "invoice_number": f.get("invoice_number") or "",
            "invoice_date": f.get("invoice_date") or "",
            "buyer_name": f.get("buyer_name") or "",
            "buyer_tax_id": f.get("buyer_tax_id") or "",
            "seller_name": f.get("seller_name") or "",
            "seller_tax_id": f.get("seller_tax_id") or "",
            "item_name": f.get("item_name") or "",
            "amount": f.get("amount"),
            "tax_rate": f.get("tax_rate") or "",
            "tax_amount": f.get("tax_amount"),
            "total_with_tax": f.get("total_with_tax"),
            "total_cn": f.get("total_cn") or "",
            "expense_subject": subject,
            "drawer": f.get("drawer") or "",
            "remark": f.get("remark") or "",
            "color_flag": f.get("color_flag") or "蓝字",
            "created_at": now_str(),
            "updated_at": now_str(),
            "source_file": base,
            "raw_text": res.raw_text,
            "confidence": res.confidence,
            "source_path": os.path.abspath(path),
            "source_hash": src_hash,
            "page_no": layout.page_no,
        }
        out.append(
            Recognized(record=rec, warnings=list(res.warnings), template=res.template,
                       confidence=res.confidence)
        )
    return out