"""详情弹窗与设置弹窗。"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.expense import SUBJECTS, ExpenseClassifier, Rule
from ..core.fields import FIELDS


class DetailDialog(QDialog):
    """查看/修正一条记录；所有文本都可用鼠标选中复制。"""

    def __init__(self, row: dict[str, Any], classifier: ExpenseClassifier, parent=None):
        super().__init__(parent)
        self.setWindowTitle("发票详情")
        self.resize(760, 720)
        self.row = dict(row)

        root = QVBoxLayout(self)
        box = QGroupBox("字段（可直接用鼠标选中复制）")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.edits: dict[str, QLineEdit] = {}
        for f in FIELDS:
            v = row.get(f.key)
            if v is None or v == "":
                text = ""
            elif f.kind == "money":
                try:
                    text = f"{float(v):,.2f}"
                except (TypeError, ValueError):
                    text = str(v)
            else:
                text = str(v)
            ed = QLineEdit(text)
            ed.setReadOnly(True)
            ed.setCursorPosition(0)
            ed.setMinimumWidth(430)
            self.edits[f.key] = ed
            form.addRow(f.label + "：", ed)
        root.addWidget(box)

        edit_box = QGroupBox("可修改项")
        elay = QFormLayout(edit_box)
        self.cb_subject = QComboBox()
        self.cb_subject.setEditable(True)
        subjects = list(SUBJECTS)
        cur = str(row.get("expense_subject") or "其他")
        if cur not in subjects:
            subjects.append(cur)
        self.cb_subject.addItems(subjects)
        self.cb_subject.setCurrentText(cur)
        elay.addRow("费用科目：", self.cb_subject)

        self.ed_remark = QLineEdit(str(row.get("remark") or ""))
        elay.addRow("备注：", self.ed_remark)
        root.addWidget(edit_box)

        raw = str(row.get("raw_text") or "")
        if raw:
            raw_box = QGroupBox("票面原文")
            rlay = QVBoxLayout(raw_box)
            te = QPlainTextEdit(raw)
            te.setReadOnly(True)
            te.setMaximumHeight(140)
            rlay.addWidget(te)
            root.addWidget(raw_box)

        btns = QDialogButtonBox()
        self.btn_save = btns.addButton("保存修改", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_open = btns.addButton("打开原文件", QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        self.btn_open.clicked.connect(self._open_source)
        root.addWidget(btns)

        self.changed = False

    def _save(self) -> None:
        self.row["expense_subject"] = self.cb_subject.currentText().strip() or "其他"
        self.row["remark"] = self.ed_remark.text().strip()
        self.changed = True
        self.accept()

    def _open_source(self) -> None:
        path = str(self.row.get("source_path") or "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "提示", "原文件不在原位置，可能已被移动或删除。")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", str(exc))


class SettingsDialog(QDialog):
    """费用科目规则维护 + 运行信息。"""

    def __init__(self, classifier: ExpenseClassifier, rules_path: str, db_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(720, 640)
        self.classifier = classifier
        self.rules_path = rules_path
        self.db_path = db_path

        root = QVBoxLayout(self)

        info = QGroupBox("运行信息")
        ilay = QFormLayout(info)
        ilay.addRow("数据库：", QLineEdit(db_path))
        ilay.addRow("规则文件：", QLineEdit(rules_path))
        ilay.addRow("识别模式：", QLineEdit("全离线（PDF 文本层 + 本地 OCR，不联网）"))
        ilay.addRow("开发人：", QLineEdit("杨广"))
        root.addWidget(info)

        box = QGroupBox("费用科目规则（关键词 → 科目；长词优先匹配）")
        blay = QVBoxLayout(box)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["关键词", "费用科目"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        blay.addWidget(self.table, 1)

        row = QHBoxLayout()
        btn_add = QPushButton("新增一行")
        btn_del = QPushButton("删除选中行")
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        blay.addLayout(row)
        btn_add.clicked.connect(self._add_row)
        btn_del.clicked.connect(self._del_row)
        root.addWidget(box, 1)

        btns = QDialogButtonBox()
        btns.addButton("保存规则", QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self.saved = False
        self._load_rules()

    def _load_rules(self) -> None:
        self.table.setRowCount(0)
        for r in self.classifier.rules:
            self._append(r.keyword, r.subject)

    def _append(self, keyword: str, subject: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(keyword))
        self.table.setItem(row, 1, QTableWidgetItem(subject))

    def _add_row(self) -> None:
        self._append("", "其他")
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _del_row(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _save(self) -> None:
        rules: list[Rule] = []
        for r in range(self.table.rowCount()):
            kw_item = self.table.item(r, 0)
            sub_item = self.table.item(r, 1)
            kw = (kw_item.text() if kw_item else "").strip()
            sub = (sub_item.text() if sub_item else "").strip()
            if kw and sub:
                rules.append(Rule(kw, sub))
        if not rules:
            QMessageBox.warning(self, "提示", "至少要保留一条规则。")
            return
        self.classifier.set_rules(rules)
        self.classifier.save(self.rules_path)
        self.saved = True
        self.accept()