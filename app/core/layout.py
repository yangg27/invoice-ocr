"""版式层：把任意来源（PDF 文本层 / OCR）归一成带坐标的 token 与行。

所有坐标都是「页面坐标」（PDF: point，图片: pixel），y 向下增长。
解析器只依赖相对位置与行结构，因此 PDF 与截图可以共用同一套规则。
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(slots=True)
class Word:
    """一个不可切的文本块（token），带页面坐标与字号。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 7.0
    page: int = 0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def is_blank(self) -> bool:
        return not self.text.strip()

    def __repr__(self) -> str:  # 调试友好
        return f"W({self.text!r},{self.x0:.1f},{self.y0:.1f},{self.x1:.1f},{self.y1:.1f})"


@dataclass
class Line:
    """同一视觉行上的 token 序列。"""

    words: list[Word] = field(default_factory=list)

    @property
    def cy(self) -> float:
        if not self.words:
            return 0.0
        return sum(w.cy for w in self.words) / len(self.words)

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def size(self) -> float:
        vals = [w.size for w in self.words if w.text.strip()]
        return statistics.median(vals) if vals else 7.0

    @property
    def text(self) -> str:
        """按视觉间距拼接；中文之间不插空格，明显空隙处插一个空格。"""
        out: list[str] = []
        prev: Word | None = None
        for w in self.words:
            if w.is_blank():
                out.append(" ")
                prev = None
                continue
            if prev is not None:
                gap = w.x0 - prev.x1
                if gap > max(0.45 * min(w.size, prev.size), 1.2):
                    out.append(" ")
            out.append(w.text)
            prev = w
        return re.sub(r"\s+", " ", "".join(out)).strip()

    @property
    def raw(self) -> str:
        """无空格拼接，用于处理被拆开的号码。"""
        return "".join(w.text for w in self.words if not w.is_blank())

    def find(self, pattern: str | re.Pattern[str]) -> list[Word]:
        rgx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return [w for w in self.words if rgx.search(w.text)]

    def right_of(self, x: float, y_tol: float = 99) -> list[Word]:
        return sorted(
            [w for w in self.words if w.x0 >= x - 0.6 and abs(w.cy - self.cy) <= y_tol],
            key=lambda w: w.x0,
        )

    def after(self, word: Word) -> list[Word]:
        """同一行内位于 word 右侧的 token（按 x 升序）。

        允许少量重叠：PDF 里标签 bbox 常把冒号算进去，值却在冒号前起笔。
        """
        limit = word.x1 - 0.4 * max(word.w, 1.0)
        return sorted(
            [w for w in self.words if w is not word and w.x0 >= limit],
            key=lambda w: w.x0,
        )

    def before(self, word: Word) -> list[Word]:
        return sorted(
            [w for w in self.words if w is not word and w.x1 <= word.x0 + 0.5],
            key=lambda w: w.x0,
        )


@dataclass
class PageLayout:
    """一页的完整版式结果。"""

    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    page_no: int = 0
    source: str = "pdf"  # pdf | image | clipboard
    dpi: float = 200.0

    _lines: list[Line] | None = None

    def lines(self) -> list[Line]:
        return build_lines(self.words)

    @property
    def line_list(self) -> list[Line]:
        if self._lines is None:
            self._lines = build_lines(self.words)
        return self._lines

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.line_list)

    @property
    def compact(self) -> str:
        """去掉所有空白后的整页文本，用于宽松匹配。"""
        return re.sub(r"\s+", "", self.text)

    # ---- 相对坐标工具（0..1，对截图缩放不敏感） ----
    @property
    def rel_w(self) -> float:
        return self.width or 1.0

    @property
    def rel_h(self) -> float:
        return self.height or 1.0

    def rx(self, x: float) -> float:
        return x / self.rel_w

    def ry(self, y: float) -> float:
        return y / self.rel_h

    def lines_in_band(self, y0: float, y1: float) -> list[Line]:
        return [ln for ln in self.line_list if y0 <= ln.cy <= y1]

    def find_line(self, pattern: str | re.Pattern[str], start: int = 0) -> Line | None:
        rgx = re.compile(pattern) if isinstance(pattern, str) else pattern
        for ln in self.line_list[start:]:
            if rgx.search(ln.text):
                return ln
        return None

    def find_lines(self, pattern: str | re.Pattern[str]) -> list[Line]:
        rgx = re.compile(pattern) if isinstance(pattern, str) else pattern
        return [ln for ln in self.line_list if rgx.search(ln.text)]

    def all_text(self) -> str:
        return self.text

    def nearest_word(
        self,
        x: float,
        y: float,
        *,
        predicate=None,
        max_dist: float = 1e9,
    ) -> Word | None:
        best: Word | None = None
        best_d = max_dist
        for w in self.words:
            if w.is_blank():
                continue
            if predicate is not None and not predicate(w):
                continue
            dx = w.cx - x
            dy = w.cy - y
            d = (dx * dx + dy * dy) ** 0.5
            if d <= best_d:
                best_d = d
                best = w
        return best


