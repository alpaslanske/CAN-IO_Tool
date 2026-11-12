"""
PCAN-Basic (PCANBasic.dll) Transport Layer
================================================================

Directly calls the official PEAK ``PCANBasic.dll`` via ctypes, suitable for genuine PEAK
PCAN-USB / PCAN-PCI / PCAN-LAN hardware, and adapters compatible with the PCAN driver protocol.

If PCANBasic.dll is missing on the target machine, this transport method will be marked as unavailable,
disabled in the UI automatically, while other transport methods remain unaffected.
"""
from __future__ import annotations

import ctypes
import threading
import time
import sys
from ctypes import wintypes
from typing import Optional

from ..protocol import CanFrame
from .base import CanTransport, TransportError, TransportUnavailable

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PCAN_NONEBUS = 0x00

_PCAN_CHANNEL_GROUPS: dict[str, range] = {
    "USB": range(0x51, 0x59),      # PCAN_USBBUS1..8
    "PCI": range(0x41, 0x49),      # PCAN_PCIBUS1..8
    "LAN": range(0x81, 0x89),      # PCAN_LANBUS1..8
    "ISA": range(0x21, 0x25),
    "VIRTUAL": range(0x31, 0x35),  # PCAN_VIRTUALBUS1..4
}
_CHANNEL_PREFIX = {"USB": "PCAN_USBBUS", "PCI": "PCAN_PCIBUS", "LAN": "PCAN_LANBUS",
                   "ISA": "PCAN_ISABUS", "VIRTUAL": "PCAN_VIRTUALBUS"}

#: Classic BTR0BTR1 (8 MHz clock)
PCAN_BTR0BTR1: dict[int, int] = {
    10_000: 0x672F,
    20_000: 0x532F,
    50_000: 0x472F,
    100_000: 0x432F,
    125_000: 0x031C,
    250_000: 0x011C,
    500_000: 0x001C,
    800_000: 0x0016,
    1_000_000: 0x0014,
}

#: Error codes
PCAN_ERROR_OK = 0x00000
PCAN_ERROR_XMTFULL = 0x00001
PCAN_ERROR_OVERRUN = 0x00002
PCAN_ERROR_BUSLIGHT = 0x00004
PCAN_ERROR_BUSHEAVY = 0x00008
PCAN_ERROR_BUSOFF = 0x00010
PCAN_ERROR_QRCVEMPTY = 0x00020
PCAN_ERROR_QOVERRUN = 0x00040
PCAN_ERROR_QXMTFULL = 0x00080
PCAN_ERROR_REGTEST = 0x00100
PCAN_ERROR_NODRIVER = 0x00200
PCAN_ERROR_HWINUSE = 0x00400
PCAN_ERROR_NETINUSE = 0x00800
PCAN_ERROR_ILLHW = 0x01400
PCAN_ERROR_ILLNET = 0x01800
PCAN_ERROR_ILLCLIENT = 0x01C00
PCAN_ERROR_ILLHANDLE = 0x01C00
PCAN_ERROR_RESOURCE = 0x02000
PCAN_ERROR_ILLPARAMTYPE = 0x04000
PCAN_ERROR_ILLPARAMVAL = 0x08000
PCAN_ERROR_UNKNOWN = 0x10000
PCAN_ERROR_CAUTION = 0x2000000
PCAN_ERROR_INITIALIZE = 0x4000000

#: Message type bits
PCAN_MESSAGE_STANDARD = 0x00
PCAN_MESSAGE_RTR = 0x01
PCAN_MESSAGE_EXTENDED = 0x02
PCAN_MESSAGE_FD = 0x04
PCAN_MESSAGE_BRS = 0x08
PCAN_MESSAGE_ECHO = 0x10
PCAN_MESSAGE_ERRFRAME = 0x40
PCAN_MESSAGE_STATUS = 0x80

