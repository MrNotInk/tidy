"""Safety checks and recursive scanning used by every tidy command."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

#: Directories that are always skipped, in addition to hidden directories.
IGNORED_DIR_NAMES: frozenset[str] = frozenset({"node_modules", ".venv"})


class SafetyError(RuntimeError):
    """Raised when a target path is too dangerous to operate on."""


@dataclass(frozen=True)
class FileInfo:
    """A regular file discovered during a scan."""

    path: Path
    size: int
    mtime: float


@dataclass
class ScanResult:
    """Files found by :func:`scan_tree` plus counters for skipped entries."""

    files: list[FileInfo] = field(default_factory=list)
    skipped_symlinks: int = 0
    skipped_hidden: int = 0
    skipped_ignored: int = 0
    skipped_special: int = 0
    errors: list[str] = field(default_factory=list)


def is_hidden(name: str) -> bool:
    """Return ``True`` for dot-prefixed file or directory names."""
    return name.startswith(".")


def is_ignored_dir(name: str) -> bool:
    """Return ``True`` for directories tidy must never descend into."""
    return is_hidden(name) or name in IGNORED_DIR_NAMES


def guard_target(path: Path, *, force: bool = False) -> None:
    """Refuse filesystem roots and the user's home directory.

    Args:
        path: The directory tidy is about to operate on.
        force: When ``True``, skip the check entirely.

    Raises:
        SafetyError: If *path* is a filesystem root or the home directory and
            *force* is not set.
    """
    if force:
        return
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
    except OSError as exc:  # pragma: no cover - defensive
        raise SafetyError(f"cannot resolve {path}: {exc}") from exc
    if resolved.parent == resolved:
        raise SafetyError(
            f"{path} is a filesystem root; pass --force to operate on it anyway."
        )
    if resolved == home:
        raise SafetyError(
            f"{path} is your home directory; pass --force to operate on it anyway."
        )


def scan_tree(root: Path) -> ScanResult:
    """Walk *root* and collect regular files, skipping unsafe entries.

    Symlinks are never followed or returned, hidden entries and VCS or
    virtual-environment directories are skipped, and per-file errors are
    collected in the result instead of aborting the scan.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"no such directory: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    result = ScanResult()

    def _on_error(error: OSError) -> None:
        result.errors.append(f"{error.filename}: {error.strerror or error}")

    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_on_error
    ):
        base = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            full = base / name
            try:
                if full.is_symlink():
                    result.skipped_symlinks += 1
                    continue
            except OSError:  # pragma: no cover - defensive
                pass
            if is_ignored_dir(name):
                result.skipped_ignored += 1
                continue
            kept.append(name)
        dirnames[:] = sorted(kept)

        for name in sorted(filenames):
            if is_hidden(name):
                result.skipped_hidden += 1
                continue
            full = base / name
            try:
                info = full.lstat()
            except OSError as exc:
                result.errors.append(f"{full}: {exc.strerror or exc}")
                continue
            if stat.S_ISLNK(info.st_mode):
                result.skipped_symlinks += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                result.skipped_special += 1
                continue
            result.files.append(
                FileInfo(path=full, size=info.st_size, mtime=info.st_mtime)
            )
    return result
