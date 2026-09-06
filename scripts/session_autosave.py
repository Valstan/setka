#!/usr/bin/env python3
"""Автоснимок состояния сессии разработки — без участия модели (хук ``Stop``).

Зачем. Нитку разработки до сих пор фиксировал только `/close_session`, то есть
рукой модели. Ночная сессия к утру весит под миллион токенов, и если кэш промпта
уже протух, «просто закрыть сессию» стоит полного пересыла контекста. Владелец
хочет закрывать вкладку молча — и ничего не терять: ни правки, ни нитку.

Механика. Хук ``Stop`` срабатывает после КАЖДОГО хода агента и получает на stdin
JSON с ``session_id``/``cwd`` (док-контракт хуков, сверено 2026-09-06). Скрипт
читает журнал сессии (``~/.claude/projects/<slug>/<session_id>.jsonl``, его пишет
сам Claude Code) и складывает выжимку в ``.claude/session-state/`` — реплики
владельца, последние строки агента, тронутые PR и состояние git. Дальше эту
выжимку печатает SessionStart-хук новой сессии.

``SessionEnd`` для этого не годится: документация не гарантирует его при закрытии
вкладки и даёт всем хукам события 1.5 с суммарно. Единственная надёжная точка —
``Stop``, поэтому пишем часто и дёшево (троттл ``--min-interval``).

Три свойства, ради которых написан модуль:

1. **Никогда не мешает.** Любая ошибка — тихий ``exit 0``: хук, роняющий ход
   агента, хуже отсутствующего хука.
2. **Ничего не отправляет наружу.** Только локальный файл под ``.gitignore``:
   репозиторий публичный, а в репликах владельца может оказаться что угодно.
3. **Чистое ядро.** Разбор журнала и рендер — чистые функции (тесты без файлов и
   без git); ввод-вывод инъектируется.

Два вывода из одной выжимки:

* **локальный снимок** ``.claude/session-state/latest.md`` — полный, с репликами
  владельца, под ``.gitignore``, никогда не покидает машину;
* **публикуемое состояние** ``--redacted`` — те же факты БЕЗ дословных реплик
  (ветка, HEAD, незакоммиченные файлы, номера PR, мои собственные строки о ходе
  работы). Его хук кладёт в ветку ``session-state`` на GitHub после каждого
  мержа, чтобы другая машина подхватила нитку. Репозиторий публичный, поэтому
  дословную речь владельца туда не отправляем.

Использование (вызывается хуком, руками — для проверки):
    echo '{"session_id":"<id>","cwd":"D:/valstan/REPO/setka"}' \\
        | ./venv/bin/python scripts/session_autosave.py --force
    ./venv/bin/python scripts/session_autosave.py --redacted --print
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

STATE_DIRNAME = ".claude/session-state"
LATEST_NAME = "latest.md"
#: Реплик владельца в выжимке (нитка) и строк агента (где остановились).
MAX_PROMPTS = 12
MAX_NARRATION = 3
MAX_PROMPT_CHARS = 400
MAX_NARRATION_CHARS = 700
#: Чаще этого не переписываем файл: хук зовётся после каждого хода.
DEFAULT_MIN_INTERVAL = 120.0

# Служебные подстановки Claude Code внутри user-записей: это не речь владельца.
# Уведомления фоновых задач приходят тем же каналом и на живой сессии забивают
# выжимку целиком (замер 06.09: 10 из 12 «реплик» оказались ими).
_SYSTEM_MARKERS = (
    "<system-reminder>",
    "<task-notification>",
    "<ci-monitor-event>",
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "[Request interrupted",
    "Caveat: The messages below",
    "This session is being continued from a previous conversation",
    "I hit my usage limit while you were working",
)
#: Тело вызванной slash-команды приходит user-записью — это не вопрос владельца.
_SKILL_BODY_PREFIX = "# /"
#: Тело подгруженного скилла/справочника тоже приходит user-записью. Владелец не
#: печатает двухкилобайтные markdown-документы, начинающиеся с заголовка.
_INJECTED_DOC_MIN_CHARS = 1500


# ---------------------------------------------------------------- разбор журнала


def iter_records(path: Path) -> Iterable[Dict[str, Any]]:
    """Записи журнала сессии (JSONL). Битые строки пропускаются молча."""
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def _text_of(message: Any) -> str:
    """Текст реплики: content бывает строкой и списком блоков."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "".join(parts).strip()
    return ""


