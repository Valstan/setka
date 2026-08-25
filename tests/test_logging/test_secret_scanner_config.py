"""Секрет-сканер и маскирование логов обязаны считать секретом одно и то же.

**Зачем этот файл.** У защиты от утечки секретов две половины: маскирование в
логах (``utils/log_redaction.py``) и гейт на мерже (``.gitleaks.toml``). До
2026-08-20 они расходились молча, и цена расхождения оказалась буквальной:

* редактор логов знал форму ``vk1.a.…`` и форму ``/bot<ТОКЕН>/sendMessage`` —
  их написали по фактической строке из прод-лога после угона ботов 12.08;
* гейт полагался на дефолтный набор gitleaks, где правила под VK нет вовсе, а
  правило под Telegram требует тело ``A[a-z0-9_-]{34}`` (после первой ``A``
  строго строчные) рядом со словом ``telegr`` и оператором присваивания.

У настоящих токенов Bot API после двоеточия всегда ``AA``, поэтому дефолтное
правило не ловило ни один живой токен ни в одной форме. Гейт при этом
рапортовал ``no leaks found`` о репозитории, в истории которого лежали и оба
токена угнанных ботов, и три VK-токена (см. ``.gitleaksignore``).

**Что проверяет тест.** Не текст регулярок — их можно синхронно испортить, — а
поведение на фикстурах реальной формы: каждая обязана и маскироваться, и
ловиться гейтом. Плюс два анти-гниения: ссылки в allowlist ведут на живые
файлы, а подавления в ``.gitleaksignore`` — на существующие правила (гейт
переживал переименования, и мёртвая ссылка тут молчалива).

Значения синтетические и содержат ``placeholder``: так они попадают под
allowlist gitleaks и по пути, и по содержимому — файл остаётся безопасным даже
если запись о нём в allowlist когда-нибудь потеряется.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import pytest

from utils.log_redaction import REDACTED, redact

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".gitleaks.toml"
IGNORE_PATH = ROOT / ".gitleaksignore"

# Тело длиной 35 символов, как у настоящих токенов Bot API, и обязательно с
# ``AA`` после двоеточия — форма, на которой слепнет дефолтное правило.
FAKE_TG_LOWER = "5945194659:AAHplaceholderplaceholderplaceholder"
FAKE_TG_MIXED = "5945194659:AAHplaceholderQWERTYplaceholderZXCV"
FAKE_VK = "vk1.a.placeholderplaceholderplaceholder"

# (ярлык, текст, секрет) — формы, которыми у нас реально утекало.
LEAK_SHAPES = [
    ("присвоение, строчное тело", f'TELEGRAM_BOT_TOKEN = "{FAKE_TG_LOWER}"', FAKE_TG_LOWER),
    ("присвоение, смешанный регистр", f'TOKEN = "{FAKE_TG_MIXED}"', FAKE_TG_MIXED),
    (
        "токен в пути URL (инцидент 12.08)",
        f"Max retries exceeded with url: /bot{FAKE_TG_MIXED}/sendMessage",
        FAKE_TG_MIXED,
    ),
    ("VK в присвоении", f'VK_ACCESS_TOKEN = "{FAKE_VK}"', FAKE_VK),
    (
        "VK в query-строке",
        f"https://api.vk.com/method/wall.get?access_token={FAKE_VK}&v=5.199",
        FAKE_VK,
    ),
    (
        "VK в repr объекта (инцидент 19.08)",
        f"VKToken(community_id=166980909, access_token={FAKE_VK}, valid=True)",
        FAKE_VK,
    ),
]


def _config() -> dict:
    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _own_rules() -> dict[str, re.Pattern[str]]:
    """Регулярки наших правил, скомпилированные питоном.

    gitleaks исполняет их движком Go (RE2); наши шаблоны намеренно держатся в
    подмножестве, понятном обоим, — иначе тест проверял бы не тот язык.
    """
    return {
        rule["id"]: re.compile(rule["regex"])
        for rule in _config().get("rules", [])
        if rule["id"].startswith("setka-")
    }


def test_own_rules_are_present():
    """Правила не должны тихо исчезнуть при правке конфига.

    Список точный, а не «содержит»: правило, выпавшее из конфига, обязано ронять
    тест, а не растворяться в проверке на подмножество.

    ⛔ Правя этот набор, помни: id правил привязаны к отпечаткам в
    ``.gitleaksignore`` (``<коммит>:<файл>:<правило>:<строка>``). Переименование
    обесточит подавления разом, а CI сканирует историю целиком — гейт станет
    красным на каждом PR.
    """
    assert set(_own_rules()) == {
        "setka-telegram-bot-token",
        "setka-vk-access-token",
        # Легаси-формат VK (85 hex) добавлен 2026-08-25 по D-036: в живых данных
        # его больше нет (все 36 токенов на проде — vk1.a./vk2.a., длина 220), но
        # в git-истории он остался, а история сканируется целиком.
        "setka-vk-legacy-hex-token",
    }


@pytest.mark.parametrize("label,text,secret", LEAK_SHAPES, ids=[s[0] for s in LEAK_SHAPES])
def test_leak_shape_is_redacted_in_logs(label, text, secret):
    out = redact(text)
    assert secret not in out, f"{label}: маскирование логов пропустило секрет"
    assert REDACTED in out


@pytest.mark.parametrize("label,text,secret", LEAK_SHAPES, ids=[s[0] for s in LEAK_SHAPES])
def test_leak_shape_is_caught_by_scanner(label, text, secret):
    """Та же форма обязана ловиться гейтом на мерже, а не только в логах."""
    rules = _own_rules()
    matched = [rid for rid, pattern in rules.items() if pattern.search(text)]
    assert matched, f"{label}: ни одно правило .gitleaks.toml не поймало секрет"


def test_default_telegram_rule_alone_would_be_blind():
    """Регресс-сторож на причину дыры, а не на её следствие.

    Дефолтное правило gitleaks требует после двоеточия ``A`` и далее строго
    строчные. Если кто-то однажды решит, что «дефолта достаточно», и снесёт
    наше правило — этот тест напомнит, чем именно дефолт не годится.
    """
    default_like = re.compile(r"[0-9]{5,16}:A[a-z0-9_\-]{34}")
    assert not default_like.search(FAKE_TG_MIXED)
    assert not default_like.search(FAKE_TG_LOWER)
    assert _own_rules()["setka-telegram-bot-token"].search(FAKE_TG_MIXED)


def test_allowlist_paths_point_at_existing_files():
    """Мёртвая ссылка в allowlist молчалива: правило перестаёт что-либо разрешать."""
    for pattern in _config()["allowlist"]["paths"]:
        literal = pattern.replace("\\.", ".")
        if re.search(r"[\[\]()*+?|^$]", literal):
            continue  # настоящая регулярка, а не путь с экранированными точками
        assert (ROOT / literal).exists(), f"allowlist ссылается на несуществующий {literal}"


def test_gitleaksignore_references_known_rules():
    """Подавление по отпечатку молча перестаёт работать при переименовании правила."""
    known = set(rule["id"] for rule in _config().get("rules", []))
    known |= {"generic-api-key", "telegram-bot-api-token"}  # дефолтные, если понадобятся
    for line in IGNORE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        assert len(parts) == 4, f"непонятный отпечаток: {line}"
        assert parts[2] in known, f"отпечаток ссылается на неизвестное правило {parts[2]}"
