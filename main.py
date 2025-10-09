"""
SETKA - Main FastAPI application
Multimedia management system for news resources
"""
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
import logging

from database.connection import get_db_session, init_db, close_db
from web.api import health, regions, communities, posts, workflow

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

# Include routers
app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(regions.router, prefix="/api/regions", tags=["Regions"])
app.include_router(communities.router, prefix="/api/communities", tags=["Communities"])
app.include_router(posts.router, prefix="/api/posts", tags=["Posts"])
app.include_router(workflow.router, tags=["Workflow"])


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "SETKA",
        "version": "1.0.0",
        "description": "Multimedia Management System for News Resources",
        "docs": "/docs",
        "health": "/api/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

