"""StoreNet Partner API: create, AI-edit and list shop designs from Python or the shell."""

from __future__ import annotations

from storenet._version import __version__
from storenet.client import Edit, Site, StorenetClient
from storenet.errors import StorenetConfigError, StorenetError

__all__ = [
    "Edit",
    "Site",
    "StorenetClient",
    "StorenetConfigError",
    "StorenetError",
    "__version__",
]
