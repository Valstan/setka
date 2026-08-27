"""Тексты раскрутки.

Один принцип определяет здесь всё: **пост должен читаться как сообщение соседям,
а не как реклама.** Это не про вкус. VK отвечает кодом 219 («рекламный пост
недавно добавлен») на то, что его антиспам счёл рекламой, и такой ответ дороже
пропущенной публикации: он ставит метку на стену донора. Поэтому в шаблонах нет
призывов в повелительном наклонении, нет цепочек эмодзи, нет восклицательных
знаков подряд и нет слов «подпишись», «скорее», «только сегодня».

Второй принцип — не врать про расстояние. На первом хопе район действительно
соседний, на втором это уже неточно, и для него отдельный шаблон: «в сети» вместо
«по соседству». Мелочь, которую местный читатель замечает мгновенно.

Атрибуция даётся только текстом: параметр ``copyright`` у ``wall.post`` VK молча
отбрасывает для внутренних vk.com-ссылок (GOTCHAS G64), так что ссылка обязана
жить в теле поста.
"""

from __future__ import annotations

from typing import Optional, Sequence

# Потолок футера в сводке. Сводка и так упирается в 4096 символов, а вытеснить
# новость ради ссылки — обменять содержание на рекламу самих себя.
FOOTER_MAX_LENGTH = 160

# Сколько соседей показываем в футере. Больше трёх — это уже не подпись, а список.
FOOTER_MAX_ITEMS = 3


def _clean(text: str) -> str:
    """Убрать лишние пробелы по краям строк и схлопнуть пустые строки."""
    lines = [line.rstrip() for line in (text or "").strip().splitlines()]
    out = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def render_promo_post(
    *,
    target_name: str,
    target_url: str,
    hop: int = 1,
    district_hint: Optional[str] = None,
) -> str:
    """Промо-пост на стене донора.

    Args:
        target_name: человекочитаемое имя района («Суна», «Кумёны»).
        target_url: ссылка на сообщество — VK сам развернёт её в карточку, поэтому
            вложения не нужны (а значит, не нужен и user-токен на заливку фото).
        hop: 1 — прямой сосед, 2 — через одного, 3 — областная лента.
        district_hint: чем район интересен читателю донора («там работает
            автостанция», «оттуда возят молоко») — если владелец захочет уточнить.
    """
    if hop <= 1:
        opening = f"У соседей появилась своя лента новостей — {target_name}."
        relation = "Если у вас там родня, дача или работа, теперь всё видно в одном месте."
    else:
        opening = f"В нашей сети районных лент прибавление — {target_name}."
        relation = "Кому этот район близок по делам или родне — теперь есть куда заглянуть."

    tail = "Каждый день: что произошло, объявления, афиша."
    hint = f"\n{district_hint.strip()}" if district_hint and district_hint.strip() else ""

    return _clean(f"{opening}\n{relation}{hint}\n\n{target_url}\n\n{tail}")


def render_footer_line(neighbors: Sequence[dict]) -> str:
    """Строка-подпись со ссылками на соседние ленты для обычной сводки.

    Args:
        neighbors: элементы вида ``{"name": ..., "url": ...}`` — уже отобранные и
            отсортированные вызывающим (слабые районы вперёд).

    Returns:
        Пустая строка, если показывать нечего или подпись не влезает в лимит.
        Пустой результат — штатный исход: лучше сводка без подписи, чем сводка
        с обрезанной новостью.
    """
    items = [n for n in (neighbors or []) if n.get("name") and n.get("url")]
    if not items:
        return ""

    for count in range(min(FOOTER_MAX_ITEMS, len(items)), 0, -1):
        chunk = items[:count]
        rendered = "Ленты соседей: " + " · ".join(f"{item['name']} {item['url']}" for item in chunk)
        if len(rendered) <= FOOTER_MAX_LENGTH:
            return rendered
    return ""


def render_group_description(
    *,
    district_name: str,
    center_city: Optional[str] = None,
    site_url: Optional[str] = None,
) -> str:
    """Описание сообщества для автооформления.

    Описание — то, по чему VK находит сообщество во внутреннем поиске, и то, что
    человек читает первым, решая подписаться. У молодых групп оно пустое, и это
    одна из причин, по которой их не находят.
    """
    center = (center_city or "").strip()
    where = f" Центр — {center}." if center else ""
    site = f"\nСписок всех районов сети: {site_url}" if site_url else ""

    return _clean(
        f"Новости, объявления и афиша {district_name}.{where}\n"
        "Каждый день собираем, что пишут местные сообщества, и публикуем самое важное "
        "одной лентой — чтобы не листать десяток групп.\n"
        "Прислать новость или объявление можно сообщением сообществу."
        f"{site}"
    )


def render_welcome_post(
    *,
    district_name: str,
    neighbors: Sequence[dict] = (),
    site_url: Optional[str] = None,
) -> str:
    """Закреплённый пост-визитка нового сообщества."""
    lines = [
        f"Здесь — новости, объявления и афиша {district_name}.",
        "",
        "Каждый день просматриваем местные сообщества и собираем важное в одну ленту: "
        "что произошло, что продают и покупают, куда сходить.",
        "",
        "Есть новость или объявление — напишите сообщению сообщества, опубликуем.",
    ]

    picked = [n for n in (neighbors or []) if n.get("name") and n.get("url")][:5]
    if picked:
        lines.append("")
        lines.append("Ленты соседних районов:")
        lines.extend(f"{item['name']} — {item['url']}" for item in picked)

    if site_url:
        lines.append("")
        lines.append(f"Все районы сети: {site_url}")

    return _clean("\n".join(lines))


def render_oblast_digest(targets: Sequence[dict], *, site_url: Optional[str] = None) -> str:
    """Пост областной ленты со списком молодых районных лент.

    Один пост на несколько районов, а не по посту на каждый: с квотой «один промо
    в неделю на донора» очередь из девятнадцати районов растянулась бы на
    девятнадцать недель, и модуль умер бы, не начав работать.
    """
    picked = [t for t in (targets or []) if t.get("name") and t.get("url")]
    if not picked:
        return ""

    lines = [
        "В районах области заработали свои ленты новостей — небольшие, местные, " "каждый день.",
        "",
    ]
    lines.extend(f"{item['name']} — {item['url']}" for item in picked)
    if site_url:
        lines.append("")
        lines.append(f"Полный список: {site_url}")
    return _clean("\n".join(lines))


def render_outreach_draft(
    *,
    group_name: str,
    district_name: str,
    target_url: str,
    author_name: str = "",
) -> str:
    """Черновик обращения к администратору местной группы.

    Текст пишется от первого лица и отправляется **владельцем вручную** из своего
    профиля. SETKA его только готовит: автоматическая рассылка личных сообщений —
    это ровно то, что правила VK называют спамом, а страницы, заведённые ради неё,
    удаляют.
    """
    greeting = f"Здравствуйте! Пишу по поводу «{group_name}»."
    body = (
        f"Мы ведём ленту новостей {district_name} — {target_url}. "
        "Каждый день собираем, что пишут местные сообщества, и публикуем важное одной "
        "лентой; вашу группу читаем и ссылаемся на неё, когда берём оттуда новость."
    )
    ask = (
        "Если посчитаете полезным для своих подписчиков — буду признателен за упоминание "
        "или ссылку в описании. Со своей стороны готов так же поставить ссылку на вас."
    )
    signature = f"\n\n{author_name.strip()}" if author_name and author_name.strip() else ""

    return _clean(f"{greeting}\n\n{body}\n\n{ask}{signature}")
