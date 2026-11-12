"""Reusable UI widgets (Cards, LEDs, Channel tiles, Bitfield tables...)."""
from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import (QPointF, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme as T


# --------------------------------------------------------------------------- #
# Base Containers
# --------------------------------------------------------------------------- #
class Card(QFrame):
    """Rounded card with a title. `body` is the content layout where widgets are added."""

    def __init__(self, title: str = "", hint: str = "", parent=None,
                 flat: bool = False):
        super().__init__(parent)
        self.setObjectName("CardFlat" if flat else "Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(13, 11, 13, 13)
        outer.setSpacing(9)

        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            lbl = QLabel(title)
            lbl.setObjectName("CardTitle")
            head.addWidget(lbl)
            head.addStretch(1)
            self.head = head
            if hint:
                h = QLabel(hint)
                h.setObjectName("CardHint")
                head.addWidget(h)
            outer.addLayout(head)
        else:
            self.head = None

        self.body = QVBoxLayout()
        self.body.setSpacing(9)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body)

    def add(self, w) -> None:
        self.body.addWidget(w)

    def add_layout(self, lay) -> None:
        self.body.addLayout(lay)


def section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("SectionLabel")
    return lbl


def hint_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{T.BORDER}; border:none;")
    return f


# --------------------------------------------------------------------------- #
# LED Indicator
# --------------------------------------------------------------------------- #
class Led(QWidget):
    """Circular indicator LED, supports glow effect."""

    OFF, ON, WARN, ERR, DIM = range(5)

    _COLORS = {
        OFF: ("#2A3644", "#151C25"),
        ON: (T.OK, "#0C3A22"),
        WARN: (T.WARN, "#3A2A08"),
        ERR: (T.ERR, "#3A0F16"),
        DIM: ("#3A4A5C", "#1A222C"),
    }

    def __init__(self, size: int = 13, state: int = OFF, parent=None):
        super().__init__(parent)
        self._size = size
        self._state = state
        self.setFixedSize(size + 6, size + 6)

    def set_state(self, state: int) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def state(self) -> int:
        return self._state

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        core, halo = self._COLORS.get(self._state, self._COLORS[self.OFF])
        cx, cy = self.width() / 2, self.height() / 2
        r = self._size / 2

        if self._state in (self.ON, self.WARN, self.ERR):
            g = QRadialGradient(QPointF(cx, cy), r * 2.0)
            g.setColorAt(0.0, T.c(halo, 210))
            g.setColorAt(0.55, T.c(core, 90))
            g.setColorAt(1.0, T.c(core, 0))
            p.setBrush(QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r * 2.0, r * 2.0)

        g2 = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.35), r * 1.6)
        g2.setColorAt(0.0, T.c(core).lighter(150))
        g2.setColorAt(0.55, T.c(core))
        g2.setColorAt(1.0, T.c(core).darker(160))
        p.setBrush(QBrush(g2))
        p.setPen(QPen(T.c(core).darker(200), 1))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.end()


# --------------------------------------------------------------------------- #
# Status Pill
# --------------------------------------------------------------------------- #
class StatusPill(QLabel):
    """Connection status pill label."""

    def __init__(self, text: str = "Disconnected", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state("off")

    def set_state(self, kind: str, text: Optional[str] = None) -> None:
        if text is not None:
            self.setText(text)
        colors = {
            "off": (T.IDLE, "#0E141C", T.TEXT_DIM),
            "ok": (T.OK, "#0C2A1B", T.OK),
            "busy": (T.WARN, "#2C2109", T.WARN),
            "err": (T.ERR, "#2E0D13", T.ERR),
            "info": (T.ACCENT, "#08262C", T.ACCENT),
        }
        border, bg, fg = colors.get(kind, colors["off"])
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {border};"
            f"border-radius:11px; padding:3px 13px; font-weight:600; font-size:11px;")
        self.setMinimumHeight(22)