def build_lines(words: Sequence[Word], y_factor: float = 0.45) -> list[Line]:
    """按 y 中心聚类成行，行内按 x 排序。"""
    ws = [w for w in words if not w.is_blank()]
    if not ws:
        return []
    sizes = [w.size for w in ws if w.size > 0]
    base = statistics.median(sizes) if sizes else 7.0
    tol = max(1.6, y_factor * base)

    ordered = sorted(ws, key=lambda w: (w.cy, w.x0))
    lines: list[Line] = []
    cur: list[Word] = []
    cur_cy = None
    for w in ordered:
        if cur_cy is None or abs(w.cy - cur_cy) <= tol:
            cur.append(w)
            cur_cy = (cur_cy * (len(cur) - 1) + w.cy) / len(cur) if cur_cy is not None else w.cy
        else:
            cur.sort(key=lambda t: t.x0)
            lines.append(Line(cur))
            cur = [w]
            cur_cy = w.cy
    if cur:
        cur.sort(key=lambda t: t.x0)
        lines.append(Line(cur))

    # 行内按视觉 y 再校正一次（同一行 token 基线可能略有偏差）
    for ln in lines:
        ln.words.sort(key=lambda t: (round(t.y0), t.x0))
    lines.sort(key=lambda ln: (ln.cy, ln.x0))
    return lines


# ---------------------------------------------------------------- 文本归一化

_FW = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}
_FW.update({"　": " ", "￥": "¥", "，": ","})


def to_halfwidth(s: str) -> str:
    return "".join(_FW.get(ch, ch) for ch in s)


_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def norm_digits(s: str) -> str:
    return s.translate(_DIGITS)


AMOUNT_RE = re.compile(r"-?¥?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def parse_amount(s: str) -> float | None:
    """从一段文本中取出金额；支持 ¥、千分位、括号负数、汉字负号。"""
    if not s:
        return None
    t = to_halfwidth(s).replace(" ", "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t.startswith("-") or t.startswith("－"):
        neg, t = True, t[1:]
    t = t.replace("¥", "").replace("￥", "")
    m = re.search(r"[0-9][0-9,]*(?:\.[0-9]{1,2})?", t)
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


DATE_CN_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
DATE_ISO_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
DATETIME_COMPACT_RE = re.compile(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def parse_date(s: str) -> str | None:
    """返回 YYYY-MM-DD。"""
    if not s:
        return None
    t = norm_digits(to_halfwidth(s))
    for rgx in (DATE_CN_RE, DATE_ISO_RE):
        m = rgx.search(t)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    m = DATETIME_COMPACT_RE.search(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


TAX_RATE_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%|免税|不征税|零税率|0%")


def parse_tax_rate(s: str) -> str | None:
    if not s:
        return None
    t = to_halfwidth(s)
    if "免税" in t:
        return "免税"
    if "不征税" in t:
        return "不征税"
    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", t)
    if m:
        v = m.group(1)
        try:
            f = float(v)
        except ValueError:
            return None
        if f == 0:
            return "0%"
        return f"{f:g}%"
    return None


TAX_ID_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{15,20}")
INVOICE_NO_20_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")
INVOICE_NO_8_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
INVOICE_CODE_RE = re.compile(r"(?<!\d)(\d{10,12})(?!\d)")
CHECK_CODE_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")


def cn_upper_amount(s: str) -> str | None:
    """抽取大写金额串，例如 伍仟圆整 / 肆拾贰圆伍角肆分。"""
    if not s:
        return None
    m = re.search(
        r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元角分整正]{3,}",
        to_halfwidth(s),
    )
    return m.group(0) if m else None