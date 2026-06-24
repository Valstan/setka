"""LLM-курация сводок (PoC, письмо brain 2026-06-07).

Фаза 1 — shadow: после публикации сводки его посты паркуются в
`digest_curation_runs` для пост-фактум LLM-вердикта (/curate). Публикация при
этом не затрагивается. См. modules/curation/recorder.py.
"""
