"""Приёмка обеих половин защиты от утечки секретов на одном корпусе подсадных.

**Зачем ещё один файл, если есть test_secret_scanner_config.py.** Тот проверяет
*инвариант* «редактор логов и гейт считают секретом одно и то же» на шести
фикстурах и компилирует регулярки **питоном**. Питонова половина слабее гейта:
``re.search`` не знает ни про ``entropy``, ни про ``[[rules.allowlists]]``, ни
про префильтр ``keywords`` — то есть показывает «поймано» там, где настоящий
сканер ещё может отсеять находку. Этот файл зовёт **бинарник** на тех же
формах, которыми у нас реально утекало, и на формах, на которые сканер обязан
**молчать**.

**Что такое подсадной.** Запись корпуса ``decoys.corpus``: кусок текста
(``@payload``) + вердикт для каждой полосы. Вердикты:

* ``hit``  — сканер обязан найти ``@secret``; редактор обязан вычистить его так,
  чтобы не выжил ни один фрагмент длиной ≥ 16 символов (``MIN_FRAGMENT``).
  Порог не косметический: неудачная граница шаблона съедает половину значения и
  оставляет хвост, которым всё ещё можно пользоваться.
* ``miss`` — сканер обязан дать **ноль** находок; редактор обязан вернуть
  payload дословно. Ложное срабатывание тут дороже пропуска: гейт, который
  краснеет на git-sha и на наших собственных масках, глушат целиком.
* ``gap``  — дыра известна и осознанно не закрыта. Тест падает при изменении
  **в любую** сторону: и когда дыру закроют (перевести флаг на ``hit``), и когда
  она закроется случайно.
* ``fp``   — сканер срабатывает на не-секрете, и это **принятая** цена. Тоже
  падает в обе стороны: исчезло срабатывание — значит правило сузили, и рядом,
  скорее всего, тихо умер парный ``hit``.

**Три способа провалить эту приёмку молча** (все три закрыты ниже, и все три
уже случались в проектах такого рода):

1. Прогнать корпус одним файлом. ``keywords`` у gitleaks — префильтр по
   **фрагменту**, а не по строке; в общем файле слова из соседних записей
   открывают префильтр всем сразу, и правило, которое в бою промолчало бы,
   на корпусе честно зеленеет. Поэтому каждая запись живёт в собственном
   подкаталоге под именем из ``@as`` (расширение значимо: у дефолтных правил
   бывают ``path``-условия).
2. Прогнать с включённым path-allowlist. Сам ``decoys.corpus`` из-под гейта
   выведен (иначе он валит каждый коммит: 44 находки), но временные копии под
   исключение попадать не должны — иначе полоса ``scan`` зелена «потому что
   путь исключён». Держится тестом ``test_corpus_path_is_allowlisted*``.
3. Оставить включённым слой «по значению» в маскировании. Если значения
   токенов лежат в окружении, полоса ``redact`` зеленеет, не проверив ни одной
   регулярки формы. Фабрика здесь ставится с ``env={}`` принудительно.

**Почему без ``--redact``.** Обычно он обязателен: без него найденное значение
печатается в вывод. Здесь наоборот — нужно убедиться, что найден именно
``@secret`` записи, а не что-то соседнее; ``--redact`` это делает невозможным.
Безопасно ровно потому, что все значения корпуса синтетические, и это
проверяется отдельным тестом ``test_corpus_findings_are_all_declared``.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field

import pytest

from utils.log_redaction import _reset_for_tests, install_log_redaction

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS = pathlib.Path(__file__).with_name("decoys.corpus")
CONFIG = ROOT / ".gitleaks.toml"
CORPUS_REL = "tests/test_logging/decoys.corpus"

#: Минимальная длина уцелевшего куска секрета, которую считаем утечкой.
MIN_FRAGMENT = 16

#: Где искать бинарник. Первым — корень репозитория: в CI шаг «Secret scan»
#: распаковывает gitleaks именно туда, и к моменту запуска тестов он ещё лежит
#: там. Дальше — те же места, что у scripts/gitleaks_precommit.py.
_BINARY_CANDIDATES = (
    ROOT / "gitleaks",
    ROOT / "gitleaks.exe",
    pathlib.Path.home() / ".local" / "bin" / "gitleaks.exe",
    pathlib.Path.home() / ".local" / "bin" / "gitleaks",
    pathlib.Path("/usr/local/bin/gitleaks"),
)


@dataclass(frozen=True)
class Case:
    case: str
    expect_scan: str
    expect_redact: str
    as_name: str
    via: str
    secret: str
    payload: str
    nothit: tuple[str, ...] = field(default=())


def _parse(text: str) -> list[Case]:
    """Разбор корпуса. Payload — сырые байты между ``@payload`` и ``@end``."""
    cases: list[Case] = []
    meta: dict[str, object] = {}
    payload: list[str] | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        if payload is not None:
            if line == "@end":
                meta["payload"] = "\n".join(payload)
                cases.append(
                    Case(
                        case=str(meta["case"]),
                        expect_scan=str(meta["expect_scan"]),
                        expect_redact=str(meta["expect_redact"]),
                        as_name=str(meta["as"]),
                        via=str(meta["via"]),
                        secret=str(meta["secret"]),
                        payload=str(meta["payload"]),
                        nothit=tuple(meta.get("nothit", ())),  # type: ignore[arg-type]
                    )
                )
                meta, payload = {}, None
            else:
                payload.append(line)
            continue
        if not line.strip() or line.startswith("#"):
            continue
        assert line.startswith("@"), f"{CORPUS.name}:{lineno}: не директива и не комментарий"
        if line == "@payload":
            payload = []
            continue
        key, _, value = line[1:].partition(" ")
        if key == "why":
            continue  # пояснение для человека, в ассертах не участвует
        if key == "nothit":
            meta.setdefault("nothit", [])
            meta["nothit"].append(value)  # type: ignore[union-attr]
        else:
            meta[key] = value
    assert payload is None, f"{CORPUS.name}: незакрытый @payload"
    return cases


CASES = _parse(CORPUS.read_text(encoding="utf-8"))
IDS = [c.case for c in CASES]


def _find_gitleaks() -> str | None:
    found = shutil.which("gitleaks")
    if found:
        return found
    for candidate in _BINARY_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.fixture(scope="session")
def scan(tmp_path_factory) -> dict[str, list[dict]]:
    """Находки бинарника по каждой записи: ``{case: [finding, ...]}``.

    Один запуск на весь корпус, но каждая запись — в собственном подкаталоге и
    под собственным именем файла. Изоляция от этого не страдает: и префильтр
    ``keywords``, и подавление дублей у ``generic-*`` работают в пределах
    фрагмента, а фрагмент в ``dir``-режиме — это один файл (см. отпечаток
    ``<файл>:<правило>:<строка>``).
    """
    binary = _find_gitleaks()
    if binary is None:
        message = (
            "gitleaks не найден: полоса scan НЕ ПРОВЕРЕНА. "
            "Поставить: см. шапку scripts/gitleaks_precommit.py"
        )
        # В CI бинарник есть всегда (его качает шаг Secret scan). Если его там
        # нет — это поломка гейта, а не повод пропустить приёмку.
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)

    root = tmp_path_factory.mktemp("decoys")
    for case in CASES:
        target = root / case.case / case.as_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(case.payload, encoding="utf-8", newline="")
    # Отдельно — корпус целиком, для аудита «в файле нет ничего, кроме
    # объявленных синтетических значений».
    whole = root / "_whole"
    whole.mkdir()
    shutil.copyfile(CORPUS, whole / CORPUS.name)

    report = root.parent / "decoys-report.json"
    subprocess.run(
        [
            binary,
            "dir",
            str(root),
            "--config",
            str(CONFIG),
            "--no-banner",
            "--exit-code",
            "1",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,  # находки — это exit 1, и он здесь ожидаем
    )
    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
    grouped: dict[str, list[dict]] = {c.case: [] for c in CASES}
    grouped["_whole"] = []
    for finding in findings:
        bucket = pathlib.PurePath(finding["File"]).parts[-2]
        grouped.setdefault(bucket, []).append(finding)
    return grouped


def _covers(finding: dict, secret: str) -> bool:
    return secret in finding["Match"] or finding["Secret"] in secret


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_scanner(case: Case, scan: dict[str, list[dict]]) -> None:
    findings = scan[case.case]
    rules = sorted({f["RuleID"] for f in findings})
    covering = [f for f in findings if case.secret != "-" and _covers(f, case.secret)]

    if case.expect_scan == "hit":
        assert (
            covering
        ), f"{case.case}: секрет не найден (сработали правила: {rules or 'ни одного'})"
    elif case.expect_scan == "fp":
        assert findings, (
            f"{case.case}: принятое ложное срабатывание исчезло — правило сузили. "
            "Проверить парную запись-hit, прежде чем править вердикт"
        )
        assert not covering, f"{case.case}: запись-fp не должна содержать объявленного секрета"
    else:  # miss | gap
        assert not findings, f"{case.case}: ложное срабатывание правил {rules}"

    for bad in case.nothit:
        assert all(bad not in f["Secret"] for f in findings), f"{case.case}: {bad} принят за секрет"


class _Stringy:
    """Объект, который станет строкой ПОСЛЕ фабрики (форма инцидента 19.08)."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _emit(via: str, payload: str) -> str:
    """Пропустить payload через настоящую фабрику LogRecord нужным носителем."""
    previous = logging.getLogRecordFactory()
    # Снимаем чужую фабрику принудительно: install_log_redaction идемпотентен и
    # на уже установленной фабрике стал бы no-op — вместе с её значениями из
    # окружения. Нам нужен ЧИСТЫЙ слой «по форме», env={} обязателен.
    logging.setLogRecordFactory(logging.LogRecord)
    install_log_redaction(env={})
    logger = logging.getLogger(f"decoy.{via}")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    capture = _Capture()
    logger.addHandler(capture)
    try:
        if via == "msg":
            logger.info(payload)
        elif via == "arg":
            logger.info("%s", payload)
        elif via == "argobj":
            logger.info("%s", _Stringy(payload))
        elif via == "dictarg":
            logger.info("%(payload)s", {"payload": payload})
        elif via == "exc":
            logger.info("%s", RuntimeError(payload))
        elif via == "excinfo":
            try:
                raise RuntimeError(payload)
            except RuntimeError:
                logger.error("boom", exc_info=True)
        else:
            raise AssertionError(f"неизвестный @via: {via}")
        record = capture.records[-1]
        out = record.getMessage()
        if record.exc_text:
            out += "\n" + record.exc_text
        return out
    finally:
        logger.handlers = []
        _reset_for_tests(previous)


