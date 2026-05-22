"""
VK Token Router

Помощник: выдать «правильный» VK-токен под конкретную операцию.

Логика (по умолчанию):
- Если у нас есть community access token для группы (см. `vk_tokens.community_id`)
  — используем его. Это снимает нагрузку с пользовательских токенов
  (VALSTAN / VITA) и спасает от их rate-limit / VK-бана.
- Иначе — fallback на пользовательский токен.

Для парсинга чужих сообществ community-токенов нет в принципе — там
автоматически остаётся пользовательский токен.
"""

from __future__ import annotations

from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VKToken


async def load_community_tokens(session: AsyncSession) -> Dict[int, str]:
    """Вернуть {abs(group_id): token} для всех активных community-токенов.

    Один SELECT, кешировать не нужно — таблица крошечная (десятки записей).
    """
    q = await session.execute(
        select(VKToken).where(
            VKToken.community_id.isnot(None),
            VKToken.is_active.is_(True),
        )
    )
    return {t.community_id: t.token for t in q.scalars()}


def pick_token(
    community_tokens: Dict[int, str],
    group_id: int,
    user_token_fallback: str,
) -> tuple[str, bool]:
    """Выбрать токен для операции над данной группой.

    Returns:
        (token, is_community): сам токен и признак «это community-токен».
        Если для группы есть community-токен — берём его; иначе fallback.
    """
    cid = abs(int(group_id))
    tok = community_tokens.get(cid)
    if tok:
        return tok, True
    return user_token_fallback, False
