"""程序入口：python -m app.main

无参数启动主窗口；`--screenshot out.png` 用于离屏自检（开发用）。
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

# 允许以 `python app/main.py` 直接运行
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "app"

from app.ui.main_window import MainWindow  # noqa: E402

STYLE = """
QMainWindow, QWidget { background: #fbfbfc; }
QGroupBox {
    border: 1px solid #dde3ea; border-radius: 6px; margin-top: 8px;
    background: #ffffff; font-weight: 600; color: #33475b;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background: #ffffff; border: 1px solid #c7d2dd; border-radius: 5px;
    padding: 5px 12px; color: #24313f;
}
QPushButton:hover { background: #eef5fd; border-color: #8fb6dd; }
QPushButton:pressed { background: #e0ecfa; }
QPushButton:disabled { color: #a6b1bd; border-color: #e2e7ec; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {
    background: #ffffff; border: 1px solid #c7d2dd; border-radius: 5px;
    padding: 4px 6px; selection-background-color: #bcd9f7; color: #1e2a36;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border-color: #4a90d9; }
QTableWidget {
    background: #ffffff; gridline-color: #e6ebf0; border: 1px solid #dde3ea;
    selection-background-color: #d7e8fa; selection-color: #10233a;
    alternate-background-color: #f6f9fc;
}
QHeaderView::section {
    background: #eef2f7; border: 0px; border-right: 1px solid #dde3ea;
    border-bottom: 1px solid #dde3ea; padding: 4px 2px; color: #33475b; font-weight: 600;
}
QToolButton { background: #ffffff; border: 1px solid #c7d2dd; border-radius: 5px; padding: 4px 8px; }
QStatusBar { background: #f2f5f9; border-top: 1px solid #dde3ea; }
QMenu { background: #ffffff; border: 1px solid #d5dde5; }
QMenu::item:selected { background: #e3eefb; }
"""


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _write_crash_log(text: str) -> None:
    """把未捕获异常写到程序目录（或 TEMP）的 crash.log，便于离线排障。"""
    cands = []
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
        cands.append(os.path.join(base, "crash.log"))
    except Exception:  # noqa: BLE001
        pass
    import tempfile

    cands.append(os.path.join(tempfile.gettempdir(), "invoice_crash.log"))
    for path in cands:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            return
        except Exception:  # noqa: BLE001
            continue


def run_selftest(args: list[str]) -> int:
    """命令行自检：exe --selftest <文件> <输出.json>（用于验证打包后的识别链路）。"""
    import json
    import traceback

    try:
        src = args[args.index("--selftest") + 1]
        out = args[args.index("--selftest") + 2]
    except (IndexError, ValueError):
        print("用法: --selftest <发票文件> <输出.json>")
        return 2
    try:
        from app.core.engine import recognize_file
        from app.core.expense import ExpenseClassifier

        recs = recognize_file(src, ExpenseClassifier())
        payload = {
            "ok": True,
            "count": len(recs),
            "records": [
                {**{k: v for k, v in r.record.items() if k != "raw_text"},
                 "warnings": r.warnings, "template": r.template}
                for r in recs
            ],
        }
        code = 0
    except Exception:  # noqa: BLE001
        payload = {"ok": False, "error": traceback.format_exc()}
        code = 1
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return code


def _main_guard() -> int:
    """兜住所有未捕获异常：写 crash.log，selftest 模式写结果文件，GUI 模式弹窗而不是静默卡死。"""
    import json
    import traceback

    try:
        return main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        tb = traceback.format_exc()
        _write_crash_log(tb)
        if "--selftest" in sys.argv:
            try:
                out = sys.argv[sys.argv.index("--selftest") + 2]
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump({"ok": False, "error": tb}, fh, ensure_ascii=False)
            except Exception:  # noqa: BLE001
                pass
            return 1
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            QApplication.instance() or QApplication([])
            QMessageBox.critical(
                None,
                "启动失败",
                "程序启动时出错，详细信息已写入 crash.log。\n\n" + tb[-1500:],
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        return run_selftest(args)
    shot = None
    if "--screenshot" in args:
        idx = args.index("--screenshot")
        if idx + 1 < len(args):
            shot = args[idx + 1]

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv[:1] + [])
    app.setApplicationName("电子发票识别助手")
    app.setStyleSheet(STYLE)
    icon_path = resource_path(os.path.join("assets", "app.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    font = QFont("Microsoft YaHei UI", 10)
    app.setFont(font)

    win = MainWindow()
    win.show()

    if shot:
        def _grab() -> None:
            win.grab().save(shot, "PNG")
            app.quit()

        QTimer.singleShot(900, _grab)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(_main_guard())