"""
Extended SQLAlchemy models for Postopus migration
Adds tables needed for full parity with old_postopus functionality
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database.connection import Base


def _new_sub() -> str:
    """Opaque OIDC subject для новых RadarUser (ADR-0002 §2)."""
    return str(uuid.uuid4())


class ParsingStats(Base):
    """Статистика парсинга (из old_postopus stat_mode)"""

    __tablename__ = "parsing_stats"

    id = Column(Integer, primary_key=True, index=True)
    region_code = Column(String(50), nullable=False, index=True)  # mi, vp, ur, etc
    theme = Column(String(50), nullable=False, index=True)  # novost, kultura, sport, reklama, etc

    # Execution info
    run_date = Column(DateTime, nullable=False, index=True)
    run_type = Column(String(20), default="scheduled")  # scheduled, manual, test
    duration_seconds = Column(Float, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)

    # Scanning stats
    total_groups_checked = Column(Integer, default=0)
    total_posts_scanned = Column(Integer, default=0)

    # Filter rejection stats
    posts_filtered_old = Column(Integer, default=0)
    posts_filtered_duplicate_lip = Column(Integer, default=0)
    posts_filtered_duplicate_text = Column(Integer, default=0)
    posts_filtered_duplicate_foto = Column(Integer, default=0)
    posts_filtered_black_id = Column(Integer, default=0)
    posts_filtered_no_region_words = Column(Integer, default=0)
    posts_filtered_advertisement = Column(Integer, default=0)
    posts_filtered_no_attachments = Column(Integer, default=0)
    posts_filtered_blacklist_text = Column(Integer, default=0)

    # Result stats
    posts_final_count = Column(Integer, default=0)
    groups_with_posts = Column(Integer, default=0)

    # Publishing info
    published_post_id = Column(Integer, nullable=True)  # VK post ID
    published_url = Column(String(500), nullable=True)
    published_to_test_polygon = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_parsing_stats_region_theme_date", "region_code", "theme", "run_date"),
    )

    def __repr__(self):
        return f"<ParsingStats {self.region_code}/{self.theme} @ {self.run_date}>"

    def to_dict(self):
        return {
            "id": self.id,
            "region_code": self.region_code,
            "theme": self.theme,
            "run_date": self.run_date.isoformat() if self.run_date else None,
            "run_type": self.run_type,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "error_message": self.error_message,
            "total_groups_checked": self.total_groups_checked,
            "total_posts_scanned": self.total_posts_scanned,
            "posts_filtered_old": self.posts_filtered_old,
            "posts_filtered_duplicate_lip": self.posts_filtered_duplicate_lip,
            "posts_filtered_duplicate_text": self.posts_filtered_duplicate_text,
            "posts_filtered_duplicate_foto": self.posts_filtered_duplicate_foto,
            "posts_filtered_black_id": self.posts_filtered_black_id,
            "posts_filtered_no_region_words": self.posts_filtered_no_region_words,
            "posts_filtered_advertisement": self.posts_filtered_advertisement,
            "posts_filtered_no_attachments": self.posts_filtered_no_attachments,
            "posts_filtered_blacklist_text": self.posts_filtered_blacklist_text,
            "posts_final_count": self.posts_final_count,
            "groups_with_posts": self.groups_with_posts,
            "published_post_id": self.published_post_id,
            "published_url": self.published_url,
            "published_to_test_polygon": self.published_to_test_polygon,
        }


class RegionConfig(Base):
    """Расширенная конфигурация региона (из MongoDB config collection)

    Хранит все настройки которые были в MongoDB:
    - zagolovki (заголовки сводок по темам)
    - heshteg (хештеги по темам)
    - heshteg_local (локальные хештеги)
    - black_id (заблокированные источники)
    - filter_group_by_region_words
    - kirov_words / tatar_words
    - time_old_post (возрастные пороги)
    - text_post_maxsize_simbols
    - delete_msg_blacklist
    - clear_text_blacklist
    - sosed (соседние регионы)
    - setka_regim_repost
    - baraban (темы для addons roulette)
    - only_main_news
    """

    __tablename__ = "region_configs"

    id = Column(Integer, primary_key=True, index=True)
    region_code = Column(String(50), unique=True, nullable=False, index=True)  # mi, vp, ur, etc

    # Bulletin configuration
    zagolovki = Column(JSON, nullable=True)  # {"novost": "Header", "kultura": "...", ...}
    heshteg = Column(JSON, nullable=True)  # {"novost": "новости", "kultura": "культура", ...}
    heshteg_local = Column(JSON, nullable=True)  # {"raicentr": "малмыж", ...}

    # Filtering configuration
    black_id = Column(JSON, nullable=True)  # [owner_id1, owner_id2, ...]
    filter_group_by_region_words = Column(JSON, nullable=True)  # {group_id: [words]}
    region_words = Column(JSON, nullable=True)  # {"kirov": ["слово1", ...], "tatar": [...]}
    # Населённые пункты района (список строк) — расширяет region_words
    # для RegionalRelevanceFilter, чтобы посты с упоминанием конкретных
    # сёл/деревень тоже считались релевантными региону.
    localities = Column(JSON, nullable=True)  # ["Цепочкино", "Гоньба", ...]
    only_main_news = Column(JSON, nullable=True)  # [group_id1, group_id2, ...]

    # Age thresholds (legacy / другие пайплайны)
    time_old_post = Column(
        JSON, nullable=True
    )  # {"hard": 86400, "medium": 172800, "light": 604800}

    # Пайплайн сводки: defaults + by_topic (см. modules/bulletin_pipeline_settings.py)
    bulletin_filters = Column(JSON, nullable=True)

    # Post limits
    text_post_maxsize_simbols = Column(Integer, default=4096)

    # Blacklists
    delete_msg_blacklist = Column(JSON, nullable=True)  # ["слово1", "фраза2", ...]
    fast_del_msg_blacklist = Column(JSON, nullable=True)  # динамический черный список
    clear_text_blacklist = Column(JSON, nullable=True)  # regex patterns для очистки

    # Neighbor configuration
    sosed = Column(String(500), nullable=True)  # "Малмыж - Инфо,Уржум - Инфо"

    # Repost mode
    setka_regim_repost = Column(Boolean, default=False)  # True = repost, False = copy

    # Addons roulette themes
    baraban = Column(JSON, nullable=True)  # ["novost", "kultura", "sport", ...]

    # Repost words blacklist
    repost_words_blacklist = Column(JSON, nullable=True)  # слова для дисквалификации репостов

    # MongoDB collection name mapping
    mongo_collection_name = Column(String(50), nullable=True)  # "mi", "vp", "ur", etc

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<RegionConfig {self.region_code}>"

    def to_dict(self):
        return {
            "id": self.id,
            "region_code": self.region_code,
            "zagolovki": self.zagolovki,
            "heshteg": self.heshteg,
            "heshteg_local": self.heshteg_local,
            "black_id": self.black_id,
            "filter_group_by_region_words": self.filter_group_by_region_words,
            "region_words": self.region_words,
            "localities": self.localities,
            "only_main_news": self.only_main_news,
            "time_old_post": self.time_old_post,
            "bulletin_filters": self.bulletin_filters,
            "text_post_maxsize_simbols": self.text_post_maxsize_simbols,
            "delete_msg_blacklist": self.delete_msg_blacklist,
            "fast_del_msg_blacklist": self.fast_del_msg_blacklist,
            "clear_text_blacklist": self.clear_text_blacklist,
            "sosed": self.sosed,
            "setka_regim_repost": self.setka_regim_repost,
            "baraban": self.baraban,
            "repost_words_blacklist": self.repost_words_blacklist,
            "mongo_collection_name": self.mongo_collection_name,
        }


class WorkTable(Base):
    """Рабочие таблицы из MongoDB (lip и hash для дедупликации)

    В old_postopus каждая тема имела свою таблицу с lip (ID постов) и hash (фото/видео отпечатки).
    Теперь храним в PostgreSQL.
    """

    __tablename__ = "work_tables"

    id = Column(Integer, primary_key=True, index=True)
    region_code = Column(String(50), nullable=False, index=True)  # mi, vp, ur, etc
    theme = Column(String(50), nullable=False, index=True)  # novost, kultura, sport, etc

    # Published post IDs (lip = "{abs(owner_id)}_{id}")
    lip = Column(JSON, nullable=True)  # ["123456_789", "987654_321", ...]

    # Photo/video fingerprint hashes (histogram MD5)
    hash = Column(JSON, nullable=True)  # ["md5hash1", "md5hash2", ...]

    # Bezfoto (text-only posts waiting to be published)
    bezfoto = Column(JSON, nullable=True)  # [{"text": "...", "source": "..."}, ...]

    # All bezfoto archive (published text-only posts)
    all_bezfoto = Column(JSON, nullable=True)  # archived text-only posts

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_work_tables_region_theme", "region_code", "theme", unique=True),)

    def __repr__(self):
        return f"<WorkTable {self.region_code}/{self.theme}>"

    def to_dict(self):
        return {
            "id": self.id,
            "region_code": self.region_code,
            "theme": self.theme,
            "lip_count": len(self.lip) if self.lip else 0,
            "hash_count": len(self.hash) if self.hash else 0,
            "bezfoto_count": len(self.bezfoto) if self.bezfoto else 0,
            "all_bezfoto_count": len(self.all_bezfoto) if self.all_bezfoto else 0,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ScheduledPublication(Base):
    """Запланированные публикации (для Smart Scheduler)

    Таблица для хранения запланированных публикаций которые ещё не выполнены.
    """

    __tablename__ = "scheduled_publications"

    id = Column(Integer, primary_key=True, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)

    # Publication details
    theme = Column(String(50), nullable=False)  # novost, kultura, etc
    category = Column(String(50), nullable=False)  # same as theme for now

    # Scheduled time
    scheduled_for = Column(DateTime, nullable=False, index=True)

    # Content (prepared bulletin)
    content = Column(Text, nullable=True)  # prepared bulletin text
    attachments = Column(JSON, nullable=True)  # prepared attachments
    post_ids = Column(JSON, nullable=True)  # [post_id1, post_id2, ...] included in bulletin

    # Status
    status = Column(
        String(20), default="scheduled", index=True
    )  # scheduled, processing, published, failed, cancelled
    published_at = Column(DateTime, nullable=True)
    published_vk_post_id = Column(Integer, nullable=True)
    published_url = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(50), default="scheduler")  # scheduler, manual, api

    # Relationships
    region = relationship("Region", backref="scheduled_publications")

    __table_args__ = (
        Index("ix_scheduled_pub_region_status", "region_id", "status"),
        Index("ix_scheduled_pub_scheduled_for", "scheduled_for"),
    )

    def __repr__(self):
        return f"<ScheduledPublication {self.theme} @ {self.scheduled_for}>"

    def to_dict(self):
        return {
            "id": self.id,
            "region_id": self.region_id,
            "theme": self.theme,
            "category": self.category,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "status": self.status,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "published_vk_post_id": self.published_vk_post_id,
            "published_url": self.published_url,
            "error_message": self.error_message,
            "post_count": len(self.post_ids) if self.post_ids else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
        }


class BulletinCurationRun(Base):
    """Shadow-журнал LLM-курации сводок (PoC, миграция 035).

    Один прогон = одна опубликованная порция сводки. После публикации
    (текущим детерминированным путём) вошедшие посты паркуются сюда; /curate
    проставляет per-post вердикт. Публикация не зависит от этой строки —
    recorder изолирован (отдельная сессия, best-effort). См.
    modules/curation/recorder.py и письмо brain 2026-06-07."""

    __tablename__ = "bulletin_curation_runs"

    id = Column(Integer, primary_key=True, index=True)
    region_code = Column(String(50), nullable=False, index=True)
    theme = Column(String(50), nullable=False, index=True)
    kind = Column(String(20), nullable=False, default="regular")  # regular|mourning|neighbors
    status = Column(String(20), nullable=False, default="pending", index=True)  # pending|reviewed
    shadow = Column(Boolean, nullable=False, default=True)

    # [{lip, owner_id, post_id, text, has_media, url}] — посты сводки
    candidates = Column(JSON, nullable=False)
    total_count = Column(Integer, nullable=False)

    # [{lip, verdict: keep|drop, reason}] — заполняет /curate
    verdicts = Column(JSON, nullable=True)
    flagged_count = Column(Integer, nullable=True)  # сколько drop = дельта над алгоритмом
    tokens_estimate = Column(Integer, nullable=True)

    published_post_id = Column(Integer, nullable=True)
    published_url = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            f"<BulletinCurationRun {self.region_code}/{self.theme} "
            f"status={self.status} n={self.total_count}>"
        )

    def to_dict(self):
        return {
            "id": self.id,
            "region_code": self.region_code,
            "theme": self.theme,
            "kind": self.kind,
            "status": self.status,
            "shadow": self.shadow,
            "candidates": self.candidates or [],
            "total_count": self.total_count,
            "verdicts": self.verdicts,
            "flagged_count": self.flagged_count,
            "tokens_estimate": self.tokens_estimate,
            "published_post_id": self.published_post_id,
            "published_url": self.published_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
        }


class RadarUser(Base):
    """Пользователь web-слоя setka: оператор или radar-user (миграция 037, Ф0.1).

    Роли: ``operator`` — весь существующий setka (регионы/CRM/токены/...);
    ``radar`` — только контент-радар (свои источники/лента/архив). Изоляцию
    enforce'ит AuthGateMiddleware (middleware/auth_gate.py), а не route-код.

    quota_bytes/used_bytes — учёт личного архива радара: схема сразу (решение
    владельца «вечно + предупредительные квоты»), enforcement — Ф1.
    """

    __tablename__ = "radar_users"

    id = Column(Integer, primary_key=True, index=True)
    # login/password_hash nullable с миграции 052: соц-only аккаунты без пароля.
    login = Column(String(64), nullable=True, unique=True, index=True)
    password_hash = Column(String(256), nullable=True)
    role = Column(String(16), nullable=False, default="radar")  # operator|radar
    is_active = Column(Boolean, nullable=False, default=True)

    # Радар-ID / OIDC аккаунт-слой (миграция 052, ADR-0002 §2).
    # sub — opaque OIDC subject (UUID строкой), НЕ serial PK.
    sub = Column(String(36), nullable=False, unique=True, index=True, default=_new_sub)
    email = Column(String(255), nullable=True)  # unique по lower(email) в БД
    email_verified = Column(Boolean, nullable=False, default=False)
    display_name = Column(String(128), nullable=True)
    vk_user_id = Column(BigInteger, nullable=True)  # partial-unique в БД
    telegram_user_id = Column(BigInteger, nullable=True)  # partial-unique в БД
    yandex_id = Column(String(64), nullable=True)  # partial-unique в БД

    quota_bytes = Column(BigInteger, nullable=False, default=209_715_200)  # 200 MB
    used_bytes = Column(BigInteger, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    # Курсор новизны ленты (миграция 039, Ф0.4): всё с RadarItem.id больше
    # курсора UI показывает как непрочитанное.
    last_seen_item_id = Column(BigInteger, nullable=True)

    def __repr__(self):
        return f"<RadarUser {self.login} role={self.role} active={self.is_active}>"

    def to_dict(self):
        return {
            "id": self.id,
            "login": self.login,
            "role": self.role,
            "is_active": self.is_active,
            "quota_bytes": self.quota_bytes,
            "used_bytes": self.used_bytes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


class OAuthClient(Base):
    """OIDC-клиент Радар-ID (миграция 052, ADR-0002 §8 — ручная регистрация).

    ``redirect_uris`` — JSON-массив ТОЧНЫХ uri (символ-в-символ, punycode
    для .рф — G108). ``allowed_scopes`` — space-separated потолок: клиент
    физически не получит claims сверх разрешённого (ADR-0002 §3).
    """

    __tablename__ = "oauth_clients"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String(64), nullable=False, unique=True, index=True)
    client_secret_hash = Column(String(256), nullable=True)  # NULL = public PKCE-only
    name = Column(String(128), nullable=False)
    redirect_uris = Column(JSON, nullable=False, default=list)
    allowed_scopes = Column(String(255), nullable=False, default="openid")
    is_confidential = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def scope_list(self) -> list:
        return [s for s in (self.allowed_scopes or "").split() if s]

    def __repr__(self):
        return f"<OAuthClient {self.client_id} ({self.name})>"


class OAuthAuthCode(Base):
    """Single-use authorization code (миграция 052).

    Храним sha256(code), не сырой код. Single-use enforce'ится ``used_at``:
    повторный обмен кода — признак атаки/бага, отклоняем.
    """

    __tablename__ = "oauth_auth_codes"

    # BIGSERIAL в PG; variant Integer — автоинкремент в SQLite-тестах.
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    code_hash = Column(String(128), nullable=False, unique=True, index=True)
    client_id = Column(String(64), nullable=False)
    user_id = Column(Integer, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False)
    redirect_uri = Column(Text, nullable=False)
    scope = Column(String(255), nullable=False)
    code_challenge = Column(String(128), nullable=True)
    code_challenge_method = Column(String(10), nullable=True)  # S256
    nonce = Column(String(255), nullable=True)
    auth_time = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<OAuthAuthCode client={self.client_id} user={self.user_id}>"


class OAuthRefreshToken(Base):
    """Refresh-токен с ротацией и family-based reuse-detection (ADR-0002 §5.2).

    Все ротации одной сессии делят ``family_id``; предъявление уже
    погашенного (rotated/revoked) токена → отзыв всей family.
    """

    __tablename__ = "oauth_refresh_tokens"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    family_id = Column(String(36), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(String(64), nullable=False)
    scope = Column(String(255), nullable=False)
    rotated_from = Column(BigInteger, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<OAuthRefreshToken family={self.family_id} user={self.user_id}>"


class ContentClassification(Base):
    """Пер-пост вердикт HITL-классификатора (миграция 054, ADR-0003).

    Shadow-фаза: только пишем, контент не трогаем. Источник постов — свод­ки
    (``bulletin_curation_runs.candidates``): активный конвейер не пишет Post-строки
    (posts пуста), а копит кандидатов в свод­ках. Ключ поста — ``lip``
    ("<owner_abs>_<post_id>", структурный фингерпринт). Текст/URL — снапшот на
    момент классификации (кандидат в свод­ке транзиентен). ``verdict`` JSONB —
    схема ADR-0003 §B; ``merge_with`` — список lip.
    """

    __tablename__ = "content_classifications"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    lip = Column(String(50), nullable=False, unique=True, index=True)
    region_code = Column(String(50), nullable=False, index=True)
    post_text = Column(Text, nullable=True)
    post_url = Column(String(300), nullable=True)
    source = Column(String(20), nullable=False, default="routine")  # routine | api
    model = Column(String(50), nullable=True)
    verdict = Column(JSON, nullable=False)
    confidence = Column(Integer, nullable=True)
    shadow = Column(Boolean, nullable=False, default=True)
    escalated = Column(Boolean, nullable=False, default=False)
    tokens_estimate = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # Финализация оператором (миграция 055): пост уходит из ленты только когда
    # оператор явно завершил вердикт («Согласен со всем» / «Готово»), а не после
    # первого клика — иначе нельзя внести составной вердикт (тема И действие).
    reviewed_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "lip": self.lip,
            "region_code": self.region_code,
            "post_text": (self.post_text or "").strip(),
            "post_url": self.post_url,
            "source": self.source,
            "model": self.model,
            "verdict": self.verdict or {},
            "confidence": self.confidence,
            "shadow": self.shadow,
            "escalated": self.escalated,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<ContentClassification lip={self.lip} region={self.region_code}>"


class ClassificationCorrection(Base):
    """Лог реакции оператора на вердикт (миграция 053, ADR-0003).

    ``outcome``: ``agree`` (согласие одним тыком) | ``correct`` (поправка).
    Согласие тоже строка здесь, чтобы agree-rate = agrees / (agrees + corrects)
    по ``verdict_type`` (theme|action|merge) считался одним запросом. Сырьё для
    метрики shadow-гейта + дистилляции в файл-корректировщик (родня deny-лог #054).
    """

    __tablename__ = "classification_corrections"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    classification_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("content_classifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lip = Column(String(50), nullable=False)
    verdict_type = Column(String(20), nullable=False)  # theme | action | merge
    outcome = Column(String(10), nullable=False, default="correct")  # agree | correct
    ai_value = Column(JSON, nullable=True)
    operator_value = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<ClassificationCorrection cls={self.classification_id} "
            f"{self.verdict_type}:{self.outcome}>"
        )


class CollectedPostAudit(Base):
    """Shadow-журнал каждого собранного поста с решением фильтра (миграция 056, ADR-0004).

    Классификатор видит ОБЕ стороны сбора: ``kept`` (пост прошёл детерминированный
    фильтр — кандидат в публикацию) и ``dropped`` + причина (выброшен). Пишется
    fail-safe рекордером на границе сбора (``modules/curation/collection_audit.py``),
    причина пере-выводится теми же чистыми функциями, что и ``_filter_post``.
    Механические дропы (возраст/дедуп/black_id) НЕ пишутся — только content-дропы.
    Ключ ``lip`` совпадает с ``content_classifications.lip``.
    """

    __tablename__ = "collected_post_audit"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    lip = Column(String(50), nullable=False, unique=True, index=True)
    region_code = Column(String(50), nullable=False, index=True)
    theme = Column(String(50), nullable=True)
    post_text = Column(Text, nullable=True)
    post_url = Column(String(300), nullable=True)
    has_media = Column(Boolean, nullable=False, default=False)
    # Компактная сводка вложений [{type, url?, ext?, title?}] (миграция 060) —
    # чтобы классификатор мог посмотреть фото/PDF постов без текста через
    # media-прокси. NULL = вложений нет / собрано до миграции.
    media = Column(JSON, nullable=True)
    decision = Column(String(12), nullable=False)  # kept | dropped
    # advertisement | blacklist_text | no_region_words | no_attachments (NULL для kept)
    drop_reason = Column(String(32), nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "lip": self.lip,
            "region_code": self.region_code,
            "theme": self.theme,
            "post_text": (self.post_text or "").strip(),
            "post_url": self.post_url,
            "has_media": self.has_media,
            "media": self.media or [],
            "decision": self.decision,
            "drop_reason": self.drop_reason,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
        }

    def __repr__(self):
        return f"<CollectedPostAudit lip={self.lip} {self.decision}:{self.drop_reason}>"


class ClassificationRule(Base):
    """Выученное правило классификатора (миграция 057, ADR-0005).

    Overlay поверх базового ``config/classification_postulates.md`` (git): рутина
    дистиллирует коррекции оператора в ЧЕРНОВИКИ правил (``proposed``), оператор в
    ленте ``/classifier`` утверждает/правит/отклоняет. ``approved`` подмешиваются в
    эффективные постулаты, которые рутина читает каждый прогон. Нейросеть правила
    сама не применяет — только предлагает; человек в петле. Родня deny-лог #054.
    """

    __tablename__ = "classification_rules"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    region_code = Column(String(50), nullable=True, index=True)  # NULL = глобальное
    rule_text = Column(Text, nullable=False)
    # proposed | approved | rejected | retired
    status = Column(String(12), nullable=False, default="proposed", index=True)
    source = Column(String(12), nullable=False, default="routine")  # routine | operator
    rationale = Column(Text, nullable=True)
    evidence = Column(JSON, nullable=True)
    model = Column(String(50), nullable=True)
    norm_key = Column(String(200), nullable=True, index=True)  # нормализация для дедупа
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)
    last_effective_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "region_code": self.region_code,
            "rule_text": (self.rule_text or "").strip(),
            "status": self.status,
            "source": self.source,
            "rationale": self.rationale,
            "evidence": self.evidence or [],
            "model": self.model,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "last_effective_at": (
                self.last_effective_at.isoformat() if self.last_effective_at else None
            ),
        }

    def __repr__(self):
        return f"<ClassificationRule id={self.id} {self.status}: {(self.rule_text or '')[:40]!r}>"


class ClassifierTheme(Base):
    """Канонический словарь тем классификатора (миграция 069, заказ владельца 2026-07-18).

    Тема вердикта была свободной строкой — за две недели накопилось ~180
    вариантов (дубли рус/англ, опечатки). Теперь темы живут словарём: рутина
    получает список в эффективных постулатах, оператор управляет через
    редактор на ``/classifier`` (добавить / удалить с переносом постов).
    """

    __tablename__ = "classifier_themes"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    position = Column(Integer, nullable=False, default=0)  # порядок в списках UI/постулатах
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<ClassifierTheme {self.name!r}>"


class RadarSource(Base):
    """Источник контент-радара (миграция 038, Ф0.2).

    Fan-out: источник поллится один раз на всех подписчиков (требование
    директивы brain 2026-06-11). ``key`` — нормализованный идентификатор
    внутри ``type``: vk → owner_id стены строкой ('-218688001'),
    rss → канонизированный URL фида, tg → username канала (Ф0.3).
    """

    __tablename__ = "radar_sources"
    __table_args__ = (Index("uq_radar_sources_type_key", "type", "key", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(8), nullable=False)  # vk|tg|rss
    key = Column(String(512), nullable=False)
    title = Column(String(256), nullable=True)
    url = Column(String(1024), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    last_polled_at = Column(DateTime, nullable=True)
    last_item_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, nullable=False, default=0)
    last_error = Column(String(512), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<RadarSource {self.type}:{self.key} active={self.is_active}>"

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "key": self.key,
            "title": self.title,
            "url": self.url,
            "is_active": self.is_active,
            "last_polled_at": self.last_polled_at.isoformat() if self.last_polled_at else None,
            "last_item_at": self.last_item_at.isoformat() if self.last_item_at else None,
            "fail_count": self.fail_count,
        }


class RadarSubscription(Base):
    """Подписка radar-юзера на источник (миграция 038, Ф0.2)."""

    __tablename__ = "radar_subscriptions"
    __table_args__ = (
        Index("uq_radar_subscriptions_user_source", "user_id", "source_id", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(
        BigInteger, ForeignKey("radar_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Per-user пауза источника без удаления (миграция 045, кабинет радара): fan-out
    # не страдает — источник поллится, пока на него есть хоть одна подписка; пауза
    # лишь убирает его из ленты/выводов этого юзера.
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = relationship("RadarSource", lazy="joined")

    def __repr__(self):
        return f"<RadarSubscription user={self.user_id} source={self.source_id}>"


class RadarItem(Base):
    """Элемент ленты радара — общий seen-стор (миграция 038, Ф0.2).

    Уникальность (source_id, external_id) даёт дедуп на уровне БД: поллер
    вставляет ON CONFLICT DO NOTHING, повторный фетч того же поста — no-op.
    ``media`` — превью-метаданные ([{type, url}]); байты архива — Ф0.4.
    """

    __tablename__ = "radar_items"
    __table_args__ = (
        Index("uq_radar_items_source_external", "source_id", "external_id", unique=True),
        Index("ix_radar_items_source_published", "source_id", "published_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(
        BigInteger, ForeignKey("radar_sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id = Column(String(256), nullable=False)
    url = Column(String(1024), nullable=True)
    title = Column(String(512), nullable=True)
    text = Column(Text, nullable=True)
    media = Column(JSON, nullable=True)
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<RadarItem source={self.source_id} ext={self.external_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "source_id": self.source_id,
            "external_id": self.external_id,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "media": self.media or [],
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


class RadarSaved(Base):
    """Сохранёнка радара — СНИМОК элемента ленты (миграция 039, Ф0.4).

    Не FK-ссылка на содержимое: элементы ленты подлежат ретенции, сохранёнки
    живут вечно (решение владельца). ``item_id`` — только для дедупа «уже
    сохранено», гаснет в NULL при чистке элемента. Фото скачаны на диск
    (см. modules/radar/archive.py), видео — ссылкой.
    """

    __tablename__ = "radar_saved"
    __table_args__ = (Index("ix_radar_saved_user_saved_at", "user_id", "saved_at"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(BigInteger, ForeignKey("radar_items.id", ondelete="SET NULL"), nullable=True)

    source_title = Column(String(256), nullable=True)
    url = Column(String(1024), nullable=True)
    title = Column(String(512), nullable=True)
    text = Column(Text, nullable=True)
    media = Column(JSON, nullable=True)  # [{type, url|file, bytes}]
    published_at = Column(DateTime, nullable=True)

    archived_bytes = Column(BigInteger, nullable=False, default=0)
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<RadarSaved user={self.user_id} item={self.item_id}>"

    def to_dict(self):
        return {
            "id": self.id,
            "item_id": self.item_id,
            "source_title": self.source_title,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "media": self.media or [],
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "archived_bytes": self.archived_bytes,
            "saved_at": self.saved_at.isoformat() if self.saved_at else None,
        }


class RadarPushSubscription(Base):
    """Web-push подписка браузера radar-юзера (миграция 040, Ф0.5).

    endpoint/p256dh/auth — поля PushSubscription из Push API. Подписок у
    юзера может быть несколько (телефон + десктоп). 404/410 от push-сервиса
    = подписка умерла → строка удаляется (modules/radar/push.py).
    """

    __tablename__ = "radar_push_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint = Column(String(1024), nullable=False, unique=True)
    p256dh = Column(String(256), nullable=False)
    auth = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_success_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<RadarPushSubscription user={self.user_id} endpoint={self.endpoint[:40]}...>"


class RadarOutput(Base):
    """Целевой канал вывода радара — куда слать найденное (миграция 045, кабинет).

    Ядро запроса владельца: пользователь сам набирает свой набор выводов.
    Типы:
      ``feed``     — внутренняя лента Ф0.4 (дефолт, всегда доступен, без внешних
                     прав); доставка — no-op, лента наполняется поллером сама;
      ``telegram`` — бот ``sendMessage`` в личку/канал юзера (``target`` —
                     chat_id или @channel; ``config.bot_name`` — какой бот, дефолт
                     радар-бот). api.telegram.org с этого бокса доступен (probe
                     2026-06-14) — relay для Bot API не нужен;
      ``vk``       — ``wall.post`` в стену/сообщество (``target`` — owner_id,
                     отрицательный для сообщества). Текст+ссылка; медиа не
                     рехостится (атрибуцию текстом — урок G64).

    ``mode`` — режим пересылки: ``excerpt_link`` (начало+ссылка, дефолт, дёшево)
    или ``full`` (целиком). ``last_item_id`` — курсор доставки (at-most-once по
    монотонному ``radar_items.id``); при создании = текущий MAX(id), чтобы новый
    вывод не выстрелил бэклогом.
    """

    __tablename__ = "radar_outputs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("radar_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type = Column(String(16), nullable=False)  # feed|telegram|vk
    title = Column(String(200), nullable=True)
    target = Column(String(512), nullable=True)  # tg: chat_id/@channel; vk: owner_id; feed: NULL
    mode = Column(String(16), nullable=False, default="excerpt_link")  # excerpt_link|full
    config = Column(JSON, nullable=True)  # {bot_name?} и пр. (креды-ref в env)

    is_active = Column(Boolean, nullable=False, default=True)
    last_item_id = Column(BigInteger, nullable=False, default=0)  # курсор доставки
    last_delivery_at = Column(DateTime, nullable=True)
    fail_count = Column(Integer, nullable=False, default=0)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<RadarOutput user={self.user_id} type={self.type} active={self.is_active}>"

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "target": self.target,
            "mode": self.mode,
            "config": self.config or {},
            "is_active": self.is_active,
            "last_delivery_at": (
                self.last_delivery_at.isoformat() if self.last_delivery_at else None
            ),
            "fail_count": self.fail_count,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
