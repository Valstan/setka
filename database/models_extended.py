"""
Extended SQLAlchemy models for Postopus migration
Adds tables needed for full parity with old_postopus functionality
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey, Index, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from database.connection import Base


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
        Index('ix_parsing_stats_region_theme_date', 'region_code', 'theme', 'run_date'),
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
    - zagolovki (заголовки дайджестов по темам)
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
    
    # Digest configuration
    zagolovki = Column(JSON, nullable=True)  # {"novost": "Header", "kultura": "...", ...}
    heshteg = Column(JSON, nullable=True)  # {"novost": "новости", "kultura": "культура", ...}
    heshteg_local = Column(JSON, nullable=True)  # {"raicentr": "малмыж", ...}
    
    # Filtering configuration
    black_id = Column(JSON, nullable=True)  # [owner_id1, owner_id2, ...]
    filter_group_by_region_words = Column(JSON, nullable=True)  # {group_id: [words]}
    region_words = Column(JSON, nullable=True)  # {"kirov": ["слово1", ...], "tatar": [...]}
    only_main_news = Column(JSON, nullable=True)  # [group_id1, group_id2, ...]
    
    # Age thresholds
    time_old_post = Column(JSON, nullable=True)  # {"hard": 86400, "medium": 172800, "light": 604800}
    
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
            "only_main_news": self.only_main_news,
            "time_old_post": self.time_old_post,
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

    __table_args__ = (
        Index('ix_work_tables_region_theme', 'region_code', 'theme', unique=True),
    )

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
    
    # Content (prepared digest)
    content = Column(Text, nullable=True)  # prepared digest text
    attachments = Column(JSON, nullable=True)  # prepared attachments
    post_ids = Column(JSON, nullable=True)  # [post_id1, post_id2, ...] included in digest
    
    # Status
    status = Column(String(20), default="scheduled", index=True)  # scheduled, processing, published, failed, cancelled
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
        Index('ix_scheduled_pub_region_status', 'region_id', 'status'),
        Index('ix_scheduled_pub_scheduled_for', 'scheduled_for'),
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
