"""生成一份「全假数据」的演示库，用于截图/说明图，避免泄露真实发票信息。

用法：python tools/make_demo_db.py [目标目录]
输出：<目录>/invoices.db（默认 logs/usage_demo）
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core.db import Database  # noqa: E402

BUYERS = [
    ("示例科技有限公司", "91430100MA000001XA"),
    ("样例物业服务有限公司", "91430100MA000002XB"),
    ("演示商贸有限公司", "91430100MA000003XC"),
    ("某某餐饮管理有限公司", "91430100MA000004XD"),
    ("测试信息技术有限公司", "91430100MA000005XE"),
]

SELLERS = [
    ("样例软件服务有限公司", "91430100MA000101YA"),
    ("测试酒店管理有限公司", "91430100MA000102YB"),
    ("演示出行科技有限公司", "91430100MA000103YC"),
    ("某某电脑经营部", "91430100MA000104YD"),
    ("样例物业服务有限公司", "91430100MA000105YE"),
    ("某某餐饮管理有限公司", "91430100MA000106YF"),
]

# 项目与卖家对应，看起来更真实
ITEMS = [
    ("*信息技术服务*软件服务费", "软件服务费", 6, 4716.98, 0),
    ("*餐饮服务*餐费", "业务招待费", 6, 287.74, 5),
    ("*住宿服务*住宿费", "差旅费", 6, 169.81, 1),
    ("*运输服务*客运服务费", "差旅费", 3, 412.62, 2),
    ("*计算机外部设备*内存卡", "办公费", 13, 966.37, 3),
    ("*物业管理服务*物业费", "物业费", 6, 8018.87, 4),
    ("*信息技术服务*运维服务费", "信息技术服务费", 6, 2830.19, 0),
    ("*软件*软件维护费", "软件服务费", 6, 1132.08, 0),
]


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "logs", "usage_demo")
    os.makedirs(out_dir, exist_ok=True)
    db_path = os.path.join(out_dir, "invoices.db")
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)

    db = Database(db_path)
    base = datetime(2026, 5, 6, 9, 0, 0)
    n = 0
    for i in range(14):
        buyer = BUYERS[i % len(BUYERS)]
        item, subject, rate, amount, seller_idx = ITEMS[i % len(ITEMS)]
        seller = SELLERS[seller_idx]
        tax = round(amount * rate / 100, 2)
        total = round(amount + tax, 2)
        created = base + timedelta(days=i, minutes=i * 7)
        rec = {
            "invoice_type": "电子发票（增值税专用发票）" if i % 3 else "电子发票（普通发票）",
            "invoice_code": "",
            "invoice_number": f"2450000000000000{i:04d}",
            "invoice_date": (datetime(2026, 1, 5) + timedelta(days=i * 9)).strftime("%Y-%m-%d"),
            "buyer_name": buyer[0],
            "buyer_tax_id": buyer[1],
            "seller_name": seller[0],
            "seller_tax_id": seller[1],
            "item_name": item,
            "amount": amount,
            "tax_rate": f"{rate}%",
            "tax_amount": tax,
            "total_with_tax": total,
            "total_cn": "",
            "expense_subject": subject,
            "drawer": "示 例",
            "remark": "",
            "color_flag": "蓝字",
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "source_file": f"示例发票_{i + 1:02d}.pdf",
            "raw_text": "",
            "confidence": 1.0,
            "source_path": "",
            "source_hash": "",
            "page_no": 0,
        }
        db.insert(rec)
        n += 1
    print(f"demo db: {db_path}  记录 {n} 条")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())