#!/usr/bin/env python3
"""Probe: стабильность/полнота парсинга `t.me/s/<канал>` (контент-радар Ф0).

Probe-before-build (#020), директива brain 2026-06-11 (content-radar kickoff):
ДО постройки TG-source-адаптера проверяем, что web-превью `t.me/s/` реально
отдаёт с нашей машины и с прод-VPS (RU-хостинг — RKN-блокировки Telegram
могут резать именно серверный IP, это ключевой вопрос probe):

  1. Доступность и редиректы (канал без превью редиректит на t.me/<ch>).
  2. Полнота: сколько сообщений на странице, есть ли id/дата/текст/медиа.
  3. Пагинация: `?before=<id>` отдаёт более старые сообщения.
  4. Rate-limit: burst одинаковых запросов — когда появляется 429/капча.

Read-only, stdlib-only (urllib + re) — запускается на проде голым python3
без зависимостей: `ssh setka "python3 -" < scripts/probe_tme_s_parsing.py`.

Примеры:

    # дефолтный набор каналов
    python scripts/probe_tme_s_parsing.py

    # свои каналы + глубина пагинации + burst
    python scripts/probe_tme_s_parsing.py --channels tass_agency,malmyzh_info \
        --pages 3 --burst 8
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# Каналы по умолчанию: свои (malmyzh_info — туда же зеркалим дайджесты mi,
# gonba_life — поток B) + высокочастотный публичный + заведомо несуществующий.
DEFAULT_CHANNELS = ["malmyzh_info", "gonba_life", "tass_agency", "no_such_channel_zzz_404"]

MSG_RE = re.compile(r'data-post="(?P<ch>[^/"]+)/(?P<id>\d+)"')
PHOTO_RE = re.compile(r"tgme_widget_message_photo_wrap")
VIDEO_RE = re.compile(r"tgme_widget_message_video_player|tgme_widget_message_video_wrap")
DOC_RE = re.compile(r"tgme_widget_message_document")
DATE_RE = re.compile(r'<time datetime="([^"]+)"')
TEXT_RE = re.compile(r"tgme_widget_message_text")


def fetch(url: str, timeout: int = 15) -> dict:
    """GET url → {status, final_url, body, elapsed}; ошибки — в status/error."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": resp.status,
                "final_url": resp.geturl(),
                "body": body,
                "elapsed": round(time.monotonic() - started, 2),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "final_url": url,
            "body": "",
            "elapsed": round(time.monotonic() - started, 2),
        }
    except Exception as exc:  # noqa: BLE001 - probe: любой сбой = факт для отчёта
        return {
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "final_url": url,
            "body": "",
            "elapsed": round(time.monotonic() - started, 2),
        }


def parse_page(body: str) -> dict:
    """Вынуть из HTML страницы факты: ids, медиа, даты, текстовые блоки."""
    ids = sorted(int(m.group("id")) for m in MSG_RE.finditer(body))
    return {
        "messages": len(ids),
        "min_id": ids[0] if ids else None,
        "max_id": ids[-1] if ids else None,
        "photos": len(PHOTO_RE.findall(body)),
        "videos": len(VIDEO_RE.findall(body)),
        "documents": len(DOC_RE.findall(body)),
        "text_blocks": len(TEXT_RE.findall(body)),
        "datetimes": len(DATE_RE.findall(body)),
    }


def probe_channel(channel: str, pages: int) -> dict:
    """Доступность + полнота + пагинация одного канала."""
    base = f"https://t.me/s/{channel}"
    out: dict = {"channel": channel, "pages": []}
    res = fetch(base)
    redirected_away = "final_url" in res and "/s/" not in res["final_url"]
    page = {
        "url": base,
        "status": res.get("status"),
        "error": res.get("error"),
        "elapsed": res["elapsed"],
        "redirected_to": res["final_url"] if redirected_away else None,
        **(parse_page(res["body"]) if res.get("body") else {}),
    }
    out["pages"].append(page)

    # Пагинация вглубь: ?before=<min_id> должен отдавать строго более старые id.
    before = page.get("min_id")
    for _ in range(pages - 1):
        if not before:
            break
        res = fetch(f"{base}?before={before}")
        parsed = parse_page(res["body"]) if res.get("body") else {}
        parsed.update(
            url=f"{base}?before={before}",
            status=res.get("status"),
            error=res.get("error"),
            elapsed=res["elapsed"],
            older_ok=bool(parsed.get("max_id") and parsed["max_id"] < before),
        )
        out["pages"].append(parsed)
        before = parsed.get("min_id")
    return out


def probe_burst(channel: str, n: int) -> dict:
    """Burst подряд без пауз — ловим rate-limit/капчу (429/302/некорректный HTML)."""
    statuses, timings = [], []
    for _ in range(n):
        res = fetch(f"https://t.me/s/{channel}")
        statuses.append(res.get("status") or res.get("error"))
        timings.append(res["elapsed"])
    return {
        "channel": channel,
        "requests": n,
        "statuses": statuses,
        "avg_elapsed": round(sum(timings) / len(timings), 2),
        "max_elapsed": max(timings),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe t.me/s/<channel> parsing")
    parser.add_argument("--channels", default=",".join(DEFAULT_CHANNELS))
    parser.add_argument("--pages", type=int, default=3, help="глубина пагинации")
    parser.add_argument("--burst", type=int, default=8, help="размер burst-теста")
    parser.add_argument("--burst-channel", default="tass_agency")
    args = parser.parse_args()

    report: dict = {"probe": "tme_s_parsing", "channels": [], "burst": None}
    for channel in [c.strip() for c in args.channels.split(",") if c.strip()]:
        report["channels"].append(probe_channel(channel, args.pages))

    if args.burst > 0:
        report["burst"] = probe_burst(args.burst_channel, args.burst)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
