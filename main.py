"""
SETKA - Main FastAPI application
Multimedia management system for news resources
"""
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from database.connection import get_db_session, init_db, close_db
from modules.module_activity_notifier import notify_system_startup
from web.api import health, regions, communities, posts, workflow, notifications, scheduler, vk_monitoring, token_management, tokens, service_notifications, test_workflow, schedule_management, system_monitoring, task_monitoring, publisher

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/valstan/SETKA/logs/app.log'),
        logging.StreamHandler()
    ]
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
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting middleware (защита от DoS)
from middleware.rate_limiter import RateLimitMiddleware
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=100,  # 100 requests per minute per IP
    burst_size=20,
    whitelist=["127.0.0.1", "localhost"],  # Whitelist localhost
)

# Metrics middleware (мониторинг производительности)
from middleware.metrics_middleware import MetricsMiddleware
app.add_middleware(MetricsMiddleware)

# Setup templates and static files
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")

# Include routers
app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(regions.router, prefix="/api/regions", tags=["Regions"])
app.include_router(communities.router, prefix="/api/communities", tags=["Communities"])
app.include_router(posts.router, prefix="/api/posts", tags=["Posts"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(scheduler.router, prefix="/api/scheduler", tags=["Smart Scheduler"])
app.include_router(vk_monitoring.router, prefix="/api/vk", tags=["VK Monitoring"])
app.include_router(token_management.router, prefix="/api/tokens", tags=["Token Management"])
app.include_router(tokens.router, tags=["Tokens"])
app.include_router(service_notifications.router, tags=["Service Notifications"])
app.include_router(test_workflow.router, tags=["Test Workflow"])
app.include_router(schedule_management.router, tags=["Schedule Management"])
app.include_router(workflow.router, tags=["Workflow"])
app.include_router(system_monitoring.router, tags=["System Monitoring"])
app.include_router(task_monitoring.router, tags=["Task Monitoring"])
app.include_router(publisher.router, prefix="/api/publisher", tags=["VK Publisher"])


@app.get("/")
async def root(request: Request):
    """Main dashboard page"""
    return templates.TemplateResponse("index.html", {"request": request})


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


@app.get("/favicon.ico")
async def favicon():
    """Favicon"""
    from fastapi.responses import Response
    # Простой SVG favicon
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
        <rect width="32" height="32" fill="#0d6efd"/>
        <text x="16" y="20" text-anchor="middle" fill="white" font-family="Arial" font-size="16" font-weight="bold">S</text>
    </svg>'''
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint
    
    Returns metrics in Prometheus format
    """
    from fastapi.responses import Response
    from monitoring.metrics import get_metrics, update_business_metrics
    
    # Update business metrics before export
    await update_business_metrics()
    
    # Get metrics
    content, content_type = get_metrics()
    
    return Response(content=content, media_type=content_type)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

