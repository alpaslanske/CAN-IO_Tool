"""
SLCAN / Lawicel ASCII Serial Transport Layer
================================================================

Suitable for CANable, CANtact, and most USB-CAN adapters like CH340 / CH343 / 
CP2102 / FT232 / STM32-VCP.

Protocol Commands (all ASCII, terminated by CR)
----------------------------------
``S<n>``  Set CAN baud rate         ``O``     Open CAN channel
``C``     Close CAN channel         ``F``     Read status flags
``V``     Read version/serial num   ``tIIIDDDD...``  Send standard frame
``TIIIIIIIIDDDD...``                Send extended frame

Where ``t`` frame has a 3-digit hex ID + up to 16-digit hex data;
``T`` frame has an 8-digit hex ID + up to 16-digit hex data.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from ..protocol import CanFrame, EXT_MAGIC, frame_from_slcan
from .base import CanTransport, TransportError, TransportUnavailable

try:                                    # pyserial is an optional dependency
    import serial
    from serial.tools import list_ports as _list_ports
    _HAVE_SERIAL = True
except Exception:                       # pragma: no cover
    serial = None                        # type: ignore
    _list_ports = None                   # type: ignore
    _HAVE_SERIAL = False

#: CAN baud rate -> SLCAN S code
BPS_TO_S_CODE: dict[int, int] = {
    10_000: 0,
    20_000: 1,
    50_000: 2,
    100_000: 3,
    125_000: 4,
    250_000: 5,
    500_000: 6,
    800_000: 7,
    1_000_000: 8,
}
S_CODE_TO_BPS: dict[int, int] = {v: k for k, v in BPS_TO_S_CODE.items()}

#: Common baud rates for the serial side (USB side)
HOST_BAUDS = [9600, 19200, 38400, 57600, 115200, 230400, 250000, 500000,
              921600, 1000000, 2000000]


def list_serial_ports() -> list[tuple[str, str]]:
    """Return [(device_name, description), ...], sorted by device name."""
    if not _HAVE_SERIAL:
        return []
    out = []
    for p in _list_ports.comports():
        desc = p.description or ""
        if p.manufacturer and p.manufacturer not in desc:
            desc = f"{desc} ({p.manufacturer})".strip()
        out.append((p.device, desc))
    out.sort(key=lambda t: (len(t[0]), t[0]))
    return out


class SlcanTransport(CanTransport):
    """Serial-based SLCAN Transport."""

    display_name = "SLCAN (Serial Port)"

    def __init__(self, port: str = "", host_baud: int = 115200,
                 can_bitrate: int = 250_000, open_delay: float = 0.08,
                 echo_suppress: bool = True) -> None:
        super().__init__()
        self.port = port
        self.host_baud = int(host_baud)
        self.can_bitrate = int(can_bitrate)
        self.open_delay = float(open_delay)
        self.echo_suppress = bool(echo_suppress)

        self._ser: Optional["serial.Serial"] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._buf = bytearray()
        self._write_lock = threading.Lock()
        self._recent_tx: deque[tuple[bytes, float]] = deque(maxlen=64)
        self.version = ""

    # ------------------------------------------------------------------ #
    @classmethod
    def is_available(cls) -> bool:
        return _HAVE_SERIAL

    @classmethod
    def supported_bitrates(cls) -> list[int]:
        return sorted(BPS_TO_S_CODE)

    # ------------------------------------------------------------------ #
    def open(self) -> None:
        if self._open:
            return
        if not _HAVE_SERIAL:
            raise TransportUnavailable("pyserial not installed, cannot use serial transport")
        if not self.port:
            raise TransportError("No serial port selected")

        code = BPS_TO_S_CODE.get(self.can_bitrate)
        if code is None:
            raise TransportError(
                f"SLCAN does not support {self.can_bitrate // 1000} kbps "
                "(Only supports 10/20/50/100/125/250/500/800/1000 kbps)")

        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.host_baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=1.0,
            )
        except Exception as exc:
            raise TransportError(f"Failed to open serial port {self.port}: {exc}") from exc

        self._stop.clear()
        self._buf.clear()
        self._recent_tx.clear()

        self._emit_log("SYS", f"Opened serial port {self.port} @ {self.host_baud} bps")

        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

        # ---- SLCAN Initialization Timing: C -> S<n> -> O ---- #
        self._raw_write("C")
        time.sleep(self.open_delay)
        self._raw_write(f"S{code}")
        time.sleep(self.open_delay)
        self._raw_write("O")
        time.sleep(self.open_delay)

        self._open = True
        self._rx_thread = threading.Thread(target=self._reader_loop,
                                           name="slcan-rx", daemon=True)
        self._rx_thread.start()

        # Best effort to read version (not supported by all adapters)
        try:
            self._raw_write("V")
        except Exception:
            pass

        self._emit_log(
            "SYS",
            f"CAN channel opened: S{code} = {self.can_bitrate // 1000} kbps "
            + (" (Standard/Extended frames supported)" if True else ""))

    def close(self) -> None:
        if not self._open and self._ser is None:
            return
        was_open = self._open
        self._open = False
        self._stop.set()

        if self._ser is not None:
            if was_open:
                try:
                    self._raw_write("C")
                except Exception:
                    pass
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

        if self._rx_thread is not None and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        self._rx_thread = None
        self._emit_log("SYS", "Serial port closed")

    # ------------------------------------------------------------------ #
    def _raw_write(self, cmd: str) -> None:
        if self._ser is None:
            raise TransportError("Serial port not open")
        line = cmd + "\r"
        with self._write_lock:
            self._ser.write(line.encode("ascii"))
            self._ser.flush()
        self._emit_log("TX", cmd.strip())

    def send(self, frame: CanFrame) -> None:
        if not self._open:
            raise TransportError("CAN channel not open")
        body = "".join(f"{b:02X}" for b in frame.data)
        if frame.extended:
            cmd = f"T{frame.can_id:08X}{body}"
        else:
            cmd = f"t{frame.can_id:03X}{body}"
        self._raw_write(cmd)
        if self.echo_suppress:
            key = (frame.can_id.to_bytes(4, "big") + (b"\x01" if frame.extended else b"\x00")
                   + bytes(frame.data))
            self._recent_tx.append((key, time.monotonic()))

    # ------------------------------------------------------------------ #
    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ser = self._ser
                if ser is None:
                    break
                chunk = ser.read(256)
            except Exception as exc:
                if not self._stop.is_set():
                    self._emit_error(f"Serial read error: {exc}")
                break
            if not chunk:
                continue
            self._buf.extend(chunk)
            self._drain_buffer()
        self._buf.clear()

    def _drain_buffer(self) -> None:
        """Split by CR / LF, process SLCAN replies line by line."""
        while True:
            idx = -1
            for i, b in enumerate(self._buf):
                if b in (0x0D, 0x0A):
                    idx = i
                    break
            if idx < 0:
                return
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            if not raw:
                continue
            self._handle_line(raw.decode("ascii", "replace").strip())

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        head = line[0]
        # Receiving a frame
        if head in "tT":
            frame = frame_from_slcan(line, timestamp=time.time(), tx=False)
            if frame is None:
                self._emit_log("RX", line, "Unparseable frame")
                return
            if self.echo_suppress and self._is_recent_echo(frame):
                self._emit_log("SYS", f"(Ignored echo) {line}")
                return
            self._emit_log("RX", line)
            self._emit_frame(frame)
            return
        if head in "rR":
            self._emit_log("RX", line, "Remote frame")
            return
        if head == "V":
            self.version = line[1:].strip()
            self._emit_log("SYS", f"Adapter version: {self.version}")
            return
        if head == "F":
            self._emit_log("SYS", f"Status flags: {line[1:]}")
            return
        if head == "Z":
            self._emit_log("SYS", f"Timestamp setting: {line[1:]}")
            return
        if head == "S":
            self._emit_log("RX", line, "Baud rate echo")
            return
        if head in "CO":
            self._emit_log("RX", line, "Channel command echo")
            return
        if head == "\a":                        # BEL = Command failed
            self._emit_error("Adapter returned BEL: Command execution failed")
            return
        self._emit_log("SYS", f"Unknown reply: {line!r}")

    def _is_recent_echo(self, frame: CanFrame) -> bool:
        key = (frame.can_id.to_bytes(4, "big")
               + (b"\x01" if frame.extended else b"\x00") + bytes(frame.data))
        now = time.monotonic()
        for k, t in reversed(self._recent_tx):
            if k == key and (now - t) < 1.0:
                return True
        return False

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return (f"SLCAN {self.port} @{self.host_baud} "
                f"| CAN {self.can_bitrate // 1000}kbps")

    def read_status_flags(self) -> None:
        """Send F command to query bus status."""
        if self._open:
            self._raw_write("F")


__all__ = ["SlcanTransport", "list_serial_ports", "BPS_TO_S_CODE",
           "S_CODE_TO_BPS", "HOST_BAUDS"]
