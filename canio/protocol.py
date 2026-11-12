"""
CANIO Digital I/O Module (CAN Version) —— Communication Protocol Codec Layer
================================================================

Strictly implemented according to Chapter 2 "Communication Protocol" of the "Digital I/O Series User Manual (CAN Version) V3.0".

Frame Format
------
* Standard Frame: 11-bit ID = [Function Code(3bit)][Address(8bit)]
* Extended Frame: 29-bit ID = [Arbitrary(5bit)][0xAA(8bit)][Function Code(8bit)][Address(8bit)]

Each message has a fixed 8 bytes of data. Bytes 1~6 sequentially represent channels 1~8 / 9~16 / 17~24 /
25~32 / 33~40 / 41~48, where Bit0 -> Bit7 within a byte correspond to lower -> higher channels.
Bytes 7 and 8 are reserved.

Function Codes
------
0x01 Write relay status     0x02 Read relay status
0x03 Read input status      0x04 Parameter configuration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DLEN = 8                      # Fixed 8-byte data field
MAX_CHANNELS = 48             # Data field 6 bytes x 8 bits = 48 channels
EXT_MAGIC = 0xAA              # Magic byte in Extended Frame ID

ADDR_MIN = 1
ADDR_MAX = 255

#: Function Codes
FUNC_WRITE_RELAY = 0x01
FUNC_READ_RELAY = 0x02
FUNC_READ_INPUT = 0x03
FUNC_CONFIG = 0x04

FUNC_NAMES = {
    FUNC_WRITE_RELAY: "Write Relay Status",
    FUNC_READ_RELAY: "Read Relay Status",
    FUNC_READ_INPUT: "Read Input Status",
    FUNC_CONFIG: "Parameter Config",
}

#: Parameter sub-commands for Function Code 0x04
CFG_READ_ADDR = 0xA1
CFG_READ_BAUD = 0xA2
CFG_WRITE_ADDR = 0xB1
CFG_WRITE_BAUD = 0xB2
CFG_WRITE_UPLOAD = 0xB3

CFG_NAMES = {
    CFG_READ_ADDR: "Read Address Code (0xA1)",
    CFG_READ_BAUD: "Read Baud Rate Code (0xA2)",
    CFG_WRITE_ADDR: "Write Address Code (0xB1)",
    CFG_WRITE_BAUD: "Write Baud Rate Code (0xB2)",
    CFG_WRITE_UPLOAD: "Write Active Upload Interval (0xB3)",
}

#: Baud Rate Code -> bps (Manual Table 2.6)
BAUD_CODE_TO_BPS: dict[int, int] = {
    0x02: 20_000,
    0x03: 50_000,
    0x04: 100_000,
    0x05: 125_000,
    0x06: 200_000,
    0x07: 250_000,
    0x08: 400_000,
    0x09: 500_000,
    0x0A: 800_000,
    0x0B: 1_000_000,
}
BPS_TO_BAUD_CODE: dict[int, int] = {v: k for k, v in BAUD_CODE_TO_BPS.items()}

#: Factory default parameters (Manual Chapter 2 intro / 3.3.1)
DEFAULT_ADDR = 1
DEFAULT_BPS = 250_000
DEFAULT_UPLOAD_MS = 0


def bps_label(bps: Optional[int]) -> str:
    if not bps:
        return "--"
    if bps >= 1_000_000:
        return "1 Mbps"
    return f"{bps // 1000} kbps"


def fmt_hex_id(can_id: int, extended: bool) -> str:
    """0x101 / 0xAA0101 —— Manual style ID display."""
    width = 8 if extended else 3
    return f"0x{can_id:0{width}X}"


def fmt_data(data: Sequence[int]) -> str:
    return " ".join(f"{b:02X}" for b in data)


# --------------------------------------------------------------------------- #
# Frame Object
# --------------------------------------------------------------------------- #

@dataclass
class CanFrame:
    """A CAN message (generic representation independent of transport layer)."""

    can_id: int
    data: bytes = b""
    extended: bool = False
    timestamp: float = 0.0
    #: True = sent by local machine, False = returned by module
    tx: bool = False
    #: Note for UI display (e.g., "Write relay 1-2,45-48")
    note: str = ""

    @property
    def dlc(self) -> int:
        return len(self.data)

    @property
    def id_text(self) -> str:
        return fmt_hex_id(self.can_id, self.extended)

    @property
    def data_text(self) -> str:
        return fmt_data(self.data)

    def to_slcan(self) -> str:
        """Convert to Lawicel/SLCAN ASCII command (excluding trailing CR)."""
        body = "".join(f"{b:02X}" for b in self.data)
        if self.extended:
            return f"T{self.can_id:08X}{body}"
        return f"t{self.can_id:03X}{body}"

    def describe(self) -> str:
        """Human-readable full frame description."""
        parsed = parse_id(self.can_id, self.extended)
        return (f"{self.id_text}  [{parsed.func_name}] addr={parsed.addr}  "
                f"data= {self.data_text}")


# --------------------------------------------------------------------------- #
# ID Construction / Parsing
# --------------------------------------------------------------------------- #

@dataclass
class IdParts:
    """Bitfield breakdown result of a CAN ID."""

    can_id: int
    extended: bool
    func: int
    addr: int
    magic_ok: bool = True
    arbitrary: int = 0          # Extended frame bits 28~24 (arbitrary value)
    func_known: bool = False

    @property
    def func_name(self) -> str:
        return FUNC_NAMES.get(self.func, f"Unknown Function Code 0x{self.func:02X}")

    def bit_rows(self) -> list[tuple[str, str, str, str]]:
        """Returns (Field, Bit Range, Binary, Value) for easy UI bitfield table rendering."""
        if self.extended:
            bits = f"{self.can_id:029b}"
            rows = [
                ("Arbitrary", "28~24", bits[0:5],
                 f"0x{int(bits[0:5], 2):02X}"),
                ("Magic 0xAA", "23~16", bits[5:13],
                 f"0x{int(bits[5:13], 2):02X}"),
                ("Function Code", "15~8", bits[13:21],
                 f"0x{self.func:02X}  {self.func_name}"),
                ("Address Code", "7~0", bits[21:29],
                 f"{self.addr}  (0x{self.addr:02X})"),
            ]
        else:
            bits = f"{self.can_id:011b}"
            rows = [
                ("Function Code", "10~8", bits[0:3],
                 f"0x{self.func:X}  {self.func_name}"),
                ("Address Code", "7~0", bits[3:11],
                 f"{self.addr}  (0x{self.addr:02X})"),
            ]
        return rows


def make_id(func: int, addr: int, extended: bool = False) -> int:
    """Generate CAN ID according to the manual."""
    func &= 0x0F
    addr &= 0xFF
    if extended:
        return ((EXT_MAGIC & 0xFF) << 16) | ((func & 0xFF) << 8) | addr
    return ((func & 0x07) << 8) | addr


def parse_id(can_id: int, extended: bool) -> IdParts:
    """Deconstruct CAN ID back into Function Code + Address Code."""
    if extended:
        arbitrary = (can_id >> 24) & 0x1F
        magic = (can_id >> 16) & 0xFF
        func = (can_id >> 8) & 0xFF
        addr = can_id & 0xFF
        return IdParts(can_id=can_id, extended=True, func=func, addr=addr,
                       magic_ok=(magic == EXT_MAGIC), arbitrary=arbitrary,
                       func_known=(func in FUNC_NAMES))
    func = (can_id >> 8) & 0x07
    addr = can_id & 0xFF
    return IdParts(can_id=can_id, extended=False, func=func, addr=addr,
                   func_known=(func in FUNC_NAMES))


# --------------------------------------------------------------------------- #
# Channel <-> Data Field
# --------------------------------------------------------------------------- #

def channels_to_data(channels: Iterable[int]) -> bytes:
    """Pack channel number set into 8-byte data field (Bit=1 means closed/triggered)."""
    buf = bytearray(DLEN)
    for ch in channels:
        if 1 <= ch <= MAX_CHANNELS:
            idx = (ch - 1) // 8
            bit = (ch - 1) % 8
            buf[idx] |= 1 << bit
    return bytes(buf)


def data_to_channels(data: Sequence[int]) -> set[int]:
    """Extract all channel numbers set to 1 from the data field."""
    out: set[int] = set()
    for idx in range(min(6, len(data))):
        byte = data[idx]
        for bit in range(8):
            if byte >> bit & 1:
                out.add(idx * 8 + bit + 1)
    return out


def channel_bit_table(data: Sequence[int], limit: int = MAX_CHANNELS
                      ) -> list[tuple[int, int, int]]:
    """Returns (Channel No, Byte Index 0-based, Bit Index) -> Value details, used for bitfield display."""
    rows = []
    for ch in range(1, limit + 1):
        idx = (ch - 1) // 8
        bit = (ch - 1) % 8
        val = (data[idx] >> bit) & 1 if idx < len(data) else 0
        rows.append((ch, idx, bit, val))
    return rows


def compress_channel_ranges(channels: Iterable[int]) -> str:
    """Compress channel numbers into a range string like '1-2, 45-48'."""
    chs = sorted(set(channels))
    if not chs:
        return "None"
    parts, start, prev = [], chs[0], chs[0]
    for c in chs[1:]:
        if c == prev + 1:
            prev = c
            continue
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = c
    parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def parse_channel_spec(text: str, limit: int = MAX_CHANNELS) -> set[int]:
    """
    Parse channel expression like ``1,2,45-48`` (inverse operation of compress).

    Tolerates spaces, Chinese commas, semicolons; channels outside 1~limit will be discarded.
    """
    out: set[int] = set()
    if not text:
        return out
    norm = text.replace("，", ",").replace("；", ",").replace(";", ",")
    norm = norm.replace(" ", "").replace("\t", "")
    for token in norm.split(","):
        if not token:
            continue
        if "-" in token[1:]:                     # Supports "1-8"
            a, _, b = token.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            out.update(range(max(1, lo), min(limit, hi) + 1))
        else:
            try:
                v = int(token)
            except ValueError:
                continue
            if 1 <= v <= limit:
                out.add(v)
    return out


# --------------------------------------------------------------------------- #
# Frame Construction
# --------------------------------------------------------------------------- #

def build_frame(func: int, addr: int, data: bytes | bytearray | Sequence[int] = b"",
                extended: bool = False) -> CanFrame:
    """Construct a message for any function code (data field automatically padded to 8 bytes)."""
    buf = bytearray(DLEN)
    for i, b in enumerate(list(data)[:DLEN]):
        buf[i] = b & 0xFF
    return CanFrame(can_id=make_id(func, addr, extended), data=bytes(buf),
                    extended=extended)


def build_write_relay(addr: int, channels: Iterable[int], extended: bool = False) -> CanFrame:
    """Function Code 0x01 —— Write relay status (Whole frame written at once, unlisted channels are opened)."""
    payload = channels_to_data(channels)
    f = build_frame(FUNC_WRITE_RELAY, addr, payload, extended)
    f.note = f"Write Relay {compress_channel_ranges(channels)}"
    return f


def build_read_relay(addr: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_READ_RELAY, addr, b"", extended)
    f.note = "Read Relay Status"
    return f


def build_read_input(addr: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_READ_INPUT, addr, b"", extended)
    f.note = "Read Input Status"
    return f


def build_cfg_read_addr(addr: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_CONFIG, addr, bytes([CFG_READ_ADDR]), extended)
    f.note = "Read Module Address"
    return f


def build_cfg_read_baud(addr: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_CONFIG, addr, bytes([CFG_READ_BAUD]), extended)
    f.note = "Read Module Baud Rate"
    return f


def build_cfg_write_addr(addr: int, new_addr: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_CONFIG, addr, bytes([CFG_WRITE_ADDR, new_addr & 0xFF]), extended)
    f.note = f"Write New Address {new_addr}"
    return f


def build_cfg_write_baud(addr: int, baud_code: int, extended: bool = False) -> CanFrame:
    f = build_frame(FUNC_CONFIG, addr, bytes([CFG_WRITE_BAUD, baud_code & 0xFF]), extended)
    f.note = f"Write Baud Rate Code 0x{baud_code:02X}"
    return f


def build_cfg_write_upload(addr: int, interval_ms: int, extended: bool = False) -> CanFrame:
    """
    Active Upload Interval: 16-bit unsigned integer, unit ms, min 5ms, 0 = disable upload.

    Manual example `0x401 B3 00 0A ...` description says "Set active upload interval to 10ms",
    meaning Byte 2 = High byte, Byte 3 = Low byte —— **Big-Endian**.
    """
    iv = max(0, min(0xFFFF, int(interval_ms)))
    f = build_frame(FUNC_CONFIG, addr,
                    bytes([CFG_WRITE_UPLOAD, (iv >> 8) & 0xFF, iv & 0xFF]), extended)
    f.note = f"Write Active Upload Interval {iv} ms"
    return f


# --------------------------------------------------------------------------- #
# Frame Parsing
# --------------------------------------------------------------------------- #

@dataclass
class ParsedFrame:
    """Complete semantic parsing result of a message, for UI display."""

    frame: CanFrame
    parts: IdParts
    raw_channels: set[int] = field(default_factory=set)
    #: Active when function code is 0x04
    cfg_cmd: Optional[int] = None
    cfg_value: Optional[int] = None
    error: str = ""

    @property
    def is_relay_frame(self) -> bool:
        return self.parts.func in (FUNC_WRITE_RELAY, FUNC_READ_RELAY)

    @property
    def is_input_frame(self) -> bool:
        return self.parts.func == FUNC_READ_INPUT

    def summary(self) -> str:
        if self.error:
            return f"Parse Failed: {self.error}"
        p = self.parts
        if not p.func_known:
            return f"Unknown Function Code 0x{p.func:02X}"
        if p.func == FUNC_WRITE_RELAY:
            return f"Write Relay -> {compress_channel_ranges(self.raw_channels)} Closed"
        if p.func == FUNC_READ_RELAY:
            return f"Relay Status -> {compress_channel_ranges(self.raw_channels)} Closed"
        if p.func == FUNC_READ_INPUT:
            return f"Input Status -> {compress_channel_ranges(self.raw_channels)} Triggered"
        # 0x04
        name = CFG_NAMES.get(self.cfg_cmd or -1, f"Unknown Sub-command 0x{(self.cfg_cmd or 0):02X}")
        if self.cfg_cmd in (CFG_READ_ADDR, CFG_WRITE_ADDR):
            return f"{name} -> Address {self.cfg_value}"
        if self.cfg_cmd in (CFG_READ_BAUD, CFG_WRITE_BAUD):
            bps = BAUD_CODE_TO_BPS.get(self.cfg_value or -1)
            return f"{name} -> 0x{(self.cfg_value or 0):02X} ({bps_label(bps)})"
        if self.cfg_cmd == CFG_WRITE_UPLOAD:
            return f"{name} -> {self.cfg_value} ms"
        return name


def parse_frame(frame: CanFrame) -> ParsedFrame:
    """Parse a message into structured semantics."""
    parts = parse_id(frame.can_id, frame.extended)
    pf = ParsedFrame(frame=frame, parts=parts)

    if frame.extended and not parts.magic_ok:
        pf.error = f"Extended frame magic byte should be 0xAA, actual is 0x{(frame.can_id >> 16) & 0xFF:02X}"
        # Continue parsing to facilitate debugging

    data = frame.data
    if not parts.func_known:
        return pf

    if parts.func in (FUNC_WRITE_RELAY, FUNC_READ_RELAY, FUNC_READ_INPUT):
        pf.raw_channels = data_to_channels(data)
        return pf

    # Function Code 0x04
    if len(data) < 1:
        pf.error = "Parameter config frame missing sub-command byte"
        return pf
    pf.cfg_cmd = data[0]
    if pf.cfg_cmd in (CFG_READ_ADDR, CFG_WRITE_ADDR, CFG_READ_BAUD, CFG_WRITE_BAUD):
        pf.cfg_value = data[1] if len(data) > 1 else None
    elif pf.cfg_cmd == CFG_WRITE_UPLOAD:
        if len(data) >= 3:
            pf.cfg_value = (data[1] << 8) | data[2]      # Big-endian 16-bit (see manual example)
        else:
            pf.error = "Active upload interval should be 2 bytes"
    return pf


def frame_from_slcan(text: str, timestamp: float = 0.0, tx: bool = False
                     ) -> Optional[CanFrame]:
    """Parse SLCAN ASCII line, e.g., 't1010300000000F00000' / 'T0000AA0101...'."""
    s = text.strip()
    if not s:
        return None
    kind, rest = s[0], s[1:]
    if kind in "tT":
        idlen = 8 if kind == "T" else 3
        if len(rest) < idlen:
            return None
        try:
            can_id = int(rest[:idlen], 16)
        except ValueError:
            return None
        hexdata = rest[idlen:]
        # Allow short data fields with 0 / 2 byte alignment
        if len(hexdata) % 2:
            hexdata = hexdata[:-1]
        try:
            data = bytes.fromhex(hexdata)
        except ValueError:
            return None
        return CanFrame(can_id=can_id, data=data, extended=(kind == "T"),
                        timestamp=timestamp, tx=tx)
    if kind in "rR":
        # Remote frame, not used by this module protocol, just record
        return None
    return None


__all__ = [
    "CanFrame", "IdParts", "ParsedFrame",
    "DLEN", "MAX_CHANNELS", "EXT_MAGIC", "ADDR_MIN", "ADDR_MAX",
    "FUNC_WRITE_RELAY", "FUNC_READ_RELAY", "FUNC_READ_INPUT", "FUNC_CONFIG",
    "FUNC_NAMES", "CFG_NAMES", "CFG_READ_ADDR", "CFG_READ_BAUD",
    "CFG_WRITE_ADDR", "CFG_WRITE_BAUD", "CFG_WRITE_UPLOAD",
    "BAUD_CODE_TO_BPS", "BPS_TO_BAUD_CODE", "DEFAULT_ADDR", "DEFAULT_BPS",
    "DEFAULT_UPLOAD_MS",
    "make_id", "parse_id", "channels_to_data", "data_to_channels",
    "channel_bit_table", "compress_channel_ranges", "parse_channel_spec",
    "build_frame",
    "build_write_relay", "build_read_relay", "build_read_input",
    "build_cfg_read_addr", "build_cfg_read_baud", "build_cfg_write_addr",
    "build_cfg_write_baud", "build_cfg_write_upload",
    "parse_frame", "frame_from_slcan", "bps_label", "fmt_hex_id", "fmt_data",
]
