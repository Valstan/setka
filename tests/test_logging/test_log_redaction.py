"""Маскирование секретов в логах (инцидент 2026-08-12 — угон Telegram-ботов).

Ключевой тест здесь — ``test_requests_exception_text_is_redacted``: он
воспроизводит ровно ту строку, которой токен утёк в прод-лог. Остальные
проверяют, что маскирование не съедает полезное и не зависит от того, кто
владеет логгером.
"""

from __future__ import annotations

import logging

import pytest

from utils.log_redaction import (
    REDACTED,
    _reset_for_tests,
    collect_secret_values,
    install_log_redaction,
    redact,
)

# Синтетические значения того же ФОРМАТА, что настоящие. Реальных секретов в
# тестах нет и быть не может — иначе тест сам станет каналом утечки.
FAKE_TG_TOKEN = "5945194659:AAHplaceholderplaceholderplaceholder00"
FAKE_VK_TOKEN = "vk1.a.placeholderplaceholderplaceholder"


@pytest.fixture
def restore_factory():
    """Чистая фабрика записей на время теста.

    Сброс на ``logging.LogRecord`` обязателен, а не косметика: фабрика —
    глобальное состояние процесса, и любой тест, импортировавший
    ``tasks.celery_app`` или ``main``, уже поставил её на import-е — с
    РЕАЛЬНЫМИ значениями из окружения. Без сброса тесты ниже проверяли бы
    чужую установку, ``monkeypatch.setenv`` на неё не влиял бы, и слой
    «по значению» остался бы непокрытым, маскируясь зелёным слоем «по форме».
    """
    previous = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    yield
    _reset_for_tests(previous)


def _capture(caplog, level=logging.WARNING):
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


# ─── слой «по форме» ─────────────────────────────────────────────────────────


def test_telegram_token_redacted_but_bot_id_kept():
    out = redact(f"sending via {FAKE_TG_TOKEN}")
    assert FAKE_TG_TOKEN not in out
    # Числовой id — публичная часть: по нему опознаётся, КАКОЙ бот засветился.
    assert "5945194659" in out
    assert REDACTED in out


def test_url_with_token_in_path_redacted():
    text = f"Max retries exceeded with url: /bot{FAKE_TG_TOKEN}/sendMessage"
    out = redact(text)
    assert "AAHplaceholder" not in out
    assert "sendMessage" in out  # диагностика выживает


@pytest.mark.parametrize(
    "text,secret",
    [
        (f"https://api.vk.com/method/wall.post?access_token={FAKE_VK_TOKEN}", FAKE_VK_TOKEN),
        ("GET /api/secrets api_key=supersecretvalue123", "supersecretvalue123"),
        ("Authorization: Bearer abcdef0123456789xyz", "abcdef0123456789xyz"),
        ("postgresql://setka:hunter2hunter2@localhost:5432/setka", "hunter2hunter2"),
    ],
)
def test_common_secret_shapes_are_redacted(text, secret):
    out = redact(text)
    assert secret not in out
    assert REDACTED in out


def test_plain_text_is_untouched():
    text = "публикация сводки mi прошла: 12 постов, 3 вложения"
    assert redact(text) == text


# ─── слой «по значению» ──────────────────────────────────────────────────────


def test_collect_secret_values_picks_secrets_and_skips_config():
    env = {
        "TELEGRAM_TOKEN_AFONYA": FAKE_TG_TOKEN,
        "VK_TOKEN_VALSTAN": FAKE_VK_TOKEN,
        "VMALMYZHE_INGEST_KEY": "ingestkeyvalue1234",
        # не секреты, хотя имя подходит под правило
        "VK_PUBLISH_TOKEN_NAME": "VALSTAN",
        "RADAR_BOT_NAME": "KARMAN",
        # слишком короткое, чтобы маскировать без ложных срабатываний
        "SOME_KEY": "on",
        "CONVEYOR_SITES": "vmalmyzhe",
    }
    values = collect_secret_values(env)
    assert FAKE_TG_TOKEN in values
    assert FAKE_VK_TOKEN in values
    assert "ingestkeyvalue1234" in values
    assert "VALSTAN" not in values
    assert "KARMAN" not in values
    assert "on" not in values
    assert "vmalmyzhe" not in values


def test_longest_secret_replaced_first():
    """Короткий секрет — подстрока длинного: замена в обратном порядке разрезала
    бы длинный и оставила хвост в логе."""
    env = {"A_KEY": "abcdef123456", "B_KEY": "abcdef123456789extra"}
    values = collect_secret_values(env)
    out = redact("value=abcdef123456789extra", values)
    assert "abcdef" not in out


def test_value_of_unknown_shape_is_redacted():
    """Слой «по значению» ловит секрет, под который у нас нет регулярки."""
    env = {"CLOUDFLARE_API_TOKEN": "zzz-not-a-recognised-shape-zzz"}
    out = redact("cf call failed with zzz-not-a-recognised-shape-zzz", collect_secret_values(env))
    assert "not-a-recognised-shape" not in out


# ─── интеграция с logging ────────────────────────────────────────────────────


