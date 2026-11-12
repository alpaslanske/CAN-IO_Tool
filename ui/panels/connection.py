"""Comm Link Settings Panel (Debugger Params + Frame Format + Module Address)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from canio import protocol as P
from canio.transport import (HOST_BAUDS, PCAN_LOAD_ERROR, SimulatorTransport,
                             SlcanTransport, enumerate_channels, HAVE_PCAN,
                             HAVE_SLCAN, list_serial_ports)
from canio.transport.pcan import api_version

from .. import theme as T
from ..widgets import Card, hint_label

#: CAN baud rates supported by the module (Manual Table 2.6)
MODULE_BITRATES = [20_000, 50_000, 100_000, 125_000, 200_000,
                   250_000, 400_000, 500_000, 800_000, 1_000_000]

TRANSPORT_SLCAN = "SLCAN (Serial Port)"
TRANSPORT_PCAN = "PCAN-Basic"
TRANSPORT_SIM = "Simulator (No Hardware)"


class ConnectionPanel(QWidget):
    """Left "Comm Link" card."""

    connectRequested = Signal()
    disconnectRequested = Signal()
    settingsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = Card("Communication Link", "Debugger and Bus Parameters")
        outer.addWidget(root)

        self.transport_combo = QComboBox()
        self.transport_combo.addItem(TRANSPORT_SLCAN)
        self.transport_combo.addItem(TRANSPORT_PCAN)
        self.transport_combo.addItem(TRANSPORT_SIM)
        if not HAVE_SLCAN:
            self._disable_item(self.transport_combo, TRANSPORT_SLCAN)
        if not HAVE_PCAN:
            self._disable_item(self.transport_combo, TRANSPORT_PCAN)
        if not HAVE_SLCAN:
            # Default to simulator when pyserial is missing to ensure UI usability
            self.transport_combo.setCurrentText(TRANSPORT_SIM)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(150)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("Mini")
        self.refresh_btn.clicked.connect(self.refresh_ports)

        port_row = QWidget()
        pr = QHBoxLayout(port_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(5)
        pr.addWidget(self.port_combo, 1)
        pr.addWidget(self.refresh_btn)
        self._port_row = port_row

        self.host_baud_combo = QComboBox()
        for b in HOST_BAUDS:
            self.host_baud_combo.addItem(str(b), b)
        self.host_baud_combo.setCurrentText("115200")

        self.bitrate_combo = QComboBox()
        for bps in MODULE_BITRATES:
            self.bitrate_combo.addItem(P.bps_label(bps), bps)
        self.bitrate_combo.setCurrentText(P.bps_label(P.DEFAULT_BPS))

        self.pcan_channel_combo = QComboBox()
        self.pcan_channel_combo.setMinimumWidth(150)

        self.std_radio = QRadioButton("Standard Frame 0x101")
        self.ext_radio = QRadioButton("Extended Frame 0xAA0101")
        self.std_radio.setChecked(True)

        self.addr_spin = QSpinBox()
        self.addr_spin.setRange(P.ADDR_MIN, P.ADDR_MAX)
        self.addr_spin.setValue(P.DEFAULT_ADDR)
        self.addr_spin.setToolTip("Module comm address (1~255, factory default 1)")

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("Primary")
        self.connect_btn.clicked.connect(self.connectRequested.emit)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("Danger")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self.disconnectRequested.emit)

        btn_row = QWidget()
        br = QHBoxLayout(btn_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.setSpacing(8)
        br.addWidget(self.connect_btn, 2)
        br.addWidget(self.disconnect_btn, 1)

        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(7)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        self.form.addRow("Transport", self.transport_combo)
        self.form.addRow("Serial Port", port_row)
        self.form.addRow("Serial Baud Rate", self.host_baud_combo)
        self.form.addRow("PCAN Channel", self.pcan_channel_combo)
        self.form.addRow("CAN Baud Rate", self.bitrate_combo)
        self.form.addRow("Frame Format", self._frame_row())
        self.form.addRow("Module Address", self.addr_spin)

        root.add_layout(self.form)
        root.add(btn_row)

        self.note = hint_label("")
        root.add(self.note)

        self.transport_combo.currentTextChanged.connect(self._on_transport_changed)
        self.bitrate_combo.currentIndexChanged.connect(self._update_note)
        self.port_combo.currentIndexChanged.connect(self._update_note)
        self._on_transport_changed(self.transport_combo.currentText())
        self.refresh_ports()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _disable_item(combo: QComboBox, text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            item = combo.model().item(idx)
            item.setEnabled(False)
            item.setToolTip("Current environment does not support this transport")
            if combo.currentIndex() == idx:
                combo.setCurrentIndex(0 if idx else 1)

    def _frame_row(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self.std_radio)
        lay.addWidget(self.ext_radio)
        lay.addStretch(1)
        return w

    # ------------------------------------------------------------------ #
    def refresh_ports(self) -> None:
        cur = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list_serial_ports()
        if not ports:
            self.port_combo.addItem("No serial port detected", None)
            self.port_combo.setEnabled(False)
        else:
            self.port_combo.setEnabled(True)
            
            # Cihaz yolunda USB veya ACM geçenleri en üste, diğerlerini alta sıralar
            ports.sort(key=lambda x: (0 if 'USB' in x[0] or 'ACM' in x[0] else 1, x[0]))
            
            for dev, desc in ports:
                label = f"{dev}" + (f"  ·  {desc}" if desc else "")
                self.port_combo.addItem(label, dev)
            if cur:
                idx = self.port_combo.findData(cur)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)

    def refresh_pcan_channels(self) -> None:
        cur = self.pcan_channel_combo.currentData()
        self.pcan_channel_combo.clear()
        chans = enumerate_channels()
        if not chans:
            self.pcan_channel_combo.addItem("No PCAN channel detected", None)
            return
        for ch in chans:
            label = ch["label"] + (f"  ·  {ch['name']}" if ch["name"] else "")
            if ch["occupied"]:
                label += "  (Occupied)"
            self.pcan_channel_combo.addItem(label, ch["handle"])
        if cur is not None:
            idx = self.pcan_channel_combo.findData(cur)
            if idx >= 0:
                self.pcan_channel_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    def _on_transport_changed(self, text: str) -> None:
        is_slcan = text == TRANSPORT_SLCAN
        is_pcan = text == TRANSPORT_PCAN
        self._port_row.setVisible(is_slcan)
        self.form.setRowVisible(self.host_baud_combo, is_slcan)
        self.form.setRowVisible(self._port_row, is_slcan)
        self.form.setRowVisible(self.pcan_channel_combo, is_pcan)
        if is_pcan:
            self.refresh_pcan_channels()
        self._update_note()
        self.settingsChanged.emit()

    def _update_note(self) -> None:
        text = self.transport_combo.currentText()
        bps = self.bitrate_combo.currentData()
        msgs = []
        if text == TRANSPORT_SIM:
            msgs.append("Simulator: Built-in virtual module, full experience without hardware.")
        elif text == TRANSPORT_SLCAN:
            if bps not in SlcanTransport.supported_bitrates() and HAVE_SLCAN:
                msgs.append(
                    f"⚠ SLCAN standard does not support {P.bps_label(bps)}, "
                    "please use standard rates like 250/500 kbps.")
        elif text == TRANSPORT_PCAN:
            if not HAVE_PCAN:
                msgs.append(f"⚠ PCANBasic.dll unavailable: {PCAN_LOAD_ERROR}")
            else:
                ver = api_version()
                if ver:
                    msgs.append(f"PCAN-Basic version {ver}.")
        if bps not in (200_000, 400_000) and text != TRANSPORT_SIM:
            pass
        msgs.append("Module default: Address 1 / 250 kbps (Sheet metal casing default 500 kbps).")
        if text == TRANSPORT_SLCAN:
            desc = (self.port_combo.currentText() or "").lower()
            if "蓝牙" in desc or "bluetooth" in desc or "bth" in desc:
                msgs.insert(0, "⚠ Currently selected is a Bluetooth virtual serial port, not a USB-CAN adapter —— "
                               "Please plug in the adapter, click \"Refresh\", and select a USB serial port like CH340 / CP210x / "
                               "FT232 / STM32.")
        self.note.setText(" ".join(msgs))

    # ------------------------------------------------------------------ #
    @property
    def frame_extended(self) -> bool:
        return self.ext_radio.isChecked()

    @property
    def addr(self) -> int:
        return self.addr_spin.value()

    @property
    def bitrate(self) -> int:
        return int(self.bitrate_combo.currentData() or P.DEFAULT_BPS)

    @property
    def transport_kind(self) -> str:
        return self.transport_combo.currentText()

    def build_transport(self):
        """Build transport object based on UI parameters (without opening)."""
        kind = self.transport_kind
        bps = self.bitrate

        if kind == TRANSPORT_SIM:
            return SimulatorTransport()

        if kind == TRANSPORT_PCAN:
            handle = self.pcan_channel_combo.currentData()
            from canio.transport import PcanTransport
            return PcanTransport(channel=handle or 0x51, can_bitrate=bps)

        port = self.port_combo.currentData()
        return SlcanTransport(
            port=port or "",
            host_baud=int(self.host_baud_combo.currentData() or 115200),
            can_bitrate=bps,
        )

    # ------------------------------------------------------------------ #
    def set_connected(self, connected: bool) -> None:
        self.connect_btn.setEnabled(not connected)
        self.disconnect_btn.setEnabled(connected)
        for w in (self.transport_combo, self.port_combo, self.host_baud_combo,
                  self.pcan_channel_combo, self.bitrate_combo,
                  self.std_radio, self.ext_radio):
            w.setEnabled(not connected)
        if connected:
            self.addr_spin.setEnabled(True)     # Address switching allowed online
        else:
            self.addr_spin.setEnabled(True)
