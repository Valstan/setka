"""VK Token Router / Policy.

Центральный решатель: «какой VK-токен использовать для этой операции и в каком
порядке пробовать кандидатов, если первый упал».

Контекст и история. До 2026-05-27 здесь была простая функция ``pick_token`` —
«если есть community-токен для группы, используем его, иначе fallback на
переданный user-token». Реализованы:

* Семантика операций (:class:`TokenOp`) — READ / COMMUNITY_WRITE / USER_WRITE.
* Env-конфиг ролей (``VK_PUBLISH_TOKEN_NAMES``, ``VK_NEVER_PUBLISH_TOKEN_NAMES``
  в ``config.runtime``).
* Динамическое состояние токена в БД (``vk_tokens.disabled_until``,
  ``last_error_code``, ``consecutive_errors``) — миграция 014.
* Автоматический cooldown по VK error codes 5 (invalid_token) / 17
  (validation_required) / 29 (rate_limit_per_token) — :meth:`TokenPolicy.report_error`.
* Telegram-alert при auto-disable — через :mod:`modules.notifications.telegram_notifier`.

Жизненный цикл вызова:

>>> async with AsyncSessionLocal() as s:
...     policy = TokenPolicy(s)
...     for cand in await policy.pick(TokenOp.READ):
...         try:
...             result = vk_call(cand.token, ...)
...             await policy.report_success(cand.name)
...             break
...         except ApiError as e:
...             await policy.report_error(cand.name, e.code)
...             if e.code in (5, 17, 29):
...                 continue
...             raise

Старая функция ``pick_token`` сохранена (она используется legacy-кодом
``BaseVKChecker._api_for`` и ``VKPublisher._client_for_group`` через
``community_tokens={cid: token}``). Когда весь код переедет на ``TokenPolicy``
— её можно будет удалить.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import VKToken

logger = logging.getLogger(__name__)


class TokenOp(str, enum.Enum):
    """Семантические категории операций VK API.

    Влияет на то, какой набор кандидатов вернёт :meth:`TokenPolicy.pick`.

    READ — любой read-only вызов: ``wall.get``, ``groups.search``,
    ``groups.getById``, ``database.getCities``, ``users.get``. Подходят все
    active user-токены (включая Vita).

    COMMUNITY_WRITE — публикации/действия от имени сообщества: ``wall.post``,
    ``photos.getWallUploadServer``/``photos.save``, ``wall.createComment``,
    ``messages.send``, ``likes.add``. Сначала пытаемся community-токен
    целевой группы, потом — user-tokens из ``VK_PUBLISH_TOKEN_NAMES`` минус
    ``VK_NEVER_PUBLISH_TOKEN_NAMES``.

    USER_WRITE — операции, для которых VK API в принципе не принимает
    community-токен: ``wall.repost`` (copy_setka хаб). Только user-tokens из
    whitelist, исключая deny-list. Если все недоступны — операция fail.
    """

    READ = "read"
    COMMUNITY_WRITE = "community_write"
    USER_WRITE = "user_write"


@dataclass(frozen=True)
class TokenCandidate:
    """Один кандидат для выполнения операции.

    Возвращается :meth:`TokenPolicy.pick` в упорядоченном виде. Caller
    пробует кандидатов в этом порядке, пока один не выполнит операцию или
    список не кончится.

    Attributes:
        name: имя токена (``VALSTAN``, ``VITA``, или ``COMM_<id>`` для
            community). Совпадает с ``vk_tokens.name`` в БД, либо
            ``ENV:<name>`` для токенов из env без записи в БД.
        token: сам access_token.
        source: ``'community'`` (привязан к group_id) или ``'user'``.
        community_id: только для ``source='community'`` — abs(group_id).
    """

    name: str
    token: str
    source: str  # 'community' | 'user'
    community_id: Optional[int] = None


# VK error codes, по которым TokenPolicy автоматически кладёт токен в cooldown.
# Каждому соответствует длительность блокировки (часы).
#
#   5  — invalid_token / user_authorisation_failed. Чаще всего значит, что
#        access_token аннулирован VK'ом (бан аккаунта, смена пароля, IP-pin).
#        Длительный cooldown — 24ч, чтобы не долбить заведомо мёртвый токен.
#   17 — validation_required. VK требует капчу/код от пользователя. Без
#        участия человека не решается, поэтому тоже 24ч.
#   29 — rate_limit_per_token. Токен превысил суточный лимит запросов.
#        Час cooldown'а — стандартный VK-таймаут для этой ошибки.
_AUTO_DISABLE_CODES_HOURS: Dict[int, float] = {
    5: 24.0,
    17: 24.0,
    29: 1.0,
}


async def load_community_tokens(session: AsyncSession) -> Dict[int, str]:
    """Вернуть ``{abs(group_id): token}`` для всех active community-токенов.

    Не фильтрует ``disabled_until`` — community-токены сейчас отключать через
    cooldown нечем (если group-token внезапно сломался, VKPublisher просто
    получит error 15/27 и упадёт на user-fallback тем же запросом).
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
    """LEGACY: используется ``BaseVKChecker._api_for`` и старыми кейсами.

    Новый код должен брать :class:`TokenPolicy`. Сохранено для совместимости —
    логика «есть community → берём его, иначе fallback».
    """
    cid = abs(int(group_id))
    tok = community_tokens.get(cid)
    if tok:
        return tok, True
    return user_token_fallback, False