#: Parameters (CAN_GetValue / CAN_SetValue)
PCAN_DEVICE_NUMBER = 0x01
PCAN_5VOLTS_POWER = 0x02
PCAN_RECEIVE_EVENT = 0x03
PCAN_MESSAGE_FILTER = 0x04
PCAN_API_VERSION = 0x05
PCAN_CHANNEL_VERSION = 0x06
PCAN_BUSOFF_AUTORESET = 0x07
PCAN_LISTEN_ONLY = 0x08
PCAN_LOG_LOCATION = 0x09
PCAN_LOG_STATUS = 0x0A
PCAN_LOG_CONFIGURE = 0x0B
PCAN_LOG_TEXT = 0x0C
PCAN_CHANNEL_CONDITION = 0x0D
PCAN_HARDWARE_NAME = 0x0E
PCAN_RECEIVE_STATUS = 0x0F
PCAN_CONTROLLER_NUMBER = 0x10
PCAN_TRACE_LOCATION = 0x11
PCAN_TRACE_STATUS = 0x12
PCAN_TRACE_SIZE = 0x13
PCAN_TRACE_CONFIGURE = 0x14
PCAN_CHANNEL_IDENTIFYING = 0x15
PCAN_CHANNEL_FEATURES = 0x16
PCAN_BITRATE_ADAPTING = 0x17
PCAN_BITRATE_INFO = 0x18
PCAN_BITRATE_INFO_FD = 0x19
PCAN_BUSSPEED_NOMINAL = 0x1A

PCAN_CHANNEL_UNAVAILABLE = 0x00
PCAN_CHANNEL_AVAILABLE = 0x01
PCAN_CHANNEL_OCCUPIED = 0x02
PCAN_CHANNEL_PCANVIEW = 0x03

PCAN_LANGUAGE_NEUTRAL = 0x00
PCAN_LANGUAGE_ENGLISH = 0x09
PCAN_LANGUAGE_CHINESE = 0x0A


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #

class TPCANMsg(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint32),
        ("MSGTYPE", ctypes.c_ubyte),
        ("LEN", ctypes.c_ubyte),
        ("DATA", ctypes.c_ubyte * 8),
    ]


class TPCANTimestamp(ctypes.Structure):
    _fields_ = [
        ("millis", ctypes.c_uint32),
        ("millis_overflow", ctypes.c_uint16),
        ("micros", ctypes.c_uint16),
    ]


# --------------------------------------------------------------------------- #
# DLL Loading
# --------------------------------------------------------------------------- #

_DLL_LOCK = threading.Lock()
_DLL: Optional[ctypes.WinDLL] = None
_DLL_ERROR = ""


def _load_dll() -> Optional[ctypes.WinDLL]:
    """Load PCANBasic.dll and declare function prototypes (done only once)."""
    global _DLL, _DLL_ERROR
    with _DLL_LOCK:
        if _DLL is not None:
            return _DLL
        if _DLL_ERROR:
            return None
        try:

            if sys.platform.startswith("linux"):
                dll = ctypes.CDLL("libpcanbasic.so")
            else:
                dll = ctypes.WinDLL("PCANBasic.dll")

        except OSError as exc:
            _DLL_ERROR = f"Failed to load PCANBasic.dll: {exc}"
            return None

        c_ushort, c_uint, c_ubyte = ctypes.c_ushort, ctypes.c_uint, ctypes.c_ubyte
        c_void_p = ctypes.c_void_p

        dll.CAN_Initialize.argtypes = [c_ushort, c_ushort, c_uint, c_uint, c_uint]
        dll.CAN_Initialize.restype = c_uint

        dll.CAN_Uninitialize.argtypes = [c_ushort]
        dll.CAN_Uninitialize.restype = c_uint

        dll.CAN_Reset.argtypes = [c_ushort]
        dll.CAN_Reset.restype = c_uint

        dll.CAN_Read.argtypes = [c_ushort, ctypes.POINTER(TPCANMsg),
                                 ctypes.POINTER(TPCANTimestamp)]
        dll.CAN_Read.restype = c_uint

        dll.CAN_Write.argtypes = [c_ushort, ctypes.POINTER(TPCANMsg)]
        dll.CAN_Write.restype = c_uint

        dll.CAN_GetErrorText.argtypes = [c_uint, c_ushort, ctypes.c_char_p]
        dll.CAN_GetErrorText.restype = c_uint

        dll.CAN_GetValue.argtypes = [c_ushort, c_ubyte, c_void_p, c_uint]
        dll.CAN_GetValue.restype = c_uint

        dll.CAN_SetValue.argtypes = [c_ushort, c_ubyte, c_void_p, c_uint]
        dll.CAN_SetValue.restype = c_uint

        _DLL = dll
        return _DLL


def pcan_error_text(code: int) -> str:
    dll = _load_dll()
    if dll is None:
        return f"0x{code:X} ({_DLL_ERROR})"
    buf = ctypes.create_string_buffer(512)
    dll.CAN_GetErrorText(code, PCAN_LANGUAGE_ENGLISH, buf)
    text = buf.value.decode("utf-8", "replace").strip()
    if not text or "?" in text:
        dll.CAN_GetErrorText(code, PCAN_LANGUAGE_NEUTRAL, buf)
        text = buf.value.decode("utf-8", "replace").strip()
    return f"0x{code:X} {text}"


