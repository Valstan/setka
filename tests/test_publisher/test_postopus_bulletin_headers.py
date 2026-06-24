"""postopus_bulletin_headers fallbacks."""

from types import SimpleNamespace

from modules.publisher.postopus_bulletin_headers import (
    resolve_bulletin_hashtags,
    resolve_bulletin_header,
    resolve_mourning_bulletin_format,
)


def test_resolve_header_fallback_sport_russian():
    rc = SimpleNamespace(zagolovki={}, heshteg_local={"raicentr": "лебяжье"})
    region = SimpleNamespace(name="Лебяжье", code="leb")
    h = resolve_bulletin_header(rc, "sport", region)
    assert "Спортивные новости" in h
    assert "Лебяжье" in h


def test_resolve_hashtags_fallback_combined_and_local():
    rc = SimpleNamespace(heshteg={}, heshteg_local={"raicentr": "лебяжье"})
    tags, loc = resolve_bulletin_hashtags(rc, "sport")
    assert "спорт" in tags[0].lower() or "спорт" in tags[0]
    assert "лебяжье" in tags[0].lower()
    assert loc == "#лебяжье"


def test_resolve_header_oblast_fallback():
    rc = SimpleNamespace(zagolovki={}, heshteg_local={"raicentr": "киров"})
    region = SimpleNamespace(name="Кировская область", code="kirov_obl")
    h = resolve_bulletin_header(rc, "oblast", region)
    assert "Главное в области" in h
    assert "Кировская область" in h


def test_resolve_mourning_bulletin_format_is_plain_text_only():
    header, tags, local = resolve_mourning_bulletin_format()
    assert header == ""
    assert tags == []
    assert local == ""
