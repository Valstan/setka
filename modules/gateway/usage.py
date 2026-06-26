"""Логирование запросов к VK-шлюзу (для страницы статистики /gateway-stats).

Best-effort: любая ошибка записи логируется и глотается — учёт использования
никогда не должен ломать ответ шлюза. Пишет в таблицу ``gateway_requests``
(миграция 049).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def record_request(
    project: str,
    endpoint: str,
    method: str,
    params: Optional[Dict[str, Any]],
    status: int,
    ok: bool,
    error_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Записать один запрос к шлюзу. Ошибки записи не пробрасываются."""
    try:
        from database.connection import AsyncSessionLocal
        from database.models import GatewayRequest

        async with AsyncSessionLocal() as session:
            session.add(
                GatewayRequest(
                    project=project,
                    endpoint=endpoint,
                    method=method,
                    params=params or {},
                    status=status,
                    ok=ok,
                    error_code=error_code,
                    duration_ms=duration_ms,
                )
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001 - учёт не критичен
        logger.warning("gateway usage log failed (%s %s): %s", endpoint, method, e)
