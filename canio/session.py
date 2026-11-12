"""
Device Session Layer
================================================================

Elevates "raw CAN frames sent/received by transport layer" into "semantic operations for CANIO module":

* Request/Response pairing (matched by Function Code + Address, with timeout)
* Device state caching (Relays / Inputs / Address / Baud Rate)
* Optional periodic polling (background thread, prevents UI freeze)
* Address scanning (search for modules on the bus)

This module does not depend on any GUI library, making it easy to test independently.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from . import protocol as P
from .transport.base import CanTransport, LogLine, TransportError


# --------------------------------------------------------------------------- #
# Callback Types
# --------------------------------------------------------------------------- #
FrameHandler = Callable[[P.CanFrame, P.ParsedFrame], None]
LogHandler = Callable[[LogLine], None]
ErrorHandler = Callable[[str], None]
StateHandler = Callable[[dict], None]
PollHandler = Callable[[bool], None]


@dataclass
class SessionStats:
    tx: int = 0
    rx: int = 0
    timeouts: int = 0
    errors: int = 0
    started_at: float = 0.0

    def reset(self) -> None:
        self.tx = self.rx = self.timeouts = self.errors = 0
        self.started_at = time.time()


@dataclass
class DeviceState:
    """Module state cache observed from the bus."""

    addr: int = P.DEFAULT_ADDR
    baud_code: Optional[int] = None
    upload_ms: Optional[int] = None
    #: Values sent but require power cycle to take effect (Manual 3.4)
    pending_addr: Optional[int] = None
    pending_baud_code: Optional[int] = None
    relays: set[int] = field(default_factory=set)
    inputs: set[int] = field(default_factory=set)
    #: Whether any valid reply has been received (used to determine if comms are truly established)
    online: bool = False
    last_rx_at: float = 0.0
    last_error: str = ""

    def snapshot(self) -> dict:
        return {
            "addr": self.addr,
            "baud_code": self.baud_code,
            "upload_ms": self.upload_ms,
            "pending_addr": self.pending_addr,
            "pending_baud_code": self.pending_baud_code,
            "relays": set(self.relays),
            "inputs": set(self.inputs),
            "online": self.online,
            "last_rx_at": self.last_rx_at,
            "last_error": self.last_error,
        }


class _Waiter:
    __slots__ = ("event", "frame")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.frame: Optional[P.CanFrame] = None


class DeviceSession:
    """A debugging session between "Host Machine <-> Module"."""

    def __init__(self, transport: CanTransport, addr: int = P.DEFAULT_ADDR) -> None:
        self.transport = transport
        self.state = DeviceState(addr=addr)
        self.stats = SessionStats(started_at=time.time())
        #: True = Use extended frame (0xAA....), False = Standard frame (0x101)
        self.extended: bool = False

        self._lock = threading.RLock()          # Serialize requests
        self._plock = threading.Lock()          # Protect _pending
        self._pending: dict[tuple[int, int], list[_Waiter]] = {}
        self._closed = False

        self._on_frame: Optional[FrameHandler] = None
        self._on_log: Optional[LogHandler] = None
        self._on_error: Optional[ErrorHandler] = None
        self._on_state: Optional[StateHandler] = None
        self._on_poll: Optional[PollHandler] = None

        # Polling
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._poll_interval = 200               # ms
        self._poll_read_inputs = True
        self._poll_read_relays = True

        transport.set_callbacks(on_frame=self._handle_frame,
                               on_log=self._handle_log,
                               on_error=self._handle_error)

    # ------------------------------------------------------------------ #
    # Callback Registration
    # ------------------------------------------------------------------ #
    def set_handlers(self, on_frame: Optional[FrameHandler] = None,
                     on_log: Optional[LogHandler] = None,
                     on_error: Optional[ErrorHandler] = None,
                     on_state: Optional[StateHandler] = None,
                     on_poll: Optional[PollHandler] = None) -> None:
        self._on_frame = on_frame
        self._on_log = on_log
        self._on_error = on_error
        self._on_state = on_state
        self._on_poll = on_poll

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @property
    def is_open(self) -> bool:
        return self.transport.is_open and not self._closed

    def close(self) -> None:
        self.stop_polling()
        self._closed = True
        try:
            self.transport.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Transport Layer Callbacks (running in background thread)
    # ------------------------------------------------------------------ #
    def _handle_log(self, line: LogLine) -> None:
        if self._on_log:
            try:
                self._on_log(line)
            except Exception:
                pass

    def _handle_error(self, message: str) -> None:
        self.stats.errors += 1
        self.state.last_error = message
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:
                pass

    def _handle_frame(self, frame: P.CanFrame) -> None:
        if frame.tx:
            self.stats.tx += 1
        else:
            self.stats.rx += 1
        parsed = P.parse_frame(frame)

        # 1) Wake up waiting requests first
        key = (parsed.parts.func, parsed.parts.addr)
        with self._plock:
            waiters = self._pending.get(key)
            w = None
            if waiters:
                w = waiters.pop(0)
                if not waiters:
                    self._pending.pop(key, None)
        if w is not None:
            w.frame = frame
            w.event.set()

        # 2) Update state cache
        self._update_state(parsed)

        # 3) Notify UI
        if self._on_frame:
            try:
                self._on_frame(frame, parsed)
            except Exception:
                pass

    def _update_state(self, parsed: P.ParsedFrame) -> None:
        st = self.state
        changed: dict = {}
        parts = parsed.parts

        if parts.func == P.FUNC_READ_RELAY or parts.func == P.FUNC_WRITE_RELAY:
            if parsed.raw_channels != st.relays:
                st.relays = set(parsed.raw_channels)
                changed["relays"] = set(st.relays)
        elif parts.func == P.FUNC_READ_INPUT:
            if parsed.raw_channels != st.inputs:
                st.inputs = set(parsed.raw_channels)
                changed["inputs"] = set(st.inputs)

        if parts.func == P.FUNC_CONFIG and parsed.cfg_cmd is not None:
            if parsed.cfg_cmd == P.CFG_READ_ADDR:
                # Only "Read" represents the true current address of the module
                if parsed.cfg_value is not None and parsed.cfg_value != st.addr:
                    st.addr = parsed.cfg_value
                    changed["addr"] = st.addr
            elif parsed.cfg_cmd == P.CFG_WRITE_ADDR:
                # Dispatched address requires power cycle to take effect, only record as pending
                if parsed.cfg_value is not None and parsed.cfg_value != st.pending_addr:
                    st.pending_addr = parsed.cfg_value
                    changed["pending_addr"] = st.pending_addr
            elif parsed.cfg_cmd == P.CFG_READ_BAUD:
                if parsed.cfg_value is not None and parsed.cfg_value != st.baud_code:
                    st.baud_code = parsed.cfg_value
                    changed["baud_code"] = st.baud_code
            elif parsed.cfg_cmd == P.CFG_WRITE_BAUD:
                if parsed.cfg_value is not None and parsed.cfg_value != st.pending_baud_code:
                    st.pending_baud_code = parsed.cfg_value
                    changed["pending_baud_code"] = st.pending_baud_code
            elif parsed.cfg_cmd == P.CFG_WRITE_UPLOAD:
                # Active upload interval "takes effect immediately after parameter configuration" (Manual 2.3.3)
                if parsed.cfg_value is not None and parsed.cfg_value != st.upload_ms:
                    st.upload_ms = parsed.cfg_value
                    changed["upload_ms"] = st.upload_ms

        # Consider online as soon as a reply to local address is received
        was_online = st.online
        if parts.addr == st.addr or st.addr == 0:
            st.online = True
            st.last_rx_at = time.time()
        if not was_online and st.online:
            changed["online"] = True

        if changed and self._on_state:
            try:
                self._on_state(changed)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Request / Response
    # ------------------------------------------------------------------ #
    def request(self, frame: P.CanFrame, timeout: float = 0.5
                ) -> Optional[P.CanFrame]:
        """Send a frame and wait for a matching reply (Function Code + Address)."""
        if not self.is_open:
            raise TransportError("Channel not open")
        parts = P.parse_id(frame.can_id, frame.extended)
        key = (parts.func, parts.addr)
        w = _Waiter()
        with self._plock:
            self._pending.setdefault(key, []).append(w)

        try:
            with self._lock:
                self.transport.send(frame)
        except Exception:
            with self._plock:
                lst = self._pending.get(key)
                if lst and w in lst:
                    lst.remove(w)
                if not lst:
                    self._pending.pop(key, None)
            raise

        got = w.event.wait(timeout)
        if not got:
            with self._plock:
                lst = self._pending.get(key)
                if lst and w in lst:
                    lst.remove(w)
                if not lst:
                    self._pending.pop(key, None)
            self.stats.timeouts += 1
            return None
        return w.frame

    # -- Semantic Operations ----------------------------------------------- #
    def write_relays(self, channels: Iterable[int], timeout: float = 0.5) -> bool:
        f = P.build_write_relay(self.state.addr, channels, self.extended)
        return self.request(f, timeout) is not None

    def write_relay(self, channel: int, on: bool, timeout: float = 0.5) -> bool:
        """Single channel open/close (read-modify-write, keep other channels unchanged)."""
        want = set(self.state.relays)
        if on:
            want.add(channel)
        else:
            want.discard(channel)
        return self.write_relays(want, timeout)

    def read_relays(self, timeout: float = 0.5) -> Optional[set[int]]:
        f = P.build_read_relay(self.state.addr, self.extended)
        resp = self.request(f, timeout)
        if resp is None:
            return None
        return P.data_to_channels(resp.data)

    def read_inputs(self, timeout: float = 0.5) -> Optional[set[int]]:
        f = P.build_read_input(self.state.addr, self.extended)
        resp = self.request(f, timeout)
        if resp is None:
            return None
        return P.data_to_channels(resp.data)

    def read_address(self, addr: Optional[int] = None, timeout: float = 0.5
                     ) -> Optional[int]:
        f = P.build_cfg_read_addr(addr or self.state.addr, self.extended)
        resp = self.request(f, timeout)
        if resp is None:
            return None
        pf = P.parse_frame(resp)
        return pf.cfg_value

    def read_baud(self, addr: Optional[int] = None, timeout: float = 0.5
                  ) -> Optional[int]:
        f = P.build_cfg_read_baud(addr or self.state.addr, self.extended)
        resp = self.request(f, timeout)
        if resp is None:
            return None
        pf = P.parse_frame(resp)
        return pf.cfg_value

    def write_address(self, new_addr: int, timeout: float = 0.5) -> bool:
        f = P.build_cfg_write_addr(self.state.addr, new_addr, self.extended)
        return self.request(f, timeout) is not None

    def write_baud(self, baud_code: int, timeout: float = 0.5) -> bool:
        f = P.build_cfg_write_baud(self.state.addr, baud_code, self.extended)
        return self.request(f, timeout) is not None

    def write_upload_interval(self, ms: int, timeout: float = 0.5) -> bool:
        f = P.build_cfg_write_upload(self.state.addr, ms, self.extended)
        return self.request(f, timeout) is not None

    def send_raw(self, frame: P.CanFrame) -> None:
        """Directly send any frame ("Manual Send" on UI)."""
        with self._lock:
            self.transport.send(frame)

    def scan_addresses(self, first: int = 1, last: int = 32,
                       timeout: float = 0.12,
                       stop_flag: Optional[threading.Event] = None) -> list[int]:
        """Scan module addresses on the bus (read address code, existence confirmed upon reply)."""
        found: list[int] = []
        for a in range(first, last + 1):
            if stop_flag is not None and stop_flag.is_set():
                break
            f = P.build_cfg_read_addr(a, self.extended)
            resp = self.request(f, timeout)
            if resp is not None:
                pf = P.parse_frame(resp)
                if pf.cfg_cmd == P.CFG_READ_ADDR:
                    found.append(pf.cfg_value if pf.cfg_value else a)
        return found

    # ------------------------------------------------------------------ #
    # Frame Type (Standard / Extended)
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Periodic Polling
    # ------------------------------------------------------------------ #
    def start_polling(self, interval_ms: int = 200, read_inputs: bool = True,
                      read_relays: bool = True) -> None:
        self.stop_polling()
        self._poll_interval = max(20, int(interval_ms))
        self._poll_read_inputs = read_inputs
        self._poll_read_relays = read_relays
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop,
                                             name="canio-poll", daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        self._poll_stop.set()
        t = self._poll_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        self._poll_thread = None

    @property
    def polling(self) -> bool:
        return self._poll_thread is not None and self._poll_thread.is_alive()

    def _poll_loop(self) -> None:
        # Polling shares _lock with manual operations to avoid request interleaving
        while not self._poll_stop.is_set():
            t0 = time.monotonic()
            try:
                with self._lock:
                    if self._poll_read_inputs:
                        f = P.build_read_input(self.state.addr, self.extended)
                        self._send_no_wait(f)
                    if self._poll_read_relays:
                        f = P.build_read_relay(self.state.addr, self.extended)
                        self._send_no_wait(f)
            except Exception as exc:
                self._handle_error(f"Polling failed: {exc}")
                self._poll_stop.wait(1.0)
                continue
            elapsed = (time.monotonic() - t0) * 1000
            self._poll_stop.wait(max(0.01, (self._poll_interval - elapsed) / 1000))

    def _send_no_wait(self, frame: P.CanFrame) -> None:
        """Send without waiting for polling —— reply state updated via _handle_frame."""
        key = (P.parse_id(frame.can_id, frame.extended).func, self.state.addr)
        w = _Waiter()
        with self._plock:
            self._pending.setdefault(key, []).append(w)
        try:
            self.transport.send(frame)
        except Exception:
            with self._plock:
                lst = self._pending.get(key)
                if lst and w in lst:
                    lst.remove(w)
            raise
        # Background reaping to prevent infinite growth of pending list
        threading.Timer(1.0, self._reap, args=(key, w)).start()

    def _reap(self, key: tuple[int, int], w: _Waiter) -> None:
        with self._plock:
            lst = self._pending.get(key)
            if lst and w in lst:
                lst.remove(w)
            if not lst:
                self._pending.pop(key, None)

    # ------------------------------------------------------------------ #
    def apply_extended(self, extended: bool) -> None:
        """Toggle Standard / Extended frame (0x101 <-> 0xAA0101)."""
        self.extended = bool(extended)


__all__ = ["DeviceSession", "DeviceState", "SessionStats"]
