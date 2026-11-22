"""Directory statistics for ``tidy report``."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .safety import FileInfo, scan_tree
from .sort import SortBy, bucket_for


@dataclass(frozen=True)
class BucketStats:
    """File count and total size for one extension bucket."""

    name: str
    count: int
    size: int


@dataclass
class DirectoryReport:
    """Aggregated statistics for one directory tree."""

    root: Path
    total_files: int = 0
    total_size: int = 0
    buckets: list[BucketStats] = field(default_factory=list)
    largest: list[FileInfo] = field(default_factory=list)
    oldest: list[FileInfo] = field(default_factory=list)
    skipped_symlinks: int = 0
    skipped_hidden: int = 0
    skipped_ignored: int = 0
    skipped_special: int = 0
    errors: list[str] = field(default_factory=list)


def build_report(root: Path) -> DirectoryReport:
    """Compute bucket stats, the 10 largest files, and the 5 oldest files."""
    root = Path(os.path.abspath(os.path.expanduser(str(root))))
    scan = scan_tree(root)

    counts: dict[str, tuple[int, int]] = {}
    for info in scan.files:
        bucket = bucket_for(info.path, SortBy.ext)
        count, size = counts.get(bucket, (0, 0))
        counts[bucket] = (count + 1, size + info.size)

    buckets = [
        BucketStats(name=name, count=count, size=size)
        for name, (count, size) in counts.items()
    ]
    buckets.sort(key=lambda item: (-item.size, item.name))

    largest = sorted(scan.files, key=lambda item: (-item.size, str(item.path)))[:10]
    oldest = sorted(scan.files, key=lambda item: (item.mtime, str(item.path)))[:5]

    return DirectoryReport(
        root=root,
        total_files=len(scan.files),
        total_size=sum(info.size for info in scan.files),
        buckets=buckets,
        largest=largest,
        oldest=oldest,
        skipped_symlinks=scan.skipped_symlinks,
        skipped_hidden=scan.skipped_hidden,
        skipped_ignored=scan.skipped_ignored,
        skipped_special=scan.skipped_special,
        errors=list(scan.errors),
    )
