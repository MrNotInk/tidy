"""Command line interface for tidy."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table

from . import __version__
from .dupes import DupeReport, delete_duplicates, find_duplicates
from .journal import (
    JournalError,
    RunJournal,
    archive_journal,
    journal_file,
    load_journal,
    new_run_id,
    undo_run,
    write_journal,
)
from .report import build_report
from .safety import SafetyError, guard_target
from .sort import SortBy, SortPlan, apply_plan, build_sort_plan
from .util import display_path, format_mtime, human_size

app = typer.Typer(
    name="tidy",
    help="Organize and clean up messy directories.",
    no_args_is_help=True,
    add_completion=False,
)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    """Return ``"<count> <noun>"`` with a simple plural suffix."""
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural if plural is not None else singular + 's'}"


def _resolve(path: Path) -> Path:
    """Return an absolute, user-expanded version of *path*."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _require_directory(console: Console, path: Path) -> Path:
    """Exit with a clear error unless *path* is an existing directory."""
    if not path.exists():
        console.print(f"[red]Error:[/red] no such directory: {path}")
        raise typer.Exit(code=1)
    if not path.is_dir():
        console.print(f"[red]Error:[/red] not a directory: {path}")
        raise typer.Exit(code=1)
    return path


def _guard(console: Console, *paths: Path, force: bool) -> None:
    """Apply :func:`guard_target` to each path, exiting politely on refusal."""
    for path in paths:
        try:
            guard_target(path, force=force)
        except SafetyError as exc:
            console.print(f"[red]Refusing to run:[/red] {exc}")
            raise typer.Exit(code=1) from None


def _print_skips(
    console: Console,
    *,
    symlinks: int = 0,
    hidden: int = 0,
    ignored: int = 0,
    special: int = 0,
    small: int = 0,
    small_threshold: int = 1,
) -> None:
    """Print one dim summary line describing everything that was skipped."""
    parts: list[str] = []
    if symlinks:
        parts.append(_plural(symlinks, "symlink"))
    if hidden:
        parts.append(_plural(hidden, "hidden file"))
    if ignored:
        parts.append(_plural(ignored, "ignored directory", "ignored directories"))
    if special:
        parts.append(_plural(special, "special file"))
    if small:
        parts.append(
            f"{_plural(small, 'file')} under {human_size(small_threshold)}"
        )
    if parts:
        console.print(f"[dim]Skipped: {', '.join(parts)}[/dim]")


def _print_errors(console: Console, errors: list[str]) -> None:
    """Print every per-file error in red."""
    if not errors:
        return
    console.print(f"[red]Errors ({len(errors)}):[/red]")
    for error in errors:
        console.print(f"  [red]-[/red] {error}")


