"""Probe: каким токеном грузить фото на стену сообщества (баг сетевой рассылки).

Симптом (2026-06-18): сетевая рассылка публикует только текст, картинки теряются.
Корень из логов worker: ``upload_wall_photo failed: [27] Group authorization
failed: method is unavailable with group auth`` — диспетчер грузит фото
**community-токеном**, а VK запрещает ``photos.getWallUploadServer`` под group-auth
(тот же барьер #27, что у ``wall.edit``/``stats.get`` — см. brain GOTCHAS).

Этот probe выясняет рабочий токен **БЕЗ записи**: ``photos.getWallUploadServer
(group_id)`` лишь возвращает адрес upload-сервера и НИЧЕГО не меняет (фото
появляется только после ``saveWallPhoto``, который мы НЕ вызываем — как cover-probe
полагается на ``getOwnerCoverPhotoUploadServer``). Успех = право грузить есть;
ошибка [27]/[15] = нет.

Для каждой цели сети пробуем:
- USER-токен (владелец-админ) → ожидаем upload_url (право есть);
- community-токен группы → ожидаем [27] (воспроизводим баг).

Запуск на проде: sudo + source /etc/setka/setka.env (root-only env), затем
``./venv/bin/python scripts/probe_wall_upload_token.py``. Вывод — JSON в stdout.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional


def _err_text(e: Exception) -> str:
    code = getattr(e, "code", None)
    return f"[{code}] {e}" if code is not None else str(e)


def _try_get_wall_upload_server(token: str, gid: int) -> Dict[str, Any]:
    """Только проверка прав: getWallUploadServer ничего не меняет."""
    import vk_api

    try:
        api = vk_api.VkApi(token=token).get_api()
        server = api.photos.getWallUploadServer(group_id=abs(int(gid)))
        return {"ok": bool(server.get("upload_url")), "error": None}
    except Exception as e:  # noqa: BLE001 — это и есть измеряемый сигнал
        return {"ok": False, "error": _err_text(e)}


async def main() -> None:
    from modules.broadcast.service import default_targets
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        print(json.dumps({"error": "no user token (COMMUNITY_WRITE)"}, ensure_ascii=False))
        return

    from database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        targets = await default_targets(session)

    rows: List[Dict[str, Any]] = []
    user_ok = comm_ok = comm_err27 = 0
    for t in targets:
        gid = abs(int(t["group_id"]))
        user_res = _try_get_wall_upload_server(user_token, gid)
        ctok: Optional[str] = (community_tokens or {}).get(gid)
        comm_res = (
            _try_get_wall_upload_server(ctok, gid)
            if ctok
            else {"ok": False, "error": "no community token"}
        )
        if user_res["ok"]:
            user_ok += 1
        if comm_res["ok"]:
            comm_ok += 1
        if comm_res.get("error", "").startswith("[27]"):
            comm_err27 += 1
        rows.append(
            {
                "group_id": gid,
                "name": t.get("name", ""),
                "user_token": user_res,
                "community_token": comm_res,
            }
        )

    print(
        json.dumps(
            {
                "targets": len(targets),
                "user_token_can_upload": user_ok,
                "community_token_can_upload": comm_ok,
                "community_token_err27": comm_err27,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
