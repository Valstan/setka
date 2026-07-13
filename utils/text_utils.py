"""
Text utilities migrated from old_postopus bin/utils/

Provides text cleaning, normalization, and search utilities
used by the parsing and filtering pipeline.
"""

import re
from typing import List, Optional


def text_to_rafinad(text: str) -> str:
    """
    Strips all non-word characters from text.
    Equivalent to old_postopus bin/utils/text_to_rafinad.py

    Used for text deduplication - normalizes text before fingerprint comparison.
    """
    if not text:
        return ""
    # Keep only word characters (letters, digits, underscore)
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE)


def clear_text(text: str, blacklist: Optional[List[str]] = None) -> str:
    """
    Clean text using regex patterns from blacklist.
    Migrated from old_postopus clear_text.py

    Args:
        text: Input text
        blacklist: List of regex patterns to remove

    Returns:
        Cleaned text
    """
    if not text:
        return ""

    if not blacklist:
        # Default patterns
        blacklist = [
            r"#\w+",  # hashtags
            r"@\w+",  # mentions
            r"http\S+",  # URLs
        ]

    cleaned = text
    for pattern in blacklist:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.MULTILINE)

    return cleaned.strip()


def is_advertisement(text: str, skip_for_reklama: bool = False, theme: str = "") -> bool:
    """
    Multi-level advertisement detection.
    Migrated from old_postopus bin/utils/is_advertisement.py

    Levels:
    1. VK API marked_as_ads (handled separately in parser)
    2. Legal advertising markers (#реклама, #ad, erid:, etc.) - IMMEDIATE True
    3. Commercial patterns scoring (prices, discounts, CTA, contacts)
    4. Suspicious attachments (ads links)

    Args:
        text: Post text to analyze
        skip_for_reklama: If True, skip ad detection for reklama theme
        theme: Current theme (reklama, novost, etc.)

    Returns:
        True if post is advertisement
    """
    if not text:
        return False

    # Skip ad detection for reklama theme
    if skip_for_reklama and theme == "reklama":
        return False

    text_lower = text.lower()

    # Level 2: Legal advertising markers (IMMEDIATE True)
    legal_markers = [
        "#реклама",
        "#реклама",
        "#ad",
        "#ad",
        "#sponsored",
        "#партнёрство",
        "#партнерство",
        "erid:",
        "на правах рекламы",
    ]

    for marker in legal_markers:
        if marker.lower() in text_lower:
            return True

    # Level 3: Commercial patterns scoring
    # Объединено в один список с weight=2 (см. DEV_HISTORY 2026-05-23 «Legacy
    # flake8 cleanup PR 1»): раньше тут было два списка под одинаковым ключом
    # 2 — Python молча оставлял только второй (Calls to action), а первый
    # (Prices and discounts) терялся. Объединение восстанавливает изначальный
    # intent — посты с ценами/скидками снова детектируются как реклама.
    commercial_patterns = {
        # Явное намерение купли-продажи ТОВАРА (частное объявление) → сразу реклама
        # (граница score>=4). Ловит «продам ВАЗ», «куплю косилку» — их is_advertisement
        # раньше пропускал (нет цены/телефона в тексте), и они утекали в сводку (найдено
        # 2026-07-07 при разборе коррекций оператора). УСЛУГИ мастеров используют иные
        # глаголы («оказываю», «ремонт», «проведение») и сюда НЕ попадают → остаются на
        # решение оператора (hold), как решил владелец (градация коммерции). \b считает
        # границу по \w (Cyrillic-aware в Python 3), поэтому «распродам»/«распродажа»
        # не матчатся.
        4: [
            r"\bпродам\b",
            r"\bпродаю\b",
            r"\bпрода[её]тся\b",
            r"\bкуплю\b",
            r"\bобменяю\b",
        ],
        2: [
            # Prices and discounts
            r"цена[:\s]\d+",
            r"стоимость[:\s]\d+",
            r"скидка",
            r"распродажа",
            r"акция\s*[:\s]",
            r"\d+\s*руб",
            r"\d+\s*₽",
            r"бесплатно",
            r"дешево",
            r"недорого",
            r"купить",
            r"заказать",
            # Calls to action
            r"звоните[:\s]",
            r"пишите[:\s]",
            r"тел[.:]?\s*[\d+\-]",
            r"т[.:]?\s*[\d+\-]",
            r"моб[.:]?\s*[\d+\-]",
            r"whatsapp",
            r"telegram",
        ],
        1: [
            r"подробности",
            r"узнать больше",
            r"переходите",
            r"подписывайтесь",
        ],
    }

    score = 0
    for weight, patterns in commercial_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                score += weight

    # Score >= 4 = advertisement
    if score >= 4:
        return True

    # Level 4: Suspicious attachments/links
    suspicious_links = [
        "vk.com/ads",
        "target.vk.com",
        "ads.vk.com",
    ]

    for link in suspicious_links:
        if link in text_lower:
            score += 1

    return score >= 4


