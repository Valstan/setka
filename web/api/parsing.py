"""
VK parsing API endpoints.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional
import os
import uuid
from datetime import datetime

from config.runtime import VK_TOKENS
from utils.cache import get_cache

router = APIRouter(prefix="/api/parsing", tags=["Parsing"])

OUTPUT_DIR = "/home/valstan/SETKA/logs/parser"
JOB_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_ACTIVE_JOBS = 1
ACTIVE_JOBS_KEY = "parser:active_jobs"


class ParsingRequest(BaseModel):
    source: str = Field(..., description="VK URL, screen name, или ID")
    format: str = Field("html", description="html или txt")
    download_attachments: bool = True
    include_text: bool = True
    include_photos: bool = True
    include_videos: bool = False
    include_audio: bool = False
    include_links: bool = False
    include_docs: bool = False
    include_polls: bool = False
    date_from: Optional[str] = None  # YYYY-MM-DD
    date_to: Optional[str] = None    # YYYY-MM-DD
    limit: int = Field(200, ge=1, le=5000)


class ParsingStartResponse(BaseModel):
    job_id: str
    status: str
    message: str


class ParsingSkipRequest(BaseModel):
    video_id: str


def _job_key(job_id: str) -> str:
    return f"parser:job:{job_id}"


def _validate_date(date_str: Optional[str], field_name: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Неверный формат {field_name}, нужен YYYY-MM-DD")


async def _set_job(job_id: str, payload: dict):
    cache = get_cache()
    await cache.set(_job_key(job_id), payload, ttl=JOB_TTL_SECONDS)


async def _get_job(job_id: str) -> Optional[dict]:
    cache = get_cache()
    return await cache.get(_job_key(job_id))


@router.post("/start", response_model=ParsingStartResponse)
async def start_parsing(request: ParsingRequest):
    if not request.source.strip():
        raise HTTPException(status_code=400, detail="Источник не может быть пустым")

    if not VK_TOKENS:
        raise HTTPException(status_code=400, detail="VK токены не настроены")

    fmt = request.format.lower().strip()
    if fmt not in ("html", "txt"):
        raise HTTPException(status_code=400, detail="Неверный формат, используйте html или txt")

    dt_from = _validate_date(request.date_from, "date_from")
    dt_to = _validate_date(request.date_to, "date_to")
    if dt_from and dt_to and dt_from > dt_to:
        raise HTTPException(status_code=400, detail="date_from не может быть позже date_to")

    cache = get_cache()
    redis_client = await cache.get_client()
    active_jobs = await redis_client.incr(ACTIVE_JOBS_KEY)
    await redis_client.expire(ACTIVE_JOBS_KEY, JOB_TTL_SECONDS)
    if active_jobs > MAX_ACTIVE_JOBS:
        await redis_client.decr(ACTIVE_JOBS_KEY)
        raise HTTPException(status_code=429, detail="Достигнут лимит одновременных задач, попробуйте позже")

    job_id = uuid.uuid4().hex

    payload = request.dict()
    payload["format"] = fmt

    status_payload = {
        "status": "queued",
        "progress": 0,
        "message": "Задача поставлена в очередь",
        "error": None,
        "download_url": None,
        "result_file": None,
        "active_jobs_limit": MAX_ACTIVE_JOBS,
    }
    await _set_job(job_id, status_payload)

    from tasks.parsing_tasks import parse_vk_posts_task
    parse_vk_posts_task.delay(job_id, payload)

    return ParsingStartResponse(
        job_id=job_id,
        status="queued",
        message="Парсинг запущен"
    )


@router.get("/status/{job_id}")
async def get_parsing_status(job_id: str):
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или истек срок хранения")
    if job.get("result_file"):
        job["download_url"] = f"/api/parsing/download/{job_id}"
    if job.get("report_file"):
        job["report_url"] = f"/api/parsing/report/{job_id}"
    return job


@router.get("/download/{job_id}")
async def download_result(job_id: str):
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или истек срок хранения")
    result_file = job.get("result_file")
    if not result_file or not os.path.exists(result_file):
        raise HTTPException(status_code=404, detail="Файл результата не найден")
    filename = os.path.basename(result_file)
    return FileResponse(result_file, filename=filename, media_type="application/octet-stream")


@router.get("/report/{job_id}")
async def download_report(job_id: str):
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена или истек срок хранения")
    report_file = job.get("report_file")
    if not report_file or not os.path.exists(report_file):
        raise HTTPException(status_code=404, detail="Отчет не найден")
    filename = os.path.basename(report_file)
    return FileResponse(report_file, filename=filename, media_type="text/plain")


@router.post("/skip/{job_id}")
async def skip_video_download(job_id: str, request: ParsingSkipRequest):
    cache = get_cache()
    redis_client = await cache.get_client()
    skip_key = f"parser:skip:{job_id}"
    await redis_client.sadd(skip_key, request.video_id)
    await redis_client.expire(skip_key, JOB_TTL_SECONDS)
    return {"status": "ok"}
