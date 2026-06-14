"""
SETKA - Main FastAPI application
Multimedia management system for news resources
"""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from _version import __version__ as APP_VERSION
from database.connection import close_db, init_db
from middleware.auth_gate import AuthGateMiddleware
from middleware.metrics_middleware import MetricsMiddleware
from middleware.rate_limiter import RateLimitMiddleware
from modules.module_activity_notifier import notify_system_startup
from web.api import (
    ad_cabinet,
    ad_crm,
    auth,
    broadcast,
    communities,
    discovery,
    filtration,
    health,
    notifications,
    parsing,
    parsing_stats,
    posts,
    publisher,
    radar,
    regions,
    schedule_management,
    scheduler,
    service_notifications,
    subscriber_growth,
    system_monitoring,
    task_monitoring,
)
from web.api import templates as templates_api
from web.api import test_workflow, token_management, vk_monitoring

# Setup logging
#
# Все логи идут через StreamHandler в stderr. На проде systemd (см.
# setka.service: StandardOutput=append:/home/valstan/SETKA/logs/uvicorn_production.log)
# перенаправляет stdout/stderr в файл — никакого отдельного FileHandler не
# нужно. До 2026-05-25 был дубль через logs/app.log с LOG_LEVEL=WARNING,
# но он:
#   - 100% дублировал содержимое stderr → uvicorn_production.log;
#   - копил почти ничего (порог WARNING, файл не рос неделями);
#   - усложнял main.py обработкой LOG_PATH env с try/except для Windows.
# Дефолт LOG_LEVEL=INFO даёт полезный объём для grep'а; на проде можно
# поднять до WARNING через env если станет шумно.
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events"""
    # Startup
    logger.info("Starting SETKA application...")
    await init_db()
    logger.info("Database initialized")

    # Start system status monitoring (ОТКЛЮЧЕНО - использует старую систему уведомлений)
    # await start_status_monitoring()
    logger.info("System status monitoring disabled - using new workflow notifications")

    # Уведомляем о запуске системы
    notify_system_startup()

    yield

    # Shutdown
    logger.info("Shutting down SETKA application...")
    await close_db()
    logger.info("Database connection closed")


# Create FastAPI app
app = FastAPI(
    title="SETKA",
    description="Multimedia Management System for News Resources",
    version=APP_VERSION,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# App-level auth + изоляция ролей operator|radar (Ф0.1 контент-радара).
# Secure by default: всё закрыто, кроме allowlist'а в middleware/auth_gate.py.
app.add_middleware(AuthGateMiddleware)

# Rate limiting middleware (защита от DoS)
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=100,  # 100 requests per minute per IP
    burst_size=20,
    whitelist=["127.0.0.1", "localhost"],  # Whitelist localhost
)

# Metrics middleware (мониторинг производительности)
app.add_middleware(MetricsMiddleware)

# Setup templates and static files
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
# Make APP_VERSION available in every template (footer uses {{ app_version }}).
templates.env.globals["app_version"] = APP_VERSION
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(radar.router, prefix="/api/radar", tags=["Content Radar"])
app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(regions.router, prefix="/api/regions", tags=["Regions"])
app.include_router(communities.router, prefix="/api/communities", tags=["Communities"])
app.include_router(posts.router, prefix="/api/posts", tags=["Posts"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(scheduler.router, prefix="/api/scheduler", tags=["Smart Scheduler"])
app.include_router(vk_monitoring.router, prefix="/api/vk", tags=["VK Monitoring"])
app.include_router(token_management.router, prefix="/api/tokens", tags=["Token Management"])
app.include_router(service_notifications.router, tags=["Service Notifications"])
app.include_router(test_workflow.router, tags=["Test Workflow"])
app.include_router(schedule_management.router, tags=["Schedule Management"])
app.include_router(system_monitoring.router, tags=["System Monitoring"])
app.include_router(task_monitoring.router, tags=["Task Monitoring"])
app.include_router(publisher.router, prefix="/api/publisher", tags=["VK Publisher"])
app.include_router(parsing.router, tags=["Parsing"])
app.include_router(parsing_stats.router, tags=["Parsing Stats"])  # Postopus migration
app.include_router(filtration.router, prefix="/api/filtration", tags=["Filtration"])
app.include_router(templates_api.router, prefix="/api/templates", tags=["Message Templates"])
app.include_router(ad_cabinet.router, prefix="/api/ad-cabinet", tags=["Ad Cabinet"])
app.include_router(ad_crm.router, prefix="/api/ad-crm", tags=["Ad CRM"])
app.include_router(broadcast.router, prefix="/api/broadcast", tags=["Network Broadcast"])
app.include_router(
    subscriber_growth.router, prefix="/api/subscriber-growth", tags=["Subscriber Growth"]
)
app.include_router(discovery.router, prefix="/api/discovery", tags=["Region Discovery"])


@app.get("/")
async def root(request: Request):
    """Main dashboard page"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
