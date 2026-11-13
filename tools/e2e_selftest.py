"""端到端自检：走真实界面链路的「导入 → 去重 → 粘贴截图 → 查询 → 导出 → 删除」。

运行：python tools/e2e_selftest.py
需要本地样本目录（默认 samples/，可用 INVOICE_OCR_SAMPLES 指定），找不到会自动跳过。
会使用临时数据目录，不影响正式数据。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools._samples import require_pdfs  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def wait_worker(app, win, timeout: float = 120.0) -> None:
    t0 = time.time()
    while win._worker is not None and win._worker.isRunning():
        app.processEvents()
        time.sleep(0.05)
        if time.time() - t0 > timeout:
            raise TimeoutError("识别超时")
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()


def main() -> int:
    pdfs = require_pdfs(3)
    pdf_a, pdf_b, pdf_c = pdfs[0], pdfs[1], pdfs[2]

    tmp = tempfile.mkdtemp(prefix="invoice_e2e_")
    os.environ["INVOICE_DATA_DIR"] = tmp

    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    win = MainWindow()
    check("初始为空库", win.total == 0, f"{win.total}")

    # ---- 1. 多文件导入 ----
    win.start_recognize([pdf_a, pdf_b])
    wait_worker(app, win)
    check("多文件导入 2 条", win.total == 2, f"{win.total} 条")
    check("最新记录排第一", win.rows[0]["source_file"] == os.path.basename(pdf_b),
          win.rows[0]["source_file"] if win.rows else "")
    check("识别出号码", all(bool(r.get("invoice_number")) for r in win.rows))

    # ---- 2. 重复票去重 ----
    win.start_recognize([pdf_a])
    wait_worker(app, win)
    check("重复票不重复入库", win.total == 2, f"{win.total} 条")

    # ---- 3. 粘贴截图（渲染成图片后走 OCR） ----
    import fitz

    png = os.path.join(tmp, "clip.png")
    with fitz.open(pdf_c) as doc:
        doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False).save(png)
    img = QImage(png)
    check("剪贴板图片已生成", not img.isNull())
    app.clipboard().setImage(img)
    win.paste_anything()
    wait_worker(app, win)
    check("粘贴截图识别成功", win.total == 3, f"{win.total} 条")
    pasted = win.rows[0]
    check("截图 OCR 结果非空", bool(pasted.get("invoice_number")), str(pasted.get("invoice_number")))

    # ---- 4. 查询 ----
    rec = win.rows[0]
    probe = (rec.get("seller_name") or rec.get("buyer_name") or "")[:4]
    if probe:
        win.ed_company.setText(probe)
        win.apply_filters()
        rows = list(win.db.iter_rows(win.filters))
        check(
            "公司名查询命中且结果正确",
            bool(rows) and all(probe in (r["seller_name"] or "") + (r["buyer_name"] or "") for r in rows),
            f"{len(rows)} 条",
        )
        win.reset_filters()

    amount = float(rec.get("total_with_tax") or 0)
    win.mn_min.set_value(max(0, amount - 1))
    win.mn_max.set_value(amount + 1)
    win.apply_filters()
    check("金额区间查询命中", win.total >= 1, f"{win.total} 条")
    win.reset_filters()

    # ---- 5. 复制整列 ----
    win.reload()
    app.clipboard().clear()
    win._copy_column(2)
    check("整列复制含全部行", len(app.clipboard().text().split("\t")) == win.total)

    # ---- 6. 导出 ----
    from app.core.exporter import export_rows
    from app.core.fields import EXPORT_FIELDS

    out = os.path.join(tmp, "out.xlsx")
    rows = list(win.db.iter_rows({}))
    path, n = export_rows(rows, EXPORT_FIELDS, out, fmt="xlsx")
    check("导出 xlsx 全部记录", os.path.exists(path) and n == win.total, f"{n} 行")

    # ---- 7. 勾选删除（单条/多条）与撤销 ----
    win.reload()
    ids = [r["id"] for r in win.rows[:2]]
    win.delete_rows(ids, confirm=False)
    check("勾选两条删除", win.total == 1, f"{win.total} 条")
    check("删除后物理记录仍在", win.db.count_all_including_deleted() == 3)
    check("撤销按钮已出现", not win.btn_undo.isHidden())
    win.undo_delete()
    check("撤销删除后恢复", win.total == 3, f"{win.total} 条")

    single = win.rows[0]["id"]
    win.delete_rows([single], confirm=False)
    check("删除单条", win.total == 2, f"{win.total} 条")
    win.undo_delete()
    check("单条撤销", win.total == 3, f"{win.total} 条")

    win.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("失败项：", FAILS)
        return 1
    print("端到端自检全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())