def _plan_table(
    title: str,
    rows: list[tuple[str, str, str, str]],
    *,
    from_base: Path,
    to_base: Path,
) -> Table:
    """Build the shared from -> to table used for plans and undo previews."""
    table = Table(title=title, box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("From", style="red", overflow="fold")
    table.add_column("To", style="green", overflow="fold")
    table.add_column("Size", justify="right")
    table.add_column("Note", style="yellow")
    for index, (source, target, size, note) in enumerate(rows, start=1):
        table.add_row(
            str(index),
            display_path(Path(source), from_base),
            display_path(Path(target), to_base),
            size,
            note,
        )
    return table


def _print_plan(console: Console, plan: SortPlan) -> None:
    """Print a full dry-run plan as a colored from -> to table."""
    console.print(
        f"[bold]tidy sort[/bold] by [cyan]{plan.by.value}[/cyan] · "
        f"root [dim]{display_path(plan.root)}[/dim] · "
        f"dest [dim]{display_path(plan.dest)}[/dim]"
    )
    if not plan.moves:
        console.print(
            "[green]Nothing to move.[/green] Every file is already in its bucket."
        )
    else:
        rows = [
            (
                str(move.source),
                str(move.target),
                human_size(move.size),
                "renamed" if move.renamed else "",
            )
            for move in plan.moves
        ]
        table = _plan_table(
            f"Move plan — {_plural(len(plan.moves), 'file')}, "
            f"{human_size(plan.total_size)}",
            rows,
            from_base=plan.root,
            to_base=plan.dest,
        )
        console.print(table)
    already = sum(1 for item in plan.skipped if item.reason == "already sorted")
    if already:
        console.print(f"[dim]{_plural(already, 'file')} already sorted.[/dim]")
    _print_skips(
        console,
        symlinks=plan.skipped_symlinks,
        hidden=plan.skipped_hidden,
        ignored=plan.skipped_ignored,
        special=plan.skipped_special,
    )
    _print_errors(console, plan.errors)


def _undo(console: Console, *, dry_run: bool) -> None:
    """Replay the most recent sort journal in reverse."""
    try:
        journal = load_journal()
    except JournalError as exc:
        console.print(f"[red]Cannot undo:[/red] {exc}")
        raise typer.Exit(code=1) from None

    if dry_run:
        rows = [
            (
                str(move.target),
                str(move.source),
                human_size(move.size),
                "renamed" if move.renamed else "",
            )
            for move in reversed(journal.moves)
        ]
        table = _plan_table(
            f"Undo plan — {_plural(len(journal.moves), 'move')} in reverse",
            rows,
            from_base=journal.dest,
            to_base=journal.root,
        )
        console.print(table)
        console.print(
            f"[dim]Dry run: nothing was moved. "
            f"Journal: {display_path(journal_file())}[/dim]"
        )
        return

    result = undo_run(journal)
    if result.restored:
        console.print(
            f"[green]Restored {_plural(len(result.restored), 'file')}[/green] "
            "to their original locations."
        )
    else:
        console.print("[green]Nothing to undo.[/green]")
    for directory in result.removed_dirs:
        console.print(
            f"[dim]Removed empty directory {display_path(directory, journal.root)}[/dim]"
        )
    if result.failures:
        _print_errors(console, result.failures)
        raise typer.Exit(code=1)
    archived = archive_journal()
    if archived is not None:
        console.print(f"[dim]Journal archived to {display_path(archived)}[/dim]")


@app.command()
def sort(
    path: Annotated[Path, typer.Argument(help="Directory to organize.")],
    by: Annotated[
        SortBy, typer.Option("--by", help="Bucketing strategy: ext, date, or type.")
    ] = SortBy.ext,
    dest: Annotated[
        Path | None,
        typer.Option("--dest", help="Destination root (default: PATH itself)."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the plan without changing anything."),
    ] = False,
    undo: Annotated[
        bool,
        typer.Option("--undo", help="Reverse the previous sort using its journal."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow operating on / or the home directory."),
    ] = False,
) -> None:
    """Organize files in PATH into buckets."""
    console = Console()
    if undo:
        _undo(console, dry_run=dry_run)
        return

    root = _resolve(path)
    _require_directory(console, root)
    dest_root = _resolve(dest) if dest is not None else root
    _guard(console, root, dest_root, force=force)

    plan = build_sort_plan(root, dest_root, by)
    if dry_run:
        _print_plan(console, plan)
        if plan.errors:
            raise typer.Exit(code=1)
        return

    if plan.moves:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Moving files", total=len(plan.moves))
            result = apply_plan(
                plan, on_progress=lambda _move: progress.advance(task)
            )
    else:
        result = apply_plan(plan)

    failures = [*result.failures, *plan.errors]
    journal = RunJournal(
        run_id=new_run_id(),
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        command="sort",
        root=plan.root,
        dest=plan.dest,
        by=plan.by.value,
        moves=result.moved,
        skipped=[
            {"path": str(item.path), "reason": item.reason} for item in plan.skipped
        ],
        failures=failures,
        created_dirs=result.created_dirs,
    )
    journal_path = write_journal(journal)

    if result.moved:
        console.print(
            f"[green]Moved {_plural(len(result.moved), 'file')}[/green] "
            f"({human_size(sum(move.size for move in result.moved))})."
        )
    else:
        console.print("[green]Nothing to move.[/green]")
    if result.created_dirs:
        console.print(
            f"[dim]Created {_plural(len(result.created_dirs), 'directory', 'directories')}.[/dim]"
        )
    already = sum(1 for item in plan.skipped if item.reason == "already sorted")
    if already:
        console.print(f"[dim]{_plural(already, 'file')} already sorted.[/dim]")
    _print_skips(
        console,
        symlinks=plan.skipped_symlinks,
        hidden=plan.skipped_hidden,
        ignored=plan.skipped_ignored,
        special=plan.skipped_special,
    )
    console.print(f"[dim]Journal: {display_path(journal_path)}[/dim]")
    _print_errors(console, failures)
    if failures:
        raise typer.Exit(code=1)


def _confirm(console: Console, prompt: str) -> bool:
    """Ask for an explicit ``yes``; anything else (or EOF) means no."""
    try:
        answer = typer.prompt(prompt, default="", show_default=False)
    except (typer.Abort, EOFError):
        console.print()
        return False
    return answer.strip().lower() == "yes"


def _print_dupe_report(console: Console, report: DupeReport, root: Path) -> None:
    """Print duplicate groups with per-group and total wasted space."""
    console.print(f"[bold]tidy dupes[/bold] · root [dim]{display_path(root)}[/dim]")
    if not report.groups:
        console.print("[green]No duplicate files found.[/green]")
        console.print(f"[dim]Hashed {_plural(report.hashed, 'candidate file')}.[/dim]")
        return

    table = Table(
        title=(
            f"Duplicate groups — {_plural(report.duplicate_files, 'redundant file')}, "
            f"{human_size(report.total_wasted)} wasted"
        ),
        box=box.SIMPLE_HEAVY,
        header_style="bold",
    )
    table.add_column("Group", justify="right", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Wasted", justify="right", style="yellow")
    table.add_column("Paths", overflow="fold")
    for index, group in enumerate(report.groups, start=1):
        keeper = group.keeper
        lines = []
        for item in group.files:
            marker = "keep" if item.path == keeper.path else "del "
            lines.append(f"[green]{marker}[/green] {display_path(item.path, root)}")
        table.add_row(
            str(index),
            human_size(group.size),
            str(group.count),
            human_size(group.wasted),
            "\n".join(lines),
        )
    console.print(table)
    console.print(
        f"[bold]Total:[/bold] {_plural(len(report.groups), 'group')}, "
        f"{_plural(report.duplicate_files, 'redundant file')}, "
        f"[yellow]{human_size(report.total_wasted)}[/yellow] wasted"
    )


def _print_delete_plan(console: Console, report: DupeReport, root: Path) -> None:
    """Print the keep/delete plan before asking for confirmation."""
    table = Table(
        title="Deletion plan — the oldest file in each group is kept",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
    )
    table.add_column("Group", justify="right", style="dim")
    table.add_column("Action")
    table.add_column("Path", overflow="fold")
    table.add_column("Size", justify="right")
    table.add_column("Modified")
    for index, group in enumerate(report.groups, start=1):
        keeper = group.keeper
        for item in group.files:
            is_keeper = item.path == keeper.path
            table.add_row(
                str(index),
                "[green]keep[/green]" if is_keeper else "[red]delete[/red]",
                display_path(item.path, root),
                human_size(item.size),
                format_mtime(item.mtime),
            )
    console.print(table)


@app.command()
def dupes(
    path: Annotated[Path, typer.Argument(help="Directory to scan for duplicates.")],
    min_size: Annotated[
        int,
        typer.Option("--min-size", min=0, help="Ignore files smaller than this many bytes."),
    ] = 1,
    delete: Annotated[
        bool,
        typer.Option("--delete", help="Delete duplicates after an explicit confirmation."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the delete plan without deleting anything."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow operating on / or the home directory."),
    ] = False,
) -> None:
    """Find identical files by content hash."""
    console = Console()
    root = _resolve(path)
    _require_directory(console, root)
    _guard(console, root, force=force)

    counter = {"count": 0}

    def _on_hash(_info: object) -> None:
        counter["count"] += 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning and hashing", total=None)
        report = find_duplicates(root, min_size=min_size, on_hash=_on_hash)
        progress.update(
            task, description=f"Hashed {counter['count']} candidate files"
        )

    _print_dupe_report(console, report, root)
    _print_skips(
        console,
        symlinks=report.skipped_symlinks,
        hidden=report.skipped_hidden,
        ignored=report.skipped_ignored,
        special=report.skipped_special,
        small=report.skipped_small,
        small_threshold=min_size,
    )
    _print_errors(console, report.errors)

    if not delete or not report.groups:
        if report.errors:
            raise typer.Exit(code=1)
        return

    _print_delete_plan(console, report, root)
    if dry_run:
        console.print("[dim]Dry run: nothing was deleted.[/dim]")
        if report.errors:
            raise typer.Exit(code=1)
        return

    total = sum(len(group.victims) for group in report.groups)
    if not _confirm(
        console,
        f"Delete {_plural(total, 'duplicate file')} "
        f"({human_size(report.total_wasted)})? Type 'yes' to continue",
    ):
        console.print("[yellow]Aborted.[/yellow] Nothing was deleted.")
        raise typer.Exit(code=0)

    result = delete_duplicates(report)
    console.print(
        f"[green]Deleted {_plural(len(result.deleted), 'duplicate file')}[/green], "
        f"freed {human_size(result.freed)}."
    )
    _print_errors(console, result.failures)
    if result.failures or report.errors:
        raise typer.Exit(code=1)


@app.command()
def report(
    path: Annotated[Path, typer.Argument(help="Directory to summarize.")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow reporting on / or the home directory."),
    ] = False,
) -> None:
    """Show directory statistics: buckets, largest files, and oldest files."""
    console = Console()
    root = _resolve(path)
    _require_directory(console, root)
    _guard(console, root, force=force)

    stats = build_report(root)
    console.print(f"[bold]tidy report[/bold] · [dim]{display_path(root)}[/dim]")
    console.print(
        f"{_plural(stats.total_files, 'file')}, {human_size(stats.total_size)}"
    )

    bucket_table = Table(title="Buckets", box=box.SIMPLE_HEAVY, header_style="bold")
    bucket_table.add_column("Bucket", style="bold")
    bucket_table.add_column("Files", justify="right")
    bucket_table.add_column("Size", justify="right")
    bucket_table.add_column("Share", justify="right")
    for bucket in stats.buckets:
        share = (bucket.size / stats.total_size * 100) if stats.total_size else 0.0
        bucket_table.add_row(
            bucket.name,
            str(bucket.count),
            human_size(bucket.size),
            f"{share:.1f}%",
        )
    console.print(bucket_table)

    largest_table = Table(
        title="Largest 10 files", box=box.SIMPLE_HEAVY, header_style="bold"
    )
    largest_table.add_column("#", justify="right", style="dim")
    largest_table.add_column("Size", justify="right")
    largest_table.add_column("Modified")
    largest_table.add_column("Path", overflow="fold")
    for index, info in enumerate(stats.largest, start=1):
        largest_table.add_row(
            str(index),
            human_size(info.size),
            format_mtime(info.mtime),
            display_path(info.path, root),
        )
    console.print(largest_table)

    oldest_table = Table(
        title="Oldest 5 files", box=box.SIMPLE_HEAVY, header_style="bold"
    )
    oldest_table.add_column("#", justify="right", style="dim")
    oldest_table.add_column("Modified")
    oldest_table.add_column("Size", justify="right")
    oldest_table.add_column("Path", overflow="fold")
    for index, info in enumerate(stats.oldest, start=1):
        oldest_table.add_row(
            str(index),
            format_mtime(info.mtime),
            human_size(info.size),
            display_path(info.path, root),
        )
    console.print(oldest_table)

    _print_skips(
        console,
        symlinks=stats.skipped_symlinks,
        hidden=stats.skipped_hidden,
        ignored=stats.skipped_ignored,
        special=stats.skipped_special,
    )
    _print_errors(console, stats.errors)
    if stats.errors:
        raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    """Print the version and exit when ``--version`` is passed."""
    if value:
        Console().print(f"tidy {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the version and exit.",
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    """Organize and clean up messy directories."""


def main() -> None:
    """Console-script entry point for ``tidy``."""
    app()
