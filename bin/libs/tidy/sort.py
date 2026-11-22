"""Sort files into buckets and apply or undo the resulting move plan."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from .journal import MoveRecord
from .safety import scan_tree


class SortBy(str, Enum):
    """Bucketing strategy for ``tidy sort``."""

    ext = "ext"
    date = "date"
    type = "type"


OTHER_BUCKET = "Other"

#: Extension -> bucket mapping used by ``--by ext``.
BUCKET_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "Images": (
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "webp",
        "svg",
        "tif",
        "tiff",
        "heic",
        "ico",
    ),
    "Documents": (
        "pdf",
        "doc",
        "docx",
        "txt",
        "md",
        "rtf",
        "odt",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "csv",
        "epub",
    ),
    "Video": ("mp4", "mkv", "avi", "mov", "wmv", "webm", "m4v", "mpg", "mpeg"),
    "Archives": ("zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz", "zst"),
}

EXTENSION_TO_BUCKET: dict[str, str] = {
    extension: bucket
    for bucket, extensions in BUCKET_EXTENSIONS.items()
    for extension in extensions
}

#: Name-based rules for ``--by type``. The first matching substring wins.
TYPE_RULES: tuple[tuple[str, str], ...] = (
    ("screenshot", "Screenshots"),
    ("screen-shot", "Screenshots"),
    ("invoice", "Finance"),
    ("receipt", "Finance"),
    ("statement", "Finance"),
    ("img_", "Photos"),
    ("dsc", "Photos"),
    ("resume", "Documents"),
    ("logo", "Design"),
    ("icon", "Design"),
    ("wallpaper", "Wallpapers"),
    ("backup", "Backups"),
    (".bak", "Backups"),
    (".log", "Logs"),
    ("notes", "Notes"),
    ("todo", "Notes"),
)


def bucket_for(path: Path, by: SortBy, mtime: float | None = None) -> str:
    """Return the bucket for *path* under the requested strategy.

    Date buckets are nested (``YYYY/MM``); every other strategy returns a
    single directory name.
    """
    if by is SortBy.ext:
        extension = path.suffix.lower().lstrip(".")
        return EXTENSION_TO_BUCKET.get(extension, OTHER_BUCKET)
    if by is SortBy.date:
        timestamp = path.stat().st_mtime if mtime is None else mtime
        return datetime.fromtimestamp(timestamp).strftime("%Y/%m")
    if by is SortBy.type:
        name = path.name.lower()
        for needle, bucket in TYPE_RULES:
            if needle in name:
                return bucket
        return OTHER_BUCKET
    raise ValueError(f"unsupported sort strategy: {by!r}")


@dataclass(frozen=True)
class PlannedMove:
    """A single file move planned by :func:`build_sort_plan`."""

    source: Path
    target: Path
    size: int
    mtime: float
    bucket: str
    renamed: bool = False


@dataclass(frozen=True)
class SkippedItem:
    """A file that tidy intentionally left alone."""

    path: Path
    reason: str


@dataclass
class SortPlan:
    """The complete, not-yet-applied plan for one ``tidy sort`` run."""

    root: Path
    dest: Path
    by: SortBy
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    skipped_symlinks: int = 0
    skipped_hidden: int = 0
    skipped_ignored: int = 0
    skipped_special: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        """Total size in bytes of all planned moves."""
        return sum(move.size for move in self.moves)


def build_sort_plan(root: Path, dest: Path, by: SortBy) -> SortPlan:
    """Scan *root* and compute every move needed to sort it into *dest*."""
    root = Path(os.path.abspath(os.path.expanduser(str(root))))
    dest = Path(os.path.abspath(os.path.expanduser(str(dest))))
    scan = scan_tree(root)
    plan = SortPlan(
        root=root,
        dest=dest,
        by=by,
        skipped_symlinks=scan.skipped_symlinks,
        skipped_hidden=scan.skipped_hidden,
        skipped_ignored=scan.skipped_ignored,
        skipped_special=scan.skipped_special,
        errors=list(scan.errors),
    )

    reserved: set[Path] = set()
    for info in sorted(scan.files, key=lambda item: str(item.path)):
        bucket = bucket_for(info.path, by, info.mtime)
        target_dir = dest / bucket
        if _same_directory(info.path.parent, target_dir):
            plan.skipped.append(SkippedItem(info.path, "already sorted"))
            continue
        target, renamed = _unique_target(target_dir, info.path.name, reserved)
        reserved.add(target)
        plan.moves.append(
            PlannedMove(
                source=info.path,
                target=target,
                size=info.size,
                mtime=info.mtime,
                bucket=bucket,
                renamed=renamed,
            )
        )
    return plan


def _same_directory(left: Path, right: Path) -> bool:
    """Compare two directory paths without requiring them to exist."""
    try:
        return left.resolve() == right.resolve()
    except OSError:  # pragma: no cover - defensive
        return os.path.abspath(left) == os.path.abspath(right)


def _occupied(path: Path, reserved: set[Path]) -> bool:
    """Return ``True`` if *path* exists on disk or is already claimed in this run."""
    return os.path.lexists(path) or path in reserved


def _unique_target(
    directory: Path, filename: str, reserved: set[Path]
) -> tuple[Path, bool]:
    """Return a free target path, appending `` (2)``, `` (3)`` ... on collision."""
    candidate = directory / filename
    if not _occupied(candidate, reserved):
        return candidate, False
    suffix = Path(filename).suffix
    stem = filename[: -len(suffix)] if suffix else filename
    counter = 2
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not _occupied(candidate, reserved):
            return candidate, True
        counter += 1


@dataclass
class ApplyResult:
    """Outcome of applying a :class:`SortPlan` to the filesystem."""

    moved: list[MoveRecord] = field(default_factory=list)
    created_dirs: list[Path] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def apply_plan(
    plan: SortPlan,
    on_progress: Callable[[PlannedMove], None] | None = None,
) -> ApplyResult:
    """Move every file in *plan*, recording successes and per-file failures."""
    result = ApplyResult()
    created: set[Path] = set()
    for move in plan.moves:
        try:
            # Plans may go stale between preview and execution. Refuse an
            # occupied target rather than letting shutil.move overwrite it.
            if os.path.lexists(move.target):
                raise FileExistsError(
                    f"target appeared after planning; refusing to overwrite: {move.target}"
                )
            _ensure_parent(move.target, created)
            shutil.move(os.fspath(move.source), os.fspath(move.target))
        except OSError as exc:
            result.failures.append(f"{move.source}: {exc.strerror or exc}")
            continue
        result.moved.append(
            MoveRecord(
                source=move.source,
                target=move.target,
                size=move.size,
                mtime=move.mtime,
                renamed=move.renamed,
                bucket=move.bucket,
            )
        )
        if on_progress is not None:
            on_progress(move)
    result.created_dirs = sorted(created, key=lambda item: (len(item.parts), str(item)))
    return result


def _ensure_parent(target: Path, created: set[Path]) -> None:
    """Create *target*'s parent directories, recording the ones that are new."""
    missing: list[Path] = []
    current = target.parent
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    created.update(missing)
