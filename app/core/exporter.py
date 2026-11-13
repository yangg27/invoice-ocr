"""导出：按勾选的字段与行，输出 xlsx / csv。"""
from __future__ import annotations

import csv
import os
from typing import Any, Iterable

from .fields import FIELD_BY_KEY


def _fmt(key: str, value: Any) -> Any:
    if value is None:
        return ""
    field = FIELD_BY_KEY.get(key)
    kind = field.kind if field else "text"
    if kind == "money":
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return ""
    if kind == "date":
        return str(value)[:10]
    return value


def export_rows(
    rows: Iterable[dict[str, Any]],
    field_keys: list[str],
    path: str,
    *,
    fmt: str | None = None,
) -> tuple[str, int]:
    """返回 (实际写入路径, 行数)。"""
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    rows = list(rows)
    labels = [FIELD_BY_KEY[k].label for k in field_keys if k in FIELD_BY_KEY]
    keys = [k for k in field_keys if k in FIELD_BY_KEY]

    if fmt in ("xlsx", "xlsm", "xls"):
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "发票记录"
        ws.append(labels)
        header_fill = PatternFill("solid", fgColor="DDEBF7")
        for idx, _label in enumerate(labels, start=1):
            cell = ws.cell(row=1, column=idx)
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for row in rows:
            ws.append([_fmt(k, row.get(k)) for k in keys])
        for idx, key in enumerate(keys, start=1):
            field = FIELD_BY_KEY[key]
            width = max(10, min(42, int(field.width / 7) + 4))
            ws.column_dimensions[get_column_letter(idx)].width = width
            if field.kind == "money":
                for r in range(2, ws.max_row + 1):
                    ws.cell(row=r, column=idx).number_format = "#,##0.00"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        wb.save(path)
        return path, len(rows)

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(labels)
        for row in rows:
            writer.writerow([_fmt(k, row.get(k)) for k in keys])
    return path, len(rows)