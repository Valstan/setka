"""Гейт: ни одна страница не грузит CSS/JS/шрифты с чужого сервера.

31.08 владелец пожаловался, что «сайт сарафана весь поехал». К моменту проверки
всё уже работало само: прод здоров, health 200, деплоя не было, в логах пусто.
Причину нашли не в логах, а в шаблонах — Bootstrap, иконки, Chart.js и Quill
грузились с публичных CDN **браузером оператора**, и локальной копии не было ни
одной.

Отказ этого класса устроен неприятнее обычного бага:

1. **Он невидим со стороны сервера.** Подресурс тянет браузер, поэтому сбой не
   оставляет следа ни в одном нашем логе. Здоровый прод и разъехавшаяся
   страница — совместимые состояния, и это сбивает диагностику в первую очередь.
2. **Он лечится сам.** К моменту, когда до него доходят руки, симптом обычно
   исчез. Воспроизвести нечего, и разбор сваливается на угадывание.
3. **Он бьёт по всем страницам разом**, потому что общий шаблон один. Выглядит
   как катастрофа в коде, хотя код не менялся.

Поэтому гейт стоит не на «правильном CDN», а на **самом факте внешней ссылки**:
единственная надёжная защита — чтобы страницу и её подресурсы отдавал один и тот
же сервер. Тогда «страница открылась, а стилей нет» становится невозможным
состоянием, а не редким.

Отдельно проверяется, что локальные ссылки **ведут в существующий файл**.
Опечатка в пути `/static/vendor/...` даёт ровно исходный симптом — 404 на CSS
при живой странице, — и её не поймает ни один тест про внешние хосты.

NB про списки хостов. Первая ревизия искала три известных CDN
(`jsdelivr`, `cdnjs`, `unpkg`) и **пропустила Quill** на собственном
`cdn.quilljs.com`. Список хостов всегда неполон, потому что дополняется вручную,
а ссылки добавляют не глядя в список. Здесь проверяется свойство — «ссылка ведёт
наружу», — а не принадлежность к перечислению.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "web" / "templates"
STATIC_DIR = REPO_ROOT / "web" / "static"
VENDOR_DIR = STATIC_DIR / "vendor"

# <link href="..."> и <script src="..."> — только подресурсы. Обычные <a href>
# наружу это правило не касается: гиперссылка не участвует в отрисовке страницы.
_SUBRESOURCE_RE = re.compile(
    r"""<(?:link|script)\b[^>]*?\b(?:href|src)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)

# @import и url(...) в наших собственных стилях.
_CSS_EXTERNAL_RE = re.compile(
    r"""(?:@import\s+|url\(\s*)["']?((?:https?:)?//[^)"'\s]+)""",
    re.IGNORECASE,
)

# Ссылка на внешний .css/.js внутри наших скриптов (динамическая подгрузка).
_JS_EXTERNAL_ASSET_RE = re.compile(
    r"""["'](?:https?:)?//[^"']+\.(?:css|js)(?:\?[^"']*)?["']""",
    re.IGNORECASE,
)


def _is_external(url: str) -> bool:
    return url.startswith(("http://", "https://", "//"))


def _is_dynamic(url: str) -> bool:
    """Значение собирается шаблонизатором — статически проверить нечего."""
    return "{{" in url or "{%" in url


def test_templates_have_no_external_subresources() -> None:
    offenders = []
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for url in _SUBRESOURCE_RE.findall(line):
                if not _is_dynamic(url) and _is_external(url):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {url}")

    assert not offenders, (
        "Подресурс грузится с чужого сервера — вёрстка снова зависит от чужой "
        "доступности:\n  " + "\n  ".join(offenders) + "\n\nПоложи файл в "
        "web/static/vendor/ и сошлись локально (см. web/static/vendor/README.md)."
    )


def test_own_stylesheets_have_no_external_imports() -> None:
    css_root = STATIC_DIR / "css"
    offenders = []
    for path in sorted(css_root.rglob("*.css")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for url in _CSS_EXTERNAL_RE.findall(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {url}")

    assert (
        not offenders
    ), "Наш CSS подтягивает внешний ресурс через @import/url():\n  " + "\n  ".join(offenders)


def test_own_scripts_do_not_load_external_assets() -> None:
    js_root = STATIC_DIR / "js"
    offenders = []
    for path in sorted(js_root.rglob("*.js")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _JS_EXTERNAL_ASSET_RE.findall(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {match}")

    assert not offenders, "Наш JS подгружает стиль или скрипт с чужого сервера:\n  " + "\n  ".join(
        offenders
    )


def test_local_subresources_point_at_files_that_exist() -> None:
    """Опечатка в локальном пути даёт тот же симптом, что и упавший CDN."""
    missing = []
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for url in _SUBRESOURCE_RE.findall(line):
                if _is_dynamic(url) or _is_external(url) or not url.startswith("/static/"):
                    continue
                target = STATIC_DIR / url[len("/static/") :].split("?", 1)[0]
                if not target.is_file():
                    missing.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {url}")

    assert not missing, (
        "Шаблон ссылается на локальный файл, которого нет — страница откроется "
        "без стилей:\n  " + "\n  ".join(missing)
    )


def test_bootstrap_icons_fonts_sit_next_to_their_stylesheet() -> None:
    """bootstrap-icons.css зовёт шрифты относительным `./fonts/…`.

    Если подкаталог переехал, вёрстка останется целой, а иконки молча исчезнут —
    отказ заметен не сразу, поэтому проверяется отдельно от остальных путей.
    """
    css = VENDOR_DIR / "bootstrap-icons" / "bootstrap-icons.css"
    assert css.is_file(), f"нет {css.relative_to(REPO_ROOT)}"

    referenced = {
        ref.split("?", 1)[0].lstrip("./")
        for ref in re.findall(r"""url\(["']([^"')]+)["']\)""", css.read_text(encoding="utf-8"))
    }
    assert referenced, "в bootstrap-icons.css не нашлось ни одной ссылки на шрифт"

    missing = [ref for ref in sorted(referenced) if not (css.parent / ref).is_file()]
    assert not missing, f"шрифты иконок не лежат рядом с bootstrap-icons.css: {missing}"
