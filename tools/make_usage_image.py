"""生成一张「使用说明」图：实时界面截图 + 按控件真实位置标注 + 图例。

用 Qt 现场取控件坐标，避免手工估坐标导致标注错位。
用法：python tools/make_usage_image.py
输出：使用说明.png
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.ui.main_window import MainWindow  # noqa: E402

OUT = os.path.join(ROOT, "使用说明.png")
SHOT_TMP = os.path.join(ROOT, "logs", "_usage_shot.png")

W, H = 1600, 1360
MARGIN = 40
SHOT_W = 1300
SHOT_Y = 214

BLUE = (47, 111, 181)
DARK = (32, 44, 58)
GRAY = (108, 122, 136)
LIGHT = (246, 249, 252)
BORDER = (214, 222, 230)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    cands = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def collect_rects(win: MainWindow) -> dict[str, tuple[int, int, int, int]]:
    """取各控件在窗口坐标系里的矩形。"""
    def rect_of(widget) -> tuple[int, int, int, int]:
        p = widget.mapTo(win, QPoint(0, 0))
        return int(p.x()), int(p.y()), int(widget.width()), int(widget.height())

    table = win.table
    hdr = table.horizontalHeader()
    viewport = table.viewport()

    def header_section(index: int) -> tuple[int, int, int, int]:
        x = hdr.sectionViewportPosition(index)
        p = hdr.mapTo(win, QPoint(x, 0))
        return int(p.x()), int(p.y()), int(hdr.sectionSize(index)), int(hdr.height())

    first_row = table.visualRect(table.model().index(0, 0))
    row_x = viewport.mapTo(win, QPoint(first_row.x(), first_row.y())).x()
    row_y = viewport.mapTo(win, QPoint(first_row.x(), first_row.y())).y()

    return {
        "import": rect_of(win.btn_import),
        "drop": rect_of(win.lbl_drop),
        "check": (row_x, row_y, int(first_row.width()), int(first_row.height())),
        "copy": header_section(2),
        "search": rect_of(win.btn_search),
        "export": rect_of(win.btn_export),
    }


def main() -> None:
    app = QApplication(sys.argv[:1])
    win = MainWindow()
    win.show()
    app.processEvents()
    app.processEvents()

    rects = collect_rects(win)
    win.grab().save(SHOT_TMP, "PNG")
    win.close()

    shot = Image.open(SHOT_TMP).convert("RGB")
    scale0 = shot.width / win.width()

    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # ---- 顶部标题 ----
    d.rectangle([0, 0, W, 120], fill=(242, 247, 253))
    d.text((MARGIN, 24), "电子发票识别助手 · 使用说明", font=font(46, True), fill=DARK)
    d.text(
        (MARGIN + 2, 82),
        "三步上手：导入  →  核对  →  复制 / 导出        全程离线 · 数据本地 · 解压即用",
        font=font(23),
        fill=GRAY,
    )

    # ---- 截图 ----
    ratio = SHOT_W / shot.width
    shot_r = shot.resize((SHOT_W, int(shot.height * ratio)), Image.LANCZOS)
    x0, y0 = MARGIN, SHOT_Y
    d.rounded_rectangle(
        [x0 - 3, y0 - 3, x0 + shot_r.width + 3, y0 + shot_r.height + 3],
        radius=10, outline=BORDER, width=2,
    )
    img.paste(shot_r, (x0, y0))

    def to_img(px: float, py: float) -> tuple[int, int]:
        return x0 + int(px * scale0 * ratio), y0 + int(py * scale0 * ratio)

    # ---- 高亮框 + 编号 ----
    order = [
        ("import", 1),
        ("drop", 2),
        ("check", 3),
        ("copy", 4),
        ("search", 5),
        ("export", 6),
    ]
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    taken: list[tuple[int, int]] = []
    badge_pos: dict[int, tuple[int, int]] = {}

    def clamp(v: float, lo: int, hi: int) -> int:
        return int(max(lo, min(hi, v)))

    def place(x1: int, y1: int, x2: int, y2: int) -> tuple[int, int]:
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        r = 15
        cands = [
            (x1 - 16, y1 - 16), (x1 - 16, y2 + 16), (x2 + 16, y1 - 16), (x2 + 16, y2 + 16),
            (x1 - 18, cy), (x2 + 18, cy), (cx, y1 - 18), (cx, y2 + 18),
        ]
        best = None
        for bx, by in cands:
            bx = clamp(bx, r + 6, W - r - 6)
            by = clamp(by, 132, H - r - 6)
            if any((bx - a) ** 2 + (by - b) ** 2 < 46 ** 2 for a, b in taken):
                continue
            if x1 - 4 <= bx <= x2 + 4 and y1 - 4 <= by <= y2 + 4:
                continue
            best = (bx, by)
            break
        if best is None:
            best = (clamp(x1 - 16, r + 6, W - r - 6), clamp(y1 - 16, 132, H - r - 6))
        taken.append(best)
        return best

    for key, num in order:
        rx, ry, rw, rh = rects[key]
        x1, y1 = to_img(rx - 2, ry - 2)
        x2, y2 = to_img(rx + rw + 2, ry + rh + 2)
        if key == "copy":
            # 只框表头右侧的复制图标
            x1 = to_img(rx + rw - 24, ry + 3)[0]
            x2 = to_img(rx + rw + 2, ry + rh - 3)[0]
        od.rounded_rectangle([x1, y1, x2, y2], radius=6, outline=BLUE + (255,), width=4)
        od.rounded_rectangle([x1, y1, x2, y2], radius=6, fill=BLUE + (30,))
        badge_pos[num] = place(x1, y1, x2, y2)

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)
    for num, (bx, by) in badge_pos.items():
        r = 17
        d.ellipse([bx - r - 3, by - r - 3, bx + r + 3, by + r + 3], fill=(255, 255, 255))
        d.ellipse([bx - r, by - r, bx + r, by + r], fill=BLUE)
        d.text((bx, by), str(num), font=font(22, True), fill=(255, 255, 255), anchor="mm")

    # ---- 图例 ----
    legend_top = y0 + shot_r.height + 34
    items = [
        (1, "导入", "点「导入文件 / 导入文件夹」，或拖 PDF、图片进窗口"),
        (2, "粘贴", "截图后在本窗口按 Ctrl+V，直接识别"),
        (3, "勾选", "勾单条或多条，可批量导出、删除；删错点「撤销删除」"),
        (4, "复制整列", "点表头右侧图标，粘到 Excel 就是一行"),
        (5, "查询", "条件都是「且」、可全留空，填好点「查询」"),
        (6, "导出", "勾字段 + 选范围，导出 Excel / CSV"),
    ]
    col_x = [MARGIN + 4, MARGIN + 800]
    row_h = 64
    for i, (num, title, desc) in enumerate(items):
        cx = col_x[i % 2]
        cy = legend_top + (i // 2) * row_h
        d.ellipse([cx, cy + 3, cx + 32, cy + 35], fill=BLUE)
        d.text((cx + 16, cy + 19), str(num), font=font(20, True), fill=(255, 255, 255),
               anchor="mm")
        d.text((cx + 46, cy), title, font=font(25, True), fill=DARK)
        tw = d.textlength(title, font=font(25, True))
        d.text((cx + 46 + tw + 12, cy + 4), desc, font=font(21), fill=GRAY)

    # ---- 底部信息 ----
    bar_y = H - 112
    d.rectangle([0, bar_y, W, H], fill=LIGHT)
    d.line([0, bar_y, W, bar_y], fill=BORDER, width=2)
    d.text((MARGIN, bar_y + 18),
           "数据位置：程序目录  data\\invoices.db（SQLite 单文件，永久保存；换电脑拷整个文件夹即可）",
           font=font(22), fill=DARK)
    d.text((MARGIN, bar_y + 56),
           "运行环境：Windows 10 64 位（1809+）/ Windows 11，无需安装 Python、VC++ 运行库等任何依赖",
           font=font(22), fill=GRAY)
    d.text((W - MARGIN, bar_y + 22), "图中为示例数据，已脱敏", font=font(20), fill=GRAY, anchor="rt")
    d.text((W - MARGIN, H - 22), "开发人：杨广", font=font(20), fill=GRAY, anchor="rs")

    img.save(OUT, "PNG")
    try:
        os.remove(SHOT_TMP)
    except OSError:
        pass
    print("saved:", OUT, os.path.getsize(OUT) // 1024, "KB", img.size)


if __name__ == "__main__":
    main()
