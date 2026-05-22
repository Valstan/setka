"""Unit tests for scripts/migrate.py — bookkeeping-aware migration runner.

The tests never touch a real database. ``psql`` calls are replaced with a
fake runner that captures arguments and stdin scripts, so we can assert the
SQL the runner *intends* to send.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate  # noqa: E402  -- script-style import


@dataclass
class FakeCall:
    args: tuple[str, ...]
    stdin: str | None


class FakeRunner:
    def __init__(self, *, applied_filenames=None, table_missing=False, raise_other=False):
        self.calls: list[FakeCall] = []
        self._applied = list(applied_filenames or [])
        self._table_missing = table_missing
        self._raise_other = raise_other

    def __call__(self, args, stdin=None):
        self.calls.append(FakeCall(tuple(args), stdin))
        # Reads — psql -tA -c "SELECT ..."
        if "-c" in args and any("SELECT filename FROM applied_migrations" in a for a in args):
            if self._table_missing:
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=tuple(args),
                    output="",
                    stderr='ERROR:  relation "applied_migrations" does not exist',
                )
            if self._raise_other:
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=tuple(args),
                    output="",
                    stderr="ERROR:  permission denied",
                )
            return "\n".join(self._applied) + ("\n" if self._applied else "")
        # Writes — psql -q with stdin script. Nothing to return.
        return ""


@pytest.fixture
def tmp_migrations(tmp_path, monkeypatch):
    """Set up a tmp migrations dir and point migrate.MIGRATIONS_DIR at it."""
    d = tmp_path / "migrations"
    d.mkdir()
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", d)
    return d


def _write(d: Path, name: str, body: str = "-- noop\n") -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------- discover_migrations / compute_sha256 ----------


def test_compute_sha256_is_deterministic(tmp_path):
    p = tmp_path / "a.sql"
    p.write_text("CREATE TABLE x();", encoding="utf-8")
    assert migrate.compute_sha256(p) == migrate.compute_sha256(p)


def test_compute_sha256_changes_with_content(tmp_path):
    a = tmp_path / "a.sql"
    b = tmp_path / "b.sql"
    a.write_text("one", encoding="utf-8")
    b.write_text("two", encoding="utf-8")
    assert migrate.compute_sha256(a) != migrate.compute_sha256(b)


def test_discover_returns_sorted_by_filename(tmp_migrations):
    _write(tmp_migrations, "010_apply.sql")
    _write(tmp_migrations, "003_first.sql")
    _write(tmp_migrations, "007_third.sql")
    names = [m.name for m in migrate.discover_migrations()]
    assert names == ["003_first.sql", "007_third.sql", "010_apply.sql"]


def test_discover_ignores_non_sql_files(tmp_migrations):
    _write(tmp_migrations, "003_ok.sql")
    (tmp_migrations / "README.md").write_text("doc", encoding="utf-8")
    names = [m.name for m in migrate.discover_migrations()]
    assert names == ["003_ok.sql"]


# ---------- fetch_applied ----------


def test_fetch_applied_parses_psql_output():
    runner = FakeRunner(applied_filenames=["003_a.sql", "004_b.sql"])
    assert migrate.fetch_applied(runner) == {"003_a.sql", "004_b.sql"}


def test_fetch_applied_returns_empty_when_table_missing():
    runner = FakeRunner(table_missing=True)
    assert migrate.fetch_applied(runner) == set()


def test_fetch_applied_propagates_other_errors():
    runner = FakeRunner(raise_other=True)
    with pytest.raises(subprocess.CalledProcessError):
        migrate.fetch_applied(runner)


def test_fetch_applied_strips_blank_lines():
    runner = FakeRunner(applied_filenames=["", "003.sql", "  "])
    assert migrate.fetch_applied(runner) == {"003.sql"}


# ---------- build_apply_script ----------


def test_build_apply_script_wraps_in_transaction(tmp_migrations):
    p = _write(tmp_migrations, "010_x.sql", body="CREATE TABLE foo();\n")
    m = migrate.Migration(name=p.name, path=p, sha256="deadbeef")
    script = migrate.build_apply_script(m)
    assert script.startswith("BEGIN;\n")
    assert script.rstrip().endswith("COMMIT;")
    assert "CREATE TABLE foo();" in script
    assert "INSERT INTO applied_migrations" in script
    assert "ON CONFLICT (filename) DO UPDATE" in script
    assert "'010_x.sql'" in script
    assert "'deadbeef'" in script


def test_build_apply_script_escapes_single_quotes_in_name(tmp_migrations):
    p = _write(tmp_migrations, "010_x.sql", body="-- noop\n")
    m = migrate.Migration(name="weird'name.sql", path=p, sha256="abc'def")
    script = migrate.build_apply_script(m)
    assert "'weird''name.sql'" in script
    assert "'abc''def'" in script


# ---------- apply_migration ----------


def test_apply_migration_calls_runner_with_quiet_flag(tmp_migrations):
    p = _write(tmp_migrations, "010_x.sql", body="-- noop\n")
    m = migrate.Migration(name=p.name, path=p, sha256="hash")
    runner = FakeRunner()
    migrate.apply_migration(m, runner)
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.args == ("-q",)
    assert call.stdin is not None
    assert "BEGIN;" in call.stdin
    assert "COMMIT;" in call.stdin


# ---------- cmd_up ----------


def test_cmd_up_aborts_if_bootstrap_missing(tmp_migrations, capsys):
    _write(tmp_migrations, "003_x.sql")
    rc = migrate.cmd_up(runner=FakeRunner())
    assert rc == 2
    err = capsys.readouterr().err
    assert "010_applied_migrations.sql" in err


def test_cmd_up_no_pending(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql")
    runner = FakeRunner(applied_filenames=["010_applied_migrations.sql"])
    rc = migrate.cmd_up(runner=runner)
    assert rc == 0
    out = capsys.readouterr().out
    assert "up to date" in out
    # No write call should have been made.
    write_calls = [c for c in runner.calls if c.stdin is not None]
    assert write_calls == []


def test_cmd_up_dry_run_does_not_apply(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql")
    _write(tmp_migrations, "011_extra.sql")
    runner = FakeRunner(applied_filenames=["010_applied_migrations.sql"])
    rc = migrate.cmd_up(dry_run=True, runner=runner)
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "011_extra.sql" in out
    write_calls = [c for c in runner.calls if c.stdin is not None]
    assert write_calls == []


def test_cmd_up_applies_pending_in_order(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql")
    _write(tmp_migrations, "011_a.sql", body="-- a\n")
    _write(tmp_migrations, "012_b.sql", body="-- b\n")
    runner = FakeRunner(applied_filenames=["010_applied_migrations.sql"])
    rc = migrate.cmd_up(runner=runner)
    assert rc == 0
    write_calls = [c for c in runner.calls if c.stdin is not None]
    assert len(write_calls) == 2
    assert "'011_a.sql'" in write_calls[0].stdin
    assert "'012_b.sql'" in write_calls[1].stdin


def test_cmd_up_bootstrap_when_table_absent(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql", body="-- create table\n")
    _write(tmp_migrations, "011_a.sql", body="-- a\n")
    runner = FakeRunner(table_missing=True)
    rc = migrate.cmd_up(runner=runner)
    assert rc == 0
    write_calls = [c for c in runner.calls if c.stdin is not None]
    # Both migrations should be applied because the table was empty / missing.
    assert len(write_calls) == 2
    assert "'010_applied_migrations.sql'" in write_calls[0].stdin
    assert "'011_a.sql'" in write_calls[1].stdin


def test_cmd_up_fails_on_psql_error(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql")
    _write(tmp_migrations, "011_broken.sql", body="-- bad sql\n")

    class FailingRunner(FakeRunner):
        def __call__(self, args, stdin=None):
            if stdin is not None:
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=args,
                    output="",
                    stderr="ERROR: syntax error at or near",
                )
            return super().__call__(args, stdin)

    runner = FailingRunner(applied_filenames=["010_applied_migrations.sql"])
    rc = migrate.cmd_up(runner=runner)
    assert rc == 3
    err = capsys.readouterr().err
    assert "FAILED on 011_broken.sql" in err


# ---------- cmd_status ----------


def test_cmd_status_lists_all_with_marks(tmp_migrations, capsys):
    _write(tmp_migrations, "010_applied_migrations.sql")
    _write(tmp_migrations, "011_pending.sql")
    runner = FakeRunner(applied_filenames=["010_applied_migrations.sql"])
    rc = migrate.cmd_status(runner=runner)
    assert rc == 0
    out = capsys.readouterr().out
    assert "010_applied_migrations.sql" in out
    assert "011_pending.sql" in out
    assert "applied" in out
    assert "pending" in out
    assert "Pending to apply: 1" in out
