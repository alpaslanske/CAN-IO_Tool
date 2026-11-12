"""CAN Transport Layer Abstract Interface."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from ..protocol import CanFrame

#: Transport layer event callbacks
FrameCallback = Callable[[CanFrame], None]
LogCallback = Callable[["LogLine"], None]
ErrorCallback = Callable[[str], None]


@dataclass
class LogLine:
    """Transport layer raw interaction log (e.g., SLCAN ASCII lines)."""

    direction: str          # "TX" | "RX" | "SYS"
    text: str
    detail: str = ""


class TransportError(Exception):
    """Transport layer open/read/write error."""


class TransportUnavailable(TransportError):
    """Transport unavailable in current environment (missing dll, no serial port, etc.)."""


class CanTransport(ABC):
    """Common base class for all transport methods.

    Convention:
      * ``open()`` / ``close()`` executed by calling thread;
      * ``send()`` is thread-safe;
      * Received messages sent via ``on_frame`` callback, **callback runs in background thread**,
        upper layer must switch to UI thread itself.
    """

    #: Name displayed on UI
    display_name = "Unnamed"
    #: Whether this transport method is available on current machine
    @classmethod
    def is_available(cls) -> bool:
        return True

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._open = False
        self._on_frame: Optional[FrameCallback] = None
        self._on_log: Optional[LogCallback] = None
        self._on_error: Optional[ErrorCallback] = None

    # -- Callback Registration --------------------------------------------- #
    def set_callbacks(self, on_frame: Optional[FrameCallback] = None,
                      on_log: Optional[LogCallback] = None,
                      on_error: Optional[ErrorCallback] = None) -> None:
        self._on_frame = on_frame
        self._on_log = on_log
        self._on_error = on_error

    def _emit_frame(self, frame: CanFrame) -> None:
        cb = self._on_frame
        if cb:
            try:
                cb(frame)
            except Exception:                       # Exception in callback must not interrupt receiving thread
                pass

    def _emit_log(self, direction: str, text: str, detail: str = "") -> None:
        cb = self._on_log
        if cb:
            try:
                cb(LogLine(direction, text, detail))
            except Exception:
                pass

    def _emit_error(self, message: str) -> None:
        cb = self._on_error
        if cb:
            try:
                cb(message)
            except Exception:
                pass

    # -- Lifecycle --------------------------------------------------------- #
    @property
    def is_open(self) -> bool:
        return self._open

    @abstractmethod
    def open(self) -> None:
        """Open channel, raises TransportError on failure."""

    @abstractmethod
    def close(self) -> None:
        """Close channel, must be callable repeatedly."""

    @abstractmethod
    def send(self, frame: CanFrame) -> None:
        """Send one frame; raises TransportError if not open."""

    # -- Optional Capabilities --------------------------------------------- #
    @classmethod
    def supported_bitrates(cls) -> list[int]:
        """CAN baud rates (bps) supported by this transport method."""
        return []

    @property
    def description(self) -> str:
        """One-line description for status bar upon successful connection."""
        return self.display_name
