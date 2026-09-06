"""Unit-тесты ``scripts/session_autosave.py`` — автоснимок сессии для следующей сессии.

Скрипт — CLI вне устанавливаемого пакета, грузим через importlib (как
``test_wait_for_health.py``). Ни git, ни каталог ``~/.claude`` не нужны: разбор
журнала и рендер — чистые функции, ввод-вывод инъектируется или уводится в tmp.

Что охраняется: служебные вставки Claude Code не принимаются за речь владельца;
выжимка переживает битые строки журнала; троттл не даёт писать файл на каждый
ход; снимок находит журнал по ``session_id``, когда хук не принёс путь; сбой git
не роняет рендер.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "session_autosave", REPO_ROOT / "scripts" / "session_autosave.py"
)
sa = importlib.util.module_from_spec(_spec)
sys.modules["session_autosave"] = sa
_spec.loader.exec_module(sa)


def _rec(kind, **kw):
    return {"type": kind, **kw}


def _user(text, ts="2026-09-06T10:00:00"):
    return _rec("user", timestamp=ts, message={"role": "user", "content": text})


def _assistant(text, ts="2026-09-06T10:00:05", branch="main"):
    return _rec(
        "assistant",
        timestamp=ts,
        gitBranch=branch,
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


# ───────── разбор журнала ─────────


def test_digest_keeps_owner_speech_and_drops_service_inserts():
    records = [
        _user("почини планировщик"),
        _user("<system-reminder>\nInstruction files were re-read\n</system-reminder>"),
        _user("[Request interrupted by user]"),
        _user("<local-command-caveat>Caveat</local-command-caveat>"),
        _user("<task-notification> <task-id>b1</task-id> </task-notification>"),
        _user("# /close_session — закрыть сессию. Тело вызванной команды."),
        _user("This session is being continued from a previous conversation..."),
        _user("# Справочник на 2000 знаков " + "ы" * 2000),
        _rec(
            "user",
            timestamp="2026-09-06T10:00:00",
            origin={"kind": "task-notification"},
            message={"role": "user", "content": "текст уведомления без маркера"},
        ),
        _user(""),
        _user([{"type": "tool_result", "content": "ok"}]),  # результат инструмента
        _user([{"type": "text", "text": "продолжай"}]),
        _assistant("Правлю диспетчер репостов."),
        _rec("pr-link", prNumber=649, prUrl="https://github.com/Valstan/setka/pull/649"),
        _rec("pr-link", prNumber=649, prUrl="https://github.com/Valstan/setka/pull/649"),
        _rec("pr-link", prNumber=650, prUrl="https://github.com/Valstan/setka/pull/650"),
        _rec("custom-title", customTitle="САРАФАН"),
    ]
    d = sa.digest_records(records)
    assert [t for _ts, t in d["prompts"]] == ["почини планировщик", "продолжай"]
    assert d["narration"] == [("2026-09-06T10:00:05", "Правлю диспетчер репостов.")]
    assert [n for n, _u in d["prs"]] == [649, 650]  # дедуп и сортировка
    assert d["branch"] == "main" and d["title"] == "САРАФАН" and d["turns"] == 1


def test_iter_records_survives_broken_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(_user("раз")),
                "не json вовсе",
                "",
                json.dumps(["список, а не объект"]),
                json.dumps(_assistant("два")),
            ]
        ),
        encoding="utf-8",
    )
    d = sa.digest_records(sa.iter_records(path))
    assert [t for _ts, t in d["prompts"]] == ["раз"]
    assert d["turns"] == 1


def test_digest_of_empty_journal_is_harmless():
    d = sa.digest_records([])
    assert d["prompts"] == [] and d["narration"] == [] and d["prs"] == []


# ───────── рендер ─────────


def _git(**kw):
    base = {"branch": "main", "head": "abc123 тест", "dirty": [], "ahead": [], "commits": []}
    base.update(kw)
    return base


def test_render_shows_thread_and_dirty_files():
    d = sa.digest_records([_user("сделай X"), _assistant("Сделал X."), _rec("pr-link", prNumber=7)])
    text = sa.render(
        d, _git(dirty=[" M a.py", "?? b.py"]), now_iso="2026-09-06 10:00:00 +0300", session_id="sid"
    )
    assert "сделай X" in text and "Сделал X." in text
    assert "#7" in text and "`sid`" in text
    assert "Незакоммичено файлов:** 2" in text and "a.py" in text
    assert "docs/SESSION_HANDOFF.md" in text  # снимок честно указывает на курируемую нитку


def test_render_clean_tree_and_empty_journal():
    text = sa.render(sa.digest_records([]), _git(), now_iso="ts", session_id="sid")
    assert "рабочее дерево чистое" in text
    assert "_Реплик не было._" in text and "_Агент ещё ничего не сказал._" in text


def test_render_clips_long_texts_and_caps_lists():
    prompts = [
        _user(f"реплика {i} " + "ы" * 900, ts=f"2026-09-06T10:{i:02d}:00") for i in range(20)
    ]
    text = sa.render(sa.digest_records(prompts), _git(), now_iso="ts", session_id="sid")
    assert "…и ещё 8 реплик раньше" in text
    assert max(len(line) for line in text.splitlines()) < sa.MAX_PROMPT_CHARS + 60


def test_git_facts_never_raises_on_broken_git():
    def boom(cmd):
        raise OSError("git отсутствует")

    facts = sa.git_facts(REPO_ROOT, run=boom)
    assert facts["branch"] == "" and facts["dirty"] == []
    # рендер на пустых фактах тоже не падает
    assert "?" in sa.render(sa.digest_records([]), facts, now_iso="ts", session_id="s")


# ───────── ввод-вывод ─────────


def test_resolve_transcript_prefers_payload_then_searches_by_session_id(tmp_path):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "D--valstan-REPO-setka"
    proj.mkdir(parents=True)
    journal = proj / "sid-42.jsonl"
    journal.write_text("{}", encoding="utf-8")

    assert sa.resolve_transcript({"session_id": "sid-42"}, home=home) == journal
    assert sa.resolve_transcript({"session_id": "нет-такой"}, home=home) is None
    assert sa.resolve_transcript({}, home=home) is None

    direct = tmp_path / "direct.jsonl"
    direct.write_text("{}", encoding="utf-8")
    assert (
        sa.resolve_transcript({"transcript_path": str(direct), "session_id": "sid-42"}, home=home)
        == direct
    )
    # путь из хука есть, но файла нет — падаем на поиск, а не на исключение
    assert (
        sa.resolve_transcript(
            {"transcript_path": str(tmp_path / "нет"), "session_id": "sid-42"}, home=home
        )
        == journal
    )


def test_should_write_throttles_but_force_wins(tmp_path):
    target = tmp_path / "s.md"
    assert sa.should_write(target, min_interval=120, now=1000.0, force=False)  # файла нет
    target.write_text("x", encoding="utf-8")
    import os

    os.utime(target, (1000.0, 1000.0))
    assert not sa.should_write(target, min_interval=120, now=1050.0, force=False)
    assert sa.should_write(target, min_interval=120, now=1200.0, force=False)
    assert sa.should_write(target, min_interval=120, now=1050.0, force=True)
    assert sa.should_write(target, min_interval=0, now=1050.0, force=False)


def test_write_state_writes_snapshot_and_latest(tmp_path):
    written = sa.write_state(tmp_path, "sid", "тело снимка")
    assert written.read_text(encoding="utf-8") == "тело снимка"
    latest = tmp_path / sa.STATE_DIRNAME / sa.LATEST_NAME
    assert latest.read_text(encoding="utf-8") == "тело снимка"


def test_main_writes_snapshot_from_hook_payload(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    journal = tmp_path / "j.jsonl"
    journal.write_text(json.dumps(_user("собери снимок")), encoding="utf-8")
    payload = {"session_id": "sid-1", "cwd": str(root), "transcript_path": str(journal)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sa, "git_facts", lambda root_, run=None: _git())

    assert sa.main([]) == 0
    body = (root / sa.STATE_DIRNAME / "sid-1.md").read_text(encoding="utf-8")
    assert "собери снимок" in body
    assert "session autosave" in capsys.readouterr().out


def test_main_is_silent_outside_a_repo_and_on_garbage_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO('{"cwd": "' + str(tmp_path).replace("\\", "/") + '"}')
    )
    assert sa.main(["--quiet"]) == 0  # нет .git — снимок не пишем
    assert not (tmp_path / sa.STATE_DIRNAME).exists()

    root = tmp_path / "repo2"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "stdin", io.StringIO("не json"))
    monkeypatch.setattr(sa, "git_facts", lambda root_, run=None: _git())
    assert sa.main(["--quiet", "--force"]) == 0
    assert (root / sa.STATE_DIRNAME / "unknown.md").is_file()


@pytest.mark.parametrize("marker", list(sa._SYSTEM_MARKERS))
def test_every_service_marker_is_filtered(marker):
    d = sa.digest_records([_user(f"{marker} что-то ещё")])
    assert d["prompts"] == []


# ───────── публикация в git ─────────


def test_is_sync_trigger_reacts_only_to_merges_and_pushes():
    def payload(cmd, tool="Bash"):
        return {"tool_name": tool, "tool_input": {"command": cmd}}

    assert sa.is_sync_trigger(payload("gh pr merge 651 --squash --delete-branch"))
    assert sa.is_sync_trigger(payload("git push -u origin feat/x"))
    assert sa.is_sync_trigger(payload("git   merge   --no-edit main"))  # лишние пробелы
    assert sa.is_sync_trigger(payload("GH PR MERGE 1"))  # регистр
    assert not sa.is_sync_trigger(payload("git status --short"))
    assert not sa.is_sync_trigger(payload("pytest tests/ -q"))
    assert not sa.is_sync_trigger({"tool_name": "Read", "tool_input": {"command": "git push"}})
    assert not sa.is_sync_trigger({})


def _init_repo(tmp_path):
    import subprocess

    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "test"],
    ):
        subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)
    (root / "a.txt").write_text("раз", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(root), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "первый"], cwd=str(root), capture_output=True, check=True
    )
    return root


def test_publish_state_names_file_without_carriage_return(tmp_path):
    """Гвоздь на ловушку Windows: text=True в конвейере уводил CR в ИМЯ файла."""
    root = _init_repo(tmp_path)
    assert sa.publish_state(root, "тело состояния\n", "pc-test")
    tree = sa._git(root, "ls-tree", f"refs/heads/{sa.STATE_BRANCH}")
    assert tree.endswith(sa.STATE_FILE) and "\\r" not in tree and '"' not in tree
    body = sa._git(root, "show", f"refs/heads/{sa.STATE_BRANCH}:{sa.STATE_FILE}")
    assert "тело состояния" in body
    # второй вызов продолжает ветку, а не начинает заново
    assert sa.publish_state(root, "второе состояние", "pc-test")
    assert len(sa._git(root, "log", "--oneline", f"refs/heads/{sa.STATE_BRANCH}").splitlines()) == 2


def test_publish_worktree_snapshots_changes_without_touching_the_tree(tmp_path):
    root = _init_repo(tmp_path)
    assert sa.publish_worktree(root, "pc-test") is None  # чисто — публиковать нечего

    (root / "a.txt").write_text("два", encoding="utf-8")
    (root / "новый.txt").write_text("свежий", encoding="utf-8")
    (root / ".gitignore").write_text("секрет.txt\n", encoding="utf-8")
    (root / "секрет.txt").write_text("не должен уехать", encoding="utf-8")

    head_before = sa._git(root, "rev-parse", "HEAD")
    branch_before = sa._git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status_before = sa._git(root, "status", "--short")

    assert sa.publish_worktree(root, "pc-test")

    assert sa._git(root, "rev-parse", "HEAD") == head_before  # HEAD не двигали
    assert sa._git(root, "rev-parse", "--abbrev-ref", "HEAD") == branch_before
    assert sa._git(root, "status", "--short") == status_before  # ничего не застейджено
    # core.quotepath=false: иначе git отдаёт не-ASCII имена в восьмеричных escape'ах
    files = sa._git(
        root,
        "-c",
        "core.quotepath=false",
        "ls-tree",
        "-r",
        "--name-only",
        "refs/heads/wip/pc-test",
    ).splitlines()
    assert "новый.txt" in files and "a.txt" in files
    assert "секрет.txt" not in files  # .gitignore соблюдён
    assert "два" in sa._git(root, "show", "refs/heads/wip/pc-test:a.txt")
    assert not (root / ".git" / "session-wip.index").exists()  # временный индекс убран


def test_machine_name_is_branch_safe(monkeypatch):
    import platform

    monkeypatch.setattr(platform, "node", lambda: "ПК Валентина / дом")
    name = sa._machine_name()
    assert " " not in name and "/" not in name and name


def test_render_redacted_has_facts_but_no_owner_quotes():
    d = sa.digest_records(
        [
            _user("секретная просьба владельца"),
            _assistant("Правлю диспетчер."),
            _rec("pr-link", prNumber=651),
        ]
    )
    text = sa.render_redacted(d, _git(dirty=[" M a.py"]), now_iso="ts", machine="pc-test")
    assert "секретная просьба" not in text
    assert "реплик владельца: 1" in text  # факт диалога виден, содержание — нет
    assert "Правлю диспетчер." in text and "#651" in text and "wip/pc-test" in text


def test_main_if_merge_skips_ordinary_commands(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    payload = {
        "session_id": "sid",
        "cwd": str(root),
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    called = []
    monkeypatch.setattr(sa, "publish_state", lambda *a, **k: called.append("state"))
    assert sa.main(["--sync", "--if-merge", "--force", "--quiet"]) == 0
    assert called == [] and not (root / sa.STATE_DIRNAME).exists()
