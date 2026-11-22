# Building a tidy project

A Weld-ready tidy package is a directory with `config.json` and a `bin/` folder.
The executable entry point is `bin/main.py`; tidy's implementation modules live
under `bin/libs/tidy/`. The same source tree remains usable as a uv-managed
Python project for development and tests.

- Shortest possible layout: [Minimum layout](#minimum-layout)
- Every project part: [Full layout](#full-layout)
- Package metadata: [`config.json` reference](#configjson-reference)
- Creating a Weld archive: [Building the archive](#building-the-archive)
- CLI usage: [Commands](#commands)
- Pitfalls and checks: [Common mistakes](#common-mistakes)

---

## Minimum layout

```
tidy/
├── config.json                 # Weld package metadata
└── bin/
    ├── main.py                 # Weld command entry point: tidy
    └── libs/
        └── tidy/               # tidy implementation package
            ├── __init__.py
            ├── cli.py
            ├── sort.py
            ├── dupes.py
            ├── journal.py
            ├── report.py
            ├── safety.py
            └── util.py
```

`config.json` declares the package, dependencies, entry script, and supported
Python version:

```json
{
  "name": "tidy",
  "version": "0.1.0",
  "desc": "Organize and inspect messy directories from the terminal.",
  "deps": ["typer", "rich"],
  "entry": "main.py",
  "python_requires": ">=3.12"
}
```

`bin/main.py` imports the implementation from `libs.tidy.cli` and calls its
`main()` function. Weld installs `bin/libs/` beside the generated command, so
the `libs.tidy` import resolves there. The package's internal relative imports
remain unchanged.

---

## Full layout

```
tidy/
├── config.json                 # required by Weld
├── Readme                       # optional Weld package description
├── Changelog                    # optional package history
├── bin/
│   ├── main.py                 # tidy command entry point
│   └── libs/
│       ├── __init__.py
│       └── tidy/
│           ├── __init__.py
│           ├── py.typed
│           ├── cli.py          # Typer commands and Rich output
│           ├── sort.py         # bucket rules, move plan and execution
│           ├── dupes.py        # chunked SHA-256 duplicate detection
│           ├── journal.py      # JSON journal and undo replay
│           ├── report.py       # directory summary calculations
│           ├── safety.py       # guarded paths and safe recursive scanning
│           └── util.py         # formatting helpers
├── pyproject.toml              # uv development packaging and dependencies
├── uv.lock                     # pinned development environment
├── .python-version             # uv selects Python 3.12
├── README.md
├── tests/                      # pytest suite
└── demo/messy/                 # sample files for smoke testing
```

`bin/libs/tidy/` is the single source of implementation code. The `pyproject.toml`
configures Hatchling to package that directory as the importable `tidy` package,
so development imports and the Weld entry point use the same code.

---

## `config.json` reference

Required keys are `name` and `version`. The tidy package uses the following
optional Weld keys as well:

```json
{
  "name": "tidy",
  "version": "0.1.0",
  "desc": "Organize and inspect messy directories from the terminal.",
  "deps": ["typer", "rich"],
  "entry": "main.py",
  "python_requires": ">=3.12"
}
```

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Weld package and command name: `tidy`. |
| `version` | string | Current package version. |
| `desc` | string | Short description displayed by Weld. |
| `deps` | list of strings | Runtime pip dependencies: Typer and Rich. |
| `entry` | string | `main.py` under `bin/`, which Weld turns into the `tidy` command. |
| `python_requires` | string | Requires Python 3.12 or newer. |

Weld's other metadata keys can be added if needed. `pyproject.toml` is retained
for the separate uv workflow; it is not a replacement for the Weld `config.json`.

---

## Building the archive

### With Weld

From this project directory, build the package with Weld:

```bash
weld --build .
```

Weld reads the root `config.json`, selects `bin/main.py`, includes
`bin/libs/tidy/`, and writes `tidy.wld` next to the project directory. The tidy
command and `libs.tidy` implementation are packaged together. `--build` needs
`ruff` on `PATH`; from a Weld source checkout you can run it like this:

```bash
PATH="/path/to/Weld/.venv/bin:$PATH" \
  /path/to/Weld/.venv/bin/python /path/to/Weld/weld.py --build .
```

Or, if Weld is installed as a command, use `weld --build .`. Install and inspect
the resulting archive with:

```bash
weld --install ./tidy.wld
weld --list
weld --info tidy
```

The repo was built and smoke-tested with Weld 0.6.4. Weld's build archive also
carries non-excluded repository files such as `tests/` and `demo/`; those are
not installed as commands or `bin/` data. Use the manual minimal archive below
if you want to exclude all development fixtures.

### Without Weld

A `.wld` is a zip archive with `config.json` at its root. To create one by hand,
zip the package contents (not the containing `tidy/` folder):

```bash
cd /path/to/tidy
zip -qr ../tidy.wld config.json Readme Changelog bin
```

This minimal manual archive excludes the uv development files, tests, and demo
fixtures. If optional `Readme`, `Changelog`, or `plugins/` files are absent,
omit them from the zip command.

### uv development build

The uv project is for development, editable installation, and tests:

```bash
uv sync
uv run tidy --help
uv run pytest
uv build
```

It installs the same implementation from `bin/libs/tidy/` and registers the
usual `tidy` console script.

---

## Commands

```text
tidy sort PATH [--by ext|date|type] [--dest DIR] [--dry-run] [--undo] [--force]
tidy dupes PATH [--min-size BYTES] [--delete] [--dry-run] [--force]
tidy report PATH [--force]
```

| Command / option | Behaviour |
|---|---|
| `sort PATH` | Sort into folders under `PATH`, by extension by default. |
| `sort --by date` | Use modification-time `YYYY/MM/` folders. |
| `sort --by type` | Use filename hints such as `invoice`, `screenshot`, or `logo`. |
| `sort --dest DIR` | Set another destination root; no external move occurs without this option. |
| `sort --dry-run` | Print all planned moves without touching files or writing a journal. |
| `sort --undo` | Reverse the latest real sort from its journal. |
| `dupes --min-size BYTES` | Ignore files smaller than the threshold (default: 1 byte). |
| `dupes --delete` | Show the plan and delete only after the exact confirmation `yes`. |
| `dupes --delete --dry-run` | Show the delete plan without prompting or deleting. |
| `report PATH` | Show bucket counts/sizes, largest 10 files, and oldest 5 files. |
| `--force` | Override the guard against filesystem roots and the home directory. |

Extension buckets include `Images/`, `Documents/`, `Video/`, `Archives/`, and
`Other/`. Name collisions receive ` (2)`, ` (3)`, etc. before the extension;
existing files are not intentionally overwritten. A real sort records successful
moves in `$XDG_STATE_HOME/tidy/last-run.json` (normally
`~/.local/state/tidy/last-run.json`) for undo.

---

## Common mistakes

| Symptom | Cause / fix |
|---|---|
| `config.json missing from archive` | Put it at the archive root; zip package contents, not the `tidy/` parent directory. |
| `bin/ missing from archive` | Keep `main.py` and `libs/` beneath the root `bin/` directory. |
| `bin/main.py not found` | Ensure the configured `entry` (`main.py` by default) exists under `bin/`. |
| `No module named 'libs'` | Preserve `bin/libs/` when building; it must sit beside `bin/main.py`. |
| `No module named 'typer'` or `'rich'` | Keep those runtime dependencies in `config.json` so Weld installs them. |
| uv cannot import `tidy` from the source tree | Run `uv sync`; for direct pytest, `pythonpath` is configured to `bin/libs`. |
| Sorting a protected path is refused | Use a non-root, non-home target, or pass `--force` deliberately. |
| A file cannot be moved or read | tidy reports the per-file error, continues, then exits with status 1. |

Tidy never follows symlinks; it skips hidden files/directories and
`.git/`, `node_modules/`, and `.venv/`. Check a plan before making changes:

```bash
uv run tidy sort demo/messy --dry-run
uv run tidy dupes demo/messy
uv run tidy report demo/messy
```
