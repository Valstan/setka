"""Парсер отпускает память волны до фазы публикации.

Экземпляр живёт одну волну, и сборщик мусора забрал бы всё сам — но когда
дойдут руки. На боксе с 1536 МБ и без swap (P163/P164) районы идут в одном
дочернем процессе подряд, поэтому хвост предыдущей волны складывается с пиком
следующей. Тест держит два обещания: состояние действительно очищается, и
очистка не ломает повторный прогон.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from modules.vk_monitor.advanced_parser import AdvancedVKParser

WAVE_STATE = (
    "_wall_cache",
    "_batch_lips",
    "_batch_lip_by_key",
    "_batch_text_fps",
    "_batch_core_fps",
    "_batch_media_sigs",
    "_batch_text_simhashes",
    "_batch_token_sets",
    "_historical_text_simhashes",
    "_skipped_duplicates",
    "_blocked_lips",
)


def _loaded_parser() -> AdvancedVKParser:
    parser = AdvancedVKParser(MagicMock())
    parser._wall_cache = {-1: [{"id": 1, "text": "x" * 100}]}
    parser._batch_lips = {"1_1"}
    parser._batch_lip_by_key = {"k": "1_1"}
    parser._batch_text_fps = {"fp"}
    parser._batch_core_fps = {"core"}
    parser._batch_media_sigs = {"sig"}
    parser._batch_text_simhashes = {"sh"}
    parser._batch_token_sets = [{"token"}]
    parser._historical_text_simhashes = [(1, "sh")]
    parser._skipped_duplicates = [{"lip": "1_1"}]
    parser._blocked_lips = {"2_2"}
    return parser


def test_release_wave_state_empties_everything_it_names():
    parser = _loaded_parser()

    parser.release_wave_state()

    for attr in WAVE_STATE:
        assert not getattr(parser, attr), f"{attr} остался занятым после release_wave_state"


def test_release_wave_state_is_idempotent():
    """Повторный вызов не должен падать: место вызова может переехать."""
    parser = _loaded_parser()

    parser.release_wave_state()
    parser.release_wave_state()

    assert parser._wall_cache == {}


def test_stats_survive_the_release():
    """Счётчики волны читаются ПОСЛЕ сбора и не являются частью её памяти."""
    parser = _loaded_parser()
    parser.stats["posts_parsed"] = 7

    parser.release_wave_state()

    assert parser.get_stats()["posts_parsed"] == 7
