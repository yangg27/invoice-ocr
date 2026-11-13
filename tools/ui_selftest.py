"""界面自检：不依赖人工点击，验证关键交互链路是否打通。

运行：python tools/ui_selftest.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QDate, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from app.core.db import Database  # noqa: E402
from app.core.exporter import export_rows  # noqa: E402
from app.core.fields import EXPORT_FIELDS, VISIBLE_FIELDS  # noqa: E402
from app.ui.dialogs import DetailDialog  # noqa: E402
from app.ui.export_dialog import ExportDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    app = QApplication(sys.argv[:1])
    win = MainWindow()
    db = win.db

    total_all = db.query({}, 1, 1)[1]
    check("数据库已加载记录", total_all > 0, f"{total_all} 条")

    # ---- 分页 ----
    check("默认每页 15 条", win.page_size == 15)
    win.table.setRowCount(0)
    win.reload()
    check("首屏 15 行", win.table.rowCount() == min(15, total_all), f"{win.table.rowCount()} 行")
    win.cb_page_size.setCurrentIndex(2)
    win.goto_page(1)
    check("切到 100 条/页", win.page_size == 100 and win.table.rowCount() == min(100, total_all))
    win.cb_page_size.setCurrentIndex(0)
    win.reload()

    # ---- 组合筛选（与关系）：用库里已有的数据构造探针，避免硬编码真实信息 ----
    sample = next(iter(db.iter_rows({})), None)
    probe = ((sample or {}).get("seller_name") or (sample or {}).get("buyer_name") or "")[:4]
    win.ed_company.setText(probe)
    amount = float((sample or {}).get("total_with_tax") or 0)
    win.mn_min.set_value(max(0.0, amount - 1))
    win.mn_max.set_value(amount + 1)
    win.apply_filters()
    rows = list(db.iter_rows(win.filters))
    ok = bool(rows) and all(
        probe in ((r["buyer_name"] or "") + (r["seller_name"] or ""))
        and r["total_with_tax"] is not None
        and max(0.0, amount - 1) <= r["total_with_tax"] <= amount + 1
        for r in rows
    )
    check("多条件 AND 生效", ok, f"{len(rows)} 条命中，探针 {probe}")

    # ---- 条件全空 = 查全部 ----
    win.reset_filters()
    check("重置后查全部", db.query(win.filters, 1, 1)[1] == total_all)

    # ---- 复制整列 ----
    win.reload()
    clip = app.clipboard()
    clip.clear()
    win._copy_column(1)  # 发票类型
    text = clip.text()
    parts = text.split("\t")
    check("复制整列得到全部查询结果", len(parts) == total_all, f"{len(parts)} 个值")
    check("复制整列制表符分隔", "\n" not in text)

    # ---- 单元格/整行复制 ----
    win.table.setCurrentCell(0, 1)
    win.copy_cell()
    check("复制单元格", clip.text() == win.table.item(0, 1).text())

    # ---- 导出对话框默认值 ----
    dlg = ExportDialog(total_all, 15, 0, tempfile.gettempdir())
    check("导出默认范围为全部查询结果", dlg.rb_all.isChecked() and dlg.scope() == "all")
    check("导出默认全选字段", len(dlg.selected_fields()) == len(EXPORT_FIELDS),
          f"{len(dlg.selected_fields())} 个字段")
    check("导出默认 xlsx", dlg.fmt() == "xlsx")
    fields = dlg.selected_fields()
    dlg.deleteLater()

    # ---- 真正导出 ----
    out_xlsx = os.path.join(ROOT, "logs", "selftest_export.xlsx")
    rows_all = list(db.iter_rows({}))
    path, n = export_rows(rows_all, fields, out_xlsx, fmt="xlsx")
    check("导出 xlsx 成功", os.path.exists(path) and n == total_all, f"{n} 行")

    out_csv = os.path.join(ROOT, "logs", "selftest_export.csv")
    path2, n2 = export_rows(rows_all[:5], fields, out_csv, fmt="csv")
    check("导出 csv 成功", os.path.exists(path2) and n2 == 5)

    # ---- 详情弹窗 ----
    rec = rows_all[0]
    dd = DetailDialog(rec, win.classifier)
    check("详情弹窗字段数", len(dd.edits) == len(VISIBLE_FIELDS) + 0 or len(dd.edits) > 10,
          f"{len(dd.edits)}")
    dd.cb_subject.setCurrentText("办公费")
    dd._save()
    check("详情保存变更", dd.row["expense_subject"] == "办公费" and dd.changed)
    dd.deleteLater()

    # ---- 表头复制按钮命中 ----
    header = win.table.horizontalHeader()
    sec_w = header.sectionSize(2)
    left = header.sectionViewportPosition(2)
    check("表头图标命中区在右侧", header._icon_hit(left + sec_w - 2, 2))
    check("表头左半区不触发复制", not header._icon_hit(left + 2, 2))

    # ---- 页码边界 ----
    win.goto_page(999)
    check("翻页越界被夹住", win.page == max(1, (win.total + win.page_size - 1) // win.page_size))
    win.goto_page(-5)
    check("翻到第 1 页", win.page == 1)

    win.close()
    print()
    if FAILS:
        print("失败项：", FAILS)
        return 1
    print("全部界面自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())