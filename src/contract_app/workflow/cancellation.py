"""Thread-safe cooperative cancellation signals shared by task handlers."""

from typing import Protocol


class CancellationSignal(Protocol):
    def set(self) -> None: ...

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...