async def login_page(request: Request):
    """Логин/регистрация (Ф0.1) — единственная публичная страница."""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/radar")
async def radar_page(request: Request):
    """Радар (Ф0.4): PWA-лента по подпискам + архив + управление источниками."""
    return templates.TemplateResponse("radar.html", {"request": request})


@app.get("/radar/sw.js")
async def radar_service_worker():
    """Service worker Радара. Отдаётся с /radar/* (внутри RADAR_PREFIXES гейта);
    Service-Worker-Allowed расширяет scope до /radar (сам файл лежит глубже)."""
    return FileResponse(
        str(BASE_DIR / "web" / "static" / "radar" / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/radar"},
    )


@app.get("/regions")
async def regions_page(request: Request):
    """Regions page"""
    return templates.TemplateResponse("regions.html", {"request": request})


@app.get("/posts")
async def posts_page(request: Request):
    """Posts page"""
    return templates.TemplateResponse("posts.html", {"request": request})


@app.get("/communities")
async def communities_page(request: Request):
    """Communities page"""
    return templates.TemplateResponse("communities.html", {"request": request})


@app.get("/notifications")
async def notifications_page(request: Request):
    """Notifications page"""
    return templates.TemplateResponse("notifications.html", {"request": request})


@app.get("/templates")
async def templates_page(request: Request):
    """Message templates CRUD page (etap 4b)"""
    return templates.TemplateResponse("templates.html", {"request": request})


@app.get("/ad")
async def ad_page(request: Request):
    """Единый рекламный кабинет (С1): инбокс, CRM, планировщик и статистика во вкладках."""
    return templates.TemplateResponse("ad.html", {"request": request})


@app.get("/broadcast")
async def broadcast_page(request: Request):
    """Сетевая рассылка: композер кампании + цели + расписание/повтор + очередь."""
    return templates.TemplateResponse("broadcast.html", {"request": request})


@app.get("/ad-cabinet")
async def ad_cabinet_page():
    """Старый путь — редирект на единый /ad (вкладка «Входящие заявки»)."""
    return RedirectResponse(url="/ad")


@app.get("/ad-crm")
async def ad_crm_page():
    """Старый путь — редирект на единый /ad (вкладка «Клиенты и воронка»)."""
    return RedirectResponse(url="/ad#crm")


@app.get("/subscriber-growth")
async def subscriber_growth_page(request: Request):
    """Сравнительная динамика роста подписчиков сообществ (один график + чекбоксы)."""
    return templates.TemplateResponse("subscriber_growth.html", {"request": request})


@app.get("/regions/new")
async def region_new_page(request: Request):
    """Wizard для добавления нового региона (big idea 2026-05-22)."""
    return templates.TemplateResponse("region_new.html", {"request": request})


@app.get("/regions/{region_code}/discovery")
async def region_discovery_page(request: Request, region_code: str):
    """Список кандидатов на сообщества для региона (big idea 2026-05-22)."""
    return templates.TemplateResponse(
        "region_discovery.html",
        {"request": request, "region_code": region_code},
    )


@app.get("/regions/{region_code}/prepare")
async def region_prepare_page(request: Request, region_code: str):
    """Подготовка discovery: localities + keywords для региона."""
    return templates.TemplateResponse(
        "region_prepare.html",
        {"request": request, "region_code": region_code},
    )


@app.get("/regions/{region_code}/diagnostics")
async def region_diagnostics_page(request: Request, region_code: str):
    """Прогон пайплайна без публикации (dry-run): что отфильтровалось / собралось."""
    return templates.TemplateResponse(
        "region_diagnostics.html",
        {"request": request, "region_code": region_code},
    )


@app.get("/regions/{region_code}/discovery/ai-batch")
async def region_ai_batch_page(request: Request, region_code: str):
    """Human-in-the-loop AI categorisation через clipboard."""
    return templates.TemplateResponse(
        "region_ai_batch.html",
        {"request": request, "region_code": region_code},
    )


@app.get("/tokens")
async def tokens_page(request: Request):
    """Token management page"""
    return templates.TemplateResponse("tokens.html", {"request": request})


@app.get("/publisher")
async def publisher_page(request: Request):
    """VK Publisher page"""
    return templates.TemplateResponse("publisher.html", {"request": request})


@app.get("/monitoring")
async def monitoring_page(request: Request):
    """Service monitoring page"""
    return templates.TemplateResponse("monitoring.html", {"request": request})


@app.get("/test_monitoring")
async def test_monitoring_page(request: Request):
    """Test monitoring page"""
    with open("test_monitoring.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)


@app.get("/diagnostic_monitoring")
async def diagnostic_monitoring_page(request: Request):
    """Diagnostic monitoring page"""
    with open("diagnostic_monitoring.html", "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content)


@app.get("/schedule")
async def schedule_page(request: Request):
    """Schedule management page"""
    return templates.TemplateResponse("schedule.html", {"request": request})


@app.get("/parsing")
async def parsing_page(request: Request):
    """VK parsing page"""
    return templates.TemplateResponse("parsing.html", {"request": request})


@app.get("/parsing-stats")
async def parsing_stats_page(request: Request):
    """Parsing statistics page (Postopus migration)"""
    return templates.TemplateResponse("parsing_stats.html", {"request": request})


@app.get("/publications")
async def publications_page(request: Request):
    """История публикаций дайджестов по регионам/темам (со ссылками на VK)."""
    return templates.TemplateResponse("publications.html", {"request": request})


@app.get("/filtration")
async def filtration_page(request: Request):
    """Настройки фильтрации дайджестов и правил отбора постов"""
    return templates.TemplateResponse("filtration.html", {"request": request})


@app.get("/favicon.ico")
async def favicon():
    """Favicon"""
    from fastapi.responses import Response

    # Простой SVG favicon
    svg_content = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" '
        'viewBox="0 0 32 32">'
        '<rect width="32" height="32" fill="#0d6efd"/>'
        '<text x="16" y="20" text-anchor="middle" fill="white" '
        'font-family="Arial" font-size="16" font-weight="bold">S</text>'
        "</svg>"
    )
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/metrics")
async def metrics(request: Request):
    """
    Prometheus metrics endpoint.

    Доступ — только локально (127.0.0.1). Prometheus scrape'ит из того же
    хоста, для внешних запросов отдаём 404, чтобы не светить structure.
    Override через env ``SETKA_METRICS_PUBLIC=1`` если надо открыть наружу
    (например, dev-локально или специально настроенный proxy).
    """
    import os

    from fastapi import HTTPException
    from fastapi.responses import Response

    from monitoring.metrics import get_metrics, update_business_metrics

    if os.getenv("SETKA_METRICS_PUBLIC", "").strip() not in ("1", "true", "yes"):
        client_host = request.client.host if request.client else None
        # Через nginx-proxy client.host = 127.0.0.1, но реальный IP — в X-Forwarded-For.
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        effective_ip = forwarded or client_host or ""
        if effective_ip not in ("127.0.0.1", "::1", "localhost", ""):
            raise HTTPException(status_code=404)

    await update_business_metrics()
    content, content_type = await get_metrics()
    return Response(content=content, media_type=content_type)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
