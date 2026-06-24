"""
Single source of truth for SETKA application version.

Versioning is roughly aligned with major milestones from git history:
- 1.0.0  2025-10  Initial release (FastAPI + PostgreSQL + Redis + Celery)
- 1.1.0  2026-04  Postopus pipeline migration (27 themes, beat schedules)
- 1.2.0  2026-04  Bulletin formatting + token roles + mourning split
- 1.3.0  2026-04  copy_setka hub + Filtration UI + Kirov oblast aggregation
- 1.4.0  2026-05  RegionalRelevanceFilter + morphology + localities + dedup hardening
- 1.4.1  2026-05  Log noise tame-down, SERVER['domain'], /metrics async, VK messages.get fix
- 1.4.2  2026-05  Empty-bulletin guard (no more header-only posts in VK)
- 1.5.0  2026-05  UI: grouped dropdown navigation, dynamic footer version
"""

__version__ = "1.5.0"
