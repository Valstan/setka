"""HTML-страницы, которые браузер обязан перепроверять перед показом.

Зачем это существует
--------------------

31.08 несогласованную пару «новый HTML + старый CSS» вылечили в статике —
``web/static_files.py`` отдаёт ``Cache-Control: no-cache``. **Сам документ в ту
починку не попал**, и 06.09 это стоило второй итерации: правку вкладки
планировщика ([#657](https://github.com/Valstan/setka/pull/657)) выкатили на
прод, файл на диске новый, сервисы подняты, health 200 — а браузер при обычном
переходе на ``/ad#scheduler`` показал **старый** шаблон. Замер на живом проде:

* в инлайн-скрипте страницы нет ``adShowTabByHash`` — то есть шаблон прошлой
  версии;
* тег скрипта несёт прежний кэш-бастер ``ad_cabinet.js?v=20260905_outreach``;
* тот же адрес с уникальным query отдал новую страницу и рабочую вкладку;
* ``fetch('/ad', {cache: 'no-store'})`` показывает ответ **без единого**
  кэш-заголовка: ни ``Cache-Control``, ни ``Expires``, ни ``ETag``, ни
  ``Last-Modified``.

Почему одной статики было мало
------------------------------

Кэш-бастеры ``?v=…`` живут **внутри** HTML. Пока документ берётся из кэша, из
него же берётся и разметка ссылок на статику — со старыми версиями. Механизм,
которым мы пробиваем кэш статики, сам оказывается за кэшем документа: свежий
``no-cache`` на ``/static/js/ad_cabinet.js`` ничего не решает, если браузер до
этого файла просто не дошёл, потому что старый HTML попросил другой URL.

Отсюда правило, которое держит этот модуль: **дверь запирается со стороны
документа**. Ревалидация статики отвечает за «файл свежий», ревалидация
документа — за «мы вообще спросили про этот файл».

Тот же разрыв «отправлено ≠ доставлено», что в
[#110](../brain_matrica/cross-project-ideas/ideas/110-delivered-means-echo-from-receiver.md):
со стороны сервера отказ невидим — в логах успешная отдача свежей страницы, и
«выкачено» перестаёт означать «видно».

Почему ``no-cache``, а не ``no-store``
--------------------------------------

``no-cache`` не запрещает **хранить** копию — он запрещает **показывать её без
переспроса**. Для операторской панели это дешевле полного запрета: копия
остаётся, перепроверка стоит один условный запрос. ``no-store`` оставлен там,
где он осмыслен по существу — ответам OIDC в ``web/api/radar_id.py``.

Чужую директиву модуль не трогает: если роут выставил ``Cache-Control``
сознательно (как ``no-store`` выше), он знает про свой ответ больше, чем общий
слой. Заголовок ставится только там, где его нет вообще.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["HtmlRevalidationMiddleware"]


class HtmlRevalidationMiddleware(BaseHTTPMiddleware):
    """Ставит ``Cache-Control: no-cache`` HTML-ответам без своей директивы."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("text/html"):
            return response
        # Явная директива роута — сознательное решение, оно старше общего слоя.
        if "cache-control" in response.headers:
            return response

        response.headers["Cache-Control"] = "no-cache"
        return response