def _is_owner_speech(text: str, origin: Any = None) -> bool:
    """Реплика владельца, а не служебная вставка/результат инструмента.

    Уведомления фоновых задач помечены полем ``origin`` — это надёжнее текста;
    остальное отсеивается по началу сообщения.
    """
    if not text:
        return False
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        return False
    stripped = text.lstrip()
    head = stripped[:200]
    if head.startswith(_SKILL_BODY_PREFIX):
        return False
    if head.startswith("# ") and len(stripped) >= _INJECTED_DOC_MIN_CHARS:
        return False
    return not any(marker in head for marker in _SYSTEM_MARKERS)


def digest_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Журнал → ``{prompts, narration, prs, branch, title, turns}``. Чистая."""
    prompts: List[Tuple[str, str]] = []
    narration: List[Tuple[str, str]] = []
    prs: Dict[int, str] = {}
    branch: Optional[str] = None
    title: Optional[str] = None
    turns = 0
    for rec in records:
        kind = rec.get("type")
        stamp = str(rec.get("timestamp") or "")[:19]
        if kind == "user":
            text = _text_of(rec.get("message"))
            if _is_owner_speech(text, rec.get("origin")):
                prompts.append((stamp, text))
        elif kind == "assistant":
            text = _text_of(rec.get("message"))
            if text:
                turns += 1
                narration.append((stamp, text))
            if rec.get("gitBranch"):
                branch = str(rec["gitBranch"])
        elif kind == "pr-link":
            try:
                prs[int(rec.get("prNumber"))] = str(rec.get("prUrl") or "")
            except (TypeError, ValueError):
                pass
        elif kind == "custom-title" and rec.get("customTitle"):
            title = str(rec["customTitle"])
    return {
        "prompts": prompts,
        "narration": narration,
        "prs": [(n, prs[n]) for n in sorted(prs)],
        "branch": branch,
        "title": title,
        "turns": turns,
    }


# ---------------------------------------------------------------- состояние git


def git_facts(root: Path, run: Optional[Callable[[Sequence[str]], str]] = None) -> Dict[str, Any]:
    """Ветка, HEAD, рассинхрон с origin и незакоммиченные файлы. Ошибки — пустые поля."""

    def _default(cmd: Sequence[str]) -> str:
        out = subprocess.run(  # noqa: S603 - фиксированный список аргументов
            list(cmd),
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return out.stdout.strip()

    run = run or _default
    facts: Dict[str, Any] = {"branch": "", "head": "", "dirty": [], "ahead": "", "commits": []}
    try:
        facts["branch"] = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        facts["head"] = run(["git", "log", "-1", "--format=%h %s"])
        facts["dirty"] = [line for line in run(["git", "status", "--short"]).splitlines() if line]
        facts["ahead"] = run(["git", "status", "--short", "--branch"]).splitlines()[:1]
        facts["commits"] = run(["git", "log", "--oneline", "-8"]).splitlines()
    except Exception:  # noqa: BLE001 - наблюдаемость не роняет хук
        pass
    return facts


# ---------------------------------------------------------------- рендер


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_redacted(
    digest: Dict[str, Any], git: Dict[str, Any], *, now_iso: str, machine: str
) -> str:
    """Публикуемое состояние: факты и мои строки, БЕЗ дословных реплик владельца.

    Едет в ветку ``session-state`` публичного репозитория, поэтому речь владельца
    сюда не попадает — только счётчик, чтобы было видно, что диалог был. Чистая.
    """
    lines = [
        "# Состояние сессии (пишет хук, не модель)",
        "",
        "> Машинное состояние последней сессии на машине `" + machine + "`. Обновляется",
        "> автоматически после мержей и пушей, чтобы сессию можно было закрыть молча и",
        "> подхватить с другого компьютера. **Курируемая нитка — `docs/SESSION_HANDOFF.md`**",
        "> (её пишет `/close_session`): здесь есть «что происходило», но нет «почему так",
        "> решили». Дословные реплики владельца остаются на его машине и сюда не едут.",
        "",
        f"**Обновлено:** {now_iso}",
        f"**Машина:** `{machine}`",
        f"**Ветка:** `{git.get('branch') or digest.get('branch') or '?'}`",
        f"**HEAD:** {git.get('head') or '?'}",
        "**Ходов агента:** "
        + f"{digest.get('turns', 0)}, реплик владельца: {len(digest.get('prompts') or [])}",
    ]
    dirty = git.get("dirty") or []
    if dirty:
        lines.append(
            f"**Незакоммичено файлов:** {len(dirty)} — снимок рабочего дерева в ветке "
            "`wip/" + machine + "`:"
        )
        lines += [f"- `{line}`" for line in dirty[:30]]
        if len(dirty) > 30:
            lines.append(f"- …и ещё {len(dirty) - 30}")
    else:
        lines.append("**Незакоммичено:** ничего — рабочее дерево чистое.")
    prs = digest.get("prs") or []
    if prs:
        shown = prs[-15:]
        tail = f" (и ещё {len(prs) - len(shown)} раньше)" if len(prs) > len(shown) else ""
        lines += ["", "**PR, которых касались:** " + ", ".join(f"#{n}" for n, _ in shown) + tail]
    lines += ["", "## Где остановился агент (его собственные слова)", ""]
    narration = digest.get("narration") or []
    if narration:
        for stamp, text in narration[-MAX_NARRATION:]:
            lines.append(f"- **{stamp}** — {_clip(text, MAX_NARRATION_CHARS)}")
    else:
        lines.append("_Агент ещё ничего не сказал._")
    commits = git.get("commits") or []
    if commits:
        lines += ["", "## Последние коммиты", "", "```"] + commits + ["```"]
    lines += [
        "",
        "---",
        "",
        "> Полный снимок с репликами владельца — на его машине,",
        "> `.claude/session-state/latest.md`.",
        "",
    ]
    return "\n".join(lines)


def render(digest: Dict[str, Any], git: Dict[str, Any], *, now_iso: str, session_id: str) -> str:
    """Выжимка → Markdown. Чистая: ни файлов, ни времени внутри."""
    lines = [
        "# Автоснимок сессии (пишет хук, не модель)",
        "",
        "> Машинная выжимка последней сессии в этом каталоге: о чём просил владелец, где",
        "> остановился агент, какие PR трогали. Пишется после каждого хода скриптом",
        "> `scripts/session_autosave.py`, лежит вне git. **Курируемая нитка — в",
        "> `docs/SESSION_HANDOFF.md`**, её по-прежнему пишет `/close_session`.",
        "",
        f"**Обновлён:** {now_iso}",
        f"**Сессия:** `{session_id}`" + (f" — «{digest['title']}»" if digest.get("title") else ""),
        f"**Ходов агента:** {digest.get('turns', 0)}",
        f"**Ветка:** `{git.get('branch') or digest.get('branch') or '?'}`",
        f"**HEAD:** {git.get('head') or '?'}",
    ]
    dirty = git.get("dirty") or []
    if dirty:
        lines.append(
            f"**Незакоммичено файлов:** {len(dirty)} — работа осталась только на этой машине:"
        )
        lines += [f"- `{line}`" for line in dirty[:20]]
        if len(dirty) > 20:
            lines.append(f"- …и ещё {len(dirty) - 20}")
    else:
        lines.append("**Незакоммичено:** ничего — рабочее дерево чистое.")

    prs = digest.get("prs") or []
    if prs:
        shown = prs[-12:]
        tail = f" (и ещё {len(prs) - len(shown)} раньше)" if len(prs) > len(shown) else ""
        lines += ["", "**PR, которых касались:** " + ", ".join(f"#{n}" for n, _ in shown) + tail]

    lines += ["", "## О чём просил владелец (его реплики, последние сверху)", ""]
    prompts = digest.get("prompts") or []
    if prompts:
        for stamp, text in reversed(prompts[-MAX_PROMPTS:]):
            lines.append(f"- **{stamp}** — {_clip(text, MAX_PROMPT_CHARS)}")
        if len(prompts) > MAX_PROMPTS:
            lines.append(f"- …и ещё {len(prompts) - MAX_PROMPTS} реплик раньше")
    else:
        lines.append("_Реплик не было._")

    lines += ["", "## Где остановился агент (его последние слова)", ""]
    narration = digest.get("narration") or []
    if narration:
        for stamp, text in narration[-MAX_NARRATION:]:
            lines.append(f"- **{stamp}** — {_clip(text, MAX_NARRATION_CHARS)}")
    else:
        lines.append("_Агент ещё ничего не сказал._")

    commits = git.get("commits") or []
    if commits:
        lines += ["", "## Последние коммиты", "", "```"] + commits + ["```"]
    lines += [
        "",
        "---",
        "",
        "> Снимок машинный и приблизительный: он говорит, ЧТО происходило, но не заменяет",
        "> курируемый handoff. Если нитка важна — закройте сессию через `/close_session`.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- ввод-вывод


def _machine_name() -> str:
    """Короткое имя машины для веток снимков (буквы/цифры/дефис)."""
    import platform
    import re

    raw = platform.node() or os.environ.get("COMPUTERNAME") or "unknown"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower()[:40] or "unknown"


def resolve_transcript(payload: Dict[str, Any], *, home: Optional[Path] = None) -> Optional[Path]:
    """Путь к журналу сессии: из полезной нагрузки хука либо поиском по session_id.

    ``Stop`` не приносит ``transcript_path`` (в отличие от ``SessionStart``/
    ``SessionEnd``), поэтому ищем файл ``<session_id>.jsonl`` в каталогах
    ``~/.claude/projects/*`` — имя каталога кодирует путь проекта, и правило
    кодирования нигде не описано, а значит на него нельзя опираться.
    """
    direct = payload.get("transcript_path")
    if direct:
        path = Path(str(direct))
        if path.is_file():
            return path
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return None
    base = (home or Path.home()) / ".claude" / "projects"
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"*/{session_id}.jsonl"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def should_write(target: Path, *, min_interval: float, now: float, force: bool) -> bool:
    """Троттл: хук зовётся после каждого хода, а файл нужен не чаще раза в N секунд."""
    if force or min_interval <= 0:
        return True
    try:
        return now - target.stat().st_mtime >= min_interval
    except OSError:
        return True


