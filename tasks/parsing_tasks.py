"""
Celery task for safe VK parsing with progress tracking.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import zipfile
from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from celery_app import app

from config.runtime import VK_TOKENS
from core.exceptions import (
    VKAccessDeniedException,
    VKAPIException,
    VKRateLimitException,
    VKTokenInvalidException,
)
from modules.vk_monitor.vk_client_async import VKTokenRotatorAsync, VKClientAsync
from utils.cache import get_cache
from utils.celery_asyncio import run_coro


OUTPUT_DIR = "/home/valstan/SETKA/logs/parser"
JOB_TTL_SECONDS = 7 * 24 * 60 * 60
REQUEST_DELAY_SECONDS = 0.35
ACTIVE_JOBS_KEY = "parser:active_jobs"
MAX_VIDEO_SIZE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_VIDEO_BYTES = 1000 * 1024 * 1024
REPORTS_DIR = "/home/valstan/SETKA/logs/parser/reports"
SKIP_VIDEOS_KEY_PREFIX = "parser:skip:"


def _init_logger() -> logging.Logger:
    logger = logging.getLogger("vk_parser")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        os.makedirs("/home/valstan/SETKA/logs", exist_ok=True)
        handler = logging.FileHandler("/home/valstan/SETKA/logs/parser.log")
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


logger = _init_logger()
VIDEO_REPORT_PATH = "/home/valstan/SETKA/logs/parser_video_report.log"


def _job_key(job_id: str) -> str:
    return f"parser:job:{job_id}"


def _set_job(job_id: str, payload: dict) -> None:
    cache = get_cache()
    run_coro(cache.set(_job_key(job_id), payload, ttl=JOB_TTL_SECONDS))


def _get_job(job_id: str) -> Optional[dict]:
    cache = get_cache()
    return run_coro(cache.get(_job_key(job_id)))


async def _set_job_async(job_id: str, payload: dict) -> None:
    cache = get_cache()
    await cache.set(_job_key(job_id), payload, ttl=JOB_TTL_SECONDS)


def _parse_date(date_str: Optional[str], end_of_day: bool) -> Optional[int]:
    if not date_str:
        return None
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def _cleanup_old_files(days: int = 7) -> None:
    cutoff = time.time() - days * 24 * 60 * 60
    if not os.path.isdir(OUTPUT_DIR):
        return
    for name in os.listdir(OUTPUT_DIR):
        path = os.path.join(OUTPUT_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
            elif os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                for root, dirs, files in os.walk(path, topdown=False):
                    for file_name in files:
                        os.remove(os.path.join(root, file_name))
                    for dir_name in dirs:
                        os.rmdir(os.path.join(root, dir_name))
                os.rmdir(path)
        except Exception:
            continue


def _extract_screen_name(source: str) -> str:
    source = source.strip()
    source = source.replace("https://", "").replace("http://", "")
    source = source.replace("www.", "")
    source = source.split("?")[0]
    source = source.replace("vk.com/", "").strip("/")
    return source


async def _resolve_owner_id(client: VKClientAsync, source: str) -> Tuple[int, str]:
    source = source.strip()
    screen_name = _extract_screen_name(source)

    numeric_match = re.fullmatch(r"-?\d+", screen_name)
    if numeric_match:
        numeric_id = int(screen_name)
        if numeric_id < 0:
            return numeric_id, f"group_{abs(numeric_id)}"

        try:
            await client._make_request("groups.getById", {"group_id": numeric_id})
            return -numeric_id, f"group_{numeric_id}"
        except VKAPIException:
            await client._make_request("users.get", {"user_ids": numeric_id})
            return numeric_id, f"user_{numeric_id}"

    if screen_name.startswith("club") or screen_name.startswith("public") or screen_name.startswith("event"):
        numeric_id = int(re.sub(r"\D", "", screen_name) or "0")
        if numeric_id:
            return -numeric_id, f"group_{numeric_id}"

    if screen_name.startswith("id") and screen_name[2:].isdigit():
        numeric_id = int(screen_name[2:])
        return numeric_id, f"user_{numeric_id}"

    resolved = await client._make_request("utils.resolveScreenName", {"screen_name": screen_name})
    if not resolved:
        raise ValueError(f"Не удалось найти объект '{screen_name}' в VK")

    obj_type = resolved.get("type")
    obj_id = resolved.get("object_id")
    if obj_type == "group":
        return -int(obj_id), f"group_{obj_id}"
    if obj_type == "user":
        return int(obj_id), f"user_{obj_id}"

    raise ValueError(f"Объект '{screen_name}' не является сообществом или пользователем")


def _parse_attachments(post: Dict[str, Any]) -> List[Dict[str, Any]]:
    attachments = []
    for att in post.get("attachments", []) or []:
        att_type = att.get("type")
        if att_type == "photo":
            photo = att.get("photo", {})
            sizes = photo.get("sizes", [])
            if sizes:
                largest = max(sizes, key=lambda x: x.get("width", 0) * x.get("height", 0))
                attachments.append({
                    "type": "photo",
                    "url": largest.get("url"),
                })
        elif att_type == "video":
            video = att.get("video", {})
            attachments.append({
                "type": "video",
                "title": video.get("title"),
                "video_id": f"video{video.get('owner_id')}_{video.get('id')}",
                "owner_id": video.get("owner_id"),
                "id": video.get("id"),
            })
        elif att_type == "audio":
            audio = att.get("audio", {})
            attachments.append({
                "type": "audio",
                "artist": audio.get("artist"),
                "title": audio.get("title"),
                "url": audio.get("url"),
            })
        elif att_type == "link":
            link = att.get("link", {})
            attachments.append({
                "type": "link",
                "url": link.get("url"),
                "title": link.get("title"),
            })
        elif att_type == "doc":
            doc = att.get("doc", {})
            attachments.append({
                "type": "document",
                "title": doc.get("title"),
                "url": doc.get("url"),
            })
        elif att_type == "poll":
            poll = att.get("poll", {})
            attachments.append({
                "type": "poll",
                "question": poll.get("question"),
            })
    return attachments


def _render_html(posts: List[Dict[str, Any]], include: dict) -> str:
    lines = [
        "<!doctype html>",
        "<html lang='ru'>",
        "<head><meta charset='utf-8'><title>VK Export</title></head>",
        "<body>",
        "<h1>VK Export</h1>",
    ]
    for post in posts:
        lines.append("<div style='border:1px solid #ddd;padding:12px;margin:12px 0;'>")
        lines.append(f"<div><strong>{escape(post['date'])}</strong> | <a href='{post['url']}'>Источник</a></div>")
        if include.get("text"):
            lines.append(f"<pre style='white-space:pre-wrap'>{escape(post.get('text') or '')}</pre>")
        attachments = post.get("attachments", [])
        if attachments:
            lines.append("<ul>")
            for att in attachments:
                att_type = att.get("type")
                if att_type == "photo" and include.get("photos"):
                    src = att.get("local_path") or att.get("url")
                    if src:
                        lines.append(f"<li><img src='{src}' alt='photo' style='max-width:100%'></li>")
                elif att_type == "video" and include.get("videos"):
                    size_bytes = att.get("size_bytes") or 0
                    size_mb = f" ({size_bytes // (1024 * 1024)}MB)" if size_bytes else ""
                    if att.get("local_path"):
                        lines.append(f"<li>Видео{size_mb}: <a href='{att.get('local_path')}'>{escape(att.get('title') or 'video')}</a></li>")
                    else:
                        video_id = att.get("video_id")
                        lines.append(f"<li>Видео{size_mb}: <a href='https://vk.com/{video_id}'>{escape(att.get('title') or 'video')}</a></li>")
                elif att_type == "audio" and include.get("audio"):
                    if att.get("local_path"):
                        lines.append(f"<li>Аудио: <a href='{att.get('local_path')}'>{escape(att.get('artist') or '')} - {escape(att.get('title') or '')}</a></li>")
                    else:
                        lines.append(f"<li>Аудио: {escape(att.get('artist') or '')} - {escape(att.get('title') or '')}</li>")
                elif att_type == "link" and include.get("links"):
                    lines.append(f"<li>Ссылка: <a href='{att.get('url')}'>{escape(att.get('title') or att.get('url') or '')}</a></li>")
                elif att_type == "document" and include.get("docs"):
                    if att.get("local_path"):
                        lines.append(f"<li>Документ: <a href='{att.get('local_path')}'>{escape(att.get('title') or '')}</a></li>")
                    else:
                        lines.append(f"<li>Документ: <a href='{att.get('url')}'>{escape(att.get('title') or '')}</a></li>")
                elif att_type == "poll" and include.get("polls"):
                    lines.append(f"<li>Опрос: {escape(att.get('question') or '')}</li>")
            lines.append("</ul>")
        lines.append("</div>")
    lines.append("</body></html>")
    return "\n".join(lines)


def _render_txt(posts: List[Dict[str, Any]]) -> str:
    lines = []
    for post in posts:
        lines.append(f"{post['date']} | {post['url']}")
        lines.append(post.get("text") or "")
        lines.append("-" * 60)
    return "\n".join(lines)


async def _download_file(url: str, dest_path: str) -> bool:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    return False
                data = await resp.read()
                with open(dest_path, "wb") as f:
                    f.write(data)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        return True
    except Exception:
        return False


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value[:120] or "file"


async def _download_attachments(posts: List[Dict[str, Any]], include: dict, attachments_dir: str) -> None:
    os.makedirs(attachments_dir, exist_ok=True)
    counter = 0

    for post in posts:
        for att in post.get("attachments", []):
            att_type = att.get("type")
            if att_type == "photo" and include.get("photos") and att.get("url"):
                filename = _safe_filename(f"photo_{post['id']}_{counter}.jpg")
                dest = os.path.join(attachments_dir, filename)
                if await _download_file(att["url"], dest):
                    att["local_path"] = f"attachments/{filename}"
                counter += 1
            elif att_type == "document" and include.get("docs") and att.get("url"):
                filename = _safe_filename(f"doc_{post['id']}_{counter}")
                dest = os.path.join(attachments_dir, filename)
                if await _download_file(att["url"], dest):
                    att["local_path"] = f"attachments/{filename}"
                counter += 1
            elif att_type == "audio" and include.get("audio") and att.get("url"):
                filename = _safe_filename(f"audio_{post['id']}_{counter}.mp3")
                dest = os.path.join(attachments_dir, filename)
                if await _download_file(att["url"], dest):
                    att["local_path"] = f"attachments/{filename}"
                counter += 1


async def _download_videos(posts: List[Dict[str, Any]], include: dict, job_id: str, attachments_dir: str) -> None:
    if not include.get("videos"):
        return
    os.makedirs(attachments_dir, exist_ok=True)
    tokens = [t for t in VK_TOKENS.values() if t]
    if not tokens:
        return
    rotator = VKTokenRotatorAsync(tokens)

    total_downloaded = 0
    for post in posts:
        for att in post.get("attachments", []):
            if att.get("type") != "video":
                continue
            post_id = post.get("id")
            owner_id = att.get("owner_id")
            video_id = att.get("id")
            if owner_id is None or video_id is None:
                continue
            video_key = f"{owner_id}_{video_id}"
            title = att.get("title") or video_key
            if await _is_video_skipped(job_id, video_key):
                await _update_video_status(job_id, video_key, title, 0, "skipped")
                await _add_video_report(job_id, f"[post {post_id}] {title}: пропущено пользователем")
                att["skip_reason"] = "skipped"
                continue
            client = await rotator.get_client()
            if not client:
                await _update_video_status(job_id, video_key, title, 0, "error")
                await _add_video_report(job_id, f"[post {post_id}] {title}: нет доступного VK клиента")
                att["skip_reason"] = "no_client"
                continue
            await _update_video_status(job_id, video_key, title, 0, "downloading")
            url = await _get_video_url(client, owner_id=owner_id, video_id=video_id)
            if not url:
                await _update_video_status(job_id, video_key, title, 0, "no_url")
                await _add_video_report(job_id, f"[post {post_id}] {title}: нет прямой ссылки на файл")
                att["skip_reason"] = "no_url"
                continue
            filename = _safe_filename(f"video_{video_key}.mp4")
            dest = os.path.join(attachments_dir, filename)
            remaining = max(0, MAX_TOTAL_VIDEO_BYTES - total_downloaded)
            ok, size_bytes, reason = await _download_video_with_progress(
                job_id, client, video_key, title, url, dest, remaining_bytes=remaining, post_id=post_id
            )
            if ok:
                att["local_path"] = f"attachments/{filename}"
                att["size_bytes"] = size_bytes
                total_downloaded += size_bytes or 0
                await _update_video_limit(job_id, total_downloaded)
            else:
                att["size_bytes"] = size_bytes
                att["skip_reason"] = reason
                await _add_video_report(job_id, f"[post {post_id}] {title}: причина {reason}")
    await rotator.close_all()


async def _get_video_url(client: VKClientAsync, owner_id: int, video_id: int) -> Optional[str]:
    try:
        response = await client._make_request("video.get", {
            "videos": f"{owner_id}_{video_id}",
        })
        items = response.get("items", [])
        if not items:
            return None
        video = items[0]
        files = video.get("files", {}) or {}
        for key in ("mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144"):
            if files.get(key):
                return files.get(key)
        return files.get("external") or video.get("player")
    except Exception:
        return None


async def _update_video_status(job_id: str, video_id: str, title: str, progress: int, status: str) -> None:
    cache = get_cache()
    job = await cache.get(_job_key(job_id)) or {}
    downloads = job.get("video_downloads", [])
    found = False
    for item in downloads:
        if item.get("id") == video_id:
            item.update({"title": title, "progress": progress, "status": status})
            found = True
            break
    if not found:
        downloads.append({"id": video_id, "title": title, "progress": progress, "status": status})
    job["video_downloads"] = downloads
    await cache.set(_job_key(job_id), job, ttl=JOB_TTL_SECONDS)


async def _add_video_report(job_id: str, message: str) -> None:
    cache = get_cache()
    job = await cache.get(_job_key(job_id)) or {}
    reports = job.get("video_reports", [])
    reports.append(message)
    job["video_reports"] = reports
    await cache.set(_job_key(job_id), job, ttl=JOB_TTL_SECONDS)
    try:
        report_file = job.get("report_file")
        if not report_file:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            report_file = os.path.join(REPORTS_DIR, f"{job_id}.log")
        with open(report_file, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [{job_id}] {message}\n")
    except Exception:
        pass


async def _update_video_limit(job_id: str, used_bytes: int) -> None:
    cache = get_cache()
    job = await cache.get(_job_key(job_id)) or {}
    job["video_limit"] = {
        "used_bytes": used_bytes,
        "max_bytes": MAX_TOTAL_VIDEO_BYTES,
        "used_mb": used_bytes // (1024 * 1024),
        "max_mb": MAX_TOTAL_VIDEO_BYTES // (1024 * 1024),
    }
    await cache.set(_job_key(job_id), job, ttl=JOB_TTL_SECONDS)


async def _is_video_skipped(job_id: str, video_id: str) -> bool:
    cache = get_cache()
    client = await cache.get_client()
    return await client.sismember(f"{SKIP_VIDEOS_KEY_PREFIX}{job_id}", video_id)


async def _download_video_with_progress(
    job_id: str,
    client: VKClientAsync,
    video_id: str,
    title: str,
    url: str,
    dest_path: str,
    remaining_bytes: int,
    post_id: Optional[int],
) -> tuple[bool, int, str]:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=60) as resp:
                if resp.status != 200:
                    await _update_video_status(job_id, video_id, title, 0, "error")
                    await _add_video_report(job_id, f"[post {post_id}] {title}: ошибка загрузки (HTTP {resp.status})")
                    return False, 0, "http_error"
                total = int(resp.headers.get("Content-Length") or 0)
                if total == 0:
                    await _update_video_status(job_id, video_id, title, 0, "no_size")
                    await _add_video_report(job_id, f"[post {post_id}] {title}: размер неизвестен (нет Content-Length), пропущено")
                    return False, total, "no_size"
                if total and total > MAX_VIDEO_SIZE_BYTES:
                    await _update_video_status(job_id, video_id, title, 0, "too_large")
                    await _add_video_report(job_id, f"[post {post_id}] {title}: размер {total // (1024 * 1024)}MB > 200MB, пропущено")
                    return False, total, "too_large"
                if total and total > remaining_bytes:
                    await _update_video_status(job_id, video_id, title, 0, "limit_total")
                    await _add_video_report(job_id, f"[post {post_id}] {title}: лимит 1000MB исчерпан, пропущено")
                    return False, total, "limit_total"
                downloaded = 0
                with open(dest_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        if await _is_video_skipped(job_id, video_id):
                            await _update_video_status(job_id, video_id, title, 0, "skipped")
                            try:
                                f.close()
                                if os.path.exists(dest_path):
                                    os.remove(dest_path)
                            except Exception:
                                pass
                            await _add_video_report(job_id, f"[post {post_id}] {title}: пропущено пользователем")
                            return False, total, "skipped"
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = int((downloaded / total) * 100) if total else 0
                        await _update_video_status(job_id, video_id, title, pct, "downloading")
        await _update_video_status(job_id, video_id, title, 100, "done")
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        return True, total, "ok"
    except Exception:
        await _update_video_status(job_id, video_id, title, 0, "error")
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        await _add_video_report(job_id, f"[post {post_id}] {title}: ошибка загрузки")
        return False, 0, "error"


def _make_zip(zip_path: str, root_dir: str) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(root_dir):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(full_path, root_dir)
                zf.write(full_path, rel_path)


async def _collect_posts(
    tokens: List[str],
    source: str,
    date_from: Optional[str],
    date_to: Optional[str],
    limit: int,
    job_id: str,
) -> Tuple[List[Dict[str, Any]], str]:
    rotator = VKTokenRotatorAsync(tokens)
    client = await rotator.get_client()
    if not client:
        raise ValueError("Нет доступных VK токенов")

    owner_id, owner_label = await _resolve_owner_id(client, source)
    start_ts = _parse_date(date_from, end_of_day=False)
    end_ts = _parse_date(date_to, end_of_day=True)

    collected: List[Dict[str, Any]] = []
    offset = 0
    batch_size = 100
    reached_old = False

    while len(collected) < limit:
        posts = None
        for attempt in range(5):
            client = await rotator.get_client()
            if not client:
                raise ValueError("Нет доступных VK токенов")
            try:
                response = await client._make_request("wall.get", {
                    "owner_id": owner_id,
                    "count": min(batch_size, 100),
                    "offset": offset,
                })
                posts = response.get("items", [])
                break
            except VKRateLimitException:
                await asyncio.sleep(1 + attempt)
            except (VKTokenInvalidException, VKAccessDeniedException) as e:
                logger.warning(f"VK token issue: {e.message}")
                await asyncio.sleep(1)
                continue
        if posts is None:
            raise VKAPIException("Не удалось получить посты из VK", method="wall.get")
        if not posts:
            break

        for post in posts:
            post_ts = int(post.get("date", 0))
            if end_ts and post_ts > end_ts:
                continue
            if start_ts and post_ts < start_ts:
                reached_old = True
                break
            collected.append({
                "id": post.get("id"),
                "date": datetime.fromtimestamp(post_ts).strftime("%Y-%m-%d %H:%M:%S"),
                "text": post.get("text", ""),
                "url": f"https://vk.com/wall{owner_id}_{post.get('id')}",
                "attachments": _parse_attachments(post),
            })
            if len(collected) >= limit:
                break

        progress = int(min(80, (len(collected) / max(limit, 1)) * 80))
        status = {
            "status": "running",
            "progress": progress,
            "message": f"Собрано постов: {len(collected)}",
            "error": None,
            "download_url": None,
            "result_file": None,
        }
        await _set_job_async(job_id, status)

        if reached_old or len(posts) < batch_size:
            break

        offset += batch_size
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    await rotator.close_all()
    return collected, owner_label


@app.task(name="tasks.parsing_tasks.parse_vk_posts_task")
def parse_vk_posts_task(job_id: str, payload: Dict[str, Any]):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    _cleanup_old_files(days=7)

    status = _get_job(job_id) or {}
    status.update({
        "status": "running",
        "progress": 1,
        "message": "Инициализация...",
        "error": None,
        "download_url": None,
        "report_file": os.path.join(REPORTS_DIR, f"{job_id}.log"),
        "video_limit": {
            "used_bytes": 0,
            "max_bytes": MAX_TOTAL_VIDEO_BYTES,
            "used_mb": 0,
            "max_mb": MAX_TOTAL_VIDEO_BYTES // (1024 * 1024),
        },
    })
    _set_job(job_id, status)

    try:
        tokens = [token for token in VK_TOKENS.values() if token]
        if not tokens:
            raise ValueError("VK токены не настроены")

        download_attachments = bool(payload.get("download_attachments", True))
        include = {
            "text": payload.get("include_text", True),
            "photos": payload.get("include_photos", False),
            "videos": payload.get("include_videos", False),
            "audio": payload.get("include_audio", False),
            "links": payload.get("include_links", False),
            "docs": payload.get("include_docs", False),
            "polls": payload.get("include_polls", False),
        }
        include_render = include
        include_download = include

        posts, owner_label = run_coro(_collect_posts(
            tokens=tokens,
            source=payload.get("source", ""),
            date_from=payload.get("date_from"),
            date_to=payload.get("date_to"),
            limit=int(payload.get("limit", 200)),
            job_id=job_id,
        ))

        status.update({
            "progress": 85,
            "message": f"Собрано постов: {len(posts)}",
        })
        _set_job(job_id, status)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_dir = os.path.join(OUTPUT_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        if download_attachments:
            run_coro(_download_attachments(posts, include=include_download, attachments_dir=os.path.join(job_dir, "attachments")))
            run_coro(_download_videos(posts, include=include_download, job_id=job_id, attachments_dir=os.path.join(job_dir, "attachments")))

        html_path = os.path.join(job_dir, f"vk_export_{owner_label}_{timestamp}.html")
        txt_path = os.path.join(job_dir, f"vk_export_{owner_label}_{timestamp}.txt")

        html_content = _render_html(posts, include=include_render)
        txt_content = _render_txt(posts)

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(txt_content)

        zip_path = os.path.join(OUTPUT_DIR, f"vk_export_{owner_label}_{timestamp}.zip")
        _make_zip(zip_path, job_dir)

        current_job = _get_job(job_id) or {}
        if current_job.get("video_downloads"):
            status["video_downloads"] = current_job.get("video_downloads")
        status.update({
            "status": "done",
            "progress": 100,
            "message": "Готово",
            "result_file": zip_path,
        })
        _set_job(job_id, status)
        logger.info(f"Parsing completed: {zip_path}")

    except (VKTokenInvalidException, VKAccessDeniedException, VKRateLimitException) as e:
        logger.error(f"VK error: {e.message}")
        status.update({
            "status": "error",
            "progress": status.get("progress", 0),
            "message": "Ошибка VK API",
            "error": e.message,
        })
        _set_job(job_id, status)
    except VKAPIException as e:
        logger.error(f"VK API error: {e.message}")
        status.update({
            "status": "error",
            "progress": status.get("progress", 0),
            "message": "Ошибка VK API",
            "error": e.message,
        })
        _set_job(job_id, status)
    except Exception as e:
        logger.error(f"Parsing failed: {e}", exc_info=True)
        status.update({
            "status": "error",
            "progress": status.get("progress", 0),
            "message": "Ошибка парсинга",
            "error": str(e),
        })
        _set_job(job_id, status)
    finally:
        try:
            cache = get_cache()
            client = run_coro(cache.get_client())
            run_coro(client.decr(ACTIVE_JOBS_KEY))
        except Exception:
            pass