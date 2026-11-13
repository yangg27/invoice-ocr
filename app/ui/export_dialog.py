"""导出对话框：勾选字段 + 选择范围（默认全选=当前查询全部结果）+ 选格式。"""
from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from ..core.fields import FIELDS


class ExportDialog(QDialog):
    def __init__(self, total_rows: int, page_rows: int, checked_rows: int, default_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出记录")
        self.resize(560, 620)
        self._total = total_rows
        self._page = page_rows
        self._checked = checked_rows

        root = QVBoxLayout(self)

        # ---- 字段 ----
        fbox = QGroupBox("1. 选择要导出的字段")
        flay = QVBoxLayout(fbox)
        top = QHBoxLayout()
        self.cb_all_fields = QCheckBox("全选字段")
        self.cb_all_fields.setChecked(True)
        self.cb_all_fields.stateChanged.connect(self._toggle_all_fields)
        top.addWidget(self.cb_all_fields)
        top.addStretch(1)
        flay.addLayout(top)

        self.list_fields = QListWidget()
        self.list_fields.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for f in FIELDS:
            it = QListWidgetItem(f.label)
            it.setData(Qt.ItemDataRole.UserRole, f.key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if f.export else Qt.CheckState.Unchecked)
            self.list_fields.addItem(it)
        flay.addWidget(self.list_fields, 1)
        root.addWidget(fbox, 1)

        # ---- 范围 ----
        rbox = QGroupBox("2. 选择导出范围")
        rlay = QVBoxLayout(rbox)
        self.rb_all = QRadioButton(f"当前查询的全部结果：{total_rows} 条（跨全部页面，默认）")
        self.rb_page = QRadioButton(f"仅当前页：{page_rows} 条")
        self.rb_checked = QRadioButton(f"仅表格中勾选的行：{checked_rows} 条")
        self.rbg = QButtonGroup(self)
        for rb in (self.rb_all, self.rb_page, self.rb_checked):
            rlay.addWidget(rb)
            self.rbg.addButton(rb)
        self.rb_all.setChecked(True)
        if checked_rows == 0:
            self.rb_checked.setEnabled(False)
        root.addWidget(rbox)

        # ---- 格式与路径 ----
        pbox = QGroupBox("3. 输出")
        play = QFormLayout(pbox)
        fmt_row = QHBoxLayout()
        self.rb_xlsx = QRadioButton("Excel (.xlsx)")
        self.rb_csv = QRadioButton("CSV (.csv，UTF-8 带 BOM)")
        self.rb_xlsx.setChecked(True)
        self.fmt_group = QButtonGroup(self)
        self.fmt_group.addButton(self.rb_xlsx)
        self.fmt_group.addButton(self.rb_csv)
        fmt_row.addWidget(self.rb_xlsx)
        fmt_row.addWidget(self.rb_csv)
        fmt_row.addStretch(1)
        play.addRow("文件格式：", fmt_row)

        path_row = QHBoxLayout()
        self.ed_path = QLineEdit(os.path.join(default_dir, "发票记录.xlsx"))
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(self.ed_path, 1)
        path_row.addWidget(btn_browse)
        play.addRow("保存到：", path_row)
        root.addWidget(pbox)

        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        root.addWidget(self.lbl_hint)

        btns = QDialogButtonBox()
        self.btn_ok = btns.addButton("开始导出", QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self.rb_csv.toggled.connect(self._sync_ext)
        self._sync_hint()

    # ---------------------------------------------------------------- 事件
    def _toggle_all_fields(self, state: int) -> None:
        on = state == int(Qt.CheckState.Checked.value)
        for i in range(self.list_fields.count()):
            it = self.list_fields.item(i)
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _sync_ext(self) -> None:
        path = self.ed_path.text().strip()
        base, _ = os.path.splitext(path)
        self.ed_path.setText(base + (".xlsx" if self.rb_xlsx.isChecked() else ".csv"))
        self._sync_hint()

    def _sync_hint(self) -> None:
        n = len(self.selected_fields())
        self.lbl_hint.setText(
            f"将导出 {self._scope_count()} 行 × {n} 个字段。"
            "全选范围 = 当前查询条件下的全部页面与条数，不只是当前页。"
        )

    def _browse(self) -> None:
        ext = "Excel 文件 (*.xlsx)" if self.rb_xlsx.isChecked() else "CSV 文件 (*.csv)"
        path, _ = QFileDialog.getSaveFileName(
            self, "选择导出位置", self.ed_path.text(), ext
        )
        if path:
            self.ed_path.setText(path)

    # ---------------------------------------------------------------- 结果
    def _scope_count(self) -> int:
        if self.rb_all.isChecked():
            return self._total
        if self.rb_page.isChecked():
            return self._page
        return self._checked

    def selected_fields(self) -> list[str]:
        out = []
        for i in range(self.list_fields.count()):
            it = self.list_fields.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(Qt.ItemDataRole.UserRole))
        return out

    def scope(self) -> str:
        if self.rb_all.isChecked():
            return "all"
        if self.rb_page.isChecked():
            return "page"
        return "checked"

    def fmt(self) -> str:
        return "xlsx" if self.rb_xlsx.isChecked() else "csv"

    def path(self) -> str:
        return self.ed_path.text().strip()