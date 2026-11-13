"""主窗口：导入 / 粘贴 / 批量识别 / 列表 / 多条件筛选 / 分页 / 复制 / 导出。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.db import Database
from ..core.engine import recognize_file
from ..core.expense import ExpenseClassifier
from ..core.exporter import export_rows
from ..core.fields import FIELD_BY_KEY, VISIBLE_FIELDS
from ..core.textextract import extract_clipboard_image, is_supported, iter_supported_files
from .dialogs import DetailDialog, SettingsDialog
from .export_dialog import ExportDialog
from .widgets import CopyHeader, MoneyEdit, MultiSelectButton, OptionalDateEdit

PAGE_SIZES = [15, 50, 100]


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fmt_value(key: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if key == "amount" or key == "tax_amount" or key == "total_with_tax":
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


class RecognizeWorker(QThread):
    progress = Signal(int, int, str)
    finished_batch = Signal(dict)

    def __init__(self, paths: list[str], db: Database, classifier: ExpenseClassifier, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.db = db
        self.classifier = classifier

    def run(self) -> None:  # noqa: D401
        ok = skipped = 0
        failed: list[str] = []
        total = len(self.paths)
        for i, path in enumerate(self.paths):
            self.progress.emit(i, total, os.path.basename(path))
            try:
                for item in recognize_file(path, self.classifier):
                    rec = item.record
                    if not rec.get("invoice_number"):
                        failed.append(
                            f"{os.path.basename(path)}：未识别到发票号码（可能不是发票或图片过糊）"
                        )
                        continue
                    if self.db.find_duplicate(rec["invoice_number"], rec["invoice_code"]):
                        skipped += 1
                        continue
                    self.db.insert(rec)
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{os.path.basename(path)}：{exc}")
        self.finished_batch.emit({"ok": ok, "skipped": skipped, "failed": failed, "total": total})


def resolve_data_dir(root: str) -> tuple[str, bool]:
    """优先用程序目录下的 data；不可写（如解压在 Program Files）时退回用户目录。"""
    forced = os.environ.get("INVOICE_DATA_DIR")
    candidates = [forced] if forced else [os.path.join(root, "data")]
    if not forced:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        candidates.append(os.path.join(base, "InvoiceOCR", "data"))
    for cand in candidates:
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".dsh_write_test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("1")
            os.remove(probe)
            return cand, bool(forced) is False and cand != candidates[0]
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("找不到可写的数据目录，请把程序解压到桌面或 D 盘等可写位置。")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("电子发票识别助手（离线版）")
        self.resize(1560, 940)
        self.setAcceptDrops(True)

        root = app_root()
        self.data_dir, self.data_fallback = resolve_data_dir(root)
        os.environ.setdefault("INVOICE_TMP_DIR", os.path.join(self.data_dir, "_tmp"))
        self.db = Database(os.path.join(self.data_dir, "invoices.db"))
        self.rules_path = os.path.join(self.data_dir, "expense_rules.json")
        self.classifier = ExpenseClassifier.load(self.rules_path)

        self.page_size = PAGE_SIZES[0]
        self.page = 1
        self.total = 0
        self.rows: list[dict[str, Any]] = []
        self.filters: dict[str, Any] = {}
        self._worker: RecognizeWorker | None = None
        self._auto_turn = False
        self._last_deleted: list[int] = []

        self._build_ui()
        self._refresh_choices()
        self.reload()
        if self.data_fallback:
            self.lbl_status.setText(
                f"程序目录不可写，数据已改存到：{self.data_dir}"
            )

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(8)

        # ---- 工具栏 ----
        bar = QHBoxLayout()
        self.btn_import = QPushButton("导入文件")
        self.btn_folder = QPushButton("导入文件夹")
        self.btn_paste = QPushButton("粘贴截图/文件")
        self.btn_export = QPushButton("导出记录")
        self.btn_settings = QPushButton("设置")
        for b in (self.btn_import, self.btn_folder, self.btn_paste, self.btn_export, self.btn_settings):
            b.setMinimumHeight(32)
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_stats = QLabel("共 0 条")
        self.lbl_stats.setStyleSheet("color:#444; font-weight:600;")
        bar.addWidget(self.lbl_stats)
        root.addLayout(bar)

        self.lbl_drop = QLabel(
            "把 PDF / 图片拖到这里，或按 Ctrl+V 粘贴截图；也支持一次拖入多个文件或整个文件夹"
        )
        self.lbl_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_drop.setStyleSheet(
            "border:1px dashed #9fb6cd; border-radius:6px; padding:7px; color:#4a6b8a; background:#f5f9ff;"
        )
        root.addWidget(self.lbl_drop)

        # ---- 筛选 ----
        fbox = QGroupBox("筛选（条件之间是「且」关系，全部可留空；全空即查全部）")
        grid = QGridLayout(fbox)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.ed_keyword = QLineEdit()
        self.ed_keyword.setPlaceholderText("号码/名称/税号/项目/备注…")
        self.ed_company = QLineEdit()
        self.ed_company.setPlaceholderText("买方或卖方公司名")
        self.ed_tax = QLineEdit()
        self.ed_tax.setPlaceholderText("买方或卖方税号")
        self.ed_number = QLineEdit()
        self.ed_number.setPlaceholderText("发票号码")

        grid.addWidget(QLabel("关键字"), 0, 0)
        grid.addWidget(self.ed_keyword, 0, 1)
        grid.addWidget(QLabel("公司名"), 0, 2)
        grid.addWidget(self.ed_company, 0, 3)
        grid.addWidget(QLabel("税号"), 0, 4)
        grid.addWidget(self.ed_tax, 0, 5)
        grid.addWidget(QLabel("发票号码"), 0, 6)
        grid.addWidget(self.ed_number, 0, 7)

        self.dt_from = OptionalDateEdit()
        self.dt_to = OptionalDateEdit()
        self.ct_from = OptionalDateEdit()
        self.ct_to = OptionalDateEdit()
        grid.addWidget(QLabel("开票日期"), 1, 0)
        row1 = QHBoxLayout()
        row1.addWidget(self.dt_from)
        row1.addWidget(QLabel("~"))
        row1.addWidget(self.dt_to)
        grid.addLayout(row1, 1, 1)
        grid.addWidget(QLabel("识别时间"), 1, 2)
        row2 = QHBoxLayout()
        row2.addWidget(self.ct_from)
        row2.addWidget(QLabel("~"))
        row2.addWidget(self.ct_to)
        grid.addLayout(row2, 1, 3)
        grid.addWidget(QLabel("金额"), 1, 4)
        row3 = QHBoxLayout()
        self.mn_min = MoneyEdit("最小")
        self.mn_max = MoneyEdit("最大")
        row3.addWidget(self.mn_min)
        row3.addWidget(QLabel("~"))
        row3.addWidget(self.mn_max)
        grid.addLayout(row3, 1, 5)

        self.cb_subject = MultiSelectButton("费用科目")
        self.cb_rate = MultiSelectButton("税率")
        self.cb_type = MultiSelectButton("发票类型")
        grid.addWidget(self.cb_subject, 1, 6)
        grid.addWidget(self.cb_rate, 1, 7)

        act_row = QHBoxLayout()
        act_row.addWidget(QLabel("发票类型"), 0)
        act_row.addWidget(self.cb_type, 0)
        act_row.addStretch(1)
        self.btn_search = QPushButton("查询")
        self.btn_search.setMinimumHeight(30)
        self.btn_reset = QPushButton("重置")
        self.btn_reset.setMinimumHeight(30)
        act_row.addWidget(self.btn_search)
        act_row.addWidget(self.btn_reset)
        grid.addLayout(act_row, 2, 0, 1, 8)
        root.addWidget(fbox)

        # ---- 表格 ----
        self.table = QTableWidget(0, len(VISIBLE_FIELDS) + 1)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(27)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)

        header = CopyHeader(Qt.Orientation.Horizontal, self.table)
        self.table.setHorizontalHeader(header)
        headers = ["选"] + [f.label for f in VISIBLE_FIELDS]
        self.table.setHorizontalHeaderLabels(headers)
        header.copyRequested.connect(self._copy_column)
        header.setMinimumSectionSize(48)

        self.table.setColumnWidth(0, 36)
        for i, f in enumerate(VISIBLE_FIELDS, start=1):
            self.table.setColumnWidth(i, f.width)
        root.addWidget(self.table, 1)

        # ---- 分页 ----
        pager = QHBoxLayout()
        self.cb_page_size = QComboBox()
        for n in PAGE_SIZES:
            self.cb_page_size.addItem(f"每页 {n} 条", n)
        self.cb_page_size.setCurrentIndex(0)
        self.btn_prev = QPushButton("上一页")
        self.btn_next = QPushButton("下一页")
        self.lbl_page = QLabel("第 1 / 1 页")
        self.cb_autoturn = QCheckBox("滚动到底自动翻页")
        self.cb_autoturn.setChecked(True)
        self.cb_all_page = QCheckBox("全选本页")
        self.btn_undo = QPushButton("撤销删除")
        self.btn_undo.setVisible(False)
        self.btn_del = QPushButton("删除勾选")
        pager.addWidget(QLabel("每页"))
        pager.addWidget(self.cb_page_size)
        pager.addWidget(self.btn_prev)
        pager.addWidget(self.lbl_page)
        pager.addWidget(self.btn_next)
        pager.addStretch(1)
        pager.addWidget(self.cb_autoturn)
        pager.addWidget(self.cb_all_page)
        pager.addWidget(self.btn_undo)
        pager.addWidget(self.btn_del)
        root.addLayout(pager)

        # ---- 状态栏 ----
        self.status = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setFixedWidth(240)
        self.progress.setVisible(False)
        self.lbl_status = QLabel("就绪")
        self.status.addWidget(self.lbl_status, 1)
        self.status.addPermanentWidget(self.progress)
        self.lbl_dev = QLabel("开发人：杨广")
        self.lbl_dev.setStyleSheet("color:#6f7c89; padding:0 10px 0 6px;")
        self.lbl_dev.setToolTip("电子发票识别助手 · 开发人：杨广")
        self.status.addPermanentWidget(self.lbl_dev)

        # ---- 行为 ----
        self.btn_import.clicked.connect(self.import_files)
        self.btn_folder.clicked.connect(self.import_folder)
        self.btn_paste.clicked.connect(self.paste_anything)
        self.btn_export.clicked.connect(self.export)
        self.btn_settings.clicked.connect(self.open_settings)
        self.btn_search.clicked.connect(self.apply_filters)
        self.btn_reset.clicked.connect(self.reset_filters)
        self.cb_page_size.currentIndexChanged.connect(self._on_page_size)
        self.btn_prev.clicked.connect(lambda: self.goto_page(self.page - 1))
        self.btn_next.clicked.connect(lambda: self.goto_page(self.page + 1))
        self.cb_all_page.stateChanged.connect(self._toggle_all_page)
        self.btn_del.clicked.connect(self.delete_checked)
        self.btn_undo.clicked.connect(self.undo_delete)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(lambda *_: self.open_detail())
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)

        for ed in (self.ed_keyword, self.ed_company, self.ed_tax, self.ed_number):
            ed.returnPressed.connect(self.apply_filters)

        for seq, slot in (
            ("Ctrl+V", self.paste_anything),
            ("Ctrl+C", self.copy_selection),
            ("Ctrl+F", lambda: self.ed_keyword.setFocus()),
        ):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
        sc_del = QShortcut(QKeySequence("Delete"), self.table)
        sc_del.activated.connect(self.delete_checked)

    # ------------------------------------------------------------------ 数据
    def current_filters(self) -> dict[str, Any]:
        return {
            "keyword": self.ed_keyword.text().strip(),
            "company": self.ed_company.text().strip(),
            "tax_id": self.ed_tax.text().strip(),
            "invoice_number": self.ed_number.text().strip(),
            "date_from": self.dt_from.value(),
            "date_to": self.dt_to.value(),
            "created_from": (self.ct_from.value() or "") and self.ct_from.value() + " 00:00:00",
            "created_to": (self.ct_to.value() or "") and self.ct_to.value() + " 23:59:59",
            "amount_min": self.mn_min.value(),
            "amount_max": self.mn_max.value(),
            "subjects": self.cb_subject.values(),
            "tax_rates": self.cb_rate.values(),
            "types": self.cb_type.values(),
        }

    def apply_filters(self) -> None:
        self.filters = self.current_filters()
        self.page = 1
        self.reload()

    def reset_filters(self) -> None:
        for ed in (self.ed_keyword, self.ed_company, self.ed_tax, self.ed_number):
            ed.clear()
        for d in (self.dt_from, self.dt_to, self.ct_from, self.ct_to):
            d.set_value(None)
        self.mn_min.clear()
        self.mn_max.clear()
        for cb in (self.cb_subject, self.cb_rate, self.cb_type):
            cb.clear_selection()
        self.filters = {}
        self.page = 1
        self.reload()

    def _refresh_choices(self) -> None:
        self.cb_subject.set_items(self.db.distinct("expense_subject"))
        self.cb_rate.set_items(self.db.distinct("tax_rate"))
        self.cb_type.set_items(self.db.distinct("invoice_type"))

    def reload(self) -> None:
        rows, total = self.db.query(self.filters, self.page, self.page_size)
        max_page = max(1, (total + self.page_size - 1) // self.page_size)
        if self.page > max_page:
            self.page = max_page
            rows, total = self.db.query(self.filters, self.page, self.page_size)
        self.rows = rows
        self.total = total
        self._fill_table(rows)
        self.lbl_page.setText(f"第 {self.page} / {max_page} 页")
        self.btn_prev.setEnabled(self.page > 1)
        self.btn_next.setEnabled(self.page < max_page)
        st = self.db.stats()
        stats_text = f"共 {st['count']} 条 · 合计 ¥{st['total']:,.2f}"
        if self.filters and total != st["count"]:
            stats_text += f" · 筛选命中 {total} 条"
        self.lbl_stats.setText(stats_text)
        self.cb_all_page.blockSignals(True)
        self.cb_all_page.setChecked(False)
        self.cb_all_page.blockSignals(False)

    def _fill_table(self, rows: list[dict[str, Any]]) -> None:
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            chk = QTableWidgetItem("")
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.table.setItem(r, 0, chk)
            for c, f in enumerate(VISIBLE_FIELDS, start=1):
                text = fmt_value(f.key, row.get(f.key))
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                if f.kind == "money":
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(r, c, item)
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.btn_del.setText("删除勾选")

    def goto_page(self, page: int) -> None:
        max_page = max(1, (self.total + self.page_size - 1) // self.page_size)
        self.page = min(max(1, page), max_page)
        self.reload()

    def _on_page_size(self, _idx: int) -> None:
        self.page_size = int(self.cb_page_size.currentData())
        self.page = 1
        self.reload()

    def _on_scroll(self, value: int) -> None:
        if not self.cb_autoturn.isChecked() or self._auto_turn:
            return
        sb = self.table.verticalScrollBar()
        if value < sb.maximum() or sb.maximum() <= 0:
            return
        max_page = max(1, (self.total + self.page_size - 1) // self.page_size)
        if self.page >= max_page:
            return
        self._auto_turn = True
        QTimer.singleShot(80, self._turn_next)

    def _turn_next(self) -> None:
        self._auto_turn = False
        self.goto_page(self.page + 1)

    def _toggle_all_page(self, state: int) -> None:
        on = state == int(Qt.CheckState.Checked.value)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        n = len(self.checked_ids())
        self.btn_del.setText(f"删除勾选({n})" if n else "删除勾选")

    # ------------------------------------------------------------------ 复制
    def _copy_column(self, section: int) -> None:
        if section <= 0:
            return
        field = VISIBLE_FIELDS[section - 1]
        values = [fmt_value(field.key, row.get(field.key)) for row in self.db.iter_rows(self.filters)]
        if not values:
            self.lbl_status.setText("当前没有可复制的数据")
            return
        QGuiApplication.clipboard().setText("\t".join(values))
        self.lbl_status.setText(
            f"已复制「{field.label}」整列 {len(values)} 个值，可直接粘贴到 Excel 成为一行"
        )

    def copy_selection(self) -> None:
        ranges = self.table.selectedRanges()
        if not ranges:
            return
        lines: list[str] = []
        for rng in ranges:
            for r in range(rng.topRow(), rng.bottomRow() + 1):
                cells = []
                for c in range(max(1, rng.leftColumn()), rng.rightColumn() + 1):
                    it = self.table.item(r, c)
                    cells.append(it.text() if it else "")
                lines.append("\t".join(cells))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.lbl_status.setText(f"已复制 {len(lines)} 行（制表符分隔，可直接粘贴到 Excel）")

    def copy_cell(self) -> None:
        it = self.table.currentItem()
        if it is None:
            return
        QGuiApplication.clipboard().setText(it.text())
        self.lbl_status.setText("已复制单元格内容")

    # ------------------------------------------------------------------ 右键菜单
    def _cell_menu(self, pos) -> None:
        it = self.table.itemAt(pos)
        if it is None:
            return
        self.table.setCurrentItem(it)
        menu = QMenu(self)
        act_detail = QAction("查看 / 编辑详情", self)
        act_copy = QAction("复制该单元格", self)
        act_row = QAction("复制整行", self)
        act_open = QAction("打开原文件", self)
        act_del = QAction("删除这条记录", self)
        for a in (act_detail, act_copy, act_row, act_open):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_del)

        act_detail.triggered.connect(self.open_detail)
        act_copy.triggered.connect(self.copy_cell)
        act_row.triggered.connect(self.copy_row)
        act_open.triggered.connect(self.open_source_file)
        act_del.triggered.connect(self.delete_current_row)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def copy_row(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        cells = []
        for c in range(1, self.table.columnCount()):
            it = self.table.item(row, c)
            cells.append(it.text() if it else "")
        QGuiApplication.clipboard().setText("\t".join(cells))
        self.lbl_status.setText("已复制整行（制表符分隔）")

    def _current_record(self) -> dict[str, Any] | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            return None
        return self.rows[row]

    def open_source_file(self) -> None:
        rec = self._current_record()
        if not rec:
            return
        path = str(rec.get("source_path") or "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "提示", "原文件已不在原位置。")
            return
        try:
            os.startfile(path)  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", str(exc))

    def open_detail(self) -> None:
        rec = self._current_record()
        if not rec:
            return
        dlg = DetailDialog(rec, self.classifier, self)
        if dlg.exec() and dlg.changed:
            self.db.update_fields(
                rec["id"],
                {
                    "expense_subject": dlg.row["expense_subject"],
                    "remark": dlg.row["remark"],
                },
            )
            self._refresh_choices()
            self.reload()
            self.lbl_status.setText("已保存修改")

    # ------------------------------------------------------------------ 导入
    def import_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择发票文件（可多选）",
            "",
            "发票文件 (*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;所有文件 (*.*)",
        )
        if files:
            self.start_recognize(files)

    def import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择包含发票的文件夹")
        if folder:
            files = iter_supported_files([folder])
            if not files:
                QMessageBox.information(self, "提示", "该文件夹里没有找到 PDF 或图片文件。")
                return
            self.start_recognize(files)

    def paste_anything(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in cb.mimeData().urls() if u.isLocalFile()]
            paths = [p for p in paths if is_supported(p)]
            if paths:
                self.start_recognize(paths)
                return
        img = cb.image()
        if not img.isNull():
            fd, tmp = None, os.path.join(self.data_dir, "_clipboard.png")
            try:
                img.save(tmp, "PNG")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "提示", f"剪贴板图片保存失败：{exc}")
                return
            self.start_recognize([tmp])
            return
        QMessageBox.information(self, "提示", "剪贴板里没有图片或文件。请先截图或复制文件再粘贴。")

    def start_recognize(self, paths: list[str]) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "提示", "上一批还在识别中，请稍候。")
            return
        files = iter_supported_files(paths) if len(paths) > 1 else paths
        if not files:
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.lbl_status.setText(f"正在识别 0/{len(files)}…")
        self._set_busy(True)

        self._worker = RecognizeWorker(files, self.db, self.classifier, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_batch.connect(self._on_batch_done)
        self._worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.lbl_status.setText(f"正在识别 {done + 1}/{total}：{name}")

    def _on_batch_done(self, summary: dict[str, Any]) -> None:
        self.progress.setVisible(False)
        self._set_busy(False)
        self._refresh_choices()
        self.reload()
        msg = f"完成：新增 {summary['ok']} 条"
        if summary["skipped"]:
            msg += f"，跳过重复 {summary['skipped']} 条"
        if summary["failed"]:
            msg += f"，失败 {len(summary['failed'])} 个"
        self.lbl_status.setText(msg)
        if summary["failed"]:
            QMessageBox.warning(
                self, "部分文件识别失败", "\n".join(summary["failed"][:20])
            )

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_import, self.btn_folder, self.btn_paste, self.btn_export, self.btn_settings):
            b.setEnabled(not busy)
        self.btn_search.setEnabled(not busy)
        self.btn_reset.setEnabled(not busy)

    # ------------------------------------------------------------------ 删除
    def checked_ids(self) -> list[int]:
        ids = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                ids.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return ids

    def selected_ids(self) -> list[int]:
        """表格里用 Ctrl/Shift 选中的行（未勾选时作为删除依据）。"""
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
        ids = []
        for r in rows:
            it = self.table.item(r, 0)
            if it is not None:
                ids.append(int(it.data(Qt.ItemDataRole.UserRole)))
        return ids

    def delete_checked(self) -> None:
        """删除：优先删勾选的行，没有勾选就删当前选中的行。"""
        ids = self.checked_ids()
        if not ids:
            ids = self.selected_ids()
        if not ids:
            QMessageBox.information(
                self, "提示", "请先在表格最左侧勾选要删除的记录（可勾多条），或用鼠标选中若干行。"
            )
            return
        self.delete_rows(ids)

    def delete_current_row(self) -> None:
        rec = self._current_record()
        if rec:
            self.delete_rows([rec["id"]])

    def delete_rows(self, ids: list[int], *, confirm: bool = True) -> None:
        ids = list(dict.fromkeys(int(i) for i in ids))
        if not ids:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "确认删除",
                f"确定删除勾选的 {len(ids)} 条记录吗？\n\n删除后可用「撤销删除」恢复。",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        for i in ids:
            self.db.soft_delete(i)
        self._last_deleted = ids
        self.btn_undo.setVisible(True)
        self.btn_undo.setText(f"撤销删除({len(ids)})")
        self._refresh_choices()
        self.reload()
        self.lbl_status.setText(f"已删除 {len(ids)} 条，可点「撤销删除」恢复")

    def undo_delete(self) -> None:
        ids = getattr(self, "_last_deleted", [])
        if not ids:
            return
        for i in ids:
            self.db.restore(i)
        n = len(ids)
        self._last_deleted = []
        self.btn_undo.setVisible(False)
        self._refresh_choices()
        self.reload()
        self.lbl_status.setText(f"已恢复 {n} 条记录")

    # ------------------------------------------------------------------ 导出
    def export(self) -> None:
        filters = self.filters
        total = self.db.query(filters, 1, 1)[1]
        checked = self.checked_ids()
        dlg = ExportDialog(
            total_rows=total,
            page_rows=len(self.rows),
            checked_rows=len(checked),
            default_dir=os.path.join(os.path.expanduser("~"), "Desktop"),
            parent=self,
        )
        if not dlg.exec():
            return
        fields = dlg.selected_fields()
        if not fields:
            QMessageBox.information(self, "提示", "请至少勾选一个字段。")
            return
        scope = dlg.scope()
        if scope == "page":
            rows = self.rows
        elif scope == "checked":
            rows = list(self.db.iter_rows(filters, ids=checked))
        else:
            rows = list(self.db.iter_rows(filters))
        if not rows:
            QMessageBox.information(self, "提示", "没有可导出的记录。")
            return
        path = dlg.path()
        if not path:
            QMessageBox.information(self, "提示", "请选择保存位置。")
            return
        fmt = dlg.fmt()
        if not path.lower().endswith("." + fmt):
            path = os.path.splitext(path)[0] + "." + fmt
        try:
            out, n = export_rows(rows, fields, path, fmt=fmt)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.lbl_status.setText(f"已导出 {n} 条到 {out}")
        box = QMessageBox(self)
        box.setWindowTitle("导出完成")
        box.setText(f"已导出 {n} 条记录（{len(fields)} 个字段）\n{out}")
        btn_open = box.addButton("打开所在文件夹", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is btn_open:
            try:
                os.startfile(os.path.dirname(out))  # noqa: S606
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 设置
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.classifier, self.rules_path, self.db.db_path, self)
        if not dlg.exec() or not dlg.saved:
            return
        if QMessageBox.question(
            self,
            "重新归类",
            "是否用新规则重新归类全部历史记录？（会覆盖手工修改过的费用科目）",
        ) == QMessageBox.StandardButton.Yes:
            for rec in list(self.db.iter_rows({})):
                subject = self.classifier.classify(
                    f"{rec.get('item_name') or ''}{rec.get('seller_name') or ''}"
                )
                if subject != rec.get("expense_subject"):
                    self.db.update_fields(rec["id"], {"expense_subject": subject})
            self._refresh_choices()
            self.reload()
        self.lbl_status.setText("规则已保存")

    # ------------------------------------------------------------------ 拖放
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        md = event.mimeData()
        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
            self.lbl_drop.setStyleSheet(
                "border:1px dashed #2f7dd1; border-radius:6px; padding:7px; color:#1c5fa8; background:#e8f2ff;"
            )

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.lbl_drop.setStyleSheet(
            "border:1px dashed #9fb6cd; border-radius:6px; padding:7px; color:#4a6b8a; background:#f5f9ff;"
        )
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.lbl_drop.setStyleSheet(
            "border:1px dashed #9fb6cd; border-radius:6px; padding:7px; color:#4a6b8a; background:#f5f9ff;"
        )
        md = event.mimeData()
        if md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            paths = [p for p in paths if os.path.isdir(p) or is_supported(p)]
            if paths:
                self.start_recognize(paths)
                event.acceptProposedAction()
                return
        if md.hasImage():
            img = md.imageData()
            if img is not None:
                tmp = os.path.join(self.data_dir, "_drop.png")
                img.save(tmp, "PNG")
                self.start_recognize([tmp])
                event.acceptProposedAction()

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        self.db.close()
        super().closeEvent(event)