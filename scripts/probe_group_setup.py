"""Проба возможностей автооформления сообществ (канал setup). Probe-before-build (#020).

Ребрендинг сети (план 2026-08-29) хочет программно править 25+ сообществ:
описание, город, статус, аватар, обложку, закреп. Из всего этого проверено
живьём только одно — ``groups.edit(description=...)`` работает community-ключом
(проба 2026-08-28). Остальное документация не решает, у ВК два шрама класса
«метод тихо умер или требует другую роль» (G19, G253), поэтому — вызовом.

Что проверяем, каждым из двух токенов (community-ключ полигона + user VALSTAN):

1. ``groups.edit`` с ``city_id`` — лечится ли баг «город Калинино у 25 групп»;
2. статус: поле ``status`` в ``groups.edit`` против отдельного ``status.set``;
3. аватар: **полный цикл** ``photos.getOwnerPhotoUploadServer`` → upload →
   ``photos.saveOwnerPhoto``. Get-server ничего не доказывает — прошлая проба
   обложек остановилась на нём и вопрос «а save-то работает?» остался открытым;
4. обложка: полный цикл до ``photos.saveOwnerCoverPhoto``;
5. ``wall.post`` + ``wall.pin`` + ``wall.unpin`` (закреп-визитка);
6. ``database.getCities`` — есть ли в справочнике ВК райцентры, которых боимся
   не найти («Кирс», «Ленинское», «Уни»);
7. публикует ли ``saveOwnerPhoto`` системный пост «обновил фотографию» — если
   да, канал setup обязан удалять его сразу, иначе шум в 25 группах.

**Безопасность.** Запись — только на тест-полигон и только с ``--apply``;
по умолчанию скрипт читает. Пробные пост/закреп удаляются в ``finally``.
Аватар и обложка полигона НЕ откатываются (у полигона их не было, а метода
«удалить обложку» у ВК нет) — полигон для того и заведён. Токены не логируются.

Запуск на проде:
    ./venv/bin/python scripts/probe_group_setup.py            # только чтение
    ./venv/bin/python scripts/probe_group_setup.py --apply    # + проба записи
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import struct
import sys
import urllib.parse
import urllib.request
import uuid
import zlib
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("probe_group_setup")

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.131"

# Тест-полигон проекта: группа без подписчиков, заведённая ровно для таких проб.
TEST_GROUP_ID = 137760500

# Райцентры, которых боимся не найти в справочнике ВК: у «Кирса» код района
# другой (verhnekame), «Ленинское» — вездесущий омоним, «Уни» — крошечное село.
CITY_PROBES = ("Кирс", "Ленинское", "Уни", "Опарино", "Малмыж")

# Итог пробы: {"метод": {"имя_токена": "ok"|"error N: ..."}}
matrix: Dict[str, Dict[str, str]] = {}


def call(token: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Вызов ВК. Токен уходит только в тело запроса, в лог — никогда."""
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = VK_VERSION
    data = urllib.parse.urlencode(payload).encode()
    try:
        with urllib.request.urlopen(VK_API + method, data=data, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:  # pragma: no cover - сеть
        return {"error_code": -1, "msg": f"сетевой сбой: {exc}"}
    if "error" in body:
        err = body["error"]
        return {"error_code": err.get("error_code"), "msg": str(err.get("error_msg"))[:200]}
    return {"ok": body.get("response")}


def describe(result: Dict[str, Any]) -> str:
    if "ok" in result:
        return "✅ доступен"
    return f"⛔ error {result.get('error_code')}: {result.get('msg')}"


def record(method: str, token_name: str, result: Dict[str, Any]) -> None:
    slot = matrix.setdefault(method, {})
    if "ok" in result:
        slot[token_name] = "ok"
    else:
        slot[token_name] = f"error {result.get('error_code')}: {result.get('msg')}"
    logger.info("  %-18s %-38s %s", token_name, method, describe(result))


def make_test_png(width: int = 200, height: int = 200) -> bytes:
    """Валидный одноцветный PNG без Pillow (stdlib: struct+zlib).

    ВК требует у аватара минимум 200×200 и ширину ≥ высоты — этого хватает,
    чтобы проверить права; красоту рисует branding.py, а не проба.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # RGB, 8 бит
    row = b"\x00" + b"\x3d\x7a\xb8" * width  # фильтр 0 + сине-серый цвет
    body = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", body) + chunk(b"IEND", b"")
    )


def upload_multipart(upload_url: str, field: str, filename: str, blob: bytes) -> Dict[str, Any]:
    """POST файла на upload-сервер ВК (образец — vk_wall_photo_upload.py)."""
    boundary = uuid.uuid4().hex
    buf = io.BytesIO()
    buf.write(f"--{boundary}\r\n".encode())
    buf.write(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
    buf.write(b"Content-Type: image/png\r\n\r\n")
    buf.write(blob)
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        upload_url,
        data=buf.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # pragma: no cover - сеть
        return {"error": f"upload failed: {exc}"}


async def load_tokens() -> Dict[str, str]:
    """Живые токены: community-ключ полигона + user-токены (VALSTAN и прочие)."""
    from database.connection import AsyncSessionLocal
    from modules.vk_token_router import TokenOp, TokenPolicy

    out: Dict[str, str] = {}
    async with AsyncSessionLocal() as session:
        policy = TokenPolicy(session)
        for candidate in await policy.pick(TokenOp.COMMUNITY_WRITE, group_id=-TEST_GROUP_ID):
            out.setdefault(candidate.name, candidate.token)
        for candidate in await policy.pick(TokenOp.READ):
            out.setdefault(candidate.name, candidate.token)
    return out


def snapshot(token: str) -> Dict[str, Any]:
    got = call(
        token,
        "groups.getById",
        {"group_id": TEST_GROUP_ID, "fields": "description,status,city,has_photo,cover"},
    )
    if "ok" not in got:
        return {}
    rows = got["ok"]
    if isinstance(rows, dict):
        rows = rows.get("groups", rows)
    return rows[0] if rows else {}


def probe_cities(tokens: Dict[str, str]) -> None:
    """Справочник городов: q= по каждому проблемному райцентру (read-only).

    ``database.*`` недоступен групповому токену (error 27, замерено этой же
    пробой) — берём первый user-токен.
    """
    logger.info("\n=== СПРАВОЧНИК ГОРОДОВ (read-only) ===")
    token = next(
        (t for n, t in tokens.items() if not n.startswith("COMM_")),
        next(iter(tokens.values())),
    )
    regions = call(token, "database.getRegions", {"country_id": 1, "q": "Кировская"})
    region_id = None
    if "ok" in regions:
        items = (regions["ok"] or {}).get("items") or []
        region_id = items[0].get("id") if items else None
    logger.info("  region_id Кировской области: %s", region_id)
    matrix["database.getRegions"] = {"any": "ok" if region_id else "not found"}

    found: Dict[str, Any] = {}
    for city in CITY_PROBES:
        params: Dict[str, Any] = {"country_id": 1, "q": city, "count": 5}
        if region_id:
            params["region_id"] = region_id
        got = call(token, "database.getCities", params)
        if "ok" in got:
            items = (got["ok"] or {}).get("items") or []
            found[city] = [
                {"id": i.get("id"), "title": i.get("title"), "area": i.get("area", "")}
                for i in items
            ]
        else:
            found[city] = describe(got)
        logger.info("  %-14s → %s", city, json.dumps(found[city], ensure_ascii=False))
    matrix["database.getCities"] = {"probes": json.dumps(found, ensure_ascii=False)}


def probe_edit_and_status(tokens: Dict[str, str], kirov_city_id: Optional[int]) -> None:
    logger.info("\n=== groups.edit(city_id, status) и status.set ===")
    for name, token in tokens.items():
        params: Dict[str, Any] = {
            "group_id": TEST_GROUP_ID,
            "description": "Тестовая группа проекта. Проба API.",
        }
        if kirov_city_id:
            params["city_id"] = kirov_city_id
        record(
            f"groups.edit(city_id={bool(kirov_city_id)})", name, call(token, "groups.edit", params)
        )

        record(
            "groups.edit(status=...)",
            name,
            call(
                token,
                "groups.edit",
                {"group_id": TEST_GROUP_ID, "status": "Проба статуса (setup)"},
            ),
        )
        record(
            "status.set(group_id=...)",
            name,
            call(token, "status.set", {"group_id": TEST_GROUP_ID, "text": "Проба status.set"}),
        )


def probe_avatar(tokens: Dict[str, str]) -> None:
    logger.info("\n=== АВАТАР: getOwnerPhotoUploadServer → upload → saveOwnerPhoto ===")
    png = make_test_png()
    for name, token in tokens.items():
        srv = call(token, "photos.getOwnerPhotoUploadServer", {"owner_id": -TEST_GROUP_ID})
        record("photos.getOwnerPhotoUploadServer", name, srv)
        if "ok" not in srv:
            continue
        upload_url = (srv["ok"] or {}).get("upload_url")
        if not upload_url:
            record("saveOwnerPhoto", name, {"error_code": -2, "msg": "нет upload_url"})
            continue
        uploaded = upload_multipart(upload_url, "photo", "avatar.png", png)
        if "error" in uploaded or "photo" not in uploaded:
            record(
                "photos.saveOwnerPhoto",
                name,
                {"error_code": -2, "msg": f"upload: {json.dumps(uploaded)[:150]}"},
            )
            continue
        saved = call(
            token,
            "photos.saveOwnerPhoto",
            {
                "server": uploaded.get("server"),
                "hash": uploaded.get("hash"),
                "photo": uploaded.get("photo"),
            },
        )
        record("photos.saveOwnerPhoto", name, saved)
        if "ok" in saved:
            check_system_post(tokens)
            return  # аватар встал — второй токен не нужен


def check_system_post(tokens: Dict[str, str]) -> None:
    """Смотрим стену: оставил ли saveOwnerPhoto системный пост «обновил фото»."""
    token = next(iter(tokens.values()))
    wall = call(token, "wall.get", {"owner_id": -TEST_GROUP_ID, "count": 3})
    if "ok" not in wall:
        matrix["system_post_after_avatar"] = {"any": describe(wall)}
        return
    items = (wall["ok"] or {}).get("items") or []
    system = [
        {"id": i.get("id"), "post_type": i.get("post_type"), "text": (i.get("text") or "")[:60]}
        for i in items
        if i.get("post_type") in ("photo", "post") and not (i.get("text") or "").startswith("Проба")
    ]
    matrix["system_post_after_avatar"] = {"wall_top3": json.dumps(system, ensure_ascii=False)}
    logger.info("  верх стены после аватара: %s", matrix["system_post_after_avatar"]["wall_top3"])


def probe_cover(tokens: Dict[str, str]) -> None:
    logger.info("\n=== ОБЛОЖКА: getOwnerCoverPhotoUploadServer → upload → saveOwnerCoverPhoto ===")
    png = make_test_png(1590, 400)
    for name, token in tokens.items():
        srv = call(
            token,
            "photos.getOwnerCoverPhotoUploadServer",
            {
                "group_id": TEST_GROUP_ID,
                "crop_x": 0,
                "crop_y": 0,
                "crop_x2": 1590,
                "crop_y2": 400,
            },
        )
        record("photos.getOwnerCoverPhotoUploadServer", name, srv)
        if "ok" not in srv:
            continue
        upload_url = (srv["ok"] or {}).get("upload_url")
        if not upload_url:
            record("saveOwnerCoverPhoto", name, {"error_code": -2, "msg": "нет upload_url"})
            continue
        uploaded = upload_multipart(upload_url, "photo", "cover.png", png)
        if "error" in uploaded or "hash" not in uploaded:
            record(
                "photos.saveOwnerCoverPhoto",
                name,
                {"error_code": -2, "msg": f"upload: {json.dumps(uploaded)[:150]}"},
            )
            continue
        saved = call(
            token,
            "photos.saveOwnerCoverPhoto",
            {"hash": uploaded.get("hash"), "photo": uploaded.get("photo")},
        )
        record("photos.saveOwnerCoverPhoto", name, saved)
        if "ok" in saved:
            return


def probe_wall_pin(tokens: Dict[str, str]) -> None:
    logger.info("\n=== wall.post → wall.pin → wall.unpin → wall.delete ===")
    for name, token in tokens.items():
        posted = call(
            token,
            "wall.post",
            {
                "owner_id": -TEST_GROUP_ID,
                "from_group": 1,
                "message": "Проба закрепа (setup) — пост будет удалён скриптом.",
            },
        )
        record("wall.post", name, posted)
        if "ok" not in posted:
            continue
        post_id = (posted["ok"] or {}).get("post_id")
        if not post_id:
            continue
        try:
            record(
                "wall.pin",
                name,
                call(token, "wall.pin", {"owner_id": -TEST_GROUP_ID, "post_id": post_id}),
            )
            record(
                "wall.unpin",
                name,
                call(token, "wall.unpin", {"owner_id": -TEST_GROUP_ID, "post_id": post_id}),
            )
        finally:
            removed = call(token, "wall.delete", {"owner_id": -TEST_GROUP_ID, "post_id": post_id})
            record("wall.delete(cleanup)", name, removed)
        return  # цикл прошёл одним токеном — матрицу прав добьёт второй запуск при нужде


def order_tokens(tokens: Dict[str, str]) -> Dict[str, str]:
    """Community-ключ полигона первым, VALSTAN вторым, прочие в хвосте.

    Порядок = приоритет канала setup: сначала пробуем дешёвый community-ключ,
    user-токен владельца бережём (бюджет 50 вызовов/день).
    """

    def rank(name: str) -> Tuple[int, str]:
        if name.startswith("COMM_"):
            return (0, name)
        if name == "VALSTAN":
            return (1, name)
        return (2, name)

    return dict(sorted(tokens.items(), key=lambda kv: rank(kv[0])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить пробу записи")
    args = parser.parse_args()

    from modules.secrets_bootstrap import bootstrap_secrets

    bootstrap_secrets()

    tokens = order_tokens(asyncio.run(load_tokens()))
    if not tokens:
        logger.error("Живых токенов не нашлось — проба невозможна")
        return 2
    logger.info("Токенов в пробе: %s", ", ".join(tokens))

    any_token = next(iter(tokens.values()))
    before = snapshot(any_token)
    logger.info(
        "Полигон до пробы: has_photo=%s cover=%s city=%s status=%r",
        before.get("has_photo"),
        (before.get("cover") or {}).get("enabled"),
        (before.get("city") or {}).get("title"),
        (before.get("status") or "")[:40],
    )

    probe_cities(tokens)

    if not args.apply:
        logger.info("\nПроба записи пропущена (запусти с --apply).")
        print(json.dumps(matrix, ensure_ascii=False, indent=1))
        return 0

    # city_id для groups.edit берём из результата probe_cities: Кирс, если нашёлся.
    kirov_city_id: Optional[int] = None
    try:
        probes = json.loads(matrix["database.getCities"]["probes"])
        for item in probes.get("Кирс") or []:
            if isinstance(item, dict) and item.get("id"):
                kirov_city_id = int(item["id"])
                break
    except Exception:  # noqa: BLE001 - без города проба edit идёт частично
        pass

    probe_edit_and_status(tokens, kirov_city_id)
    probe_avatar(tokens)
    probe_cover(tokens)
    probe_wall_pin(tokens)

    after = snapshot(any_token)
    logger.info(
        "\nПолигон после: has_photo=%s cover=%s city=%s status=%r",
        after.get("has_photo"),
        (after.get("cover") or {}).get("enabled"),
        (after.get("city") or {}).get("title"),
        (after.get("status") or "")[:40],
    )

    logger.info("\n=== ИТОГОВАЯ МАТРИЦА ===")
    print(json.dumps(matrix, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
