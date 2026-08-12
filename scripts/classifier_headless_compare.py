#!/usr/bin/env python3
"""Сверка headless-классификатора с облачной рутиной на ОДНИХ И ТЕХ ЖЕ постах.

Шаг приёмки перед выключением рутины (D-024). Берёт N последних вердиктов
рутины, переклассифицирует те же посты через DeepSeek и считает совпадение по
аспектам — раздельно по ``action`` и ``theme``, как это делает ``agree_rate``
для оператора.

**Ничего не пишет.** Ни одной строки в ``content_classifications``: сверка не
должна ни занимать посты, ни менять то, что enforce блокирует прямо сейчас.

Почему сверка нужна отдельным шагом, а не «включим и посмотрим»: вердикты
гейтят публикацию (``CLASSIFIER_ENFORCE_ENABLED=1``), и расхождение по
``action`` — это буквально разница в том, что прочитают подписчики. Совпадение
по ``theme`` мягче: тема влияет на группировку и статистику, а не на выпуск.

Важно про «эталон»: рутина — не истина, а действующий производитель. Задача
сверки — увидеть РАЗМЕР и ХАРАКТЕР расхождения, а не выставить оценку. Строки
с расхождением печатаются целиком, чтобы решал человек.

Запуск на проде:
  sudo bash -c 'set -a; . /etc/setka/setka.env; . /etc/setka/secrets-token.env; set +a;
    cd /home/valstan/SETKA && exec ./venv/bin/python
    scripts/classifier_headless_compare.py --limit 40'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from typing import Any, Dict, List

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from modules.secrets_bootstrap import bootstrap_secrets  # noqa: E402

bootstrap_secrets()

from sqlalchemy import select  # noqa: E402

# Импорт ради побочного эффекта, а не ради имени (оттого F401).
# models_extended ссылается на 'Region' СТРОКОЙ в relationship(); реестр
# SQLAlchemy разрешает такие имена лениво — при первом запросе, — и без
# загруженного database.models падает там же:
#   InvalidRequestError: expression 'Region' failed to locate a name.
# Позиция строки роли не играет (запрос уходит позже любого импорта), поэтому
# isort волен ставить её куда хочет; важен сам факт импорта. Грабля общая для
# всех ad-hoc скриптов проекта — записана в SESSION_HANDOFF.
import database.models  # noqa: E402,F401
from config.classifier import get_headless_chunk_size  # noqa: E402
from database.connection import AsyncSessionLocal  # noqa: E402
from database.models_extended import ContentClassification  # noqa: E402
from modules.classifier import headless, rules  # noqa: E402


async def _load_routine_verdicts(session, limit: int, source: str) -> List[Dict[str, Any]]:
    """Последние вердикты указанного источника вместе со снапшотом поста."""
    rows = (
        (
            await session.execute(
                select(ContentClassification)
                .where(ContentClassification.source == source)
                .order_by(ContentClassification.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "lip": r.lip,
            "region_code": r.region_code,
            "text": r.post_text or "",
            "url": r.post_url or "",
            "theme": (r.verdict or {}).get("theme"),
            "action": (r.verdict or {}).get("action"),
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _report(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    action_hits = sum(1 for p in pairs if p["routine_action"] == p["headless_action"])
    theme_hits = sum(1 for p in pairs if p["routine_theme"] == p["headless_theme"])
    n = len(pairs) or 1
    confusion = Counter((p["routine_action"], p["headless_action"]) for p in pairs)
    return {
        "compared": len(pairs),
        "action_agree_pct": round(100.0 * action_hits / n, 1),
        "theme_agree_pct": round(100.0 * theme_hits / n, 1),
        "action_confusion": {f"{a}->{b}": c for (a, b), c in sorted(confusion.items())},
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="сколько вердиктов сверять")
    ap.add_argument("--source", default="routine", help="источник-эталон (по умолчанию routine)")
    ap.add_argument("--chunk", type=int, default=0, help="постов в вызове (0 = из env)")
    ap.add_argument("--show", type=int, default=15, help="сколько расхождений напечатать")
    args = ap.parse_args()

    async with AsyncSessionLocal() as session:
        reference = await _load_routine_verdicts(session, args.limit, args.source)
        if not reference:
            print(f"нет вердиктов источника {args.source!r} — сверять не с чем")
            return 1
        postulates = await rules.render_effective_postulates(session)

    posts = [r for r in reference if headless.has_text(r)]
    print(
        "эталон: %d вердиктов (%s), с текстом: %d, без текста: %d (headless их пропускает)"
        % (len(reference), args.source, len(posts), len(reference) - len(posts))
    )

    run = headless.classify_posts(
        posts,
        postulates=postulates,
        chunk_size=args.chunk or get_headless_chunk_size(),
    )
    print("headless:", headless.summarize(run))
    if run["failures"]:
        print("отказы чанков:", run["failures"])

    by_lip = {r["lip"]: r for r in reference}
    pairs = []
    for v in run["verdicts"]:
        ref = by_lip.get(v.lip)
        if not ref:
            continue
        pairs.append(
            {
                "lip": v.lip,
                "region": ref["region_code"],
                "routine_action": ref["action"],
                "headless_action": v.normalized_action(),
                "routine_theme": ref["theme"],
                "headless_theme": v.theme.strip(),
                "confidence": v.confidence,
                "reasoning": v.reasoning,
                "text": (ref["text"] or "")[:180],
            }
        )

    if not pairs:
        print("сопоставить не удалось ни одного поста")
        return 1

    print("\n" + json.dumps(_report(pairs), ensure_ascii=False, indent=2))

    diffs = [p for p in pairs if p["routine_action"] != p["headless_action"]]
    print("\nрасхождений по action: %d из %d" % (len(diffs), len(pairs)))
    for p in diffs[: args.show]:
        print("-" * 70)
        print(
            "%s [%s] рутина=%s  headless=%s (conf %s)"
            % (p["lip"], p["region"], p["routine_action"], p["headless_action"], p["confidence"])
        )
        print("  тема: рутина=%r headless=%r" % (p["routine_theme"], p["headless_theme"]))
        print("  довод headless: %s" % p["reasoning"])
        print("  текст: %s" % p["text"].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
