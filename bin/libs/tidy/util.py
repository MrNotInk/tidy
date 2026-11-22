"""Small formatting helpers shared across tidy modules."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(num_bytes: int) -> str:
    """Return *num_bytes* formatted with a binary unit suffix (e.g. ``1.4 MB``)."""
    size = float(max(num_bytes, 0))
    for unit in _UNITS:
        if size < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} {_UNITS[-1]}"  # pragma: no cover - defensive


def format_mtime(timestamp: float) -> str:
    """Format a unix timestamp as a local ``YYYY-MM-DD HH:MM`` string."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def display_path(path: Path, base: Path | None = None) -> str:
    """Return a short, human-friendly rendering of *path*.

    When *base* is given and *path* lives underneath it, the path is shown
    relative to *base*. Otherwise absolute paths are abbreviated with ``~``.
    """
    text = str(path)
    if base is not None:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    home = str(Path.home())
    if text == home or text.startswith(home + os.sep):
        text = "~" + text[len(home) :]
    return text
