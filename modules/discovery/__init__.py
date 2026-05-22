"""Auto-registration of VK communities for SETKA regions (big idea 2026-05-22).

Three pieces:

- ``vk_search`` — composite discovery (geo + keyword + reposts-of-main),
  dedup, выдаёт raw VK-group dicts.
- ``ai_categorizer`` — Groq prompt: на каждую группу — category
  (admin/novost/reklama/sosed/kultura/sport/detsad/other) + confidence +
  reasoning + флаг "это похоже на ИНФО-страницу района".
- ``persistence`` — upsert raw candidates → ``community_candidates`` через
  ``ON CONFLICT(region_id, vk_id) DO NOTHING``.

Оркестратор — ``tasks/discovery_tasks.run_discovery_for_region``.
"""

from modules.discovery.ai_categorizer import categorize_candidate
from modules.discovery.vk_search import CATEGORY_KEYWORDS, DiscoveredGroup, discover_for_region

__all__ = [
    "CATEGORY_KEYWORDS",
    "DiscoveredGroup",
    "categorize_candidate",
    "discover_for_region",
]