def write_state(root: Path, session_id: str, body: str) -> Path:
    """Записать снимок сессии и обновить ``latest.md``."""
    state_dir = root / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / f"{session_id or 'unknown'}.md"
    target.write_text(body, encoding="utf-8", newline="\n")
    (state_dir / LATEST_NAME).write_text(body, encoding="utf-8", newline="\n")
    return target


STATE_BRANCH = "session-state"
STATE_FILE = "SESSION_STATE.md"
#: Команды, после которых состояние публикуется: работа уехала на GitHub —
#: значит сессию уже можно закрывать молча, и снимок обязан это отражать.
_SYNC_TRIGGERS = ("gh pr merge", "git push", "git merge")


def is_sync_trigger(payload: Dict[str, Any]) -> bool:
    """Был ли только что мерж/пуш — то есть момент, когда стоит опубликовать состояние.

    Смотрим на команду, которую выполнил агент (``PostToolUse`` приносит
    ``tool_input``). Чистая: ни git, ни файлов.
    """
    if str(payload.get("tool_name") or "").lower() not in ("bash", "powershell"):
        return False
    tool_input = payload.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")
    elif isinstance(tool_input, str):
        command = tool_input
    low = " ".join(command.lower().split())
    return any(trigger in low for trigger in _SYNC_TRIGGERS)


