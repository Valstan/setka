#!/usr/bin/env python3
"""Apply pending database migrations and record them in ``applied_migrations``.

Usage::

    python scripts/migrate.py status                # list applied / pending
    python scripts/migrate.py up                    # apply all pending
    python scripts/migrate.py up --dry-run          # show plan without DDL
    python scripts/migrate.py mark-applied --all-pending        # dry run
    python scripts/migrate.py mark-applied --all-pending --yes  # write

``mark-applied`` records migrations as applied **without executing them** — the
repair for bookkeeping that drifted because someone applied a file by hand.
It is dry unless ``--yes`` is passed, and it records a conclusion rather than
reaching one: verify against the real schema first.

The runner shells out to ``sudo -u postgres psql -d setka -v ON_ERROR_STOP=1``
for every read and write, so it must run on the prod VPS (or on any host
where that sudo target works). Typical flow after ``git pull``::

    ssh sarafan 'cd /home/valstan/SETKA && python3 scripts/migrate.py up'

Bootstrap: migration ``010_applied_migrations.sql`` creates the bookkeeping
table itself. The runner detects an absent table on the first ``up`` and
applies ``010`` before anything else. Once 010 is in place subsequent
sessions just read the table.

Each migration is applied inside a single transaction together with the
``INSERT INTO applied_migrations`` record. ``ON_ERROR_STOP=1`` aborts the
transaction on the first SQL error, so a failed migration leaves no trace
in the bookkeeping table.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"
DATABASE = "setka"
BOOTSTRAP_NAME = "010_applied_migrations.sql"

PSQL_BASE_CMD: tuple[str, ...] = (
    "sudo",
    "-u",
    "postgres",
    "psql",
    "-d",
    DATABASE,
    "-v",
    "ON_ERROR_STOP=1",
)


@dataclass(frozen=True)
class Migration:
    name: str
    path: Path
    sha256: str


PsqlRunner = Callable[[Sequence[str], str | None], str]


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """Return migrations sorted by filename.

    All ``*.sql`` files are picked up. The expected naming is ``NNN_<slug>.sql``
    (three-digit prefix), which sorts lexicographically into application order
    for the foreseeable future (003..999). Legacy ``add_sentiment_fields.sql``
    has no number and ends up at the start of the sorted list, but the 010
    backfill marks it as already applied so the runner skips it.
    """
    directory = directory if directory is not None else MIGRATIONS_DIR
    files = sorted(p for p in directory.glob("*.sql"))
    return [Migration(name=p.name, path=p, sha256=compute_sha256(p)) for p in files]


def _default_psql_runner(args: Sequence[str], stdin: str | None = None) -> str:
    cmd = list(PSQL_BASE_CMD) + list(args)
    result = subprocess.run(
        cmd,
        input=stdin,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def fetch_applied(runner: PsqlRunner | None = None) -> set[str]:
    """Read filenames recorded as applied. Returns empty set if the table
    does not exist yet (i.e. 010 has not been applied)."""
    runner = runner if runner is not None else _default_psql_runner
    try:
        out = runner(
            ["-tA", "-c", "SELECT filename FROM applied_migrations ORDER BY filename"],
            None,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").lower()
        if "applied_migrations" in stderr and "does not exist" in stderr:
            return set()
        raise
    return {line.strip() for line in out.splitlines() if line.strip()}


_BOOTSTRAP_SQL_LITERAL_RE = re.compile(r"'")


def _quote_sql_literal(value: str) -> str:
    return "'" + _BOOTSTRAP_SQL_LITERAL_RE.sub("''", value) + "'"


def build_apply_script(migration: Migration) -> str:
    """Build the SQL script applied for a single migration: the migration body
    wrapped in ``BEGIN`` ... ``COMMIT`` together with the bookkeeping INSERT.

    The INSERT uses ``ON CONFLICT DO UPDATE`` so re-applying a migration
    (after editing it) refreshes the sha256 and applied_at — that's what we
    want: the table reflects the *current* content of every applied file.
    """
    body = migration.path.read_text(encoding="utf-8")
    name = _quote_sql_literal(migration.name)
    sha = _quote_sql_literal(migration.sha256)
    return (
        "BEGIN;\n"
        f"{body}\n"
        "INSERT INTO applied_migrations (filename, sha256) VALUES\n"
        f"    ({name}, {sha})\n"
        "    ON CONFLICT (filename) DO UPDATE\n"
        "        SET sha256 = EXCLUDED.sha256,\n"
        "            applied_at = CURRENT_TIMESTAMP;\n"
        "COMMIT;\n"
    )


def apply_migration(migration: Migration, runner: PsqlRunner | None = None) -> None:
    runner = runner if runner is not None else _default_psql_runner
    runner(["-q"], build_apply_script(migration))


def build_mark_script(migrations: Sequence[Migration]) -> str:
    """Bookkeeping-only script: record migrations as applied WITHOUT running them.

    This exists because the alternative is worse. Bookkeeping drifts whenever a
    migration is applied by hand (2026-08-09: 30 files were live in the schema
    while the table said ``pending``), and the only repair available then was
    hand-written ``INSERT`` statements pasted over ssh — an operation with no
    review, no tests and a truncated hash waiting to be copied from ``status``
    output. Giving the runner an explicit mode makes the repair reviewable.

    The obvious hazard is that this command *tells the table something that did
    not happen*. That is exactly why ``cmd_mark_applied`` refuses to write
    without ``--yes``, and why the caller is expected to have verified the
    schema first: the command records a conclusion, it does not reach one.
    """
    values = ",\n".join(
        f"    ({_quote_sql_literal(m.name)}, {_quote_sql_literal(m.sha256)})" for m in migrations
    )
    return (
        "BEGIN;\n"
        "INSERT INTO applied_migrations (filename, sha256) VALUES\n"
        f"{values}\n"
        "    ON CONFLICT (filename) DO UPDATE\n"
        "        SET sha256 = EXCLUDED.sha256,\n"
        "            applied_at = CURRENT_TIMESTAMP;\n"
        "COMMIT;\n"
    )


def _pending(migrations: Iterable[Migration], applied: set[str]) -> list[Migration]:
    """Return pending migrations in application order.

    Filenames sort lexicographically (003 < 004 < ... < 010 < add_sentiment),
    but the bootstrap migration must run *first* — every other migration's
    apply step writes to ``applied_migrations``, which doesn't exist yet on
    a fresh database. On the first ``up`` (applied set empty), we therefore
    pull the bootstrap to the front. On subsequent runs the bootstrap is
    already applied and the special case is a no-op.
    """
    pending = [m for m in migrations if m.name not in applied]
    bootstrap = [m for m in pending if m.name == BOOTSTRAP_NAME]
    rest = [m for m in pending if m.name != BOOTSTRAP_NAME]
    return bootstrap + rest


def cmd_status(runner: PsqlRunner | None = None) -> int:
    runner = runner if runner is not None else _default_psql_runner
    migrations = discover_migrations()
    applied = fetch_applied(runner)
    print(f"Migrations dir: {MIGRATIONS_DIR}")
    print(f"Found {len(migrations)} files, {len(applied)} recorded as applied.")
    print()
    for m in migrations:
        mark = "applied" if m.name in applied else "pending"
        print(f"  [{mark:>7}]  {m.name}  sha256={m.sha256[:12]}")
    pending = _pending(migrations, applied)
    print()
    print(f"Pending to apply: {len(pending)}")
    return 0


def cmd_up(dry_run: bool = False, runner: PsqlRunner | None = None) -> int:
    runner = runner if runner is not None else _default_psql_runner
    migrations = discover_migrations()
    if not any(m.name == BOOTSTRAP_NAME for m in migrations):
        print(
            f"ERROR: bootstrap migration {BOOTSTRAP_NAME} not found in " f"{MIGRATIONS_DIR}.",
            file=sys.stderr,
        )
        return 2

    applied = fetch_applied(runner)
    pending = _pending(migrations, applied)
    if not pending:
        print("Nothing to apply — schema is up to date.")
        return 0

    print(f"Will apply {len(pending)} migration(s):")
    for m in pending:
        print(f"  - {m.name}")
    if dry_run:
        print()
        print("[dry-run] no changes made.")
        return 0

    for m in pending:
        print()
        print(f"Applying {m.name} ...")
        try:
            apply_migration(m, runner)
        except subprocess.CalledProcessError as exc:
            print(
                f"FAILED on {m.name}\nstderr:\n{exc.stderr}",
                file=sys.stderr,
            )
            return 3
        print(f"  ok  {m.name}")

    print()
    print(f"Applied {len(pending)} migration(s).")
    return 0


def cmd_mark_applied(
    names: Sequence[str],
    *,
    all_pending: bool = False,
    yes: bool = False,
    runner: PsqlRunner | None = None,
) -> int:
    """Record migrations as applied without executing them.

    Dry by default: without ``--yes`` it prints the plan and writes nothing.
    The confirmation lives in the command rather than in the operator's memory
    because the failure mode is silent — a wrongly marked migration is a file
    that will never run, and nothing downstream will ever complain about it.
    """
    runner = runner if runner is not None else _default_psql_runner
    migrations = discover_migrations()
    by_name = {m.name: m for m in migrations}

    if all_pending:
        if names:
            print("ERROR: pass either --all-pending or explicit names, not both.", file=sys.stderr)
            return 2
        selected = _pending(migrations, fetch_applied(runner))
    else:
        if not names:
            print("ERROR: nothing selected — pass filenames or --all-pending.", file=sys.stderr)
            return 2
        unknown = [n for n in names if n not in by_name]
        if unknown:
            print(f"ERROR: not found in {MIGRATIONS_DIR}: {', '.join(unknown)}", file=sys.stderr)
            return 2
        selected = [by_name[n] for n in names]

    if not selected:
        print("Nothing to mark — bookkeeping already matches.")
        return 0

    print(f"Will record {len(selected)} migration(s) as applied WITHOUT running them:")
    for m in selected:
        print(f"  - {m.name}  sha256={m.sha256[:12]}")
    if not yes:
        print()
        print("[dry-run] nothing written. Re-run with --yes once the schema is verified.")
        return 0

    try:
        runner(["-q"], build_mark_script(selected))
    except subprocess.CalledProcessError as exc:
        print(f"FAILED to record\nstderr:\n{exc.stderr}", file=sys.stderr)
        return 3
    print()
    print(f"Recorded {len(selected)} migration(s) as applied.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply SETKA database migrations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="List applied and pending migrations.")
    up = sub.add_parser("up", help="Apply all pending migrations in order.")
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be applied, don't run SQL.",
    )
    mark = sub.add_parser(
        "mark-applied",
        help="Record migrations as applied WITHOUT running them (bookkeeping repair).",
    )
    mark.add_argument("names", nargs="*", help="Migration filenames to record.")
    mark.add_argument(
        "--all-pending",
        action="store_true",
        help="Record every currently pending migration.",
    )
    mark.add_argument(
        "--yes",
        action="store_true",
        help="Actually write. Without it the command is a dry run.",
    )
    args = parser.parse_args(argv)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "up":
        return cmd_up(dry_run=args.dry_run)
    if args.cmd == "mark-applied":
        return cmd_mark_applied(args.names, all_pending=args.all_pending, yes=args.yes)
    return 1


if __name__ == "__main__":
    sys.exit(main())
