"""postopus_digest_headers fallbacks."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.publisher.postopus_digest_headers import (
    resolve_digest_header,
    resolve_digest_hashtags,
)


def test_resolve_header_fallback_sport_russian():
    rc = SimpleNamespace(zagolovki={}, heshteg_local={"raicentr": "лебяжье"})
    region = SimpleNamespace(name="Лебяжье", code="leb")
    h = resolve_digest_header(rc, "sport", region)
    assert "Спортивные новости" in h
    assert "Лебяжье" in h


def test_resolve_hashtags_fallback_combined_and_local():
    rc = SimpleNamespace(heshteg={}, heshteg_local={"raicentr": "лебяжье"})
    tags, loc = resolve_digest_hashtags(rc, "sport")
    assert "спорт" in tags[0].lower() or "спорт" in tags[0]
    assert "лебяжье" in tags[0].lower()
    assert loc == "#лебяжье"
