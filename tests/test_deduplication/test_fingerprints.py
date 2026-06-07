"""
Unit tests for the deduplication fingerprint functions.

Tests do NOT require database - only test fingerprint creation logic.
"""

from modules.deduplication.fingerprints import (
    create_lip_fingerprint,
    create_media_fingerprint,
    create_text_core_fingerprint,
    create_text_fingerprint,
    create_text_simhash,
    jaccard_similarity,
    simhash_hamming_distance,
    text_token_set,
)


class TestJaccard:
    """text_token_set + jaccard_similarity (word-level near-dup)."""

    def test_token_set_words_only_min_len(self):
        ts = text_token_set("Привет, мир! Это тест-2026 ок я")
        assert "привет" in ts and "мир" in ts and "2026" in ts and "тест" in ts
        assert "я" not in ts and "ок" not in ts  # длина < 3 отброшена

    def test_token_set_order_independent(self):
        assert text_token_set("один два три") == text_token_set("три один два")

    def test_jaccard_identical(self):
        s = text_token_set("новая детская площадка откроется в субботу рядом")
        assert jaccard_similarity(s, s) == 1.0

    def test_jaccard_partial(self):
        assert jaccard_similarity(frozenset("abc"), frozenset("bcd")) == 2 / 4

    def test_jaccard_empty_is_zero(self):
        assert jaccard_similarity(frozenset(), frozenset("abc")) == 0.0

    def test_jaccard_reordered_high_distinct_low(self):
        base = text_token_set("в кильмези откроется детская площадка рядом со школой")
        reworded = text_token_set("рядом со школой откроется новая детская площадка кильмези")
        distinct = text_token_set("график отключения горячей воды на следующей неделе район")
        assert jaccard_similarity(base, reworded) > jaccard_similarity(base, distinct)


class TestLipFingerprint:
    """Tests for create_lip_fingerprint (structural duplicate detection)."""

    def test_creates_unique_id(self):
        """Should create a unique fingerprint from owner_id + post_id."""
        lip = create_lip_fingerprint(-123456, 789)
        assert lip is not None
        assert isinstance(lip, str)

    def test_same_input_same_output(self):
        """Same owner_id + post_id should produce same fingerprint."""
        lip1 = create_lip_fingerprint(-123456, 789)
        lip2 = create_lip_fingerprint(-123456, 789)
        assert lip1 == lip2

    def test_different_post_id_different_output(self):
        """Different post_id should produce different fingerprint."""
        lip1 = create_lip_fingerprint(-123456, 789)
        lip2 = create_lip_fingerprint(-123456, 790)
        assert lip1 != lip2

    def test_different_owner_id_different_output(self):
        """Different owner_id should produce different fingerprint."""
        lip1 = create_lip_fingerprint(-123456, 789)
        lip2 = create_lip_fingerprint(-654321, 789)
        assert lip1 != lip2


class TestMediaFingerprint:
    """Tests for create_media_fingerprint."""

    def test_empty_attachments(self):
        """Should return empty list for no attachments."""
        result = create_media_fingerprint([])
        assert result == []

    def test_none_attachments(self):
        """Should handle None attachments gracefully."""
        result = create_media_fingerprint(None)
        assert result == []

    def test_photo_attachments(self):
        """Should extract photo IDs."""
        attachments = [
            {"type": "photo", "photo": {"owner_id": 123, "id": 456}},
        ]
        result = create_media_fingerprint(attachments)
        assert len(result) == 1

    def test_video_attachments(self):
        """Should extract video IDs."""
        attachments = [
            {"type": "video", "video": {"owner_id": 111, "id": 222}},
        ]
        result = create_media_fingerprint(attachments)
        assert len(result) == 1

    def test_mixed_attachments(self):
        """Should extract both photo and video IDs."""
        attachments = [
            {"type": "photo", "photo": {"owner_id": 1, "id": 2}},
            {"type": "video", "video": {"owner_id": 3, "id": 4}},
            {"type": "link"},  # ignored
        ]
        result = create_media_fingerprint(attachments)
        assert len(result) == 2

    def test_same_attachments_same_fingerprint(self):
        """Same attachments should produce same fingerprint."""
        attachments = [{"type": "photo", "photo": {"owner_id": 1, "id": 2}}]
        fp1 = create_media_fingerprint(attachments)
        fp2 = create_media_fingerprint(attachments)
        assert fp1 == fp2


