#!/usr/bin/env python3
"""Живой VK-probe для блока B2 рекламного кабинета (предложка → отложка in-place).

Отвечает на два вопроса, которые нельзя проверить без живого VK (см.
``docs/SESSION_HANDOFF.md`` «B2 требует VK-probe»):

1. ``wall.edit`` + ``publish_date`` на ПРЕДЛОЖЕННОМ посте — **планирует** его в
   «Отложенные записи» или **публикует сразу** на стену?
2. Сохраняется ли атрибуция автора («Предложил(а): Имя Фамилия» —
   ``signer_id``/``from_id``) после такой правки?

Запускается на проде (там токены + БД). **Безопасен по умолчанию:**

* без аргумента ``--post`` — только ЧИТАЕТ предложку группы и печатает список
  кандидатов (никаких записей в VK);
* с ``--post`` без ``--apply`` — dry-run: печатает ровно те параметры
  ``wall.edit``, которые ушли бы в VK, и выходит;
* с ``--post --apply`` — РЕАЛЬНО редактирует пост. Требует переменную окружения
  ``SETKA_PROBE_CONFIRM=yes`` (двойной предохранитель). Дату публикации берёт
  далеко в будущем (по умолчанию +24ч), чтобы пост ушёл в отложку, а не на
  стену; затем читает пост обратно (``wall.getById``) и печатает вердикт.
* ``--revert`` после ``--apply`` — удаляет пост (``wall.delete``), чтобы убрать
  следы probe (и из отложки, и со стены если VK всё-таки опубликовал сразу).

Примеры (на проде через `ssh setka`):

    # 1) посмотреть, что лежит в предложке Малмыжа (read-only)
    python3 scripts/probe_wall_edit_publish_date.py --group -158787639

    # 2) dry-run по конкретному посту — что ушло бы в wall.edit
    python3 scripts/probe_wall_edit_publish_date.py --group -158787639 --post 12345

    # 3) живой probe (+24ч в отложку), затем убрать след
    SETKA_PROBE_CONFIRM=yes python3 scripts/probe_wall_edit_publish_date.py \
        --group -158787639 --post 12345 --apply --revert
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

MSK = timezone(timedelta(hours=3))


def _attachment_strings(item: Dict[str, Any]) -> List[str]:
    """Собрать attachment-строки (``photo<o>_<id>``, …) из предложенного поста.

    ``wall.edit`` замещает пост целиком — чтобы не обнулить медиа, их нужно
    передать обратно. Поддержаны основные типы; ссылки/неизвестное опускаем
    (для probe не критично, текст важнее).
    """
    out: List[str] = []
    for att in item.get("attachments") or []:
        t = att.get("type")
        obj = att.get(t) if t else None
        if not isinstance(obj, dict):
            continue
        owner = obj.get("owner_id")
        oid = obj.get("id")
        if owner is None or oid is None:
            continue
        token = f"{t}{owner}_{oid}"
        access = obj.get("access_key")
        if access:
            token += f"_{access}"
        if t in ("photo", "video", "audio", "doc"):
            out.append(token)
    return out


def _post_type_verdict(post: Optional[Dict[str, Any]]) -> str:
    if not post:
        return "❓ не удалось прочитать пост обратно (wall.getById пусто)"
    ptype = post.get("post_type")
    if ptype == "postponed":
        return "✅ ЗАПЛАНИРОВАН (post_type=postponed) — ушёл в «Отложенные записи»"
    if ptype == "suggest":
        return "⚠️ остался ПРЕДЛОЖЕННЫМ (post_type=suggest) — publish_date не сработал"
    if ptype == "post":
        return "❌ ОПУБЛИКОВАН СРАЗУ (post_type=post) — publish_date проигнорирован!"
    return f"❓ неожиданный post_type={ptype!r}"


def _attribution_verdict(post: Optional[Dict[str, Any]]) -> str:
    if not post:
        return "—"
    signer = post.get("signer_id")
    from_id = post.get("from_id")
    if signer:
        return f"✅ атрибуция СОХРАНЕНА (signer_id={signer}, from_id={from_id})"
    return (
        f"⚠️ signer_id отсутствует (from_id={from_id}) — проверь в браузере, "
        "осталась ли подпись «Предложил(а): …»"
    )


async def _read_back(api, owner_id: int, post_id: int) -> Optional[Dict[str, Any]]:
    """Прочитать пост через wall.getById (sync vk_api в потоке)."""

    def call():
        res = api.wall.getById(posts=f"{owner_id}_{post_id}")
        items = res.get("items") if isinstance(res, dict) else res
        return (items or [None])[0]

    return await asyncio.to_thread(call)


async def main() -> int:
    ap = argparse.ArgumentParser(description="VK-probe wall.edit+publish_date на предложке (B2)")
    ap.add_argument("--group", type=int, required=True, help="VK group id (можно с минусом)")
    ap.add_argument("--post", type=int, default=None, help="id предложенного поста")
    ap.add_argument("--apply", action="store_true", help="реально выполнить wall.edit")
    ap.add_argument("--revert", action="store_true", help="после probe удалить пост (wall.delete)")
    ap.add_argument(
        "--minutes-ahead",
        type=int,
        default=24 * 60,
        help="на сколько минут вперёд планировать (default 1440 = +24ч)",
    )
    args = ap.parse_args()

    from modules.notifications.vk_suggested_checker import VKSuggestedChecker
    from modules.vk_token_router import load_vk_routing

    owner_id = -abs(int(args.group))
    gid_abs = abs(int(args.group))

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        print("❌ нет годного user-токена (load_vk_routing вернул None)")
        return 1

    checker = VKSuggestedChecker(user_token, community_tokens=community_tokens)
    suggested = checker.fetch_suggested_posts(owner_id)

    if not args.post:
        print(f"📬 предложка группы {owner_id}: {len(suggested)} постов\n")
        for p in suggested:
            txt = (p.get("text") or "").replace("\n", " ")[:70]
            print(
                f"  post={p.get('vk_post_id')}  "
                f"author={p.get('author_name') or p.get('author_vk_id')}  "
                f"signer={p.get('signer_id')}  ads={p.get('marked_as_ads')}  "
                f"«{txt}»"
            )
        print(
            "\nДальше: --post <id> для dry-run, "
            "затем SETKA_PROBE_CONFIRM=yes ... --apply [--revert] для живого probe."
        )
        return 0

    target = next((p for p in suggested if int(p.get("vk_post_id")) == int(args.post)), None)
    if not target:
        print(f"❌ пост {args.post} не найден в предложке {owner_id} (опубликован/удалён?)")
        return 1

    message = target.get("text") or ""
    attachments = _attachment_strings(target)
    when = datetime.now(tz=MSK) + timedelta(minutes=args.minutes_ahead)
    publish_unix = int(when.timestamp())

    print("── ПЛАН wall.edit ──")
    print(f"  owner_id      = {owner_id}")
    print(f"  post_id       = {args.post}")
    print(
        f"  publish_date  = {publish_unix}  ({when:%Y-%m-%d %H:%M} МСК, +{args.minutes_ahead}мин)"
    )
    print(f"  message       = «{message[:80]}»")
    print(f"  attachments   = {attachments or '— (нет/не восстановлены)'}")
    print(f"  signer (было) = {target.get('signer_id')}  from_id={target.get('author_vk_id')}")

    if not args.apply:
        print("\n(dry-run — ничего не отправлено. Добавь --apply + SETKA_PROBE_CONFIRM=yes)")
        return 0

    if os.environ.get("SETKA_PROBE_CONFIRM") != "yes":
        print("\n⛔ --apply без SETKA_PROBE_CONFIRM=yes — отказ (предохранитель).")
        return 2

    # Прямой vk_api-вызов (без VKPublisher.edit_post) — probe самодостаточен и
    # не требует задеплоенного seam'а. Пишем community-токеном целевой группы
    # (владелец стены), с fallback на user-токен при ошибке прав (15/27).
    import vk_api
    from vk_api.exceptions import ApiError

    comm_token = community_tokens.get(gid_abs)
    edit_params = {
        "owner_id": owner_id,
        "post_id": int(args.post),
        "message": message,
        "publish_date": publish_unix,
    }
    if attachments:
        edit_params["attachments"] = ",".join(attachments)

    def _do_edit(token):
        api = vk_api.VkApi(token=token).get_api()
        return api.wall.edit(**edit_params)

    print("\n→ выполняю wall.edit …")
    write_token = comm_token or user_token
    try:
        res = await asyncio.to_thread(_do_edit, write_token)
        print(f"  wall.edit → {res}  (via {'community' if comm_token else 'user'}-token)")
    except ApiError as e:
        # права community-токена не позволяют — повторяем user-токеном
        if comm_token and getattr(e, "code", None) in (15, 27):
            print(f"  community-token отказал ([{e.code}]) → пробую user-token …")
            try:
                res = await asyncio.to_thread(_do_edit, user_token)
                write_token = user_token
                print(f"  wall.edit → {res}  (via user-token)")
            except ApiError as e2:
                print(f"❌ wall.edit упал и user-токеном: {e2}")
                return 1
        else:
            print(f"❌ wall.edit упал: {e}")
            return 1

    # Читаем обратно тем же токеном, что писали.
    api = vk_api.VkApi(token=write_token).get_api()
    post = await _read_back(api, owner_id, int(args.post))

    print("\n── ВЕРДИКТ ──")
    print(" 1) планирование:", _post_type_verdict(post))
    print(" 2) атрибуция:   ", _attribution_verdict(post))
    if post:
        print(
            f"    raw: post_type={post.get('post_type')} signer_id={post.get('signer_id')} "
            f"from_id={post.get('from_id')} date={post.get('date')} "
            f"marked_as_ads={post.get('marked_as_ads')}"
        )

    if args.revert:
        print("\n→ revert: wall.delete (убираю след probe) …")
        try:
            d = await asyncio.to_thread(
                lambda: vk_api.VkApi(token=write_token)
                .get_api()
                .wall.delete(owner_id=owner_id, post_id=int(args.post))
            )
            print(f"  wall.delete → {d}")
        except ApiError as e:
            print(f"⚠️ wall.delete упал: {e} — убери пост вручную (post {args.post}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