# ────────────────────────────── hard spam / scam ──────────────────────────────
# «Жёсткий спам»/скам — контент, за который VK банит аккаунт-администратора
# паблика (инцидент Уржум 2026-07-08: рекламная сводка ре-транслировала объявление
# «удалённая работа / рассылка рекламы / по готовой системе» из источника-агрегатора
# → бан аккаунта на 4 дня). В ОТЛИЧИЕ от is_advertisement, этот фильтр применяется
# ДАЖЕ к теме reklama (доска объявлений): легальное частное объявление («продам
# велосипед», «продам ВАЗ») остаётся, а мошеннический / уводящий-в-обход-VK контент
# режется до публикации. Список маркеров узкий и высокоточный — цель: почти ноль
# ложных срабатываний на реальных районных объявлениях (иначе фильтр молча съест
# легальную рекламу).
_HARD_SPAM_PATTERNS = [
    # Дистанционный «заработок» / сетевой скам (главный триггер инцидента).
    r"удал[её]нн\w*\s+работ",
    r"работа\s+на\s+дому",
    r"работа\s+в\s+интернет",
    r"подработк\w*\s+(?:на\s+дому|в\s+интернет|удал[её]нн)",
    r"рассылк\w*\s+реклам",
    r"по\s+готовой\s+системе",
    r"пассивн\w+\s+доход",
    r"(?:доход|заработок)\s+от\s+\d",
    r"без\s+вложени",
    r"не\s+требует\s+вложени",
    r"финансов\w+\s+независим",
    # Ставки / казино / займы / крипта — типовой запрещённый в районных пабликах спам.
    r"казино",
    r"букмекер",
    r"ставк\w*\s+на\s+спорт",
    r"\b(?:1xbet|винлайн|фонбет|мостбет|париматч|бетсити)\b",
    r"займ\w*\s+(?:без|онлайн|на\s+карт)",
    r"кредит\w*\s+без\s+(?:отказа|справок|залога)",
    r"криптовалют",
    r"бинарн\w+\s+опцион",
    # Серые «услуги с документами» (инцидент 2026-07-13: бан VALSTAN за
    # ре-трансляцию объявления «Оkaжу поmощь в полyчeнии водитeльcкого
    # yдоcтовеpения…» из агрегатора). Матчится по НОРМАЛИЗОВАННОМУ тексту
    # (см. _normalize_lookalikes) — обфускация латиницей не спасает.
    r"помощ\w*\s+(?:в\s+)?получени\w*\s+(?:водительск|прав\b|удостоверен|"
    r"медицинск\w*\s+комисси|допуск)",
    r"помощ\w*\s+лиш[её]нн",
    r"помощ\w*\s+(?:в|с)\s+(?:замен\w*|сдач\w*)\s+(?:иностранн\w*\s+)?прав",
    r"помощ\w*\s+с\s+экзамен",
    r"куп(?:ить|лю|и)\s+права\b",
    r"права\s+без\s+экзамен",
    r"допуск\w*\s+на\s+перевозк\w*\s+опасн\w*\s+груз",
]
_HARD_SPAM_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _HARD_SPAM_PATTERNS]

# Латинские (и цифровые) двойники кириллических букв — скамеры подменяют ими
# буквы, чтобы обойти текстовые фильтры («Оkaжу поmощь», «tpанcпоpт»).
# Для ДЕТЕКЦИИ (не для отображения) приводим текст к кириллице и гоняем
# паттерны по нормализованной копии.
_LOOKALIKE_MAP = str.maketrans(
    {
        "a": "а",
        "A": "А",
        "b": "в",
        "B": "В",
        "c": "с",
        "C": "С",
        "e": "е",
        "E": "Е",
        "h": "н",
        "H": "Н",
        "k": "к",
        "K": "К",
        "m": "м",
        "M": "М",
        "o": "о",
        "O": "О",
        "p": "р",
        "P": "Р",
        "t": "т",
        "T": "Т",
        "x": "х",
        "X": "Х",
        "y": "у",
        "Y": "У",
        "3": "з",
        "0": "о",
    }
)

