"""Find and optionally delete duplicate files by content hash."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .safety import FileInfo, scan_tree

CHUNK_SIZE = 1024 * 1024


def hash_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the hex sha256 digest of *path*, reading it in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DuplicateGroup:
    """Two or more files with identical contents."""

    size: int
    files: tuple[FileInfo, ...]

    @property
    def count(self) -> int:
        """Number of files in the group."""
        return len(self.files)

    @property
    def wasted(self) -> int:
        """Bytes that would be freed by keeping only one copy."""
        return self.size * (len(self.files) - 1)

    @property
    def keeper(self) -> FileInfo:
        """The file to keep: the oldest one, with path as a stable tie-breaker."""
        return min(self.files, key=lambda item: (item.mtime, str(item.path)))

    @property
    def victims(self) -> tuple[FileInfo, ...]:
        """Every file in the group except :attr:`keeper`."""
        keeper = self.keeper
        return tuple(item for item in self.files if item.path != keeper.path)


@dataclass
class DupeReport:
    """Aggregated duplicate-detection results."""

    groups: list[DuplicateGroup] = field(default_factory=list)
    hashed: int = 0
    skipped_small: int = 0
    skipped_symlinks: int = 0
    skipped_hidden: int = 0
    skipped_ignored: int = 0
    skipped_special: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_wasted(self) -> int:
        """Total bytes occupied by redundant copies."""
        return sum(group.wasted for group in self.groups)

    @property
    def duplicate_files(self) -> int:
        """Number of redundant copies (excluding the kept file in each group)."""
        return sum(group.count - 1 for group in self.groups)


@dataclass
class DeleteResult:
    """Outcome of deleting duplicate files."""

    deleted: list[FileInfo] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def freed(self) -> int:
        """Bytes freed by the deletions."""
        return sum(item.size for item in self.deleted)


def find_duplicates(
    root: Path,
    *,
    min_size: int = 1,
    chunk_size: int = CHUNK_SIZE,
    on_hash: Callable[[FileInfo], None] | None = None,
) -> DupeReport:
    """Scan *root* for files with identical contents.

    Files smaller than *min_size* are ignored. Candidates are first grouped by
    size, then hashed with sha256 in chunks, so only plausible duplicates are
    ever read.
    """
    root = Path(os.path.abspath(os.path.expanduser(str(root))))
    scan = scan_tree(root)
    report = DupeReport(
        skipped_symlinks=scan.skipped_symlinks,
        skipped_hidden=scan.skipped_hidden,
        skipped_ignored=scan.skipped_ignored,
        skipped_special=scan.skipped_special,
        errors=list(scan.errors),
    )

    by_size: dict[int, list[FileInfo]] = {}
    for info in scan.files:
        if info.size < min_size:
            report.skipped_small += 1
            continue
        by_size.setdefault(info.size, []).append(info)

    candidates = [files for files in by_size.values() if len(files) > 1]
    candidates.sort(key=lambda files: str(files[0].path))

    for files in candidates:
        by_hash: dict[str, list[FileInfo]] = {}
        for info in files:
            try:
                digest = hash_file(info.path, chunk_size)
            except OSError as exc:
                report.errors.append(f"{info.path}: {exc.strerror or exc}")
                continue
            report.hashed += 1
            by_hash.setdefault(digest, []).append(info)
            if on_hash is not None:
                on_hash(info)

        for files_with_hash in by_hash.values():
            if len(files_with_hash) < 2:
                continue
            ordered = tuple(
                sorted(files_with_hash, key=lambda item: (item.mtime, str(item.path)))
            )
            report.groups.append(
                DuplicateGroup(size=ordered[0].size, files=ordered)
            )

    report.groups.sort(key=lambda group: (-group.wasted, str(group.files[0].path)))
    return report


def delete_duplicates(
    report: DupeReport,
    *,
    on_delete: Callable[[FileInfo], None] | None = None,
) -> DeleteResult:
    """Delete every duplicate in *report* except the oldest file of each group."""
    result = DeleteResult()
    for group in report.groups:
        for victim in group.victims:
            try:
                victim.path.unlink()
            except OSError as exc:
                result.failures.append(f"{victim.path}: {exc.strerror or exc}")
                continue
            result.deleted.append(victim)
            if on_delete is not None:
                on_delete(victim)
    return result
