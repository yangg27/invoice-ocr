"""用样本目录跑全量识别，做「无需人工标注」的自动校验并出报表。

校验项（都能客观判定）：
1. 文件名里的发票号码 / 开票日期 / 公司名 与票面解析结果是否一致；
2. 金额 + 税额 = 价税合计；
3. 大写金额 = 小写金额；
4. 关键字段是否缺失。

用法：python tools/eval_samples.py [样本目录] [--csv 输出.csv]
"""
from __future__ import annotations

import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import parser  # noqa: E402
from app.core.textextract import extract_any, iter_supported_files  # noqa: E402

DEFAULT_DIR = os.path.join(ROOT, "samples")

REQ_FIELDS = [
    ("invoice_number", "发票号码"),
    ("invoice_date", "开票日期"),
    ("buyer_name", "购买方"),
    ("seller_name", "销售方"),
    ("buyer_tax_id", "购方税号"),
    ("seller_tax_id", "销方税号"),
    ("amount", "金额"),
    ("tax_amount", "税额"),
    ("total_with_tax", "价税合计"),
    ("item_name", "项目名称"),
    ("tax_rate", "税率"),
    ("total_cn", "大写"),
]


def parse_filename(base: str) -> dict[str, str]:
    """从下载文件名里取出号码 / 日期 / 公司名（用于交叉校验）。"""
    info: dict[str, str] = {}
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    parts = stem.split("_")

    m = re.search(r"dzfp[_-](\d{20})", stem)
    if m:
        info["number"] = m.group(1)

    m = re.search(r"_(\d{14})(?:\(\d\))?$", stem)
    if m:
        d = m.group(1)[:8]
        info["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    else:
        m = re.search(r"_(\d{4})-(\d{2})-(\d{2})(?:\(\d\))?$", stem)
        if m:
            info["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 公司名 = 号码之后、日期/金额之前的那段
    rest = parts[2:] if len(parts) > 2 else []
    while rest:
        tail = rest[-1]
        if (
            re.fullmatch(r"\d{14}(\(\d\))?", tail)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}(\(\d\))?", tail)
            or re.fullmatch(r"\d+(\.\d+)?", tail)
            or tail in ("", "dzfp")
        ):
            rest.pop()
            continue
        break
    company = re.sub(r"\(\d\)$", "", "_".join(rest)).strip()
    company = company.replace(" ", "")
    if company and re.search(r"[\u4e00-\u9fff]", company):
        info["company"] = company
    return info


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else DEFAULT_DIR
    csv_path = ""
    if "--csv" in sys.argv:
        csv_path = sys.argv[sys.argv.index("--csv") + 1]

    files = iter_supported_files([src])
    files = [f for f in files if f.lower().endswith(".pdf")]
    rows = []
    fails: list[str] = []
    soft: list[str] = []
    field_miss = {k: 0 for k, _ in REQ_FIELDS}

    for fp in files:
        base = os.path.basename(fp)
        rec: dict[str, object] = {"file": base}
        try:
            layouts = extract_any(fp)
            if not layouts:
                raise RuntimeError("没有解析出页面")
            res = parser.parse_layout(layouts[0], base)
            f = res.fields
            rec.update({k: f.get(k) for k, _ in REQ_FIELDS})
            rec["template"] = res.template
            rec["confidence"] = res.confidence
            rec["warnings"] = "；".join(res.warnings)
            rec["items"] = len(res.items)
            if len(layouts) > 1:
                rec["pages"] = len(layouts)

            problems: list[str] = []
            notes: list[str] = []
            info = parse_filename(base)
            if info.get("number") and f.get("invoice_number") != info["number"]:
                problems.append(f"号码不符(文件{info['number']}≠识别{f.get('invoice_number')})")
            if info.get("date") and f.get("invoice_date") != info["date"]:
                # 下载文件名里的时间戳常是「下载时间」，与开票日期不必相同
                notes.append(f"文件名日期{info['date']}≠开票日期{f.get('invoice_date')}")
            if info.get("company"):
                hay = f"{f.get('buyer_name') or ''}{f.get('seller_name') or ''}".replace(" ", "")
                probe = info["company"][:12]
                if probe and probe not in hay:
                    problems.append(f"公司名未命中({info['company']})")

            amt, tax, total = f.get("amount"), f.get("tax_amount"), f.get("total_with_tax")
            if None not in (amt, tax, total) and abs(amt + tax - total) > 0.02:
                problems.append(f"勾稽不符({amt}+{tax}≠{total})")
            cn = f.get("total_cn")
            if cn and total is not None:
                v = parser.cn_upper_to_number(cn)
                if v is not None and abs(v - total) > 0.02:
                    problems.append(f"大写不符({cn}={v}≠{total})")

            for key, label in REQ_FIELDS:
                if f.get(key) in (None, ""):
                    if key == "seller_tax_id" and "个人" in str(f.get("seller_name") or ""):
                        continue  # 个人销售方通常无税号，属正常
                    if key == "buyer_tax_id" and "个人" in str(f.get("buyer_name") or ""):
                        continue  # 个人购买方同样无税号
                    field_miss[key] += 1
                    problems.append(f"缺{label}")

            rec["problems"] = "；".join(problems)
            rec["notes"] = "；".join(notes)
            if problems:
                fails.append(f"{base}: {'；'.join(problems)}")
            if notes:
                soft.append(f"{base}: {'；'.join(notes)}")
        except Exception as exc:  # noqa: BLE001
            rec["problems"] = f"异常: {exc}"
            fails.append(f"{base}: 异常 {exc}")
        rows.append(rec)

    total_files = len(files)
    ok = sum(1 for r in rows if not r.get("problems"))

    print(f"样本总数：{total_files}    完全通过：{ok}    有问题：{total_files - ok}")
    print()
    print("字段缺失统计：")
    for key, label in REQ_FIELDS:
        miss = field_miss[key]
        flag = "OK " if miss == 0 else "!! "
        print(f"  {flag}{label:6} 缺失 {miss:2d} / {total_files}")
    print()
    if fails:
        print("问题清单（需修正）：")
        for line in fails:
            print("  -", line)
    else:
        print("全部样本关键字段校验通过。")
    if soft:
        print()
        print("提示（不算失败，通常文件名时间戳是下载时间）：")
        for line in soft:
            print("  ·", line)

    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n明细已写入 {csv_path}")
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(main())