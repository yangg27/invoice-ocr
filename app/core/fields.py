"""发票字段定义：UI 列、导出列、数据库列的唯一事实源。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str          # 数据库列名 / 导出 key
    label: str        # 中文表头
    width: int = 120
    kind: str = "text"  # text | money | date | datetime | subject
    export: bool = True
    visible: bool = True


FIELDS: list[Field] = [
    Field("invoice_type", "发票类型", 210, "text"),
    Field("invoice_code", "发票代码", 120, "text", export=True, visible=False),
    Field("invoice_number", "发票号码", 200, "text"),
    Field("invoice_date", "开票日期", 105, "date"),
    Field("buyer_name", "购买方名称", 270, "text"),
    Field("buyer_tax_id", "购买方税号", 190, "text"),
    Field("seller_name", "销售方名称", 270, "text"),
    Field("seller_tax_id", "销售方税号", 190, "text"),
    Field("item_name", "项目名称", 270, "text"),
    Field("amount", "金额", 110, "money"),
    Field("tax_rate", "税率", 80, "text"),
    Field("tax_amount", "税额", 110, "money"),
    Field("total_with_tax", "价税合计", 120, "money"),
    Field("expense_subject", "费用科目", 120, "subject"),
    Field("total_cn", "价税合计(大写)", 200, "text", export=True, visible=False),
    Field("drawer", "开票人", 95, "text"),
    Field("remark", "备注", 200, "text", export=True, visible=False),
    Field("color_flag", "蓝红字", 75, "text", export=True, visible=False),
    Field("created_at", "识别时间", 155, "datetime"),
    Field("source_file", "来源文件", 260, "text"),
]

FIELD_BY_KEY = {f.key: f for f in FIELDS}
FIELD_KEYS = [f.key for f in FIELDS]
EXPORT_FIELDS = [f.key for f in FIELDS if f.export]

# 表格展示顺序：把金额/价税合计/费用科目前移，窗口内一屏可见
TABLE_ORDER = [
    "invoice_type",
    "invoice_number",
    "invoice_date",
    "buyer_name",
    "seller_name",
    "total_with_tax",
    "amount",
    "tax_amount",
    "expense_subject",
    "tax_rate",
    "item_name",
    "buyer_tax_id",
    "seller_tax_id",
    "drawer",
    "created_at",
    "source_file",
]
VISIBLE_FIELDS = [FIELD_BY_KEY[k] for k in TABLE_ORDER]

DB_COLUMNS = FIELD_KEYS + [
    "id",
    "raw_text",
    "confidence",
    "source_path",
    "source_hash",
    "page_no",
    "deleted",
    "updated_at",
]