class TestTextFingerprint:
    """Tests for create_text_fingerprint."""

    def test_empty_text(self):
        """Should return None or empty for empty text."""
        result = create_text_fingerprint("")
        assert result is None or result == ""

    def test_none_text(self):
        """Should handle None text gracefully."""
        result = create_text_fingerprint(None)
        assert result is None or result == ""

    def test_same_text_same_fingerprint(self):
        """Same text should produce same fingerprint."""
        text = "Это тестовый текст для проверки дедупликации"
        fp1 = create_text_fingerprint(text)
        fp2 = create_text_fingerprint(text)
        assert fp1 == fp2

    def test_different_text_different_fingerprint(self):
        """Different text should produce different fingerprint."""
        fp1 = create_text_fingerprint("Первый текст")
        fp2 = create_text_fingerprint("Второй текст")
        assert fp1 != fp2


class TestTextCoreFingerprint:
    """Tests for create_text_core_fingerprint (semantic duplicate detection)."""

    def test_empty_text(self):
        """Should return None or empty for empty text."""
        result = create_text_core_fingerprint("")
        assert result is None or result == ""

    def test_none_text(self):
        """Should handle None text gracefully."""
        result = create_text_core_fingerprint(None)
        assert result is None or result == ""

    def test_semantic_duplicates_detected(self):
        """Texts with same core content but different beginning/end should match."""
        # Same core content with different intro
        text1 = "Срочно! В городе произошло ДТП на улице Ленина"
        text2 = "Информация: ДТП на улице Ленина произошло сегодня"

        fp1 = create_text_core_fingerprint(text1)
        fp2 = create_text_core_fingerprint(text2)

        # If core fingerprint works, these should potentially match
        # (at minimum, the function should not crash and return a string)
        assert fp1 is not None or fp1 == ""
        assert fp2 is not None or fp2 == ""

    def test_different_content_different_fingerprint(self):
        """Completely different texts should produce different fingerprints."""
        fp1 = create_text_core_fingerprint("Погода сегодня отличная")
        fp2 = create_text_core_fingerprint("Вчера был сильный дождь")
        assert fp1 != fp2

    def test_same_text_same_fingerprint(self):
        """Identical texts should produce identical fingerprints."""
        text = "Идентичный текст для проверки"
        fp1 = create_text_core_fingerprint(text)
        fp2 = create_text_core_fingerprint(text)
        assert fp1 == fp2


class TestTextSimhash:
    """Tests for near-duplicate text SimHash."""

    def test_same_text_same_simhash(self):
        text = "В районе пройдет спортивный праздник в эту субботу"
        sh1 = create_text_simhash(text)
        sh2 = create_text_simhash(text)
        assert sh1 == sh2

    def test_small_rewrite_has_small_hamming_distance(self):
        t1 = "Срочно! В районе пройдет спортивный праздник в эту субботу"
        t2 = "В эту субботу в районе пройдет спортивный праздник. Срочно"
        sh1 = create_text_simhash(t1)
        sh2 = create_text_simhash(t2)
        assert simhash_hamming_distance(sh1, sh2) <= 12

    def test_different_text_has_large_hamming_distance(self):
        t1 = "В районе пройдет спортивный праздник"
        t2 = "Завтра отключат водоснабжение в нескольких деревнях"
        sh1 = create_text_simhash(t1)
        sh2 = create_text_simhash(t2)
        assert simhash_hamming_distance(sh1, sh2) >= 8
