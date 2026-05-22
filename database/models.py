"""
SQLAlchemy models for SETKA project
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, ForeignKey, Index, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from database.connection import Base


class Region(Base):
    """Регион (район)"""
    __tablename__ = "regions"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)  # mi, nolinsk, arbazh
    name = Column(String(200), nullable=False)  # МАЛМЫЖ - ИНФО
    
    # VK and Telegram
    vk_group_id = Column(Integer, nullable=True)  # ID группы VK для публикации
    telegram_channel = Column(String(100), nullable=True)  # @malmig_info
    
    # Neighboring regions
    neighbors = Column(String(500), nullable=True)  # советск,лебяж,уржум
    
    # Hashtags
    local_hashtags = Column(Text, nullable=True)
    
    # Configuration
    config = Column(JSON, nullable=True)  # Дополнительные настройки
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    communities = relationship("Community", back_populates="region")
    posts = relationship("Post", back_populates="region")
    
    def __repr__(self):
        return f"<Region {self.code}: {self.name}>"


class Community(Base):
    """Сообщество VK для мониторинга"""
    __tablename__ = "communities"
    
    id = Column(Integer, primary_key=True, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    
    # VK info
    vk_id = Column(Integer, nullable=False, index=True)  # ID группы VK (отрицательное)
    screen_name = Column(String(100), nullable=True)  # short name
    name = Column(String(300), nullable=False)
    
    # Category
    category = Column(String(50), nullable=False, index=True)  # admin, novost, reklama, etc
    
    # Monitoring settings
    is_active = Column(Boolean, default=True)
    check_interval = Column(Integer, default=300)  # seconds
    last_checked = Column(DateTime, nullable=True)
    last_post_id = Column(Integer, nullable=True)  # ID последнего поста
    
    # Statistics
    posts_count = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    region = relationship("Region", back_populates="communities")
    posts = relationship("Post", back_populates="community")
    
    __table_args__ = (
        Index('ix_communities_region_category', 'region_id', 'category'),
    )
    
    def __repr__(self):
        return f"<Community {self.name} ({self.category})>"


class Post(Base):
    """Пост из VK"""
    __tablename__ = "posts"
    
    id = Column(Integer, primary_key=True, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    community_id = Column(Integer, ForeignKey("communities.id"), nullable=False)
    
    # VK post info
    vk_post_id = Column(Integer, nullable=False)
    vk_owner_id = Column(Integer, nullable=False)
    
    # Content
    text = Column(Text, nullable=True)
    attachments = Column(JSON, nullable=True)  # photos, videos, links
    
    # Metadata
    date_published = Column(DateTime, nullable=False)
    
    # Statistics from VK
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    reposts = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    
    # AI Analysis
    ai_category = Column(String(50), nullable=True)  # novost, reklama, etc
    ai_relevance = Column(Integer, nullable=True)  # 0-100
    ai_score = Column(Integer, nullable=True)  # Общая оценка
    ai_analyzed = Column(Boolean, default=False)
    ai_analysis_date = Column(DateTime, nullable=True)
    
    # Sentiment Analysis
    sentiment_label = Column(String(20), nullable=True)  # positive, neutral, negative
    sentiment_score = Column(Float, nullable=True)  # 0.0-1.0
    sentiment_emotions = Column(JSON, nullable=True)  # {joy, sadness, anger, fear}
    
    # Publishing status
    status = Column(String(20), default="new", index=True)  # new, analyzed, approved, published, rejected
    published_at = Column(DateTime, nullable=True)
    published_vk = Column(Boolean, default=False)
    published_telegram = Column(Boolean, default=False)
    published_wordpress = Column(Boolean, default=False)
    
    # Fingerprints for deduplication (inspired by Postopus)
    fingerprint_lip = Column(String(50), nullable=True, index=True)  # Структурный: "owner_id_post_id"
    fingerprint_media = Column(JSON, nullable=True)  # [photo_id1, photo_id2, video_id1]
    fingerprint_text = Column(String(100), nullable=True, index=True)  # Hash полного "рафинада"
    fingerprint_text_core = Column(String(100), nullable=True, index=True)  # Hash "сердцевины" (20-70%)
    
    # Flags
    is_duplicate = Column(Boolean, default=False)
    is_spam = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    region = relationship("Region", back_populates="posts")
    community = relationship("Community", back_populates="posts")
    
    __table_args__ = (
        Index('ix_posts_vk_id', 'vk_owner_id', 'vk_post_id'),
        Index('ix_posts_status', 'status'),
        Index('ix_posts_region_status', 'region_id', 'status'),
        Index('ix_posts_date', 'date_published'),
    )
    
    def __repr__(self):
        return f"<Post {self.vk_owner_id}_{self.vk_post_id}>"


class Filter(Base):
    """Фильтры для контента"""
    __tablename__ = "filters"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Filter info
    type = Column(String(50), nullable=False, index=True)  # blacklist_word, spam_pattern, region_word
    category = Column(String(50), nullable=True)  # admin, novost, etc
    
    # Pattern/word
    pattern = Column(Text, nullable=False)
    
    # Action
    action = Column(String(20), nullable=False)  # delete, flag, score
    score_modifier = Column(Integer, default=0)  # Изменение оценки
    
    # Metadata
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Filter {self.type}: {self.pattern[:30]}>"


class VKToken(Base):
    """VK токены для динамического управления.

    Если `community_id` задан — это community access token, привязанный к
    конкретному сообществу (используется для `messages.getConversations` без
    группового scope у user-токена). Хранится как `abs(group_id)`.
    """
    __tablename__ = "vk_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False, index=True)  # VALSTAN, OLGA, VITA, COMM_158787639...
    token = Column(Text, nullable=False)  # VK API токен
    community_id = Column(Integer, nullable=True, index=True)  # abs(vk_group_id), если это community token
    is_active = Column(Boolean, default=True, index=True)  # Активен ли токен
    last_used = Column(DateTime, nullable=True)  # Последнее использование
    last_validated = Column(DateTime, nullable=True)  # Последняя валидация
    validation_status = Column(String(20), default='unknown', index=True)  # valid, invalid, unknown
    error_message = Column(Text)  # Сообщение об ошибке при валидации
    permissions = Column(JSON)  # Права доступа токена
    user_info = Column(JSON)  # Информация о пользователе / community-info для community-токенов
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VKToken(name='{self.name}', status='{self.validation_status}', active={self.is_active}, community_id={self.community_id})>"

    def to_dict(self):
        """Преобразовать в словарь для API"""
        return {
            "id": self.id,
            "name": self.name,
            "token": self.token[:20] + "..." if len(self.token) > 20 else self.token,  # Маскируем токен
            "community_id": self.community_id,
            "is_active": self.is_active,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_validated": self.last_validated.isoformat() if self.last_validated else None,
            "validation_status": self.validation_status,
            "error_message": self.error_message,
            "permissions": self.permissions if isinstance(self.permissions, list) else (self.permissions.get('permissions', []) if self.permissions else []),
            "user_info": self.user_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_full_token(self):
        """Получить полный токен (для внутреннего использования)"""
        return self.token


class MessageTemplate(Base):
    """Шаблон ответа на сообщение сообщества (этап 4b).

    Используется UI `/templates` для CRUD и dropdown'ом в модалке ответа
    на VK direct message. Шаблоны общие на все регионы — это сознательно:
    модератор обычно один, и шаблоны типа «спасибо за обращение, передадим»
    не зависят от региона. Если когда-то понадобится per-region — добавим
    region_id nullable + UI-фильтр.
    """
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(120), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(50), nullable=True, index=True)  # 'greeting', 'thanks', 'redirect', ...
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<MessageTemplate {self.id} {self.title!r}>"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "category": self.category,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PublishSchedule(Base):
    """Расписание публикаций"""
    __tablename__ = "publish_schedules"
    
    id = Column(Integer, primary_key=True, index=True)
    region_id = Column(Integer, ForeignKey("regions.id"), nullable=False)
    
    # Schedule
    category = Column(String(50), nullable=False)  # novost, reklama, etc
    hour = Column(Integer, nullable=False)  # 0-23
    minute = Column(Integer, nullable=False)  # 0-59
    
    # Days of week (0-6, Monday-Sunday)
    days_of_week = Column(String(20), default="0,1,2,3,4,5,6")  # All days by default
    
    # Status
    is_active = Column(Boolean, default=True)
    last_run = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<PublishSchedule {self.category} at {self.hour}:{self.minute}>"