def test_requests_exception_text_is_redacted(restore_factory, caplog, monkeypatch):
    """Дословное воспроизведение прод-утечки 2026-08-12.

    ``requests`` кладёт полный URL в текст исключения, а URL Bot API содержит
    токен в пути. Строка ``logger.warning(f"...: {e}")`` записала его в
    celery-worker.log.
    """
    monkeypatch.setenv("TELEGRAM_TOKEN_AFONYA", FAKE_TG_TOKEN)
    install_log_redaction()
    log = logging.getLogger("test.telegram")

    err = Exception(
        "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded "
        f"with url: /bot{FAKE_TG_TOKEN}/sendMessage"
    )
    with caplog.at_level(logging.WARNING):
        log.warning(f"telegram send failed: {err}")

    joined = "\n".join(_capture(caplog))
    assert "AAHplaceholder" not in joined
    assert "telegram send failed" in joined


def test_percent_args_are_redacted(restore_factory, caplog, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN_AFONYA", FAKE_TG_TOKEN)
    install_log_redaction()
    log = logging.getLogger("test.args")
    with caplog.at_level(logging.WARNING):
        log.warning("send failed: %s", f"/bot{FAKE_TG_TOKEN}/sendMessage")
    assert "AAHplaceholder" not in "\n".join(_capture(caplog))


def test_traceback_is_redacted(restore_factory, caplog, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN_AFONYA", FAKE_TG_TOKEN)
    install_log_redaction()
    log = logging.getLogger("test.traceback")

    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError(f"connect to /bot{FAKE_TG_TOKEN}/getUpdates failed")
        except RuntimeError:
            log.exception("telegram call crashed")

    record = caplog.records[-1]
    formatted = logging.Formatter("%(message)s").format(record)
    assert "AAHplaceholder" not in formatted


def test_third_party_logger_is_covered(restore_factory, caplog, monkeypatch):
    """Фабрика записей покрывает и чужие логгеры (uvicorn/httpx/celery) —
    ради этого она выбрана вместо фильтра на наших хендлерах."""
    monkeypatch.setenv("TELEGRAM_TOKEN_AFONYA", FAKE_TG_TOKEN)
    install_log_redaction()
    # Уровень задаём ИМЕННО на этом логгере: прогон всей пачки тестов оставляет
    # httpx на WARNING, records не доезжают, и проверка «секрета нет» проходит
    # на пустом выводе. Тест, зелёный от отсутствия данных, ничего не охраняет.
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info("HTTP Request: POST /bot%s/sendMessage", FAKE_TG_TOKEN)
    joined = "\n".join(_capture(caplog, logging.INFO))
    assert joined, "лог пуст — тест не проверил ничего"
    assert "AAHplaceholder" not in joined


def test_install_is_idempotent(restore_factory):
    assert install_log_redaction() is True
    assert install_log_redaction() is False


# ─── Инцидент 2026-08-19: секрет приехал в лог ОБЪЕКТОМ, не строкой ──────────


class _UrlLike:
    """Заменитель ``httpx.URL``: не строка, но её ``__str__`` несёт токен.

    Именно так утёк VK-токен: ``httpx`` пишет
    ``logger.info("HTTP Request: %s %s", request.method, request.url)``, где
    ``request.url`` — объект. Прежний ``_redact_arg`` возвращал не-строки как
    есть, а склейка в ``record.getMessage()`` происходит ПОСЛЕ фабрики — то
    есть после единственного места, где маскирование вообще случается.
    """

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def __str__(self) -> str:  # noqa: D105
        return self._raw


def test_object_arg_carrying_secret_is_redacted(restore_factory, caplog):
    """Регресс на реальную форму утечки: объект-URL с ``access_token`` в query."""
    install_log_redaction()
    url = _UrlLike(
        f"https://api.vk.com/method/messages.send?peer_id=1&access_token={FAKE_VK_TOKEN}"
    )
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET", url, "200 OK")

    joined = "\n".join(_capture(caplog, logging.INFO))
    assert joined, "лог пуст — тест не проверил ничего"
    assert "placeholderplaceholder" not in joined
    assert REDACTED in joined
    # Полезное содержимое строки не съедено — иначе лог перестанет быть логом.
    assert "api.vk.com" in joined and "200 OK" in joined


def test_object_arg_secret_masked_without_env_value(restore_factory, caplog, monkeypatch):
    """Слой «по форме» обязан сработать сам: community-токены живут в БД, а не
    в окружении, поэтому слой «по значению» их значений не знает. В инциденте
    промахнулись оба слоя сразу — этот тест держит второй."""
    monkeypatch.delenv("VK_TOKEN_VALSTAN", raising=False)
    install_log_redaction()
    url = _UrlLike(f"https://api.vk.com/method/wall.post?access_token={FAKE_VK_TOKEN}&v=5.199")
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info("HTTP Request: %s", url)
    joined = "\n".join(_capture(caplog, logging.INFO))
    assert joined, "лог пуст — тест не проверил ничего"
    assert "placeholderplaceholder" not in joined
    assert REDACTED in joined


def test_numeric_arg_survives_percent_d(restore_factory, caplog):
    """Не-строки, чья форма секрета не содержит, остаются собой — иначе ``%d``
    получил бы ``str`` и запись упала бы на форматировании."""
    install_log_redaction()
    with caplog.at_level(logging.INFO, logger="test.numeric"):
        logging.getLogger("test.numeric").info("posts=%d rate=%.2f", 200, 0.5)
    assert "posts=200 rate=0.50" in "\n".join(_capture(caplog, logging.INFO))