def api_version() -> str:
    dll = _load_dll()
    if dll is None:
        return ""
    ver = ctypes.c_uint32(0)
    if dll.CAN_GetValue(PCAN_NONEBUS, PCAN_API_VERSION,
                        ctypes.byref(ver), ctypes.sizeof(ver)) != PCAN_ERROR_OK:
        return ""
    raw = ver.value
    if raw == 0:
        return ""
    return f"{raw >> 24}.{(raw >> 16) & 0xFF}.{raw & 0xFFFF}"


def channel_condition(handle: int) -> int:
    dll = _load_dll()
    if dll is None:
        return PCAN_CHANNEL_UNAVAILABLE
    cond = ctypes.c_uint32(0)
    st = dll.CAN_GetValue(ctypes.c_ushort(handle), PCAN_CHANNEL_CONDITION,
                          ctypes.byref(cond), ctypes.sizeof(cond))
    return cond.value if st == PCAN_ERROR_OK else PCAN_CHANNEL_UNAVAILABLE


def hardware_name(handle: int) -> str:
    dll = _load_dll()
    if dll is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    if dll.CAN_GetValue(ctypes.c_ushort(handle), PCAN_HARDWARE_NAME,
                        buf, ctypes.sizeof(buf)) != PCAN_ERROR_OK:
        return ""
    return buf.value.decode("utf-8", "replace").strip()


def enumerate_channels() -> list[dict]:
    """Enumerate all installed and currently available PCAN channels."""
    dll = _load_dll()
    if dll is None:
        return []
    out = []
    for group, rng in _PCAN_CHANNEL_GROUPS.items():
        for i, handle in enumerate(rng):
            cond = channel_condition(handle)
            if cond in (PCAN_CHANNEL_AVAILABLE, PCAN_CHANNEL_OCCUPIED):
                out.append({
                    "handle": handle,
                    "group": group,
                    "index": i + 1,
                    "label": f"{_CHANNEL_PREFIX[group]}{i + 1}",
                    "name": hardware_name(handle),
                    "occupied": cond == PCAN_CHANNEL_OCCUPIED,
                })
    return out


# --------------------------------------------------------------------------- #
# Transport Implementation
# --------------------------------------------------------------------------- #

