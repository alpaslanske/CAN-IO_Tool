"""CANIO Relay Debugging Console —— Main Window."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget, QSplitter)

from canio import protocol as P

from . import theme as T
from .controller import SessionController
from .panels.analyzer_panel import AnalyzerPanel
from .panels.config_panel import ConfigPanel
from .panels.connection import ConnectionPanel
from .panels.frame_panel import FramePanel
from .panels.input_panel import InputPanel
from .panels.relay_panel import CHANNEL_CHOICES, RelayPanel
from .widgets import Card, StatChip, StatusPill, hint_label, section_label

#: Suppress modal dialogs in headless environment (automated testing) to avoid blocking
SUPPRESS_DIALOGS = os.environ.get("CANIO_NO_DIALOG") == "1"


def _warn(parent, title: str, text: str) -> None:
    if SUPPRESS_DIALOGS:
        print(f"[dialog suppressed] {title}: {text}", file=sys.stderr)
        return
    QMessageBox.warning(parent, title, text)


def make_icon() -> QIcon:
    """App icon: Dark background + cyan relay symbol."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(T.ACCENT_DK))
    p.setPen(QColor(T.ACCENT))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor("#EAFBFF"))
    f = QFont(T.FONT_MONO, 26)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "IO")
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CANIO Relay Debugging Console · Zhongsheng Digital I/O Series (CAN Version)")
        self.setWindowIcon(make_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)

        self.ctl = SessionController(self)
        self._connected = False
        self._last_sb_update = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 12, 14, 8)
        outer.setSpacing(11)

        outer.addWidget(self._header())

        body = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(self._sidebar())
        body.addWidget(self._tabs())
        # Sol tarafın varsayılan boyutu ~360, sağ tarafın ise kalan alanı almasını sağlıyoruz
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([550, 1000]) 
        
        # QSplitter bir widget olduğu için addLayout yerine addWidget kullanıyoruz
        outer.addWidget(body, 1)

        self._build_statusbar()
        self._wire()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

        self._on_connection_changed(False, "")

    # ================================================================== #
    # Header
    # ================================================================== #
    def _header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("CardFlat")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(13)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        t1 = QLabel("CANIO Relay Debugging Console")
        t1.setObjectName("Title")
        t2 = QLabel("Zhongsheng Technology · Digital I/O Series (CAN Version) · Implemented per User Manual V3.0 Protocol")
        t2.setObjectName("SubTitle")
        title_box.addWidget(t1)
        title_box.addWidget(t2)
        lay.addLayout(title_box)
        lay.addStretch(1)

        self.link_lbl = QLabel("—")
        self.link_lbl.setObjectName("Mono")
        self.link_lbl.setStyleSheet(
            f"color:{T.TEXT_DIM}; font-family:{T.FONT_MONO}; font-size:11px;"
            f"background:transparent;")
        lay.addWidget(self.link_lbl)

        self.pill = StatusPill("Disconnected")
        lay.addWidget(self.pill)
        return w

    # ================================================================== #
    # Sidebar
    # ================================================================== #
    def _sidebar(self) -> QWidget:
        host = QWidget()
        host.setMinimumWidth(320) # setFixedWidth yerine setMinimumWidth kullanıyoruz
        inner = QWidget()
        
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(11)

        self.conn_panel = ConnectionPanel()
        lay.addWidget(self.conn_panel)
        lay.addWidget(self._card_profile())
        lay.addWidget(self._card_polling())
        lay.addWidget(self._card_stats())
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return host

    def _card_profile(self) -> Card:
        card = Card("Module Specs", "Select the channel count matching the actual module")
        row = QHBoxLayout()
        row.setSpacing(7)
        col1 = QVBoxLayout()
        col1.setSpacing(3)
        col1.addWidget(section_label("Relay Output"))
        self.relay_count_combo = QComboBox()
        for n in CHANNEL_CHOICES:
            self.relay_count_combo.addItem(f"{n} Channels", n)
        self.relay_count_combo.setCurrentText("4 Channels")
        col1.addWidget(self.relay_count_combo)
        col2 = QVBoxLayout()
        col2.setSpacing(3)
        col2.addWidget(section_label("Digital Input"))
        self.input_count_combo = QComboBox()
        for n in CHANNEL_CHOICES:
            self.input_count_combo.addItem(f"{n} Channels", n)
        self.input_count_combo.setCurrentText("4 Channels")
        col2.addWidget(self.input_count_combo)
        row.addLayout(col1, 1)
        row.addLayout(col2, 1)
        card.add_layout(row)
        card.add(hint_label(
            "The data field of this protocol is 6 bytes × 8 bits, addressing up to 48 channels per frame. "
            "Please keep the default for the currently connected 4-channel module."))
        return card

    def _card_polling(self) -> Card:
        card = Card("Periodic Polling", "Auto-refresh interface status")
        self.poll_check = QCheckBox("Enable auto-polling")
        card.add(self.poll_check)

        row = QHBoxLayout()
        row.addWidget(QLabel("Interval"))
        self.poll_interval = QSpinBox()
        self.poll_interval.setRange(50, 5000)
        self.poll_interval.setValue(200)
        self.poll_interval.setSuffix(" ms")
        self.poll_interval.setSingleStep(50)
        row.addWidget(self.poll_interval, 1)
        card.add_layout(row)

        self.poll_inputs = QCheckBox("Read input status (0x03)")
        self.poll_inputs.setChecked(True)
        self.poll_relays = QCheckBox("Read relay status (0x02)")
        self.poll_relays.setChecked(True)
        card.add(self.poll_inputs)
        card.add(self.poll_relays)
        return card

    def _card_stats(self) -> Card:
        card = Card("Comm Stats", "Accumulated for this session")
        grid = QHBoxLayout()
        grid.setSpacing(7)
        self.chip_tx = StatChip("Send TX", "0", T.TX)
        self.chip_rx = StatChip("Receive RX", "0", T.RX)
        self.chip_to = StatChip("Timeout", "0", T.WARN)
        self.chip_er = StatChip("Error", "0", T.ERR)
        for c in (self.chip_tx, self.chip_rx, self.chip_to, self.chip_er):
            grid.addWidget(c, 1)
        card.add_layout(grid)

        row = QHBoxLayout()
        self.reset_stats_btn = QPushButton("Reset Stats")
        self.reset_stats_btn.setObjectName("Mini")
        self.reset_stats_btn.clicked.connect(self._reset_stats)
        row.addWidget(self.reset_stats_btn)
        row.addStretch(1)
        card.add_layout(row)
        return card

    # ================================================================== #
    # Main Tabs
    # ================================================================== #
    def _tabs(self) -> QWidget:
        self.tabs = QTabWidget()

        self.relay_panel = RelayPanel(self.ctl)
        self.input_panel = InputPanel(self.ctl)
        self.analyzer_panel = AnalyzerPanel(self.ctl)
        self.frame_panel = FramePanel(self.ctl)
        self.config_panel = ConfigPanel(self.ctl)

        self.analyzer_panel._relay_count_provider = (
            lambda: int(self.relay_count_combo.currentData()))

        self.tabs.addTab(self.relay_panel, "Relay Control")
        self.tabs.addTab(self.input_panel, "Input Monitor")
        self.tabs.addTab(self.analyzer_panel, "Frame Builder / Parser")
        self.tabs.addTab(self.frame_panel, "Raw Frame Monitor")
        self.tabs.addTab(self.config_panel, "Module Parameters")
        return self.tabs

    # ================================================================== #
    # Status Bar
    # ================================================================== #
    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.sb_left = QLabel("Ready - Please select a debugger and click 'Connect'")
        self.sb_left.setStyleSheet(
            f"color:{T.TEXT_DIM}; background:transparent; font-size:11px;")
        sb.addWidget(self.sb_left)
        self.sb_right = QLabel("")
        self.sb_right.setStyleSheet(
            f"color:{T.TEXT_MUTE}; font-family:{T.FONT_MONO}; font-size:11px;"
            f"background:transparent;")
        sb.addPermanentWidget(self.sb_right)

    def _status(self, text: str, colour: str = T.TEXT_DIM) -> None:
        self.sb_left.setText(text)
        self.sb_left.setStyleSheet(
            f"color:{colour}; background:transparent; font-size:11px;")

    # ================================================================== #
    # Signals
    # ================================================================== #
    def _wire(self) -> None:
        self.conn_panel.connectRequested.connect(self._on_connect)
        self.conn_panel.disconnectRequested.connect(self._on_disconnect)
        self.conn_panel.addr_spin.valueChanged.connect(
            lambda v: self.ctl.set_addr(v))
        self.conn_panel.std_radio.toggled.connect(
            lambda std: self.ctl.set_extended(not std))

        self.relay_count_combo.currentIndexChanged.connect(self._on_relay_count)
        self.input_count_combo.currentIndexChanged.connect(self._on_input_count)

        self.poll_check.toggled.connect(self._on_poll_toggled)
        for w in (self.poll_interval, self.poll_inputs, self.poll_relays):
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self._restart_polling)
            else:
                w.toggled.connect(self._restart_polling)

        self.ctl.connectionChanged.connect(self._on_connection_changed)
        self.ctl.frameReceived.connect(self._on_frame)
        self.ctl.logReceived.connect(self.frame_panel.on_log)
        self.ctl.stateChanged.connect(self._on_state)
        self.ctl.errorOccurred.connect(self._on_error)
        self.ctl.busyChanged.connect(self._on_busy)

        self.frame_panel.frameActivated.connect(self._on_frame_activated)
        self.frame_panel.export_btn.clicked.connect(self._on_export)

    # ================================================================== #
    # Connect / Disconnect
    # ================================================================== #
    def _on_connect(self) -> None:
        if self._connected:
            return
        kind = self.conn_panel.transport_kind
        if kind != "Simulator (No Hardware)":
            try:
                transport = self.conn_panel.build_transport()
            except Exception as exc:
                _warn(self, "Parameter Error", str(exc))
                return
            if getattr(transport, "port", "x") == "":
                _warn(self, "No Serial Port Selected",
                      "Please select a serial port first (click 'Refresh' to re-enumerate).")
                return
        else:
            transport = self.conn_panel.build_transport()

        addr = self.conn_panel.addr
        ext = self.conn_panel.frame_extended
        self.pill.set_state("busy", "Connecting...")
        self.conn_panel.connect_btn.setEnabled(False)
        self._status(f"Opening {transport.display_name}...", T.WARN)

        def work():
            transport.open()
            return transport

        def done(t):
            self.ctl.attach(t, addr, ext)
            if hasattr(t, "reconfigure"):
                t.reconfigure(addr, int(self.relay_count_combo.currentData()),
                              int(self.input_count_combo.currentData()))
            self._status("Connected, reading module parameters...", T.OK)
            self.ctl.read_all(on_done=lambda *_: self._status(
                "Connection ready. Debugging can start.", T.OK))
            if self.poll_check.isChecked():
                self._restart_polling()

        def fail(msg: str):
            self.pill.set_state("err", "Connection Failed")
            self.conn_panel.set_connected(False)
            self._status(f"Connection Failed: {msg}", T.ERR)
            _warn(
                self, "Connection Failed",
                f"Failed to open {transport.display_name}.\n\n{msg}\n\n"
                "Troubleshooting Suggestions:\n"
                "· SLCAN: Ensure the serial port is not occupied by other software\n"
                "· Ensure the USB-CAN adapter driver is installed, and the COM port is visible in Device Manager\n"
                "· Check H(CAN+)/L(CAN-) wiring and terminal resistance\n"
                "· The CAN baud rate of the module and debugger must match\n"
                "· If using PCAN-Basic: Ensure PCANBasic.dll and drivers are installed")

        self.ctl.submit(work, on_done=done, on_error=fail)

    def _on_disconnect(self) -> None:
        self.ctl.stop_polling()
        self.ctl.detach()
        self._status("Disconnected.", T.TEXT_DIM)

    def _on_connection_changed(self, connected: bool, description: str) -> None:
        self._connected = connected
        self.conn_panel.set_connected(connected)
        for panel in (self.relay_panel, self.input_panel, self.analyzer_panel,
                      self.frame_panel, self.config_panel):
            panel.setEnabled(True)
        self.config_panel.set_enabled_state(connected)
        self.poll_check.setEnabled(connected)
        if connected:
            self.pill.set_state("ok", "Connected")
            self.link_lbl.setText(description)
        else:
            self.pill.set_state("off", "Disconnected")
            self.link_lbl.setText("—")
            self.relay_panel.sync_tiles(set())
            self.input_panel.sync(set())
            self.config_panel.sync({"online": False, "addr": None,
                                    "baud_code": None, "upload_ms": None})
        self._refresh_stats()

    def _on_busy(self, busy: bool) -> None:
        if busy and self._connected:
            self.pill.set_state("busy", "Communicating...")
        elif self._connected:
            self.pill.set_state("ok", "Connected")

    # ================================================================== #
    # Module Specs
    # ================================================================== #
    def _on_relay_count(self) -> None:
        n = int(self.relay_count_combo.currentData())
        self.relay_panel.set_channel_count(n)
        if self._connected and hasattr(self.ctl.transport, "reconfigure"):
            try:
                self.ctl.transport.reconfigure(
                    self.ctl.addr, n, int(self.input_count_combo.currentData()))
            except Exception:
                pass

    def _on_input_count(self) -> None:
        n = int(self.input_count_combo.currentData())
        self.input_panel.set_channel_count(n)
        if self._connected and hasattr(self.ctl.transport, "reconfigure"):
            try:
                self.ctl.transport.reconfigure(
                    self.ctl.addr, int(self.relay_count_combo.currentData()), n)
            except Exception:
                pass

    # ================================================================== #
    # Polling
    # ================================================================== #
    def _on_poll_toggled(self, on: bool) -> None:
        if not self._connected:
            return
        if on:
            self._restart_polling()
        else:
            self.ctl.stop_polling()
            self._status("Auto-polling stopped.", T.TEXT_DIM)

    def _restart_polling(self) -> None:
        if not self._connected or not self.poll_check.isChecked():
            return
        self.ctl.start_polling(self.poll_interval.value(),
                               self.poll_inputs.isChecked(),
                               self.poll_relays.isChecked())

    # ================================================================== #
    # Data Updates
    # ================================================================== #
    def _on_frame(self, frame: P.CanFrame, parsed: P.ParsedFrame) -> None:
        self.frame_panel.on_frame(frame, parsed)
        # Throttle status bar updates for high-frequency messages to ~8Hz
        now = time.monotonic()
        if now - self._last_sb_update < 0.12:
            return
        self._last_sb_update = now
        self.sb_right.setText(f"Latest {frame.id_text}  {frame.data_text}")

    def _on_state(self, changed: dict) -> None:
        if "relays" in changed:
            self.relay_panel.sync_tiles(changed["relays"])
        if "inputs" in changed:
            self.input_panel.sync(changed["inputs"])
        self.config_panel.sync(changed)
        if "addr" in changed:
            self.conn_panel.addr_spin.blockSignals(True)
            self.conn_panel.addr_spin.setValue(changed["addr"])
            self.conn_panel.addr_spin.blockSignals(False)

    def _on_error(self, message: str) -> None:
        self._status(f"⚠ {message}", T.ERR)

    def _on_frame_activated(self, frame: P.CanFrame) -> None:
        self.analyzer_panel.load_frame(frame)
        self.tabs.setCurrentWidget(self.analyzer_panel)

    def _on_export(self) -> None:
        path = self.frame_panel.export_csv()
        if path:
            self._status(f"Exported: {path}", T.OK)

    # ================================================================== #
    def _refresh_stats(self) -> None:
        s = self.ctl.session.stats if self.ctl.session else None
        if s is None:
            for c, col in ((self.chip_tx, T.TX), (self.chip_rx, T.RX),
                           (self.chip_to, T.WARN), (self.chip_er, T.ERR)):
                c.set_value(0, T.TEXT_MUTE)
            return
        self.chip_tx.set_value(s.tx, T.TX if s.tx else T.TEXT_MUTE)
        self.chip_rx.set_value(s.rx, T.RX if s.rx else T.TEXT_MUTE)
        self.chip_to.set_value(s.timeouts, T.WARN if s.timeouts else T.TEXT_MUTE)
        self.chip_er.set_value(s.errors, T.ERR if s.errors else T.TEXT_MUTE)

    def _reset_stats(self) -> None:
        if self.ctl.session:
            self.ctl.session.stats.reset()
        self._refresh_stats()

    # ================================================================== #
    def closeEvent(self, event) -> None:
        try:
            self.ctl.stop_polling()
            self.ctl.detach()
        except Exception:
            pass
        super().closeEvent(event)
