"""发票解析内核：数电票（电子发票·普票/专票）与老版增值税电子发票。

策略
1. 模板判定（按票面关键词）；
2. 数电票：标签锚点 + 左右分栏（购买方在左、销售方在右）；
3. 老版票：固定版式，用「区块 y 带 + 值列 x 带」取值，规避密码区乱码与拆字标签；
4. 表格：表头推列 + 数值类型（税率/整数数量/金额）联合判定，避免整行数字粘连；
5. 一致性校验：金额+税额=价税合计、大写↔小写，据此给置信度。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .layout import (
    DATETIME_COMPACT_RE,
    INVOICE_NO_20_RE,
    Line,
    PageLayout,
    Word,
    cn_upper_amount,
    norm_digits,
    parse_amount,
    parse_date,
    parse_tax_rate,
    to_halfwidth,
)

KEY_FIELDS = (
    "invoice_number",
    "invoice_date",
    "buyer_name",
    "seller_name",
    "total_with_tax",
)

COL_KEYS: dict[str, tuple[str, ...]] = {
    "item": ("项目名称", "货物", "服务名称", "名称"),
    "spec": ("规格型号", "规格"),
    "unit": ("单位",),
    "qty": ("数量",),
    "price": ("单价",),
    "amount": ("金额",),
    "rate": ("税率", "征收率"),
    "tax": ("税额",),
}

RATE_TEXT_RE = re.compile(r"免税|不征税|零税率|\d{1,2}(?:\.\d+)?%|＊＊＊|\*\*\*")
DATE_LABEL_RE = re.compile(r"开票日期")
STRICT_NUM_RE = re.compile(r"^[¥￥]?\s*-?[0-9][0-9,]*(?:\.[0-9]+)?$")
STRICT_INT_RE = re.compile(r"^[0-9]{1,6}$")


@dataclass
class ItemRow:
    name: str = ""
    spec: str = ""
    unit: str = ""
    qty: str = ""
    price: float | None = None
    amount: float | None = None
    rate: str = ""
    tax: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "spec": self.spec,
            "unit": self.unit,
            "qty": self.qty,
            "price": self.price,
            "amount": self.amount,
            "rate": self.rate,
            "tax": self.tax,
        }


@dataclass
class ParseResult:
    fields: dict[str, Any] = field(default_factory=dict)
    items: list[ItemRow] = field(default_factory=list)
    template: str = "unknown"
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


# ------------------------------------------------------------------ 通用工具

def _join(words) -> str:
    return "".join(w.text for w in words)


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _after(line: Line, label: Word, *, max_gap_factor: float = 1.4) -> list[Word]:
    out: list[Word] = []
    prev: Word = label
    for w in line.after(label):
        if w.is_blank():
            continue
        gap = w.x0 - prev.x1
        if gap > max_gap_factor * max(w.size, 4.0):
            break
        out.append(w)
        prev = w
    return out


def _value_right_of_line(line: Line | None, pattern: str) -> str:
    if line is None:
        return ""
    rgx = re.compile(pattern)
    labels = [w for w in line.words if rgx.search(_squash(w.text))]
    if not labels:
        return ""
    label = max(labels, key=lambda w: w.x0)
    return _join(_after(line, label)).strip(" ：:")


def _line_of(layout: PageLayout, word: Word) -> Line | None:
    for ln in layout.line_list:
        if any(w is word for w in ln.words):
            return ln
    return None


def _num_tokens(words: list[Word]) -> list[Word]:
    return [w for w in words if parse_amount(w.text) is not None and not RATE_TEXT_RE.fullmatch(_squash(w.text))]


_CN_DIGIT = {
    "零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"拾": 10, "十": 10, "佰": 100, "百": 100, "仟": 1000, "千": 1000}
_CN_BIG = {"万": 10_000, "亿": 100_000_000}


def cn_upper_to_number(s: str) -> float | None:
    """「伍仟圆整」→ 5000.0；「肆拾贰圆伍角肆分」→ 42.54。用于与大写交叉校验。"""
    if not s:
        return None
    t = _squash(s).replace("元", "圆")
    int_part, _, frac_part = t.partition("圆")
    if not int_part:
        return None

    total = 0
    section = 0
    number = 0
    for ch in int_part:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (number or 1) * _CN_UNIT[ch]
            number = 0
        elif ch in _CN_BIG:
            section = (section + number) * _CN_BIG[ch]
            total += section
            section = 0
            number = 0
        elif ch in "整正在":  # 「整」等结尾字忽略
            continue
    total += section + number

    jiao = fen = 0
    for unit, scale in (("角", 1), ("分", 2)):
        m = re.search(rf"([零壹贰叁肆伍陆柒捌玖一二三四五六七八九]){unit}", frac_part)
        if m:
            if scale == 1:
                jiao = _CN_DIGIT[m.group(1)]
            else:
                fen = _CN_DIGIT[m.group(1)]
    val = total + jiao * 0.1 + fen * 0.01
    return round(val, 2) if val else None


def _company_tokens(words: list[Word]) -> list[str]:
    out: list[str] = []
    for w in words:
        t = _squash(w.text)
        if len(t) >= 4 and re.search(r"[\u4e00-\u9fff]{2,}", t) and "：" not in t:
            if re.search(r"公司|厂|店|中心|事务所|商行|合作社|研究院|学校|医院|个人", t):
                out.append(t)
    return out


# ------------------------------------------------------------------ 表格

def _header_columns(line: Line) -> dict[str, float]:
    words = [w for w in line.words if not w.is_blank()]
    if not words:
        return {}
    groups: list[list[Word]] = [[words[0]]]
    for w in words[1:]:
        prev = groups[-1][-1]
        if (w.x0 - prev.x1) <= 1.9 * max(w.size, 4.0):
            groups[-1].append(w)
        else:
            groups.append([w])

    cols: dict[str, float] = {}
    for g in groups:
        label = _squash(_join(g))
        cx = (g[0].x0 + g[-1].x1) / 2
        for key, kws in COL_KEYS.items():
            if any(kw in label for kw in kws):
                cols.setdefault(key, cx)
                break
    return cols


def _col_bounds(cols: dict[str, float], width: float) -> list[tuple[str, float, float]]:
    ordered = sorted(cols.items(), key=lambda kv: kv[1])
    out = []
    for i, (key, cx) in enumerate(ordered):
        left = 0.0 if i == 0 else (ordered[i - 1][1] + cx) / 2
        right = width if i == len(ordered) - 1 else (cx + ordered[i + 1][1]) / 2
        out.append((key, left, right))
    return out


def _col_of(bounds: list[tuple[str, float, float]], x: float) -> str:
    for key, lo, hi in bounds:
        if lo <= x <= hi:
            return key
    if not bounds:
        return ""
    return bounds[0][0] if x < bounds[0][1] else bounds[-1][0]


def parse_table(layout: PageLayout) -> tuple[list[ItemRow], dict[str, float], str]:
    header = None
    for ln in layout.line_list:
        txt = _squash(ln.text)
        if ("项目名称" in txt or "货物或应税" in txt or "服务名称" in txt) and (
            "金额" in txt or "税额" in txt
        ):
            header = ln
            break
    if header is None:
        return [], {}, "no-header"

    cols = _header_columns(header)
    if "item" not in cols or "amount" not in cols:
        return [], {}, "weak-header"
    bounds = _col_bounds(cols, layout.width)
    item_right = dict((k, (lo, hi)) for k, lo, hi in bounds).get("item", (0, layout.width * 0.4))[1]

    heji = None
    stop_y: float | None = None
    for ln in layout.line_list:
        if ln.cy <= header.cy + 1:
            continue
        txt = _squash(ln.text)
        left_ws = [_squash(w.text) for w in ln.words if w.x0 < layout.width * 0.3]
        if heji is None and (
            any(t in ("合计", "总计") for t in [_squash(w.text) for w in ln.words])
            or ("合" in left_ws and "计" in left_ws)
        ):
            heji = ln
            stop_y = ln.y0 - 0.5
            continue
        if stop_y is None and (
            "价税合计" in txt or "大写" in txt or "小写" in txt
            or txt.startswith("备注") or "开票人" in txt
        ):
            stop_y = ln.y0 - 0.5
            break
    y_end = stop_y if stop_y is not None else layout.height * 0.9

    band_words = [
        w for w in layout.words
        if not w.is_blank() and (header.y1 + 0.5) < w.cy < y_end
    ]
    band_words.sort(key=lambda w: (w.cy, w.x0))

    rows: list[list[Word]] = []
    for w in band_words:
        if rows and abs(rows[-1][0].cy - w.cy) <= 0.55 * max(w.size, 4.0):
            rows[-1].append(w)
        else:
            rows.append([w])

    items: list[ItemRow] = []
    pending_totals: dict[str, float] | None = None
    for ws in rows:
        ws.sort(key=lambda w: w.x0)
        cells: dict[str, list[Word]] = {}
        for w in ws:
            cells.setdefault(_col_of(bounds, w.cx), []).append(w)

        name = _squash(_join(cells.get("item", [])))
        unit = _squash(_join(cells.get("unit", [])))
        spec = _squash(_join(cells.get("spec", [])))
        if name in ("合", "计", "合计", "总计", "备注", "小写", "大写"):
            name = ""
        if "价税合计" in name:
            continue

        numeric_words = [
            w for w in ws
            if w.cx > item_right and STRICT_NUM_RE.match(_squash(w.text))
        ]
        rate_word = next(
            (w for w in ws if w.cx > item_right and RATE_TEXT_RE.fullmatch(_squash(w.text))), None
        )
        rate = parse_tax_rate(_squash(rate_word.text)) if rate_word is not None else ""

        money_words = [
            w for w in numeric_words
            if w is not rate_word and not STRICT_INT_RE.match(_squash(w.text))
        ]
        qty_word = next(
            (w for w in numeric_words if STRICT_INT_RE.match(_squash(w.text))), None
        )
        if rate_word is not None:
            left_money = [w for w in money_words if w.cx < rate_word.cx]
            right_money = [w for w in money_words if w.cx > rate_word.cx]
        else:
            left_money = money_words[:-1] if len(money_words) > 1 else money_words
            right_money = money_words[-1:] if len(money_words) > 1 else []

        price = parse_amount(left_money[0].text) if len(left_money) >= 2 else None
        amount = parse_amount(left_money[-1].text) if left_money else None
        tax = parse_amount(right_money[0].text) if right_money else None

        if not name and not unit and amount is None and tax is None:
            continue
        if not name and spec == "" and amount is None and tax is None and unit == "":
            continue

        it = ItemRow(
            name=name,
            spec=spec,
            unit=unit,
            qty=_squash(qty_word.text) if qty_word is not None else "",
            price=price,
            amount=amount,
            rate=rate,
            tax=tax,
        )

        # 名称/规格换行续行：本行没有任何数值 → 并入上一条明细
        has_numbers = bool(numeric_words) or rate_word is not None
        if not has_numbers and (it.name or it.spec) and items:
            items[-1].name += it.name
            items[-1].spec += it.spec
            continue
        if not it.name:
            # 无名称但有数字：这是「合计」行（OCR 常漏识别"合计"二字），单独记账
            if has_numbers:
                pending_totals = {}
                if amount is not None:
                    pending_totals["amount"] = amount
                if tax is not None:
                    pending_totals["tax"] = tax
            continue
        items.append(it)

    totals: dict[str, float] = {}
    if heji is not None:
        vals = []
        for w in heji.words:
            v = parse_amount(w.text)
            if v is not None:
                vals.append((w.cx, v))
        vals.sort()
        if len(vals) >= 2:
            totals["amount"] = vals[0][1]
            totals["tax"] = vals[-1][1]
        elif vals:
            totals["amount"] = vals[0][1]
    elif pending_totals:
        totals = pending_totals
    return items, totals, "ok"


# ------------------------------------------------------------------ 数电票

def _title_digital(layout: PageLayout) -> str:
    m = re.search(r"电子发票[（(]?(?:增值税)?(?:专用发票|普通发票)[）)]?", layout.compact)
    if m:
        return m.group(0)
    if "电子发票" in layout.compact:
        return "电子发票（增值税专用发票）" if "专用发票" in layout.compact else "电子发票（普通发票）"
    return ""


def _parties_side_by_side(
    layout: PageLayout, invoice_number: str = "", *, exact_len: int | None = None
) -> tuple[str, str, str, str]:
    """数电票：购买方在左、销售方在右，同一水平带内按 x 排序。"""
    name_labels = [
        w for w in layout.words if re.match(r"^名称[：:]?$", _squash(w.text))
    ]
    pairs: list[tuple[float, float, str]] = []
    for w in name_labels:
        ln = _line_of(layout, w)
        if ln is None:
            continue
        val = _join(_after(ln, w)).strip(" ：:")
        val = re.sub(r"[：:]$", "", val)
        if val:
            pairs.append((w.cy, w.x0, val))
    pairs.sort()
    if len(pairs) >= 2 and (max(p[0] for p in pairs) - min(p[0] for p in pairs)) < 5:
        pairs.sort(key=lambda p: p[1])  # 同一行：购买方在左
    a_cols = [(p[0], p[1]) for p in pairs]
    buyer = pairs[0][2] if len(pairs) > 0 else ""
    seller = pairs[1][2] if len(pairs) > 1 else ""
    if len(pairs) == 2 and abs(pairs[0][0] - pairs[1][0]) < 3:
        # 同一行左右并排
        buyer, seller = (pairs[0][2], pairs[1][2]) if pairs[0][1] < pairs[1][1] else (pairs[1][2], pairs[0][2])

    ids = []
    for w in layout.words:
        t = _squash(to_halfwidth(w.text))
        if not re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{15,20}", t):
            continue
        if invoice_number and t == invoice_number:
            continue
        if exact_len is not None and len(t) != exact_len:
            continue
        ids.append((w.cy, w.x0, t))
    ids.sort()
    if len(ids) >= 2 and (max(i[0] for i in ids) - min(i[0] for i in ids)) < 5:
        ids.sort(key=lambda i: i[1])  # 同一行：购买方在左

    buyer_id = seller_id = ""
    if a_cols and len(a_cols) >= 2:
        # 按「离哪个名称标签列更近」判定归属：个人购买方没有税号时也不会错配
        bx, sx = a_cols[0][1], a_cols[1][1]
        for _cy, x, t in ids:
            if abs(x - bx) <= abs(x - sx):
                buyer_id = buyer_id or t
            else:
                seller_id = seller_id or t
    else:
        mid = layout.width * 0.5
        for _cy, x, t in ids:
            if x < mid:
                buyer_id = buyer_id or t
            else:
                seller_id = seller_id or t
    return buyer, seller, buyer_id, seller_id


def parse_digital(layout: PageLayout) -> ParseResult:
    res = ParseResult(template="digital", raw_text=layout.text)
    f = res.fields

    f["invoice_type"] = _title_digital(layout)
    f["invoice_code"] = ""

    nums = []
    for w in layout.words:
        m = INVOICE_NO_20_RE.search(_squash(to_halfwidth(w.text)))
        if m and w.cy < layout.height * 0.45:
            nums.append((w.cy, w.x0, m.group(1)))
    nums.sort()
    f["invoice_number"] = nums[0][2] if nums else (
        (INVOICE_NO_20_RE.search(layout.compact) or [None, ""])[1]
        if INVOICE_NO_20_RE.search(layout.compact) else ""
    )

    f["invoice_date"] = parse_date(layout.text) or ""

    buyer, seller, bid, sid = _parties_side_by_side(
        layout, f["invoice_number"], exact_len=18
    )
    f["buyer_name"], f["seller_name"] = buyer, seller
    f["buyer_tax_id"], f["seller_tax_id"] = bid, sid

    items, totals, _ = parse_table(layout)
    res.items = items
    f["item_name"] = "；".join(dict.fromkeys([i.name for i in items if i.name]))
    f["tax_rate"] = "；".join(dict.fromkeys([i.rate for i in items if i.rate]))

    amt = totals.get("amount")
    if amt is None and items:
        vals = [i.amount for i in items if i.amount is not None]
        amt = sum(vals) if vals else None
    tax = totals.get("tax")
    if tax is None and items:
        vals = [i.tax for i in items if i.tax is not None]
        tax = sum(vals) if vals else None
    f["amount"] = round(amt, 2) if amt is not None else None
    f["tax_amount"] = round(tax, 2) if tax is not None else None

    f["total_cn"] = _find_total_cn(layout)
    small = _find_small_total(layout)
    if small is None and amt is not None:
        small = round((amt or 0) + (tax or 0), 2)
    f["total_with_tax"] = round(small, 2) if small is not None else None

    drawer = ""
    for ln in layout.line_list:
        if "开票人" in _squash(ln.text):
            drawer = _value_right_of_line(ln, r"开票人")
            break
    f["drawer"] = drawer

    remark = ""
    for ln in layout.line_list:
        if re.fullmatch(r"备\s*注", _squash(ln.text)) and ln.cy > layout.height * 0.6:
            break
    f["remark"] = remark
    f["color_flag"] = "红字" if ("红字" in layout.text or _negative(res)) else "蓝字"

    _normalize_tax(f)
    _validate(res)
    res.confidence = _confidence(res)
    return res


def _find_total_cn(layout: PageLayout) -> str:
    for ln in layout.line_list:
        t = _squash(ln.text)
        if "大写" in t or "价税合计" in t:
            v = cn_upper_amount(t)
            if v:
                return v
    for w in layout.words:
        v = cn_upper_amount(w.text)
        if v:
            return v
    return ""


def _find_small_total(layout: PageLayout) -> float | None:
    best: float | None = None
    for ln in layout.line_list:
        t = _squash(ln.text)
        if "小写" in t or "价税合计" in t:
            for w in ln.words:
                v = parse_amount(w.text)
                if v is not None and v > 0:
                    if best is None or v > best:
                        best = v
    if best is not None:
        return best
    cands = [parse_amount(w.text) for w in layout.words if "¥" in w.text or "￥" in w.text]
    cands = [v for v in cands if v is not None and v > 0]
    return max(cands) if cands else None


# ------------------------------------------------------------------ 老版电子发票

def _legacy_date(layout: PageLayout) -> str:
    for ln in layout.line_list:
        if DATE_LABEL_RE.search(_squash(ln.text)):
            label = next(w for w in ln.words if DATE_LABEL_RE.search(_squash(w.text)))
            nums = [
                w for w in ln.words
                if w.x0 >= label.x1 - 2 and re.fullmatch(r"\d{1,4}", _squash(w.text))
            ]
            nums.sort(key=lambda w: w.x0)
            if len(nums) >= 3:
                y, mo, d = (_squash(w.text) for w in nums[:3])
                if len(y) == 4:
                    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return parse_date(layout.text) or ""


def _legacy_parties(layout: PageLayout) -> tuple[str, str, str, str]:
    header = None
    for ln in layout.line_list:
        t = _squash(ln.text)
        if "货物或应税" in t or "项目名称" in t:
            header = ln
            break
    hdr_y = header.cy if header is not None else layout.height * 0.4
    footer_y = layout.height * 0.92
    for ln in layout.line_list:
        if "收款人" in _squash(ln.text) or "开票人" in _squash(ln.text):
            footer_y = ln.cy
            break

    def band(y0: float, y1: float) -> tuple[str, str]:
        words = [w for w in layout.words if y0 < w.cy < y1 and w.x0 > layout.width * 0.14]
        words.sort(key=lambda w: (w.cy, w.x0))
        name = ""
        for w in words:
            t = _squash(w.text)
            if re.search(r"公司|厂|店|中心|事务所|商行|合作社|个人", t) and len(t) >= 4:
                name = t
                break
        tax_id = ""
        for w in words:
            t = _squash(to_halfwidth(w.text))
            if re.fullmatch(r"[0-9A-HJ-NPQRTUWXY]{18}", t):
                tax_id = t
                break
        return name, tax_id

    buyer_name, buyer_id = band(k0 := 40.0, max(hdr_y - 2, 90.0))
    seller_name, seller_id = band(hdr_y + 2 if header is not None else hdr_y, footer_y)
    return buyer_name, seller_name, buyer_id, seller_id


def parse_legacy(layout: PageLayout) -> ParseResult:
    res = ParseResult(template="legacy", raw_text=layout.text)
    f = res.fields
    compact = layout.compact

    if "增值税电子专用发票" in compact:
        f["invoice_type"] = "增值税电子专用发票"
    elif "增值税电子普通发票" in compact:
        f["invoice_type"] = "增值税电子普通发票"
    else:
        f["invoice_type"] = "电子发票"

    code = ""
    number = ""
    for ln in layout.line_list:
        t = _squash(ln.text)
        if "发票代码" in t and not code:
            m = re.search(r"(\d{10,12})", t)
            code = m.group(1) if m else re.sub(r"\D", "", _value_right_of_line(ln, r"发票代码"))[:12]
        if "发票号码" in t and not number:
            m = re.search(r"(\d{8})(?!\d)", t)
            number = m.group(1) if m else re.sub(r"\D", "", _value_right_of_line(ln, r"发票号码"))[:8]
    if not code:
        m = re.search(r"发票代码[:：]?(\d{10,12})", compact)
        code = m.group(1) if m else ""
    if not number:
        m = re.search(r"发票号码[:：]?(\d{8})(?!\d)", compact)
        number = m.group(1) if m else ""
    f["invoice_code"] = code
    f["invoice_number"] = number

    f["invoice_date"] = _legacy_date(layout)

    buyer, seller, bid, sid = _legacy_parties(layout)
    f["buyer_name"], f["seller_name"] = buyer, seller
    f["buyer_tax_id"], f["seller_tax_id"] = bid, sid

    items, totals, _ = parse_table(layout)
    res.items = items
    f["item_name"] = "；".join(dict.fromkeys([i.name for i in items if i.name]))
    f["tax_rate"] = "；".join(dict.fromkeys([i.rate for i in items if i.rate]))

    amt = totals.get("amount")
    if amt is None and items:
        vals = [i.amount for i in items if i.amount is not None]
        amt = sum(vals) if vals else None
    tax = totals.get("tax")
    if tax is None and items:
        vals = [i.tax for i in items if i.tax is not None]
        tax = sum(vals) if vals else None
    f["amount"] = round(amt, 2) if amt is not None else None
    f["tax_amount"] = round(tax, 2) if tax is not None else None

    f["total_cn"] = _find_total_cn(layout)
    small = _find_small_total(layout)
    if small is None and amt is not None:
        small = round((amt or 0) + (tax or 0), 2)
    f["total_with_tax"] = round(small, 2) if small is not None else None

    drawer = ""
    m = re.search(r"开票人[:：]\s*([\u4e00-\u9fa5]{2,6}?)(?=收款人|复核|销售方|备注|$)", compact)
    if m:
        drawer = m.group(1)
    f["drawer"] = drawer
    f["remark"] = ""
    f["color_flag"] = "红字" if ("红字" in layout.text or _negative(res)) else "蓝字"

    _normalize_tax(f)
    _validate(res)
    res.confidence = _confidence(res)
    return res


# ------------------------------------------------------------------ 兜底

def parse_generic(layout: PageLayout) -> ParseResult:
    res = ParseResult(template="generic", raw_text=layout.text)
    f = res.fields
    compact = layout.compact

    f["invoice_type"] = "电子发票" if "发票" in compact else ""
    m = INVOICE_NO_20_RE.search(compact)
    f["invoice_number"] = m.group(1) if m else ""
    f["invoice_code"] = ""
    f["invoice_date"] = parse_date(layout.text) or ""
    buyer, seller, bid, sid = _parties_side_by_side(layout)
    f["buyer_name"], f["seller_name"] = buyer, seller
    f["buyer_tax_id"], f["seller_tax_id"] = bid, sid
    items, totals, _ = parse_table(layout)
    res.items = items
    f["item_name"] = "；".join(dict.fromkeys([i.name for i in items if i.name]))
    f["tax_rate"] = "；".join(dict.fromkeys([i.rate for i in items if i.rate]))
    f["amount"] = totals.get("amount")
    f["tax_amount"] = totals.get("tax")
    f["total_cn"] = _find_total_cn(layout)
    f["total_with_tax"] = _find_small_total(layout)
    f["drawer"] = ""
    f["remark"] = ""
    f["color_flag"] = "红字" if _negative(res) else "蓝字"
    res.confidence = _confidence(res) * 0.6
    res.warnings.append("未识别的票面模板，字段可能不完整")
    return res


# ------------------------------------------------------------------ 校验与置信度

def _negative(res: ParseResult) -> bool:
    try:
        return float(res.fields.get("total_with_tax") or 0) < 0
    except (TypeError, ValueError):
        return False


def _normalize_tax(f: dict[str, Any]) -> None:
    """免税/不征税票面税额显示「＊＊＊」，按 0 计入，避免财务汇总出现空值。"""
    rate = str(f.get("tax_rate") or "")
    if f.get("tax_amount") is None and ("免税" in rate or "不征税" in rate):
        f["tax_amount"] = 0.0


def _validate(res: ParseResult) -> None:
    f = res.fields
    amt, tax, total = f.get("amount"), f.get("tax_amount"), f.get("total_with_tax")
    if amt is not None and tax is not None and total is not None:
        if abs(amt + tax - total) > 0.02:
            res.warnings.append(f"金额勾稽不符：{amt}+{tax}≠{total}")
    cn = f.get("total_cn")
    if cn and total is not None:
        v = cn_upper_to_number(cn)
        if v is not None and abs(v - total) > 0.02:
            res.warnings.append(f"大写金额不符：{cn}({v}) vs {total}")
    if not f.get("invoice_number"):
        res.warnings.append("缺少发票号码")
    if not f.get("invoice_date"):
        res.warnings.append("缺少开票日期")
    if not f.get("seller_name"):
        res.warnings.append("缺少销售方名称")


def _confidence(res: ParseResult) -> float:
    f = res.fields
    weights = {
        "invoice_number": 0.22,
        "invoice_date": 0.12,
        "buyer_name": 0.12,
        "seller_name": 0.14,
        "seller_tax_id": 0.08,
        "total_with_tax": 0.18,
        "item_name": 0.08,
        "tax_rate": 0.06,
    }
    score = sum(w for k, w in weights.items() if f.get(k) not in (None, "", 0))
    if res.warnings:
        score -= 0.02 * len(res.warnings)
    return max(0.0, min(1.0, round(score, 3)))


# ------------------------------------------------------------------ 入口

def detect_template(layout: PageLayout) -> str:
    c = layout.compact
    if "发票代码" in c:
        return "legacy"
    if "增值税电子普通发票" in c or "增值税电子专用发票" in c:
        return "legacy"
    if "电子发票" in c and ("增值税专用发票" in c or "普通发票" in c):
        return "digital"
    if "发票号码" in c and re.search(r"\d{20}", c):
        return "digital"
    return "generic"


def parse_layout(layout: PageLayout, filename: str = "") -> ParseResult:
    tpl = detect_template(layout)
    if tpl == "digital":
        res = parse_digital(layout)
    elif tpl == "legacy":
        res = parse_legacy(layout)
    else:
        res = parse_generic(layout)

    if not res.fields.get("invoice_number"):
        m = INVOICE_NO_20_RE.search(filename or "")
        if m:
            res.fields["invoice_number"] = m.group(1)
            res.warnings = [w for w in res.warnings if "缺少发票号码" not in w]
    if not res.fields.get("invoice_date"):
        m = DATETIME_COMPACT_RE.search(filename or "")
        if m:
            d = parse_date(m.group(0))
            if d:
                res.fields["invoice_date"] = d
    return res