def _surviving_fragment(out: str, secret: str) -> str | None:
    for i in range(len(secret) - MIN_FRAGMENT + 1):
        fragment = secret[i : i + MIN_FRAGMENT]
        if fragment in out:
            return fragment
    return None


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_redaction(case: Case) -> None:
    out = _emit(case.via, case.payload)

    if case.expect_redact == "hit":
        assert case.secret != "-", f"{case.case}: вердикт hit без @secret бессмыслен"
        fragment = _surviving_fragment(out, case.secret)
        assert fragment is None, (
            f"{case.case}: в логе выжил кусок секрета длиной {MIN_FRAGMENT} "
            "— маскирование обрезало значение, но не закрыло его"
        )
    elif case.expect_redact == "fp":
        assert case.secret == "-", f"{case.case}: вердикт fp у записи с объявленным секретом"
        assert case.payload not in out, (
            f"{case.case}: принятое ложное маскирование исчезло — шаблон сузили. "
            "Проверить парную запись-hit, прежде чем править вердикт"
        )
    else:  # miss | gap
        assert (
            case.payload in out
        ), f"{case.case}: маскирование тронуло строку, которую трогать не должно"


def test_corpus_path_is_allowlisted_in_both_path_flavours() -> None:
    """Корпус обязан быть выведен из-под гейта — и в CI, и в локальном dir-режиме.

    В git-режиме (CI, pre-commit) путь приходит с прямыми слэшами, в
    ``dir``-режиме на Windows — с обратными. Запись в allowlist обязана
    покрывать обе формы, иначе локальный прогон даёт 44 «находки» на файле,
    который специально набит синтетикой.
    """
    patterns = [
        re.compile(p)
        for p in tomllib.loads(CONFIG.read_text(encoding="utf-8"))["allowlist"]["paths"]
    ]
    for flavour in (CORPUS_REL, CORPUS_REL.replace("/", "\\")):
        assert any(p.search(flavour) for p in patterns), f"корпус не в allowlist: {flavour}"


