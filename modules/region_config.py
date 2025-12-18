"""
Централизованная система настроек регионов

Этот модуль содержит все настройки регионов в одном месте:
- Главные группы для публикации
- Группы для сбора информации по тематикам
- Telegram каналы
- Локальные хештеги
- Соседние регионы
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum


class CommunityCategory(Enum):
    """Категории сообществ для сбора информации"""
    ADMINISTRATION = "administration"  # Администрация
    CULTURE = "culture"  # Культура
    YOUTH = "youth"  # Молодежь
    SPORTS = "sports"  # Спорт
    PRESCHOOL_EDUCATION = "preschool_education"  # Дошкольное образование
    NEWS = "news"  # Новости
    ORTHODOX_NEWS = "orthodox_news"  # Православные новости
    ADVERTISING = "advertising"  # Реклама
    ENTERTAINMENT = "entertainment"  # Развлекательные
    SCIENCE_NEWS = "science_news"  # Новости науки


@dataclass
class RegionCommunityGroups:
    """Группы сообществ для сбора информации по тематикам"""
    administration: List[int] = None  # Администрация
    culture: List[int] = None  # Культура
    youth: List[int] = None  # Молодежь
    sports: List[int] = None  # Спорт
    preschool_education: List[int] = None  # Дошкольное образование
    news: List[int] = None  # Новости
    orthodox_news: List[int] = None  # Православные новости
    advertising: List[int] = None  # Реклама
    entertainment: List[int] = None  # Развлекательные
    science_news: List[int] = None  # Новости науки
    
    def __post_init__(self):
        """Инициализация пустых списков"""
        if self.administration is None:
            self.administration = []
        if self.culture is None:
            self.culture = []
        if self.youth is None:
            self.youth = []
        if self.sports is None:
            self.sports = []
        if self.preschool_education is None:
            self.preschool_education = []
        if self.news is None:
            self.news = []
        if self.orthodox_news is None:
            self.orthodox_news = []
        if self.advertising is None:
            self.advertising = []
        if self.entertainment is None:
            self.entertainment = []
        if self.science_news is None:
            self.science_news = []


@dataclass
class RegionConfig:
    """Конфигурация региона"""
    code: str
    name: str
    main_group_id: int  # Главная группа для публикации
    telegram_channel: Optional[str] = None
    neighbors: List[str] = None
    local_hashtags: List[str] = None
    community_groups: RegionCommunityGroups = None
    is_active: bool = True
    
    def __post_init__(self):
        """Инициализация пустых списков"""
        if self.neighbors is None:
            self.neighbors = []
        if self.local_hashtags is None:
            self.local_hashtags = []
        if self.community_groups is None:
            self.community_groups = RegionCommunityGroups()


# Централизованная конфигурация всех регионов
REGIONS_CONFIG: Dict[str, RegionConfig] = {
    "arbazh": RegionConfig(
        code="arbazh",
        name="АРБАЖ - ИНФО",
        main_group_id=-221504685,
        telegram_channel="@arbazh_info",
        neighbors=["пижанка", "советск"],
        local_hashtags=["#арбаж", "#арбажский_округ"],
        community_groups=RegionCommunityGroups(
            administration=[-181738832],  # Администрация Арбажского муниципального округа
            culture=[-182055629, -188235724],  # Библиотеки, музей
            preschool_education=[-207545703],  # МБДОУ д с Солнышко
            news=[-144586105],  # Новости Арбаж
            entertainment=[-118213799, -220080924],  # Арбаж БАРАХОЛКА, Арбаж Любимый посёлок
            science_news=[-182055629],  # Арбажская центральная библиотека им А П Батуева
        )
    ),
    
    "bal": RegionConfig(
        code="bal",
        name="БАЛТАСИ - ИНФО",
        main_group_id=-179203620,
        telegram_channel="@bal_info",
        neighbors=["кукмор", "вп", "малмыж", "уржум", "кильмезь"],
        local_hashtags=["#балтаси", "#балтасинский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-196009197],  # Балтасинский районный исполнительный комитет
            culture=[-217063440, -217076405],  # РДК, библиотека
            preschool_education=[-216899854, -206139027],  # Детские сады
            news=[-113610369],  # Балтаси - Народные новости
        )
    ),
    
    "klz": RegionConfig(
        code="klz",
        name="КИЛЬМЕЗЬ - ИНФО",
        main_group_id=-168172770,
        telegram_channel="@kilmez_info",
        neighbors=["порез", "нема", "нолинск", "уржум", "малмыж", "вп", "балтаси", "кукмор", "сюмси", "можга"],
        local_hashtags=["#кильмезь", "#кильмезский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-215627184],  # Администрация Дамаскинского сел поселения
            culture=[-193415095, -185990658],  # ДШИ, ДДТ
            preschool_education=[-180254282, -185491282, -181515906],  # Детские сады
            news=[-144060930],  # Новости Кильмезь
        )
    ),
    
    "kukmor": RegionConfig(
        code="kukmor",
        name="КУКМОР - ИНФО",
        main_group_id=-180812597,
        telegram_channel="@kukmor_info",
        neighbors=["балтаси", "вп", "малмыж", "уржум", "кильмезь"],
        local_hashtags=["#кукмор", "#кукморский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-211434090],  # Районный дом культуры
            culture=[-217078985, -211434090],  # Музей, РДК
            preschool_education=[-219289098, -216591981],  # Детские сады
            news=[-119193519],  # Наш Кукмор
        )
    ),
    
    "leb": RegionConfig(
        code="leb",
        name="ЛЕБЯЖЬЕ - ИНФО",
        main_group_id=-170437443,
        telegram_channel="@lebyaje_info",
        neighbors=["пижанка", "советск", "нолинск", "уржум", "нема", "малмыж"],
        local_hashtags=["#лебяжье", "#лебяжский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-181741245],  # Администрация Лебяжского муниципального округа
            culture=[-180001471, -179667093],  # Дом творчества, Знамя Октября
            preschool_education=[-207553182],  # МБДОУ Детский сад 1
            news=[-145291342],  # Новости Лебяжье
        )
    ),
    
    "mi": RegionConfig(
        code="mi",
        name="МАЛМЫЖ - ИНФО",
        main_group_id=-158787639,
        telegram_channel="@malmig_info",
        neighbors=["нолинск", "уржум", "нема", "кильмезь", "кукмор", "балтаси", "вп", "лебяжье"],
        local_hashtags=["#малмыж", "#малмыжский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-170319760],  # Администрация Малмыжского района
            culture=[-217788511, -146341292],  # ДК, библиотека
            preschool_education=[-214710009, -207479193],  # Детские сады
            news=[-120893935],  # НовостиМалмыжа
            sports=[-14193653, -224443019],  # Футбольный клуб, молодежь и спорт
            entertainment=[-193646189, -72660310],  # Аквариумистика МАЛМЫЖ, Добровольцы Малмыжа
            science_news=[-146341292],  # Малмыжская центральная детская библиотека
        )
    ),
    
    "nema": RegionConfig(
        code="nema",
        name="НЕМА - ИНФО",
        main_group_id=-168169352,
        telegram_channel="@nema_info",
        neighbors=["уржум", "малмыж", "кильмезь", "нолинск", "лебяжье", "суна", "богород", "порез"],
        local_hashtags=["#нема", "#немский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-186447782],  # Администрация Немского муниципального округа
            culture=[-193878580],  # Центр дополнительного образования
            preschool_education=[-189782545],  # МКДОУ детский сад 1 Сказка
            news=[-179286260],  # Вестник труда Нема
        )
    ),
    
    "nolinsk": RegionConfig(
        code="nolinsk",
        name="НОЛИНСК - ИНФО",
        main_group_id=-179306667,
        telegram_channel="@nolinsk_info",
        neighbors=["советск", "лебяжье", "уржум", "нема", "кильмезь", "суна", "верхошижемье", "малмыж"],
        local_hashtags=["#нолинск", "#нолинский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-212248725],  # Администрация МО Нолинское городское поселение
            culture=[-194347863, -126618857],  # НОЛИНСКИЙ ДОМ КУЛЬТУРЫ, Приход Успенского собора
            preschool_education=[-207534798, -207533949, -207533410],  # Детские сады
            news=[-179306667],  # НОЛИНСК - ИНФО | Афиша, новости, события
            orthodox_news=[-126618857],  # Приход Успенского собора г Нолинска
            entertainment=[-85165516, -163848824],  # Детская театральная студия Радуга, Злой нолинчанин Нолинск
            science_news=[-198355861, -211968372],  # КОГОБУ СШ с УИОП г Нолинска, Нолинский политехнический техникум
        )
    ),
    
    "pizhanka": RegionConfig(
        code="pizhanka",
        name="ПИЖАНКА - ИНФО",
        main_group_id=-221492354,
        telegram_channel="@pizhanka_info",
        neighbors=["яранск", "туж", "арбаж", "советск"],
        local_hashtags=["#пижанка", "#пижанский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-175834381],  # Администрация Пижанского муниципального округа
            culture=[-145876022, -183268906],  # Районный Дом культуры, Храм Рождества Христова
            preschool_education=[-207537617],  # МКДОУ д с Теремок
            news=[-145197036],  # Новости Пижанка
            orthodox_news=[-183268906],  # Храм Рождества Христова
        )
    ),
    
    "sovetsk": RegionConfig(
        code="sovetsk",
        name="СОВЕТСК - ИНФО",
        main_group_id=-221480320,
        telegram_channel="@sovetsk_info",
        neighbors=["лебяжье", "верхошижемье", "нолинск", "арбаж", "пижанка"],
        local_hashtags=["#советск", "#советский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-179575298, -202016687],  # Администрация Советского района, г Советска
            culture=[-187487815, -202463683],  # РДНТ, краеведческий музей
            preschool_education=[-207530605, -209523108],  # Детские сады
            news=[-118690902],  # Новости Советск
        )
    ),
    
    "test": RegionConfig(
        code="test",
        name="Тест-Инфо",
        main_group_id=-137760500,
        telegram_channel=None,
        neighbors=[],
        local_hashtags=["#тест"],
        community_groups=RegionCommunityGroups(
            administration=[-156168183],  # Администрация Малмыжского района (для тестов)
        )
    ),
    
    "ur": RegionConfig(
        code="ur",
        name="УРЖУМ - ИНФО",
        main_group_id=-168170215,
        telegram_channel="@ur_info",
        neighbors=["лебяжье", "советск", "нолинск", "нема", "кильмезь", "малмыж", "вп"],
        local_hashtags=["#уржум", "#уржумский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-201417955, -201418118],  # Уржумское городское и сельское поселения
            culture=[-126990056, -217777091],  # КДЦ Уржума, Управление культуры
            preschool_education=[-212074880],  # детском доме Уржума
            news=[-189169977],  # Культурно информационный центр Пиляндыш
        )
    ),
    
    "verhoshizhem": RegionConfig(
        code="verhoshizhem",
        name="ВЕРХОШИЖЕМЬЕ - ИНФО",
        main_group_id=-221515888,
        telegram_channel="@verhoshizhem_info",
        neighbors=["советск", "лебяжье", "уржум", "нема", "кильмезь", "суна", "верхошижемье", "малмыж"],
        local_hashtags=["#верхошижемье", "#верхошижемский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-156863488],  # РДК п Верхошижемье
            culture=[-135546006, -156863488],  # Мой Верхошижемский район, РДК
            preschool_education=[-177762466],  # МКДОУ детский сад №1 "Сказка"
            news=[-144589844],  # Новости Верхошижемье
            sports=[-82485303, -45500301],  # Спортивная школа, Субботний баскетбол
        )
    ),
    
    "vp": RegionConfig(
        code="vp",
        name="ВЯТСКИЕ ПОЛЯНЫ - ИНФО",
        main_group_id=-166980909,
        telegram_channel="@vp_info",
        neighbors=["кукмор", "балтаси", "малмыж", "кильмезь", "уржум"],
        local_hashtags=["#вятские_поляны", "#вятскополянский_район"],
        community_groups=RegionCommunityGroups(
            administration=[-207316667],  # Управление образования города Вятские Поляны
            culture=[-209085784],  # IT-Клубе
            preschool_education=[-212691053],  # школе-интернате Сосновки
            news=[-62516242],  # ПВП
        )
    ),
}


class RegionConfigManager:
    """Менеджер конфигурации регионов"""
    
    @staticmethod
    def get_region_config(region_code: str) -> Optional[RegionConfig]:
        """Получить конфигурацию региона по коду"""
        return REGIONS_CONFIG.get(region_code.lower())
    
    @staticmethod
    def get_main_group_id(region_code: str) -> Optional[int]:
        """Получить ID главной группы региона"""
        config = RegionConfigManager.get_region_config(region_code)
        return config.main_group_id if config else None
    
    @staticmethod
    def get_community_groups_by_category(region_code: str, category: CommunityCategory) -> List[int]:
        """Получить группы сообществ по категории"""
        config = RegionConfigManager.get_region_config(region_code)
        if not config:
            return []
        
        category_field = category.value
        return getattr(config.community_groups, category_field, [])
    
    @staticmethod
    def get_all_community_groups(region_code: str) -> List[int]:
        """Получить все группы сообществ региона"""
        config = RegionConfigManager.get_region_config(region_code)
        if not config:
            return []
        
        all_groups = []
        for category in CommunityCategory:
            groups = RegionConfigManager.get_community_groups_by_category(region_code, category)
            all_groups.extend(groups)
        
        return list(set(all_groups))  # Убираем дубликаты
    
    @staticmethod
    def get_active_regions() -> List[str]:
        """Получить список активных регионов"""
        return [code for code, config in REGIONS_CONFIG.items() if config.is_active]
    
    @staticmethod
    def get_region_neighbors(region_code: str) -> List[str]:
        """Получить список соседних регионов"""
        config = RegionConfigManager.get_region_config(region_code)
        return config.neighbors if config else []
    
    @staticmethod
    def get_region_hashtags(region_code: str) -> List[str]:
        """Получить локальные хештеги региона"""
        config = RegionConfigManager.get_region_config(region_code)
        return config.local_hashtags if config else []
    
    @staticmethod
    def get_telegram_channel(region_code: str) -> Optional[str]:
        """Получить Telegram канал региона"""
        config = RegionConfigManager.get_region_config(region_code)
        return config.telegram_channel if config else None


# Функции для обратной совместимости
def get_vk_production_groups() -> Dict[str, int]:
    """Получить словарь главных групп для публикации (обратная совместимость)"""
    return {
        region_code: config.main_group_id 
        for region_code, config in REGIONS_CONFIG.items()
        if config.is_active
    }


def get_vk_test_group_id() -> int:
    """Получить ID тестовой группы"""
    return REGIONS_CONFIG["test"].main_group_id
