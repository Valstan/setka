# 🔄 Migration Guide: old_postopus → SETKA

## Overview

This document describes the migration of functionality from the old_postopus branch to the SETKA main branch.

### What Was Migrated

| Component | old_postopus | SETKA (main) | Status |
|-----------|--------------|--------------|--------|
| **Configuration** | MongoDB config collection | PostgreSQL `region_configs` | ✅ Migrated |
| **Communities** | MongoDB `all_my_groups` | PostgreSQL `communities` | ✅ Migrated |
| **Work Tables** | MongoDB collections (lip, hash) | PostgreSQL `work_tables` | ✅ Migrated |
| **Filters** | Hardcoded in parser.py | Modular `modules/filters/` | ✅ Migrated |
| **Parsing** | `bin/control/parser.py` | `modules/vk_monitor/advanced_parser.py` | ✅ Migrated |
| **Digest Building** | `bin/rw/posting_post.py` | `modules/publisher/digest_builder.py` | ✅ Migrated |
| **Publishing** | `bin/rw/post_msg.py` | `modules/publisher/vk_publisher_extended.py` | ✅ Migrated |
| **Scheduling** | Crontab | Celery Beat | ✅ Migrated |
| **Statistics** | Terminal output | Web UI + API | ✅ Migrated |
| **Special Modules** | Various `bin/control/` scripts | `modules/publisher/` | ✅ Migrated |

### What Was NOT Migrated (Yet)

- AI text classification (TensorFlow model in old_postopus)
- Instagram/TikTok integration
- Yandex Disk integration
- OCR functionality

---

## Migration Architecture

### Database Changes

**MongoDB → PostgreSQL Mapping:**

```
MongoDB postopus.config
  ↓
PostgreSQL region_configs + filters + work_tables

MongoDB postopus.{mi,vp,ur,...}.{novost,kultura,...}
  ↓
PostgreSQL work_tables (lip, hash, bezfoto)

MongoDB postopus.config.all_my_groups
  ↓
PostgreSQL regions + communities
```

### Module Mapping

| old_postopus File | SETKA Module | Purpose |
|-------------------|--------------|---------|
| `bin/control/parser.py` | `modules/vk_monitor/advanced_parser.py` | Main parsing logic |
| `bin/control/sosed.py` | `modules/publisher/neighbor_sharing.py` | Neighbor news sharing |
| `bin/control/karavan.py` | `modules/publisher/event_distribution.py` | Event distribution |
| `bin/control/repost_oleny.py` | `modules/publisher/cross_region_repost.py` | Cross-region reposting |
| `bin/rw/posting_post.py` | `modules/publisher/digest_builder.py` | Digest assembly |
| `bin/rw/post_msg.py` | `modules/publisher/vk_publisher_extended.py` | VK publishing |
| `bin/utils/is_advertisement.py` | `modules/filters/ads_filter.py` | Ad detection |
| `bin/sort/sort_old_date.py` | `modules/filters/age_filter.py` | Age filtering |
| `bin/sort/sort_po_foto.py` | `modules/filters/photo_duplicate_filter.py` | Photo dedup |
| `bin/config.py` | `config/runtime.py` + `database/region_configs` | Configuration |

### Scheduling Migration

**Crontab → Celery Beat:**

Original crontab entries from old_postopus have been converted to Celery Beat schedules in `tasks/celery_app.py`.

Example:
```bash
# Old: 40 6,11,12,16,18,20 * * *  start_paket.py novost
```
```python
# New:
'postopus-novost-6': {
    'task': 'tasks.parsing_scheduler_tasks.run_all_regions_theme',
    'schedule': crontab(minute=40, hour=6),
    'args': ('novost',),
}
```

---

## How to Use

### 1. Migrate MongoDB Data

```bash
cd /home/valstan/SETKA
source venv/bin/activate

# Run migration script
python scripts/migrate_mongodb_config.py
```

This will:
- Read MongoDB config
- Create/update regions in PostgreSQL
- Create/update communities
- Migrate filters (blacklists, region words)
- Migrate work tables (lip, hash)

### 2. Verify Migration

```bash
# Check regions
python -c "
from database.connection import async_session_maker
from database.models import Region
import asyncio

async def check():
    async with async_session_maker() as session:
        from sqlalchemy import select
        result = await session.execute(select(Region))
        regions = result.scalars().all()
        print(f'Regions: {len(regions)}')
        for r in regions:
            print(f'  {r.code}: {r.name}')

asyncio.run(check())
"
```

### 3. Start Services

```bash
# Start main app
systemctl restart setka

# Start Celery worker
systemctl restart setka-celery-worker

# Start Celery beat (with new schedule)
systemctl restart setka-celery-beat
```

### 4. Monitor Statistics

Visit: `http://your-server/parsing-stats`

The new web UI shows:
- Total runs, success rate
- Posts scanned/published
- Filter statistics
- Per-region/theme breakdown

---

## Key Differences

### old_postopus Approach
- **Synchronous**: Single-threaded, runs to completion
- **Cron-based**: Scheduled via crontab
- **MongoDB**: Config and work tables in MongoDB
- **Terminal stats**: Statistics printed to terminal
- **Hardcoded logic**: Filters hardcoded in parser.py

### SETKA Approach
- **Async-first**: AsyncIO throughout
- **Celery Beat**: Scheduled via Celery Beat
- **PostgreSQL**: Everything in PostgreSQL
- **Web UI**: Statistics in web interface
- **Modular filters**: Pluggable filter pipeline

---

## Troubleshooting

### Migration Script Fails

**Error**: `pymongo not installed`
**Solution**: `pip install pymongo`

**Error**: `MongoDB connection failed`
**Solution**: Check `MONGO_CLIENT` env var

### Parsing Tasks Not Running

**Check Celery logs**:
```bash
journalctl -u setka-celery-beat -f
journalctl -u setka-celery-worker -f
```

**Verify tasks registered**:
```python
from tasks.celery_app import app
print(app.tasks.keys())
```

### Statistics Not Showing

**Check API endpoint**:
```bash
curl http://localhost:8000/api/parsing-stats/recent
```

**Check database tables**:
```bash
sudo -u postgres psql -d setka -c "SELECT COUNT(*) FROM parsing_stats;"
```

---

## Rollback Plan

If you need to rollback:

1. **Stop SETKA services**:
   ```bash
   systemctl stop setka setka-celery-worker setka-celery-beat
   ```

2. **Restore old_postopus**:
   ```bash
   cd /path/to/old_postopus
   # Use as before - unchanged
   ```

3. **Revert crontab**:
   ```bash
   # Restore old crontab from backup
   crontab /path/to/crontab.backup
   ```

---

## Next Steps

After migration is complete:

1. **Monitor for 1 week**: Ensure all themes run correctly
2. **Disable old_postopus crontab**: Remove old cron entries
3. **Archive old_postopus**: Keep as reference but don't run
4. **Enhance UI**: Add more detailed stats pages
5. **Migrate remaining features**: AI classification, OCR, etc.

---

## Support

If you encounter issues:
- Check logs: `/home/valstan/SETKA/logs/`
- Review API docs: `http://your-server/docs`
- Check Celery task status: `/api/monitoring/tasks/`

---

**Migration Date**: April 8, 2026
**Migrated By**: AI-assisted migration
**Status**: Ready for deployment