class TokenPolicy:
    """Stateful policy: выбор токенов + учёт ошибок.

    Создаётся per-Celery-task / per-request (session-scoped). Внутри —
    минимальный кеш активных токенов: один SELECT в начале pick(), повторное
    pick() в той же сессии переиспользует кеш.

    Telegram-alert при auto-disable — мягкий: если ``telegram_notifier``
    недоступен, исключение глотается и логируется (alert — best-effort).
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._active_cache: Optional[Dict[str, VKToken]] = None
        self._community_cache: Optional[Dict[int, VKToken]] = None

    # ------------------------------------------------------------------
    # Запросы состояния
    # ------------------------------------------------------------------

    async def _load_active(self) -> Dict[str, VKToken]:
        """Кешированный список active user-токенов (community_id IS NULL).

        «Active» = ``is_active=TRUE`` И (``disabled_until IS NULL`` или
        ``disabled_until < NOW()``). Имя возвращается в верхнем регистре —
        совпадает с тем, как имена хранятся в env (``VK_TOKEN_<NAME>``).
        """
        if self._active_cache is not None:
            return self._active_cache
        now = datetime.utcnow()
        q = await self._session.execute(
            select(VKToken).where(
                VKToken.community_id.is_(None),
                VKToken.is_active.is_(True),
            )
        )
        out: Dict[str, VKToken] = {}
        for t in q.scalars():
            if t.disabled_until is not None and t.disabled_until > now:
                continue
            out[t.name.upper()] = t
        self._active_cache = out
        return out

    async def _load_communities(self) -> Dict[int, VKToken]:
        if self._community_cache is not None:
            return self._community_cache
        now = datetime.utcnow()
        q = await self._session.execute(
            select(VKToken).where(
                VKToken.community_id.isnot(None),
                VKToken.is_active.is_(True),
            )
        )
        out: Dict[int, VKToken] = {}
        for t in q.scalars():
            if t.disabled_until is not None and t.disabled_until > now:
                continue
            out[int(t.community_id)] = t
        self._community_cache = out
        return out

    def _invalidate_cache(self) -> None:
        self._active_cache = None
        self._community_cache = None

    # ------------------------------------------------------------------
    # Главный метод: pick
    # ------------------------------------------------------------------

    async def pick(
        self,
        op: TokenOp,
        group_id: Optional[int] = None,
    ) -> List[TokenCandidate]:
        """Упорядоченный список кандидатов для операции.

        Args:
            op: семантика операции — см. :class:`TokenOp`.
            group_id: для ``COMMUNITY_WRITE`` — целевая группа (любой знак, abs
                берётся внутри). Игнорируется для READ / USER_WRITE.

        Returns:
            Список :class:`TokenCandidate` в порядке предпочтения. Пустой
            список — нет ни одного подходящего токена; caller обязан вернуть
            понятную ошибку («сейчас публиковать нечем»).
        """
        from config.runtime import VK_TOKENS, get_never_publish_token_names, get_publish_token_names

        never_publish = get_never_publish_token_names()
        publish_whitelist = set(get_publish_token_names())
        active_db = await self._load_active()

        # Имена токенов из env, которые сейчас не помечены disabled в БД.
        # Если в БД записи о токене нет — считаем его «живым» (env — source of
        # truth для существования, БД — для статуса).
        env_active: Dict[str, str] = {}
        for name, tok in (VK_TOKENS or {}).items():
            if not tok:
                continue
            upper = name.upper()
            db_row = active_db.get(upper)
            # Если есть запись в БД и она НЕ в _load_active — значит disabled.
            # _load_active отбрасывает disabled_until>now, так что отсутствие
            # имени в active_db при наличии его в БД = disabled.
            if upper not in active_db:
                # Проверим, есть ли вообще запись в БД (через всю таблицу).
                # Дешевле — отдельный SELECT существования.
                if await self._token_exists_but_disabled(upper):
                    continue
            env_active[upper] = tok
            _ = db_row  # keep linter quiet

        if op == TokenOp.READ:
            # READ: любой active token, Vita разрешена.
            out: List[TokenCandidate] = []
            for name, tok in env_active.items():
                out.append(TokenCandidate(name=name, token=tok, source="user"))
            return out

        if op == TokenOp.USER_WRITE:
            # Только whitelist минус deny-list.
            out = []
            for name in publish_whitelist or env_active.keys():
                if name in never_publish:
                    continue
                tok = env_active.get(name)
                if tok:
                    out.append(TokenCandidate(name=name, token=tok, source="user"))
            return out

        # COMMUNITY_WRITE: community-token (если group_id передан) первым,
        # потом user-tokens из whitelist.
        out = []
        if group_id is not None:
            cid = abs(int(group_id))
            comms = await self._load_communities()
            ct = comms.get(cid)
            if ct is not None:
                out.append(
                    TokenCandidate(
                        name=ct.name,
                        token=ct.token,
                        source="community",
                        community_id=cid,
                    )
                )
        for name in publish_whitelist or env_active.keys():
            if name in never_publish:
                continue
            tok = env_active.get(name)
            if tok:
                out.append(TokenCandidate(name=name, token=tok, source="user"))
        return out

    async def _token_exists_but_disabled(self, name: str) -> bool:
        """True, если в БД есть запись с этим name и она сейчас в disabled."""
        q = await self._session.execute(
            select(VKToken).where(VKToken.name == name, VKToken.community_id.is_(None))
        )
        row = q.scalar_one_or_none()
        if row is None:
            return False
        if not row.is_active:
            return True
        if row.disabled_until is not None and row.disabled_until > datetime.utcnow():
            return True
        return False

    # ------------------------------------------------------------------
    # Учёт результатов
    # ------------------------------------------------------------------

    async def report_error(self, name: str, vk_error_code: int) -> None:
        """Зафиксировать VK error для токена; auto-disable по 5/17/29.

        Поднимает ``consecutive_errors`` всегда. Записывает
        ``disabled_until=now()+hours`` если ``vk_error_code`` в
        :data:`_AUTO_DISABLE_CODES_HOURS`. Шлёт Telegram-alert при
        auto-disable.

        Идемпотентна: если токена нет в БД (только в env) — записывается
        новая строка с минимальными полями. Это удобно для первого запуска,
        когда vk_tokens таблица не synced с env.
        """
        upper = name.upper()
        hours = _AUTO_DISABLE_CODES_HOURS.get(int(vk_error_code))
        now = datetime.utcnow()
        disabled_until = now + timedelta(hours=hours) if hours else None

        # Ищем существующую запись
        q = await self._session.execute(
            select(VKToken).where(VKToken.name == upper, VKToken.community_id.is_(None))
        )
        row = q.scalar_one_or_none()

        if row is None:
            # Создавать запись не будем — БД не обязана содержать всю env.
            # Просто логируем; в следующий pick этот токен останется
            # доступным (потому что _token_exists_but_disabled вернёт False).
            logger.warning(
                "TokenPolicy.report_error: no DB row for %s, vk_code=%s — skipping persistence",
                upper,
                vk_error_code,
            )
            self._invalidate_cache()
            return

        row.last_error_code = int(vk_error_code)
        row.last_error_at = now
        row.consecutive_errors = int(row.consecutive_errors or 0) + 1
        if disabled_until is not None:
            row.disabled_until = disabled_until
            row.error_message = f"auto-disable: VK error {vk_error_code} at {now.isoformat()}"
        await self._session.commit()
        self._invalidate_cache()

        if disabled_until is not None:
            logger.warning(
                "TokenPolicy: token %s auto-disabled until %s (VK error %s)",
                upper,
                disabled_until.isoformat(),
                vk_error_code,
            )
            await _send_telegram_alert_safe(
                f"🛑 VK-токен {upper} автоматически отключён до {disabled_until.isoformat()} "
                f"(VK error {vk_error_code}). Проверьте здоровье токена."
            )

    async def report_success(self, name: str) -> None:
        """Сбросить ``consecutive_errors`` после удачного вызова.

        Не трогает ``disabled_until`` — ручной enable делается отдельно через
        :meth:`enable` или ``POST /api/tokens/{name}/enable``.
        """
        upper = name.upper()
        await self._session.execute(
            update(VKToken)
            .where(VKToken.name == upper, VKToken.community_id.is_(None))
            .values(consecutive_errors=0, last_used=datetime.utcnow())
        )
        await self._session.commit()
        self._invalidate_cache()

    # ------------------------------------------------------------------
    # Manual control (UI / SQL)
    # ------------------------------------------------------------------

    async def disable(self, name: str, hours: float, reason: str = "manual") -> bool:
        """Manual disable on ``hours`` hours. Возвращает True если записано.

        Если токена нет в БД — создаётся новая запись (минимальная) с
        полями ``name`` и ``token`` из env. Это нужно, чтобы dashboard'у
        пользователя было что увидеть.
        """
        upper = name.upper()
        from config.runtime import VK_TOKENS

        until = datetime.utcnow() + timedelta(hours=float(hours))
        q = await self._session.execute(
            select(VKToken).where(VKToken.name == upper, VKToken.community_id.is_(None))
        )
        row = q.scalar_one_or_none()
        if row is None:
            env_tok = VK_TOKENS.get(upper)
            if not env_tok:
                logger.error("TokenPolicy.disable: %s not in env and not in DB", upper)
                return False
            row = VKToken(
                name=upper,
                token=env_tok,
                is_active=True,
                disabled_until=until,
                error_message=f"manual disable: {reason}",
            )
            self._session.add(row)
        else:
            row.disabled_until = until
            row.error_message = f"manual disable: {reason}"
        await self._session.commit()
        self._invalidate_cache()
        logger.info("TokenPolicy: %s disabled until %s (reason: %s)", upper, until, reason)
        return True

    async def enable(self, name: str) -> bool:
        """Сбросить ``disabled_until`` и счётчик ошибок."""
        upper = name.upper()
        res = await self._session.execute(
            update(VKToken)
            .where(VKToken.name == upper, VKToken.community_id.is_(None))
            .values(
                disabled_until=None,
                consecutive_errors=0,
                error_message=None,
            )
        )
        await self._session.commit()
        self._invalidate_cache()
        return res.rowcount > 0


async def _send_telegram_alert_safe(text: str) -> None:
    """Telegram-alert best-effort. Любые ошибки глотаются и логируются."""
    try:
        from modules.notifications.telegram_notifier import send_telegram_alert

        await send_telegram_alert(text)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("TokenPolicy: telegram alert failed: %s", e)


# ----------------------------------------------------------------------
# Sync-friendly helper для legacy кода (parsing_tasks.py, copy_setka и пр.)
# ----------------------------------------------------------------------


async def get_active_parse_tokens(session: AsyncSession) -> Dict[str, str]:
    """Вернуть ``{name: token}`` user-токенов, годных для READ прямо сейчас.

    Используется legacy-местами, которые создают
    ``VKTokenRotatorAsync(list_of_tokens)`` и не хотят интегрироваться в
    :class:`TokenPolicy` целиком. Возвращаемое значение можно скармливать
    в ``VKTokenRotatorAsync([...values()])``.

    Источник значений — **БД** (``vk_tokens``), а не env. Единый источник
    истины: токены добавляются/меняются через ``/tokens`` UI, парсинг и
    публикация читают одну и ту же запись (раньше парсинг брал значение из
    env ``VK_TOKENS``, публикация — из БД, что приводило к рассинхрону при
    ротации токена — инцидент VALSTAN 2026-05-28).

    Фильтр (user-токены, ``community_id IS NULL``):
    - ``is_active = TRUE`` и непустой ``token``;
    - не на cooldown: ``disabled_until IS NULL`` или ``< now()``;
    - ``validation_status != 'invalid'`` — явно протухший токен в парсинг не
      берём, иначе словим VK error 5 и авто-disable на ровном месте.
      ``unknown`` / ``valid`` — годятся (свежедобавленный токен ещё «unknown»).
    """
    now = datetime.utcnow()
    q = await session.execute(select(VKToken).where(VKToken.community_id.is_(None)))
    out: Dict[str, str] = {}
    for t in q.scalars():
        if not t.is_active or not t.token:
            continue
        if t.disabled_until is not None and t.disabled_until > now:
            continue
        if t.validation_status == "invalid":
            continue
        out[t.name.upper()] = t.token
    return out


async def get_publish_candidates_for_group(
    session: AsyncSession,
    group_id: int,
) -> List[TokenCandidate]:
    """Convenience-обёртка над ``TokenPolicy.pick(COMMUNITY_WRITE, group_id)``.

    Не делает report_error / report_success — caller ответственен за это.
    """
    return await TokenPolicy(session).pick(TokenOp.COMMUNITY_WRITE, group_id=group_id)


def get_active_parse_tokens_sync() -> Dict[str, str]:
    """Sync-friendly обёртка над ``get_active_parse_tokens``.

    Подразумевает наличие активного event-loop'а (Celery task через
    ``utils.celery_asyncio.run_coro`` — обычный кейс на проде).

    Основной источник — БД (см. ``get_active_parse_tokens``). env ``VK_TOKENS``
    остаётся только аварийным fallback'ом на случай недоступной БД в горячем
    пути парсинга — чтобы случайная DB-ошибка не обнулила токены. Если env
    позже почистят (токены живут в БД) — fallback вернёт пусто, парсинг
    залогирует отсутствие токенов; это допустимая деградация при DB-down.
    """
    from config.runtime import VK_TOKENS

    try:
        from database.connection import AsyncSessionLocal
        from utils.celery_asyncio import run_coro

        async def _impl() -> Dict[str, str]:
            async with AsyncSessionLocal() as s:
                return await get_active_parse_tokens(s)

        return run_coro(_impl())
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("get_active_parse_tokens_sync fallback to env-only: %s", e)
        return {k: v for k, v in (VK_TOKENS or {}).items() if v}