def test_tmp_copies_are_not_allowlisted(tmp_path) -> None:
    """…но временные копии — не должны, иначе полоса scan зелена «по пути»."""
    patterns = [
        re.compile(p)
        for p in tomllib.loads(CONFIG.read_text(encoding="utf-8"))["allowlist"]["paths"]
    ]
    for case in CASES:
        probe = str(tmp_path / case.case / case.as_name)
        assert not any(
            p.search(probe) for p in patterns
        ), f"{case.case}: временный путь попал под allowlist — приёмка бы зеленела «по пути»"


def test_corpus_findings_are_all_declared(scan: dict[str, list[dict]]) -> None:
    """В allowlist'нутом файле не должно завестись ничего, кроме объявленного.

    Файл выведен из-под гейта по пути — значит настоящий секрет, случайно
    вставленный сюда вместо синтетики, гейт бы не поймал. Эта проверка и есть
    замена гейта на этом файле: каждая находка обязана сводиться либо к
    ``@secret`` какой-нибудь записи, либо к payload записи с вердиктом ``fp``.
    """
    declared = [c.secret for c in CASES if c.secret != "-"]
    fp_payloads = "\n".join(c.payload for c in CASES if "fp" in (c.expect_scan, c.expect_redact))
    stray = [
        f
        for f in scan["_whole"]
        if not any(f["Secret"] in d or d in f["Match"] for d in declared)
        and f["Secret"] not in fp_payloads
    ]
    assert not stray, (
        "в корпусе найдено значение, не объявленное ни одной записью — "
        f"строки {[f['StartLine'] for f in stray]}, правила {[f['RuleID'] for f in stray]}"
    )
