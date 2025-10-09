"""
Deduplication module for SETKA
Based on proven patterns from Postopus project (3+ years in production)
"""
from .fingerprints import (
    create_lip_fingerprint,
    create_media_fingerprint,
    create_text_fingerprint,
    create_text_core_fingerprint,
    text_to_rafinad
)
from .detector import DuplicationDetector

__all__ = [
    'create_lip_fingerprint',
    'create_media_fingerprint',
    'create_text_fingerprint',
    'create_text_core_fingerprint',
    'text_to_rafinad',
    'DuplicationDetector'
]