class PcanTransport(CanTransport):
    """PCAN-Basic Transport."""

    display_name = "PCAN-Basic"

    def __init__(self, channel: int = 0x51, can_bitrate: int = 250_000,
                 listen_only: bool = False, auto_reset_busoff: bool = True) -> None:
        super().__init__()
        self.channel = int(channel)
        self.can_bitrate = int(can_bitrate)
        self.listen_only = bool(listen_only)
        self.auto_reset_busoff = bool(auto_reset_busoff)

        self._dll = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._msg = TPCANMsg()
        self._ts = TPCANTimestamp()
        self.hw_name = ""

    # ------------------------------------------------------------------ #
    @classmethod
    def is_available(cls) -> bool:
        return _load_dll() is not None

    @classmethod
    def unavailable_reason(cls) -> str:
        _load_dll()
        return _DLL_ERROR

    @classmethod
    def supported_bitrates(cls) -> list[int]:
        return sorted(PCAN_BTR0BTR1)

    # ------------------------------------------------------------------ #
    def open(self) -> None:
        if self._open:
            return
        dll = _load_dll()
        if dll is None:
            raise TransportUnavailable(_DLL_ERROR or "PCANBasic.dll unavailable")

        btr = PCAN_BTR0BTR1.get(self.can_bitrate)
        if btr is None:
            raise TransportError(
                f"PCAN does not support {self.can_bitrate // 1000} kbps")

        self._dll = dll
        st = dll.CAN_Initialize(ctypes.c_ushort(self.channel), btr, 0, 0, 0)
        if st != PCAN_ERROR_OK:
            raise TransportError(
                f"Failed to initialize PCAN channel 0x{self.channel:02X}: {pcan_error_text(st)}")

        self.hw_name = hardware_name(self.channel)

        # Optional settings: BusOff auto-reset / Listen-only mode
        try:
            flag = ctypes.c_uint32(1 if self.auto_reset_busoff else 0)
            dll.CAN_SetValue(ctypes.c_ushort(self.channel), PCAN_BUSOFF_AUTORESET,
                             ctypes.byref(flag), ctypes.sizeof(flag))
        except Exception:
            pass
        if self.listen_only:
            try:
                flag = ctypes.c_uint32(1)
                dll.CAN_SetValue(ctypes.c_ushort(self.channel), PCAN_LISTEN_ONLY,
                                 ctypes.byref(flag), ctypes.sizeof(flag))
            except Exception:
                pass

        self._stop.clear()
        self._open = True
        self._rx_thread = threading.Thread(target=self._reader_loop,
                                           name="pcan-rx", daemon=True)
        self._rx_thread.start()

    def close(self) -> None:
        if not self._open and self._dll is None:
            return
        self._open = False
        self._stop.set()
        if self._rx_thread is not None and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        self._rx_thread = None
        if self._dll is not None:
            try:
                self._dll.CAN_Uninitialize(ctypes.c_ushort(self.channel))
            except Exception:
                pass
        self._dll = None

    # ------------------------------------------------------------------ #
    def send(self, frame: CanFrame) -> None:
        if not self._open or self._dll is None:
            raise TransportError("PCAN channel not open")
        msg = TPCANMsg()
        msg.ID = frame.can_id
        msg.MSGTYPE = (PCAN_MESSAGE_EXTENDED if frame.extended
                       else PCAN_MESSAGE_STANDARD)
        data = bytes(frame.data)[:8]
        msg.LEN = len(data)
        for i, b in enumerate(data):
            msg.DATA[i] = b
        st = self._dll.CAN_Write(ctypes.c_ushort(self.channel), ctypes.byref(msg))
        if st != PCAN_ERROR_OK:
            raise TransportError(f"CAN_Write failed: {pcan_error_text(st)}")

    # ------------------------------------------------------------------ #
    def _reader_loop(self) -> None:
        dll = self._dll
        if dll is None:
            return
        msg = TPCANMsg()
        ts = TPCANTimestamp()
        while not self._stop.is_set():
            st = dll.CAN_Read(ctypes.c_ushort(self.channel),
                              ctypes.byref(msg), ctypes.byref(ts))
            if st == PCAN_ERROR_QRCVEMPTY:
                time.sleep(0.002)
                continue
            if st == PCAN_ERROR_OK:
                self._dispatch(msg, ts)
                continue
            # Bus warning errors: report but continue
            if st & 0xFFFF0000 or st in (PCAN_ERROR_BUSLIGHT, PCAN_ERROR_BUSHEAVY,
                                         PCAN_ERROR_BUSOFF, PCAN_ERROR_QOVERRUN):
                self._emit_error(f"PCAN status: {pcan_error_text(st)}")
                time.sleep(0.02)
                continue
            self._emit_error(f"CAN_Read failed: {pcan_error_text(st)}")
            time.sleep(0.05)

    def _dispatch(self, msg: TPCANMsg, ts: TPCANTimestamp) -> None:
        mt = msg.MSGTYPE
        if mt & PCAN_MESSAGE_STATUS:
            self._emit_log("SYS", f"Bus status event 0x{msg.ID:08X}")
            return
        if mt & PCAN_MESSAGE_ERRFRAME:
            self._emit_error(f"Error frame 0x{msg.ID:08X}")
            return
        if mt & PCAN_MESSAGE_RTR:
            self._emit_log("RX", f"Remote frame ID=0x{msg.ID:X}")
            return
        # Timestamp conversion (accumulated overflow count)
        micros = (ts.millis_overflow * 0x10000 + ts.millis) * 1000 + ts.micros
        data = bytes(msg.DATA[:msg.LEN])
        frame = CanFrame(
            can_id=int(msg.ID),
            data=data,
            extended=bool(mt & PCAN_MESSAGE_EXTENDED),
            timestamp=micros / 1_000_000.0,
            tx=bool(mt & PCAN_MESSAGE_ECHO),
        )
        self._emit_log("RX", frame.to_slcan())
        self._emit_frame(frame)

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        label = next((v["label"] for v in enumerate_channels()
                      if v["handle"] == self.channel), f"0x{self.channel:02X}")
        return (f"PCAN {label}"
                + (f" [{self.hw_name}]" if self.hw_name else "")
                + f" @{self.can_bitrate // 1000}kbps")


__all__ = ["PcanTransport", "enumerate_channels", "pcan_error_text",
           "api_version", "PCAN_BTR0BTR1", "TPCANMsg", "TPCANTimestamp",
           "channel_condition", "hardware_name"]