# Слово, в котором СМЕШАНЫ кириллица и латиница («полyчeнии», «поmощь») —
# верный признак умышленной обфускации: легальный текст пишет латинские слова
# целиком (WhatsApp, Telegram), а не вкрапляет буквы внутрь кириллических.
_MIXED_ALPHABET_WORD_RE = re.compile(r"\b(?=\w*[а-яё])(?=\w*[a-z])[a-zа-яё]{3,}\b", re.IGNORECASE)

# Порог: ≥3 обфусцированных слов в посте. Единичное смешение бывает опечаткой
# (латинская «c» вместо «с»), но 3+ слов — целенаправленная маскировка скама.
_MIXED_ALPHABET_THRESHOLD = 3


def _normalize_lookalikes(text: str) -> str:
    """Привести латинских двойников к кириллице (детекция обфусцированного скама)."""
    return text.translate(_LOOKALIKE_MAP)


def _count_mixed_alphabet_words(text: str) -> int:
    """Сколько слов смешивают кириллицу и латиницу (сигнал обфускации)."""
    return len(_MIXED_ALPHABET_WORD_RE.findall(text))


# Мессенджеры-«воронки» увода из VK. Один паттерн на мессенджер (чтобы считать
# РАЗНЫЕ мессенджеры, а не повторы). ≥3 разных в одном посте — почти всегда
# скам-контакт («Wa, Tg, Max +7…»), редкий паттерн для легального объявления,
# где обычно 1-2 контакта.
_FUNNEL_MESSENGER_RE = [
    re.compile(p, re.IGNORECASE | re.UNICODE)
    for p in [
        r"whatsapp|ват?сап\w*|вотсап\w*|\bwa\b",  # WhatsApp
        r"telegram|телеграм\w*|\btg\b",  # Telegram
        r"viber|вайбер\w*",  # Viber
        r"\bmax\b|\bмах\b",  # МАХ (мессенджер VK), латиницей/кириллицей
    ]
]


def is_hard_spam(text: str) -> bool:
    """Detect high-risk scam / off-platform-funnel content.

    В отличие от :func:`is_advertisement`, применяется ДАЖЕ к теме ``reklama`` —
    легальные частные объявления остаются, но мошеннический контент, за который VK
    банит аккаунт-администратора паблика, режется до публикации. Введён после
    инцидента Уржум 2026-07-08 (рекламная сводка → «удалённая работа / рассылка
    рекламы» → бан 4 дня).

    Returns:
        True, если текст — жёсткий спам/скам (сработал строгий маркер ИЛИ ≥3 разных
        мессенджера-воронки в одном посте).
    """
    if not text:
        return False
    # Обфускация латиницей («Оkaжу поmощь в полyчeнии…») — сама по себе скам-сигнал
    # (инцидент 2026-07-13, бан VALSTAN): легальные объявления так не пишут.
    if _count_mixed_alphabet_words(text) >= _MIXED_ALPHABET_THRESHOLD:
        return True
    # Паттерны гоняем и по сырому, и по нормализованному тексту (двойники
    # приведены к кириллице) — «kaзино» и «помощь в полyчении прав» не проскочат.
    normalized = _normalize_lookalikes(text)
    for rx in _HARD_SPAM_RE:
        if rx.search(text) or rx.search(normalized):
            return True
    # Воронка увода из VK: ≥3 РАЗНЫХ мессенджера в одном посте.
    messenger_hits = sum(1 for rx in _FUNNEL_MESSENGER_RE if rx.search(text))
    return messenger_hits >= 3


def check_blacklist(text: str, blacklist: List[str]) -> Optional[str]:
    """
    Check if text contains any blacklisted words/phrases.

    Args:
        text: Text to check
        blacklist: List of blacklisted words/phrases

    Returns:
        Matched pattern or None
    """
    if not text or not blacklist:
        return None

    text_lower = text.lower()

    for pattern in blacklist:
        pattern_lower = pattern.lower()
        if pattern_lower in text_lower:
            return pattern

    return None


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to max_length, adding suffix if truncated.

    Args:
        text: Input text
        max_length: Maximum length
        suffix: Suffix to add if truncated

    Returns:
        Truncated text
    """
    if not text or len(text) <= max_length:
        return text

    # Truncate and add suffix
    return text[: max_length - len(suffix)] + suffix
