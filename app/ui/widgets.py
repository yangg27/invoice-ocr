"""自定义控件：带「复制整列」按钮的表头、多选下拉、可空日期框。"""
from __future__ import annotations

from PySide6.QtCore import QDate, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDateEdit,
    QHeaderView,
    QLineEdit,
    QMenu,
    QStyle,
    QStyleOptionHeader,
    QToolButton,
    QWidget,
)


class CopyHeader(QHeaderView):
    """表头右侧画一个复制图标，点击复制该列（当前查询全部结果）。"""

    copyRequested = Signal(int)

    ICON_W = 18

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent: QWidget | None = None):
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("点击表头右侧的复制图标：复制整列（当前查询的全部结果）")

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        if not rect.isValid():
            return
        super().paintSection(painter, rect, logicalIndex)
        if logicalIndex <= 0:
            return  # 第 0 列是勾选列，不放复制按钮
        painter.save()
        icon = QRect(rect.right() - self.ICON_W + 2, rect.center().y() - 7, 14, 14)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.palette().text().color())
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(icon.adjusted(4, 2, -1, -4))
        painter.drawRect(icon.adjusted(1, 5, -4, -1))
        painter.restore()

    def _icon_hit(self, x: int, logical_index: int) -> bool:
        left = self.sectionViewportPosition(logical_index)
        right = left + self.sectionSize(logical_index)
        return x >= right - self.ICON_W

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self.logicalIndexAt(event.position().toPoint())
        if index >= 0 and self._icon_hit(int(event.position().x()), index):
            self.copyRequested.emit(index)
            return
        super().mousePressEvent(event)


class MultiSelectButton(QToolButton):
    """下拉多选：空选 = 不过滤。"""

    changed = Signal()

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._items: list[str] = []
        self._checked: set[str] = set()
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setText(f"{title} ▾")
        self.setMinimumWidth(120)
        self._menu = QMenu(self)
        self.setMenu(self._menu)

    def set_items(self, items: list[str]) -> None:
        self._items = list(items)
        self._checked &= set(self._items)
        self._rebuild()

    def _rebuild(self) -> None:
        self._menu.clear()
        if not self._items:
            act = self._menu.addAction("（暂无数据）")
            act.setEnabled(False)
        for name in self._items:
            act = self._menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name in self._checked)
            act.toggled.connect(lambda on, n=name: self._toggle(n, on))
        self._update_text()

    def _toggle(self, name: str, on: bool) -> None:
        if on:
            self._checked.add(name)
        else:
            self._checked.discard(name)
        self._update_text()
        self.changed.emit()

    def _update_text(self) -> None:
        if self._checked:
            self.setText(f"{self._title}({len(self._checked)}) ▾")
        else:
            self.setText(f"{self._title} ▾")

    def values(self) -> list[str]:
        return [i for i in self._items if i in self._checked]

    def clear_selection(self) -> None:
        self._checked.clear()
        self._update_text()
        for act in self._menu.actions():
            if act.isCheckable():
                act.blockSignals(True)
                act.setChecked(False)
                act.blockSignals(False)


class OptionalDateEdit(QDateEdit):
    """可留空的日期框：最小值即「不限」。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setDateRange(QDate(1900, 1, 1), QDate(2099, 12, 31))
        self.setSpecialValueText("不限")
        self.setDate(QDate(1900, 1, 1))
        self.setMinimumWidth(118)

    def value(self) -> str | None:
        d = self.date()
        if d == QDate(1900, 1, 1):
            return None
        return d.toString("yyyy-MM-dd")

    def set_value(self, text: str | None) -> None:
        if not text:
            self.setDate(QDate(1900, 1, 1))
            return
        d = QDate.fromString(text, "yyyy-MM-dd")
        self.setDate(d if d.isValid() else QDate(1900, 1, 1))


class MoneyEdit(QLineEdit):
    """金额输入：空 = 不限。"""

    def __init__(self, placeholder: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumWidth(96)
        self.setMaximumWidth(120)

    def value(self) -> float | None:
        t = self.text().strip().replace(",", "")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None

    def set_value(self, v: float | None) -> None:
        self.setText("" if v is None else f"{v:g}")