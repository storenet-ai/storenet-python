"""Errors raised by the StoreNet client."""

from __future__ import annotations


class StorenetError(Exception):
    """A failed call: `code` is the server code as sent (e.g. "409310") or a client-side word."""

    def __init__(
        self,
        code: str,
        status: int,
        message: str,
        details: dict[str, object] | None = None,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.details: dict[str, object] = dict(details or {})
        self.retry_after = retry_after

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        name = type(self).__name__
        return f"{name}(code={self.code!r}, status={self.status}, message={self.message!r})"


class StorenetConfigError(StorenetError):
    """Missing or unusable connection settings; `details["missing"]` names them."""

    def __init__(self, message: str, missing: list[str]) -> None:
        super().__init__("missing_config", 0, message, {"missing": list(missing)})