# --------------------------------------------------------------------------- #
# Channel Tiles
# --------------------------------------------------------------------------- #
class RelayTile(QAbstractButton):
    """Clickable relay channel tile."""

    toggled_by_user = Signal(int, bool)

    def __init__(self, channel: int, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(80)
        self.setMinimumWidth(84)
        self.setToolTip(
            f"Relay Channel {channel} —— Click to toggle ON/OFF\n"
            f"Corresponds to CAN data field Byte {(channel - 1) // 8 + 1} Bit{(channel - 1) % 8}")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self) -> None:
        self.toggled_by_user.emit(self.channel, self.isChecked())

    def sizeHint(self) -> QSize:
        return QSize(96, 80)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        on = self.isChecked()
        hover = self.underMouse()

        if on:
            bg = QColor("#0C2E20") if not hover else QColor("#10402C")
            border = T.c(T.OK)
            fg = T.c(T.OK)
        else:
            bg = QColor(T.BG_CARD) if not hover else QColor(T.BG_HOVER)
            border = T.c(T.ACCENT_DK if hover else T.BORDER)
            fg = T.c(T.TEXT_DIM if not hover else T.TEXT)

        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border, 1.4))
        p.drawPath(path)
        if on:                                  # Left emphasis bar
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(T.c(T.OK)))
            bar = QPainterPath()
            bar.addRoundedRect(QRectF(r.left() + 3, r.top() + 9, 3,
                                      r.height() - 18), 1.5, 1.5)
            p.fillPath(bar, QBrush(T.c(T.OK)))

        # Top: LED + Name
        p.setPen(Qt.PenStyle.NoPen)
        led_c = T.c(T.OK) if on else T.c("#39485A")
        p.setBrush(QBrush(led_c))
        p.drawEllipse(QPointF(r.left() + 17, r.top() + 18), 4.0, 4.0)
        f = QFont(T.FONT_MONO, 10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(fg))
        p.drawText(QRectF(r.left() + 27, r.top() + 9, r.width() - 32, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"Y{self.channel}")

        # Middle: Large Channel Number
        big = QFont(T.FONT_UI, 19)
        big.setBold(True)
        p.setFont(big)
        p.setPen(QPen(T.c(T.OK) if on else T.c("#41536A")))
        p.drawText(QRectF(r.left(), r.top() + 25, r.width(), 26),
                   int(Qt.AlignmentFlag.AlignCenter), str(self.channel))

        # Bottom: Status
        sf = QFont(T.FONT_UI, 9)
        sf.setBold(True)
        p.setFont(sf)
        p.setPen(QPen(fg))
        p.drawText(QRectF(r.left(), r.bottom() - 22, r.width(), 18),
                   int(Qt.AlignmentFlag.AlignCenter),
                   "Closed ON" if on else "Open OFF")
        p.end()


class InputTile(QWidget):
    """Read-only input channel indicator."""

    def __init__(self, channel: int, parent=None):
        super().__init__(parent)
        self.channel = channel
        self._active = False
        self._count = 0
        self.setFixedHeight(62)
        self.setMinimumWidth(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(
            f"Input Channel {channel} —— Bit=1 means triggered (external switch closed)\n"
            f"Corresponds to CAN data field Byte {(channel - 1) // 8 + 1} Bit{(channel - 1) % 8}")

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active and not self._active:
            self._count += 1
        if active != self._active:
            self._active = active
            self.update()

    def reset_count(self) -> None:
        self._count = 0
        self.update()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def count(self) -> int:
        return self._count

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        on = self._active

        bg = QColor("#072B33") if on else QColor(T.BG_CARD)
        border = T.c(T.ACCENT) if on else T.c(T.BORDER)
        fg = T.c(T.ACCENT) if on else T.c(T.TEXT_MUTE)

        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        p.fillPath(path, QBrush(bg))
        p.setPen(QPen(border, 1.4))
        p.drawPath(path)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fg))
        p.drawEllipse(QPointF(r.left() + 17, r.top() + 17), 4.0, 4.0)

        f = QFont(T.FONT_MONO, 10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QPen(fg))
        p.drawText(QRectF(r.left() + 27, r.top() + 8, r.width() - 34, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"X{self.channel}")

        sf = QFont(T.FONT_UI, 9)
        sf.setBold(True)
        p.setFont(sf)
        p.drawText(QRectF(r.left() + 10, r.top() + 30, r.width() - 20, 18),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "Triggered ON" if on else "Untriggered")

        # Trigger count on the right
        p.setFont(QFont(T.FONT_MONO, 8))
        p.setPen(QPen(T.c(T.TEXT_MUTE) if not on else T.c(T.ACCENT, 190)))
        p.drawText(QRectF(r.left(), r.top() + 30, r.width() - 10, 18),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                   f"×{self._count}")
        p.end()


# --------------------------------------------------------------------------- #
# Statistics Chip
# --------------------------------------------------------------------------- #
class StatChip(QFrame):
    """Numerical statistics chip."""

    def __init__(self, label: str, value: str = "0", color: str = T.TEXT,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 8)
        lay.setSpacing(1)
        self._lab = QLabel(label)
        self._lab.setStyleSheet(
            f"color:{T.TEXT_MUTE}; font-size:10px; font-weight:600; background:transparent;")
        self._val = QLabel(value)
        self._val.setStyleSheet(
            f"color:{color}; font-size:15px; font-weight:700;"
            f"font-family:{T.FONT_MONO}; background:transparent;")
        lay.addWidget(self._lab)
        lay.addWidget(self._val)

    def set_value(self, value, color: Optional[str] = None) -> None:
        self._val.setText(str(value))
        if color:
            self._val.setStyleSheet(
                f"color:{color}; font-size:15px; font-weight:700;"
                f"font-family:{T.FONT_MONO}; background:transparent;")


# --------------------------------------------------------------------------- #
# Bitfield Visualization Table
# --------------------------------------------------------------------------- #
class ByteBitTable(QTableWidget):
    """
    Draws the 8-byte data field as a 'Byte x Bit' table:

        Byte 1 (Ch 1-8)  | Bit0 | Bit1 | ... | Bit7
                         |  ch1 |  ch2 | ... |  ch8

    Highlights bits set to 1.
    """

    def __init__(self, parent=None):
        super().__init__(6, 8, parent)
        self.setHorizontalHeaderLabels([f"Bit{i}" for i in range(8)])
        self.setVerticalHeaderLabels(
            [f"Byte {i + 1}  (Ch {i * 8 + 1}-{i * 8 + 8})" for i in range(6)])
        self.verticalHeader().setFixedWidth(150)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        # Cells contain two lines of text (bit value + channel number), row height must be enough
        self.verticalHeader().setDefaultSectionSize(40)
        self.verticalHeader().setMinimumSectionSize(40)
        self.setMinimumHeight(280)
        self.setMaximumHeight(320)
        for c in range(8):
            self.setColumnWidth(c, 62)
        self.set_value(b"\x00" * 8)

    def set_value(self, data: Iterable[int], limit_channels: int = 48) -> None:
        vals = list(data) + [0] * 8
        for byte_i in range(6):
            b = vals[byte_i] & 0xFF
            for bit in range(8):
                ch = byte_i * 8 + bit + 1
                bitval = (b >> bit) & 1
                in_range = ch <= limit_channels
                item = QTableWidgetItem(
                    f"{bitval}\nch{ch}" if in_range else "--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                f = QFont(T.FONT_MONO, 9)
                if bitval:
                    item.setBackground(QBrush(T.c(T.ACCENT_DK)))
                    item.setForeground(QBrush(T.c("#EAFBFF")))
                    f.setBold(True)
                elif not in_range:
                    item.setForeground(QBrush(T.c(T.TEXT_MUTE)))
                else:
                    item.setForeground(QBrush(T.c("#5B6B80")))
                item.setFont(f)
                self.setItem(byte_i, bit, item)