def _git(root: Path, *args: str, check: bool = False) -> str:
    out = subprocess.run(  # noqa: S603 - фиксированные аргументы
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if check and out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return out.stdout.strip()


def publish_state(root: Path, body: str, machine: str) -> Optional[str]:
    """Положить состояние в ветку ``session-state`` — коммитом-сиротой мимо HEAD.

    Ни ``checkout``, ни ``add`` в рабочем индексе: собираем дерево из одного файла
    через ``hash-object``/``mktree`` и двигаем ссылку. Рабочее дерево владельца и
    его ветка остаются нетронутыми, ``main`` не задет (PR-only flow в силе).
    """
    tmp = root / ".git" / "session-state.tmp.md"
    tmp.write_text(body, encoding="utf-8", newline="\n")
    try:
        blob = _git(root, "hash-object", "-w", str(tmp), check=True)
        # Байты, а не text=True: на Windows перевод строки в конвейере становится
        # CRLF, и git забирает "\r" в ИМЯ файла ("SESSION_STATE.md\r").
        tree = (
            subprocess.run(  # noqa: S603
                ["git", "mktree"],
                cwd=str(root),
                input=f"100644 blob {blob}\t{STATE_FILE}\n".encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
            .stdout.decode("utf-8", "replace")
            .strip()
        )
        if not tree:
            return None
        parent = _git(root, "rev-parse", "-q", "--verify", f"refs/heads/{STATE_BRANCH}")
        args = ["commit-tree", tree, "-m", f"session state ({machine})"]
        if parent:
            args += ["-p", parent]
        commit = _git(root, *args, check=True)
        _git(root, "update-ref", f"refs/heads/{STATE_BRANCH}", commit, check=True)
        return commit
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def publish_worktree(root: Path, machine: str) -> Optional[str]:
    """Снимок рабочего дерева (включая неотслеживаемое) в ветку ``wip/<машина>``.

    Через временный индекс: ``GIT_INDEX_FILE`` не трогает настоящий, поэтому у
    владельца ничего не «застейджится». ``.gitignore`` соблюдается — секреты и
    локальные снимки в снимок не попадают.
    """
    dirty = _git(root, "status", "--short")
    if not dirty:
        return None
    index = root / ".git" / "session-wip.index"
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    try:
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(  # noqa: S603
                ["git", *args], cwd=str(root), env=env, capture_output=True, timeout=120
            )
        tree = subprocess.run(  # noqa: S603
            ["git", "write-tree"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout.strip()
        if not tree:
            return None
        head = _git(root, "rev-parse", "HEAD")
        commit = _git(
            root, "commit-tree", tree, "-p", head, "-m", f"wip snapshot ({machine})", check=True
        )
        _git(root, "update-ref", f"refs/heads/wip/{machine}", commit, check=True)
        return commit
    finally:
        try:
            index.unlink()
        except OSError:
            pass


def push_refs(root: Path, refs: Sequence[str], *, background: bool = True) -> None:
    """Отправить ссылки на origin. В фоне — чтобы сеть не тормозила ход агента."""
    if not refs:
        return
    cmd = ["git", "push", "--quiet", "origin", *[f"+refs/heads/{r}" for r in refs]]
    if background:
        subprocess.Popen(  # noqa: S603 - отсоединённый push, вывод не нужен
            cmd,
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return
    subprocess.run(cmd, cwd=str(root), capture_output=True, timeout=180)  # noqa: S603


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Автоснимок сессии для следующей сессии")
    parser.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL)
    parser.add_argument("--force", action="store_true", help="писать, игнорируя троттл")
    parser.add_argument("--quiet", action="store_true", help="не печатать путь снимка")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="опубликовать состояние в ветку session-state и рабочее дерево в wip/<машина>",
    )
    parser.add_argument("--no-push", action="store_true", help="собрать ветки, но не пушить")
    parser.add_argument(
        "--print", dest="do_print", action="store_true", help="напечатать состояние"
    )
    parser.add_argument("--redacted", action="store_true", help="публикуемая версия, без реплик")
    parser.add_argument(
        "--if-merge",
        action="store_true",
        help="публиковать только если ход был мержем/пушем (хук PostToolUse)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    root = Path(str(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()))
    if not (root / ".git").exists():
        return 0
    if args.if_merge and not is_sync_trigger(payload):
        return 0
    session_id = str(payload.get("session_id") or "unknown")
    target = root / STATE_DIRNAME / f"{session_id}.md"
    if not should_write(target, min_interval=args.min_interval, now=time.time(), force=args.force):
        return 0

    transcript = resolve_transcript(payload)
    digest = digest_records(iter_records(transcript)) if transcript else digest_records([])
    git = git_facts(root)
    body = render(
        digest,
        git,
        now_iso=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        session_id=session_id,
    )
    written = write_state(root, session_id, body)
    if not args.quiet:
        print(f"session autosave: {written}")

    if args.sync or args.redacted or args.do_print:
        machine = _machine_name()
        public = render_redacted(
            digest,
            git,
            now_iso=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
            machine=machine,
        )
        if args.do_print:
            print(public)
        if args.sync:
            refs = []
            if publish_state(root, public, machine):
                refs.append(STATE_BRANCH)
            if publish_worktree(root, machine):
                refs.append(f"wip/{machine}")
            if refs and not args.no_push:
                push_refs(root, refs)
            if not args.quiet:
                print("session sync: " + (", ".join(refs) if refs else "нечего публиковать"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - хук не имеет права ронять ход агента
        print(f"session autosave skipped: {exc}", file=sys.stderr)
        sys.exit(0)
