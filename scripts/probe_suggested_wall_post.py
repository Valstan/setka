#!/usr/bin/env python3
"""Живой VK-probe: публикация ПРЕДЛОЖЕННОГО поста «как есть» с подписью автора.

Отвечает на вопросы, без которых планировщик предложки (Этап 0 программы
«Кабинет под ключ», план 2026-09-05) строить нельзя — VK-документация для
инструментов недоступна, а поведение ``wall.post(post_id=<suggest>)`` в
сочетании с ``publish_date`` и ``signed`` нигде в проекте не проверялось:

1. ``wall.post(owner_id, post_id=<предложенный>, from_group=1, signed=1,
   publish_date=+N мин)`` user-токеном администратора — пост уходит в
   «Отложенные» (``post_type=postponed``) или публикуется сразу?
2. Какой ``post_id`` возвращает VK — тот же, что у предложенного, или новый?
   (От этого зависит, что писать в ``vk_postponed_post_id`` и что потом
   спрашивать у ``wall.getById``.)
3. Сохраняется ли подпись автора («Предложил(а): Имя» — ``signer_id``) после
   выхода из отложки? Контроль — тот же вызов БЕЗ ``publish_date``
   (``--minutes-ahead 0``): подпись есть сразу?
4. ``wall.repost`` вышедшего поста в другое сообщество от его имени
   (``--repost-to``) — успех и код ошибки, если нет.
5. Что отвечает community-токен на ``wall.post(post_id)`` (``--community-check``)
   — ожидаем 15/27, это обоснование «только user-токен».

Запускается на проде (там токены + БД). **Безопасен по умолчанию:**

* без ``--post`` — только ЧИТАЕТ предложку группы и печатает кандидатов;
* с ``--post`` без ``--apply`` — dry-run: печатает параметры вызова и выходит;
* с ``--post --apply`` — РЕАЛЬНО публикует. Требует ``SETKA_PROBE_CONFIRM=yes``
  (двойной предохранитель). Целиться в тестовый полигон (``-137760500``):
  предложенный пост туда кладёт НЕ-админский аккаунт, админский публикуется
  сразу и в предложку не попадает;
* ``--wait`` — после отложки дождаться выхода (publish_date + 3 мин) и прочитать
  пост ещё раз (подпись после выхода);
* ``--revert`` — удалить пост и репост (``wall.delete``), чтобы убрать следы.

Примеры (на проде через ``ssh sarafan``, из ``~/SETKA``):

    # 1) что лежит в предложке полигона (read-only)
    ./venv/bin/python scripts/probe_suggested_wall_post.py --group -137760500

    # 2) dry-run по конкретному посту
    ./venv/bin/python scripts/probe_suggested_wall_post.py --group -137760500 --post 123

    # 3) живой probe: отложка +5 мин, дождаться выхода, репост в полигон-2, убрать след
    SETKA_PROBE_CONFIRM=yes ./venv/bin/python scripts/probe_suggested_wall_post.py \\
        --group -137760500 --post 123 --apply --minutes-ahead 5 --wait \\
        --repost-to -<другая_тестовая_группа> --revert

    # 4) контроль без отложки (подпись сразу) + проверка community-токена
    SETKA_PROBE_CONFIRM=yes ./venv/bin/python scripts/probe_suggested_wall_post.py \\
        --group -137760500 --post 124 --apply --minutes-ahead 0 --community-check --revert

Значения токенов никогда не печатаются.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

MSK = timezone(timedelta(hours=3))


def _post_type_verdict(post: Optional[Dict[str, Any]], *, expected_postponed: bool) -> str:
    if not post:
        return "❓ не удалось прочитать пост обратно (wall.getById пусто)"
    ptype = post.get("post_type")
    if ptype == "postponed":
        return (
            "✅ ЗАПЛАНИРОВАН (post_type=postponed) — ушёл в «Отложенные записи»"
            if expected_postponed
            else "⚠️ ушёл в отложку, хотя publish_date не передавали"
        )
    if ptype == "suggest":
        return "⚠️ остался ПРЕДЛОЖЕННЫМ (post_type=suggest) — вызов не сработал"
    if ptype == "post":
        return (
            "❌ ОПУБЛИКОВАН СРАЗУ (post_type=post) — publish_date проигнорирован!"
            if expected_postponed
            else "✅ ОПУБЛИКОВАН (post_type=post) — как и ожидалось без publish_date"
        )
    return f"❓ неожиданный post_type={ptype!r}"


def _signature_verdict(post: Optional[Dict[str, Any]], author_id: Optional[int]) -> str:
    if not post:
        return "—"
    signer = post.get("signer_id")
    from_id = post.get("from_id")
    if signer and author_id and int(signer) == int(author_id):
        return f"✅ подпись СОХРАНЕНА (signer_id={signer} = автор предложки)"
    if signer:
        return f"⚠️ signer_id={signer}, но автор предложки был {author_id} — подпись НЕ автора"
    return f"❌ signer_id отсутствует (from_id={from_id}) — подпись автора потеряна"


def _raw(post: Optional[Dict[str, Any]]) -> str:
    if not post:
        return "raw: —"
    return (
        f"raw: id={post.get('id')} post_type={post.get('post_type')} "
        f"signer_id={post.get('signer_id')} from_id={post.get('from_id')} "
        f"owner_id={post.get('owner_id')} date={post.get('date')} "
        f"marked_as_ads={post.get('marked_as_ads')} "
        f"attachments={len(post.get('attachments') or [])}"
    )


async def _read_back(api, owner_id: int, post_id: int) -> Optional[Dict[str, Any]]:
    """Прочитать пост через wall.getById (sync vk_api в потоке)."""

    def call():
        res = api.wall.getById(posts=f"{owner_id}_{post_id}")
        items = res.get("items") if isinstance(res, dict) else res
        return (items or [None])[0]

    try:
        return await asyncio.to_thread(call)
    except Exception as e:  # noqa: BLE001 — probe печатает, а не падает
        print(f"  wall.getById {owner_id}_{post_id} → ошибка: {e}")
        return None


async def _user_candidates(token_name: Optional[str]) -> List[Tuple[str, str]]:
    """Упорядоченные user-кандидаты публикации (имя, токен) из TokenPolicy."""
    from database.connection import AsyncSessionLocal
    from modules.vk_token_router import TokenOp, TokenPolicy

    async with AsyncSessionLocal() as session:
        policy = TokenPolicy(session)
        cands = await policy.pick(TokenOp.COMMUNITY_WRITE)
        users = [(c.name, c.token) for c in cands if c.source == "user"]
        if token_name:
            wanted = token_name.upper()
            active = await policy._load_active()  # noqa: SLF001 — probe, read-only
            row = active.get(wanted)
            if row is None or not row.token:
                raise SystemExit(f"❌ токен {wanted} не активен или отсутствует в /tokens")
            users = [(wanted, row.token)]
    return users


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="VK-probe: wall.post(post_id=<suggest>) + publish_date + signed"
    )
    ap.add_argument("--group", type=int, default=-137760500, help="VK group id (полигон)")
    ap.add_argument("--post", type=int, default=None, help="id предложенного поста")
    ap.add_argument("--apply", action="store_true", help="реально выполнить wall.post")
    ap.add_argument(
        "--minutes-ahead",
        type=int,
        default=5,
        help="отложка на N минут (0 = без publish_date, публикация сразу)",
    )
    ap.add_argument("--wait", action="store_true", help="дождаться выхода и прочитать снова")
    ap.add_argument("--repost-to", type=int, default=None, help="группа для wall.repost")
    ap.add_argument("--community-check", action="store_true", help="попробовать community-токен")
    ap.add_argument("--token-name", default=None, help="принудительно user-токен (VALSTAN/MAMA)")
    ap.add_argument("--revert", action="store_true", help="удалить пост и репост после probe")
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
                f"signer={p.get('signer_id')}  ads={p.get('marked_as_ads')}  «{txt}»"
            )
        print(
            "\nДальше: --post <id> для dry-run, "
            "затем SETKA_PROBE_CONFIRM=yes ... --apply [--wait] [--repost-to] [--revert]."
        )
        return 0

    target = next((p for p in suggested if int(p.get("vk_post_id")) == int(args.post)), None)
    if not target:
        print(f"❌ пост {args.post} не найден в предложке {owner_id} (опубликован/удалён?)")
        return 1
    author_id = target.get("signer_id") or target.get("author_vk_id")

    postponed = args.minutes_ahead > 0
    when = datetime.now(tz=MSK) + timedelta(minutes=args.minutes_ahead)
    params: Dict[str, Any] = {
        "owner_id": owner_id,
        "post_id": int(args.post),
        "from_group": 1,
        "signed": 1,
    }
    if postponed:
        params["publish_date"] = int(when.timestamp())

    candidates = await _user_candidates(args.token_name)
    print("── ПЛАН wall.post ──")
    for k, v in params.items():
        print(f"  {k:13s} = {v}")
    if postponed:
        print(f"  ({when:%Y-%m-%d %H:%M} МСК, +{args.minutes_ahead} мин)")
    print(f"  автор предложки = {author_id} ({target.get('author_name')})")
    print(f"  user-кандидаты  = {[n for n, _ in candidates]}")
    if args.repost_to:
        print(f"  repost-to       = {-abs(int(args.repost_to))}")

    if not args.apply:
        print("\n(dry-run — ничего не отправлено. Добавь --apply + SETKA_PROBE_CONFIRM=yes)")
        return 0
    if os.environ.get("SETKA_PROBE_CONFIRM") != "yes":
        print("\n⛔ --apply без SETKA_PROBE_CONFIRM=yes — отказ (предохранитель).")
        return 2

    import vk_api
    from vk_api.exceptions import ApiError

    # 5) community-токен: ожидаем отказ (15/27) — доказательство «только user».
    if args.community_check:
        comm_token = community_tokens.get(gid_abs)
        if not comm_token:
            print("\n(community-check пропущен: у полигона нет community-токена в /tokens)")
        else:
            print("\n→ community-check: wall.post(post_id) community-токеном …")
            try:
                res = await asyncio.to_thread(
                    lambda: vk_api.VkApi(token=comm_token).get_api().wall.post(**params)
                )
                print(f"  ⚠️ community-токен СМОГ: {res} — модель «только user» неверна!")
            except ApiError as e:
                print(f"  ✅ community-токен отказал: [{getattr(e, 'code', '?')}] {e}")

    # 1-2) публикация user-токеном: перебираем кандидатов, печатаем, кто смог.
    print("\n→ wall.post(post_id) user-токеном …")
    result: Optional[Dict[str, Any]] = None
    used_name: Optional[str] = None
    used_token: Optional[str] = None
    for name, tok in candidates:
        try:
            result = await asyncio.to_thread(lambda t=tok: vk_api.VkApi(token=t).get_api().wall.post(**params))
            used_name, used_token = name, tok
            print(f"  {name}: wall.post → {result}")
            break
        except ApiError as e:
            print(f"  {name}: отказ [{getattr(e, 'code', '?')}] {e}")
    if not result or not used_token:
        print("❌ ни один user-токен не смог опубликовать предложенный пост")
        return 1

    new_post_id = int(result.get("post_id") or 0)
    same = new_post_id == int(args.post)
    print(
        f"  post_id: вернул {new_post_id}, предложенный был {args.post} → "
        + ("ТОТ ЖЕ id" if same else "НОВЫЙ id (в БД хранить возвращённый)")
    )

    api = vk_api.VkApi(token=used_token).get_api()
    post = await _read_back(api, owner_id, new_post_id)
    print("\n── ВЕРДИКТ сразу после вызова ──")
    print(" 1) планирование:", _post_type_verdict(post, expected_postponed=postponed))
    print(" 2) подпись:     ", _signature_verdict(post, author_id))
    print("   ", _raw(post))

    # 3) дождаться выхода и перечитать — подпись после выхода из отложки.
    if postponed and args.wait:
        deadline = when + timedelta(minutes=3)
        print(f"\n→ жду выхода до {deadline:%H:%M:%S} МСК …")
        while datetime.now(tz=MSK) < deadline:
            await asyncio.sleep(20)
            post = await _read_back(api, owner_id, new_post_id)
            if post and post.get("post_type") == "post":
                break
        print("── ВЕРДИКТ после выхода ──")
        print(" 1) состояние:", _post_type_verdict(post, expected_postponed=False))
        print(" 2) подпись:  ", _signature_verdict(post, author_id))
        print("   ", _raw(post))

    # 4) репост в другое сообщество от его имени (только если пост уже вышел).
    repost_id: Optional[int] = None
    repost_owner: Optional[int] = None
    if args.repost_to:
        if not post or post.get("post_type") != "post":
            print("\n(repost пропущен: оригинал ещё не вышел — репостить можно только вышедший пост)")
        else:
            repost_owner = -abs(int(args.repost_to))
            print(f"\n→ wall.repost wall{owner_id}_{new_post_id} → {repost_owner} …")
            try:
                rr = await asyncio.to_thread(
                    lambda: api.wall.repost(
                        object=f"wall{owner_id}_{new_post_id}", group_id=abs(repost_owner)
                    )
                )
                print(f"  wall.repost → {rr}")
                repost_id = int(rr.get("post_id") or 0) or None
                rp = await _read_back(api, repost_owner, repost_id) if repost_id else None
                print("  ", _raw(rp))
                copy = (rp or {}).get("copy_history") or []
                if copy:
                    inner = copy[0]
                    print(
                        f"   внутри репоста: signer_id={inner.get('signer_id')} "
                        f"from_id={inner.get('from_id')} — "
                        + ("✅ подпись автора видна" if inner.get("signer_id") else "❌ подписи нет")
                    )
            except ApiError as e:
                print(f"  ❌ wall.repost отказал: [{getattr(e, 'code', '?')}] {e}")

    if args.revert:
        print("\n→ revert: wall.delete …")
        for o, p in ((repost_owner, repost_id), (owner_id, new_post_id)):
            if not o or not p:
                continue
            try:
                d = await asyncio.to_thread(lambda o=o, p=p: api.wall.delete(owner_id=o, post_id=p))
                print(f"  wall.delete {o}_{p} → {d}")
            except ApiError as e:
                print(f"  ⚠️ wall.delete {o}_{p} упал: {e} — убери вручную")

    print(f"\nИтог: публиковал {used_name}; зафиксируй raw-строки в PENDING (Этап 0.1).")
    return 0


if __name__ == "__main__":
    t0 = time.time()
    rc = asyncio.run(main())
    print(f"(probe завершён за {time.time() - t0:.0f} с)")
    raise SystemExit(rc)
