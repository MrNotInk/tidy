"""Persistent JSON journal that powers ``tidy sort --undo``."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

JOURNAL_VERSION = 1
JOURNAL_NAME = "last-run.json"


class JournalError(RuntimeError):
    """Raised when the journal is missing, unreadable, or malformed."""


@dataclass
class MoveRecord:
    """One successfully completed move, as recorded in the journal."""

    source: Path
    target: Path
    size: int
    mtime: float
    renamed: bool = False
    bucket: str = ""

    def to_json(self) -> dict[str, Any]:
        """Serialize this record to a JSON-compatible dict."""
        return {
            "source": str(self.source),
            "target": str(self.target),
            "size": self.size,
            "mtime": self.mtime,
            "renamed": self.renamed,
            "bucket": self.bucket,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MoveRecord:
        """Build a record from a JSON dict."""
        return cls(
            source=Path(str(data["source"])),
            target=Path(str(data["target"])),
            size=int(data.get("size", 0)),
            mtime=float(data.get("mtime", 0.0)),
            renamed=bool(data.get("renamed", False)),
            bucket=str(data.get("bucket", "")),
        )


@dataclass
class RunJournal:
    """Everything needed to replay (undo) a completed ``tidy sort`` run."""

    run_id: str
    created_at: str
    command: str
    root: Path
    dest: Path
    by: str
    moves: list[MoveRecord] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)
    version: int = JOURNAL_VERSION

    def to_json(self) -> dict[str, Any]:
        """Serialize the whole journal to a JSON-compatible dict."""
        return {
            "version": self.version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "command": self.command,
            "root": str(self.root),
            "dest": str(self.dest),
            "by": self.by,
            "moves": [move.to_json() for move in self.moves],
            "skipped": [dict(item) for item in self.skipped],
            "failures": list(self.failures),
            "created_dirs": [str(path) for path in self.created_dirs],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunJournal:
        """Build a journal from a JSON dict."""
        if not isinstance(data, dict):
            raise TypeError("journal root must be a JSON object")
        version = int(data.get("version", 0))
        if version != JOURNAL_VERSION:
            raise ValueError(f"unsupported journal version: {version}")
        return cls(
            version=version,
            run_id=str(data["run_id"]),
            created_at=str(data.get("created_at", "")),
            command=str(data.get("command", "")),
            root=Path(str(data["root"])),
            dest=Path(str(data["dest"])),
            by=str(data.get("by", "")),
            moves=[MoveRecord.from_json(item) for item in data.get("moves", [])],
            skipped=[dict(item) for item in data.get("skipped", [])],
            failures=[str(item) for item in data.get("failures", [])],
            created_dirs=[Path(str(item)) for item in data.get("created_dirs", [])],
        )


def state_dir() -> Path:
    """Return tidy's state directory (``$XDG_STATE_HOME/tidy`` or ``~/.local/state/tidy``)."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base).expanduser() / "tidy"
    return Path.home() / ".local" / "state" / "tidy"


def journal_file() -> Path:
    """Return the path of the journal for the most recent run."""
    return state_dir() / JOURNAL_NAME


def new_run_id(now: datetime | None = None) -> str:
    """Return a unique, filesystem-safe run identifier."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def write_journal(journal: RunJournal) -> Path:
    """Atomically write *journal*, rotating any previous journal into ``history/``."""
    path = journal_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _rotate_previous(path)
    payload = json.dumps(journal.to_json(), indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, path)
    return path


def _rotate_previous(path: Path) -> None:
    """Copy the existing journal into ``history/`` before it is overwritten."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(data.get("run_id", "unknown"))
    except (OSError, ValueError):
        run_id = "unknown"
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, history / f"{stamp}-{run_id}.json")


def load_journal() -> RunJournal:
    """Load the most recent journal.

    Raises:
        JournalError: If the journal is missing, unreadable, or malformed.
    """
    path = journal_file()
    if not path.exists():
        raise JournalError(f"no journal found at {path}; run 'tidy sort' first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunJournal.from_json(data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise JournalError(f"cannot read journal {path}: {exc}") from exc


def archive_journal() -> Path | None:
    """Move the current journal into ``history/`` after a successful undo."""
    path = journal_file()
    if not path.exists():
        return None
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    target = history / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-undone.json"
    shutil.move(os.fspath(path), os.fspath(target))
    return target


@dataclass
class UndoResult:
    """Outcome of replaying a journal in reverse."""

    restored: list[MoveRecord] = field(default_factory=list)
    removed_dirs: list[Path] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def undo_run(journal: RunJournal, *, dry_run: bool = False) -> UndoResult:
    """Replay *journal* in reverse, restoring every file to its original path.

    Directories created by the original run are removed again once they are
    empty, so the original tree comes back exactly as it was.
    """
    result = UndoResult()
    for move in reversed(journal.moves):
        current = move.target
        original = move.source
        if not os.path.lexists(current):
            result.failures.append(f"missing: {current}")
            continue
        if os.path.lexists(original):
            result.failures.append(f"destination already exists: {original}")
            continue
        if dry_run:
            result.restored.append(move)
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(os.fspath(current), os.fspath(original))
        except OSError as exc:
            result.failures.append(f"{current}: {exc.strerror or exc}")
            continue
        result.restored.append(move)

    if dry_run or result.failures:
        return result

    for directory in sorted(
        journal.created_dirs, key=lambda item: len(item.parts), reverse=True
    ):
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
                result.removed_dirs.append(directory)
        except OSError:
            continue
    return result
