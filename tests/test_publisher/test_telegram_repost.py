"""Unit tests for Telegram repost (Flow A renderer/transport/media resolution)."""

import config.runtime as runtime
import modules.publisher.telegram_repost as tr
from modules.publisher.telegram_repost import (
    ResolvedMedia,
    clean_text_for_telegram,
    mirror_digest_to_telegram,
    repost_to_telegram,
    resolve_media,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"ok": True, "result": {"message_id": 1}}

    def json(self):
        return self._body


class _RecordingPost:
    """Stand-in for requests.post that records calls and returns 200."""

    def __init__(self, status_code=200, body=None):
        self.calls = []
        self.status_code = status_code
        self.body = body

    def __call__(self, url, json=None, timeout=None, **kwargs):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, json))
        return _FakeResp(self.status_code, self.body)

    def methods(self):
        return [m for m, _ in self.calls]


class _FakeVKAsync:
    """Duck-typed VKClientAsync exposing _make_request for video.get."""

    def __init__(self, files):
        self._files = files  # dict passed back as video.files

    async def _make_request(self, method, params):
        return {"items": [{"files": self._files}]}


# --------------------------------------------------------------------------- #
# clean_text_for_telegram
# --------------------------------------------------------------------------- #
def test_clean_text_strips_hashtags_and_links():
    raw = "Заголовок новости\n\n#новости #Малмыж43\nПодробнее https://vk.com/wall-1_2"
    out = clean_text_for_telegram(raw)
    assert "#" not in out
    assert "vk.com" not in out
    assert "Заголовок новости" in out


def test_clean_text_unwraps_wiki_links():
    raw = "Текст [https://vk.com/wall-1_2|Малмыж Инфо] конец"
    out = clean_text_for_telegram(raw)
    assert "Малмыж Инфо" in out
    assert "vk.com" not in out
    assert "[" not in out


def test_clean_text_collapses_blank_lines():
    raw = "a\n\n\n\nb"
    out = clean_text_for_telegram(raw)
    assert out == "a\n\nb"


def test_clean_text_appends_extra_hashtags():
    out = clean_text_for_telegram("Текст", extra_hashtags=["Малмыж", "#Инфо"])
    assert out.endswith("#Малмыж #Инфо")


# --------------------------------------------------------------------------- #
# resolve_media
# --------------------------------------------------------------------------- #
async def test_resolve_media_keeps_mp4_drops_player():
    photo = {
        "owner_id": -1,
        "id": 10,
        "sizes": [
            {"width": 100, "url": "http://s/small.jpg"},
            {"width": 800, "url": "http://s/big.jpg"},
        ],
    }
    post = {
        "attachments": [
            {"type": "photo", "photo": photo},
            {"type": "video", "video": {"owner_id": -1, "id": 20}},
        ]
    }
    client = _FakeVKAsync({"mp4_480": "https://cdn/vid.mp4?x=1"})
    media = await resolve_media(post, client)
    assert media.photos == ["http://s/big.jpg"]
    assert media.videos == ["https://cdn/vid.mp4?x=1"]
    assert media.degraded is False


async def test_resolve_media_drops_embed_player_video():
    post = {"attachments": [{"type": "video", "video": {"owner_id": -1, "id": 20}}]}
    client = _FakeVKAsync(
        {"external": "https://youtube.com/watch?v=x", "player": "https://vk.com/video_ext.php"}
    )
    media = await resolve_media(post, client)
    assert media.videos == []
    assert media.degraded is True


# --------------------------------------------------------------------------- #
# repost_to_telegram dispatch
# --------------------------------------------------------------------------- #
async def test_repost_no_token(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    res = await repost_to_telegram("AFONYA", "@c", "hi", ResolvedMedia())
    assert res["success"] is False
    assert poster.calls == []


async def test_repost_test_mode_no_network(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    res = await repost_to_telegram(
        "AFONYA", "@c", "hi", ResolvedMedia(photos=["u"]), test_mode=True
    )
    assert res["success"] is True
    assert res.get("test_mode") is True
    assert poster.calls == []


async def test_repost_text_only(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    res = await repost_to_telegram("AFONYA", "@c", "только текст", ResolvedMedia())
    assert res["success"] is True
    assert poster.methods() == ["sendMessage"]


async def test_repost_media_group_with_caption(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    media = ResolvedMedia(photos=["p1", "p2"], videos=["v1.mp4"])
    res = await repost_to_telegram("AFONYA", "@c", "короткая подпись", media)
    assert res["success"] is True
    assert poster.methods() == ["sendMediaGroup"]
    _, payload = poster.calls[0]
    assert payload["media"][0]["caption"] == "короткая подпись"
    assert len(payload["media"]) == 3


async def test_repost_media_group_long_text_followup(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    long_text = "x" * (tr.TG_CAPTION_LIMIT + 50)
    media = ResolvedMedia(photos=["p1", "p2"])
    res = await repost_to_telegram("AFONYA", "@c", long_text, media)
    assert res["success"] is True
    # No caption fits → group then a follow-up message.
    assert poster.methods() == ["sendMediaGroup", "sendMessage"]
    assert "caption" not in poster.calls[0][1]["media"][0]


async def test_repost_single_photo(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    res = await repost_to_telegram("AFONYA", "@c", "cap", ResolvedMedia(photos=["p1"]))
    assert res["success"] is True
    assert poster.methods() == ["sendPhoto"]
    assert poster.calls[0][1]["caption"] == "cap"


async def test_repost_429_retry(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})

    seq = [_FakeResp(429, {"parameters": {"retry_after": 0}}), _FakeResp(200)]

    def fake_post(url, json=None, timeout=None, **kwargs):
        return seq.pop(0)

    monkeypatch.setattr("requests.post", fake_post)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(tr.asyncio, "sleep", _no_sleep)
    res = await repost_to_telegram("AFONYA", "@c", "t", ResolvedMedia())
    assert res["success"] is True
    assert seq == []  # both responses consumed (retry happened)


async def test_repost_docs_sent_separately(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    media = ResolvedMedia(photos=["p1"], docs=[{"url": "d1", "filename": "f"}])
    await repost_to_telegram("AFONYA", "@c", "", media)
    assert poster.methods() == ["sendPhoto", "sendDocument"]


# --------------------------------------------------------------------------- #
# mirror_digest_to_telegram (integration)
# --------------------------------------------------------------------------- #
async def test_mirror_digest_builds_clean_message(monkeypatch):
    monkeypatch.setattr(runtime, "TELEGRAM_TOKENS", {"AFONYA": "tok"})
    poster = _RecordingPost()
    monkeypatch.setattr("requests.post", poster)
    posts = [
        {
            "text": "Первая новость #тег https://vk.com/wall-1_2",
            "attachments": [
                {"type": "photo", "photo": {"sizes": [{"width": 600, "url": "http://p/1.jpg"}]}}
            ],
        },
        {"text": "Вторая новость", "attachments": []},
    ]
    client = _FakeVKAsync({})
    res = await mirror_digest_to_telegram(
        "AFONYA",
        "@malmyzh_info",
        "Новости Малмыжа:",
        posts,
        client,
    )
    assert res["success"] is True
    # One photo → sendPhoto with combined clean caption (short).
    assert poster.methods() == ["sendPhoto"]
    _, payload = poster.calls[0]
    cap = payload["caption"]
    assert "Новости Малмыжа:" in cap
    assert "Первая новость" in cap and "Вторая новость" in cap
    assert "#" not in cap and "vk.com" not in cap
