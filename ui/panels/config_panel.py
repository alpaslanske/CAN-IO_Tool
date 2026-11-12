"""CANIO Module Parameter Config Panel (Address / Baud Rate / Active Upload / Bus Scan)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from canio import protocol as P

from .. import theme as T
from ..widgets import Card, hint_label

WARN_BG = f"background:#2A2208; border:1px solid {T.WARN}; border-radius:8px;" \
          f"padding:8px 10px; color:{T.WARN}; font-weight:600;"


def _kv(label: str) -> tuple[QWidget, QLabel]:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lab = QLabel(label)
    lab.setStyleSheet(
        f"color:{T.TEXT_DIM}; font-size:11px; background:transparent;")
    lab.setFixedWidth(96)
    val = QLabel("—")
    val.setStyleSheet(
        f"color:{T.TEXT}; font-family:{T.FONT_MONO}; font-size:13px;"
        f"font-weight:700; background:transparent;")
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(lab)
    lay.addWidget(val, 1)
    return w, val


class ConfigPanel(QWidget):
    """Online read/write of module parameters."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctl = controller

        root = QGridLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(12)

        root.addWidget(self._card_read(), 0, 0)
        root.addWidget(self._card_scan(), 0, 1)
        root.addWidget(self._card_addr(), 1, 0)
        root.addWidget(self._card_baud(), 1, 1)
        root.addWidget(self._card_upload(), 2, 0)
        self.sim_card = self._card_simulator()
        root.addWidget(self.sim_card, 2, 1)
        root.setRowStretch(3, 1)

    # ================================================================== #
    def _card_read(self) -> Card:
        card = Card("Current Parameters", "Function Code 0x04 / Sub-command 0xA1, 0xA2")
        self.k_addr, self.v_addr = _kv("Address Code")
        self.k_baud, self.v_baud = _kv("Baud Rate Code")
        self.k_bps, self.v_bps = _kv("Actual Baud Rate")
        self.k_up, self.v_up = _kv("Active Upload")
        self.k_online, self.v_online = _kv("Comm Status")
        for w in (self.k_addr, self.k_baud, self.k_bps, self.k_up, self.k_online):
            card.add(w)

        row = QHBoxLayout()
        self.read_btn = QPushButton("Read All Parameters")
        self.read_btn.setObjectName("Primary")
        self.read_btn.clicked.connect(lambda: self.ctl.read_all())
        row.addWidget(self.read_btn)
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _card_addr(self) -> Card:
        card = Card("Write Module Address", "Sub-command 0xB1")
        row = QHBoxLayout()
        row.addWidget(QLabel("New Address"))
        self.addr_spin = QSpinBox()
        self.addr_spin.setRange(P.ADDR_MIN, P.ADDR_MAX)
        self.addr_spin.setValue(2)
        row.addWidget(self.addr_spin, 1)
        self.addr_btn = QPushButton("Write")
        self.addr_btn.clicked.connect(
            lambda: self.ctl.write_address(self.addr_spin.value(),
                                           on_done=lambda *_: self._after_write()))
        row.addWidget(self.addr_btn)
        card.add_layout(row)

        self.addr_note = hint_label("")
        self.addr_note.setStyleSheet(WARN_BG)
        self.addr_note.setVisible(False)
        card.add(self.addr_note)
        return card

    def _card_baud(self) -> Card:
        card = Card("Write Baud Rate", "Sub-command 0xB2 (Manual Table 2.6)")
        row = QHBoxLayout()
        row.addWidget(QLabel("Baud Rate Code"))
        self.baud_combo = QComboBox()
        for code, bps in sorted(P.BAUD_CODE_TO_BPS.items()):
            self.baud_combo.addItem(f"0x{code:02X}   {P.bps_label(bps)}", code)
        self.baud_combo.setCurrentIndex(
            list(sorted(P.BAUD_CODE_TO_BPS)).index(0x09))
        row.addWidget(self.baud_combo, 1)
        self.baud_btn = QPushButton("Write")
        self.baud_btn.clicked.connect(
            lambda: self.ctl.write_baud(int(self.baud_combo.currentData()),
                                        on_done=lambda *_: self._after_write()))
        row.addWidget(self.baud_btn)
        card.add_layout(row)

        self.baud_note = hint_label("")
        self.baud_note.setStyleSheet(WARN_BG)
        self.baud_note.setVisible(False)
        card.add(self.baud_note)
        return card

    def _card_upload(self) -> Card:
        card = Card("Active Upload Interval", "Sub-command 0xB3 · 16-bit Big-Endian · Unit ms")
        row = QHBoxLayout()
        self.up_spin = QSpinBox()
        self.up_spin.setRange(0, 60000)
        self.up_spin.setValue(0)
        self.up_spin.setSuffix("  ms")
        self.up_spin.setSpecialValueText("0 ms (Disable Active Upload)")
        row.addWidget(self.up_spin, 1)
        self.up_btn = QPushButton("Write")
        self.up_btn.clicked.connect(
            lambda: self.ctl.write_upload(self.up_spin.value()))
        row.addWidget(self.up_btn)
        card.add_layout(row)
        card.add(hint_label(
            "Minimum interval 5ms, 0 means no active upload. This parameter \"takes effect immediately after setting\" and is saved upon power loss."))
        return card

    def _card_scan(self) -> Card:
        card = Card("Bus Device Scan", "Read address by address to find online modules")
        row = QHBoxLayout()
        row.addWidget(QLabel("Range"))
        self.scan_from = QSpinBox()
        self.scan_from.setRange(1, 255)
        self.scan_from.setValue(1)
        self.scan_to = QSpinBox()
        self.scan_to.setRange(1, 255)
        self.scan_to.setValue(32)
        for s in (self.scan_from, self.scan_to):
            s.setFixedWidth(62)
        row.addWidget(self.scan_from)
        row.addWidget(QLabel("~"))
        row.addWidget(self.scan_to)
        self.scan_btn = QPushButton("Start Scan")
        self.scan_btn.clicked.connect(
            lambda: self.ctl.scan(self.scan_from.value(), self.scan_to.value(),
                                  on_done=self._on_scan_done))
        row.addWidget(self.scan_btn, 1)
        card.add_layout(row)

        self.scan_result = QLabel("Not scanned yet")
        self.scan_result.setWordWrap(True)
        self.scan_result.setStyleSheet(
            f"background:{T.BG_INPUT}; border:1px solid {T.BORDER};"
            f"border-radius:8px; padding:8px 10px; color:{T.TEXT_DIM};"
            f"font-family:{T.FONT_MONO};")
        card.add(self.scan_result)
        return card

    def _card_simulator(self) -> Card:
        card = Card("Simulator Tools", "Only available when using the built-in simulator")
        card.add(hint_label(
            "Real machines must be powered off and restarted for address/baud rate changes to take effect; you can simulate this process here, "
            "or inject input signals onto the bus."))

        row = QHBoxLayout()
        self.power_btn = QPushButton("Simulate Power Cycle")
        self.power_btn.clicked.connect(self._sim_power_cycle)
        row.addWidget(self.power_btn, 1)
        card.add_layout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Inject Input Channel"))
        self.sim_ch = QSpinBox()
        self.sim_ch.setRange(1, 48)
        self.sim_ch.setValue(1)
        self.sim_ch.setFixedWidth(62)
        row2.addWidget(self.sim_ch)
        self.sim_on = QPushButton("Set to Triggered")
        self.sim_on.setObjectName("Mini")
        self.sim_on.clicked.connect(lambda: self._sim_input(True))
        self.sim_off = QPushButton("Set to Untriggered")
        self.sim_off.setObjectName("Mini")
        self.sim_off.clicked.connect(lambda: self._sim_input(False))
        row2.addWidget(self.sim_on)
        row2.addWidget(self.sim_off)
        row2.addStretch(1)
        card.add_layout(row2)

        self.sim_jitter = QCheckBox("Random Input Jitter (Demo Only)")
        self.sim_jitter.setToolTip(
            "When enabled, the simulator will randomly toggle input channels to demonstrate UI dynamics.\n"
            "It is recommended to turn this off during formal debugging so the UI only reflects signals you inject.")
        self.sim_jitter.toggled.connect(self._sim_set_jitter)
        card.add(self.sim_jitter)

        self.sim_note = hint_label("")
        card.add(self.sim_note)
        return card

    # ================================================================== #
    def _after_write(self) -> None:
        show = True
        for note, text in (
            (self.addr_note, "Address sent —— Please power off the module for about 5 seconds and power it back on for the new address to take effect."),
            (self.baud_note, "Baud rate sent —— Please power off the module for about 5 seconds and power it back on, "
                             "and synchronously change the debugger baud rate to match, otherwise communication will fail."),
        ):
            note.setText(text)
            note.setVisible(show)

    def _on_scan_done(self, found) -> None:
        if found is None:
            found = []
        if found:
            self.scan_result.setText(
                f"Found {len(found)} modules, addresses: " + ", ".join(str(a) for a in found))
            self.scan_result.setStyleSheet(
                f"background:#0C2A1B; border:1px solid {T.OK}; border-radius:8px;"
                f"padding:8px 10px; color:{T.OK}; font-family:{T.FONT_MONO};")
        else:
            self.scan_result.setText(
                "No response found. Please check: Baud rate match, H/L wiring, terminal resistance, "
                "and if the module address is within the scan range.")
            self.scan_result.setStyleSheet(
                f"background:#2A2208; border:1px solid {T.WARN}; border-radius:8px;"
                f"padding:8px 10px; color:{T.WARN}; font-weight:600;")

    # -- Simulator ------------------------------------------------------- #
    def _is_simulator(self) -> bool:
        from canio.transport import SimulatorTransport
        return isinstance(self.ctl.transport, SimulatorTransport)

    def _sim_power_cycle(self) -> None:
        if self._is_simulator():
            self.ctl.transport.power_cycle()
            self.sim_note.setText("Virtual module restarted, pending address/baud rate have taken effect.")
            self.ctl.read_all()

    def _sim_set_jitter(self, on: bool) -> None:
        if self._is_simulator():
            self.ctl.transport.random_inputs = bool(on)
            self.sim_note.setText(
                "Random input jitter enabled (Demo mode)." if on else "Random input jitter disabled.")

    def _sim_input(self, on: bool) -> None:
        if not self._is_simulator():
            return
        try:
            self.ctl.transport.set_input(self.sim_ch.value(), on,
                                         addr=self.ctl.addr)
            self.sim_note.setText(
                f"Set input X{self.sim_ch.value()} to {'Triggered' if on else 'Untriggered'}.")
            self.ctl.read_inputs()
        except Exception as exc:
            self.sim_note.setText(f"Injection failed: {exc}")

    # ================================================================== #
    def sync(self, state: dict) -> None:
        """Refresh display according to session state."""
        addr = state.get("addr")
        if addr is not None:
            self.v_addr.setText(f"{addr}   (0x{addr:02X})")
            self.addr_spin.setValue(addr)

        code = state.get("baud_code")
        if code is not None:
            self.v_baud.setText(f"0x{code:02X}")
            bps = P.BAUD_CODE_TO_BPS.get(code)
            self.v_bps.setText(P.bps_label(bps) if bps else "Unknown baud rate")
            self.v_bps.setStyleSheet(
                f"color:{T.OK}; font-family:{T.FONT_MONO}; font-size:13px;"
                f"font-weight:700; background:transparent;")
            idx = self.baud_combo.findData(code)
            if idx >= 0:
                self.baud_combo.setCurrentIndex(idx)

        up = state.get("upload_ms")
        if up is not None:
            self.v_up.setText("Disabled" if up == 0 else f"{up} ms")
            self.up_spin.setValue(up)

        pa = state.get("pending_addr")
        if pa is not None:
            self.addr_note.setText(
                f"Sent new address {pa} —— Requires module power cycle to take effect (real machine).")
            self.addr_note.setVisible(True)
        pb = state.get("pending_baud_code")
        if pb is not None:
            bps = P.BAUD_CODE_TO_BPS.get(pb)
            self.baud_note.setText(
                f"Sent baud rate code 0x{pb:02X} ({P.bps_label(bps)}) —— "
                f"Requires module power cycle to take effect, and debugger baud rate must be updated synchronously.")
            self.baud_note.setVisible(True)

        online = state.get("online")
        if online is not None:
            if online:
                self.v_online.setText("Online ✓")
                self.v_online.setStyleSheet(
                    f"color:{T.OK}; font-family:{T.FONT_MONO}; font-size:13px;"
                    f"font-weight:700; background:transparent;")
            else:
                self.v_online.setText("No Response")
                self.v_online.setStyleSheet(
                    f"color:{T.TEXT_MUTE}; font-family:{T.FONT_MONO};"
                    f"font-size:13px; font-weight:700; background:transparent;")

        self.sim_card.setVisible(self._is_simulator())

    def set_enabled_state(self, connected: bool) -> None:
        for w in (self.read_btn, self.addr_btn, self.baud_btn, self.up_btn,
                  self.scan_btn, self.addr_spin, self.baud_combo, self.up_spin,
                  self.scan_from, self.scan_to, self.sim_ch, self.sim_on,
                  self.sim_off, self.power_btn, self.sim_jitter):
            w.setEnabled(connected)
