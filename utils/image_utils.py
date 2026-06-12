"""
Image utilities migrated from old_postopus bin/rw/

Provides image fingerprinting for photo/video duplicate detection.
"""

import hashlib
import io
from typing import Optional

from PIL import Image


def image_to_histogram_md5(image_data: bytes) -> Optional[str]:
    """
    Compute histogram-based MD5 fingerprint of an image.
    Migrated from old_postopus bin/sort/sort_po_foto.py

    This is used for photo duplicate detection.

    Args:
        image_data: Image bytes

    Returns:
        MD5 hash string or None
    """
    try:
        img = Image.open(io.BytesIO(image_data))

        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Resize to small size for fingerprint
        img = img.resize((32, 32), Image.LANCZOS)

        # Compute histogram
        histogram = img.histogram()

        # Normalize histogram
        total = sum(histogram)
        if total > 0:
            normalized = [h / total for h in histogram]
        else:
            normalized = histogram

        # Create MD5 hash
        histogram_str = ",".join(f"{v:.6f}" for v in normalized)
        md5_hash = hashlib.md5(histogram_str.encode()).hexdigest()

        return md5_hash

    except Exception as e:
        print(f"⚠️  Failed to compute image fingerprint: {e}")
        return None
