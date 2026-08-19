"""Колонки метрик в аудите сбора (миграция 080, звено 5 шаг 1).

Отдельный тест на то, что поля НЕ имеют дефолта 0. Это не педантизм:
панель в #493 рисовала «токенов: 0» там, где ушло 1.4 млн, ровно потому,
что пустое поле читалось нулём. Отсутствующая метрика честнее неполной.
"""

from database.models_extended import CollectedPostAudit

METRIC_FIELDS = ("views", "likes", "comments", "reposts")


def test_model_has_metric_columns():
    cols = CollectedPostAudit.__table__.columns
    for name in METRIC_FIELDS + ("published_at", "metrics_updated_at"):
        assert name in cols, f"нет колонки {name}"


def test_metrics_are_nullable_without_zero_default():
    cols = CollectedPostAudit.__table__.columns
    for name in METRIC_FIELDS + ("published_at", "metrics_updated_at"):
        col = cols[name]
        assert col.nullable is True, f"{name} обязана быть NULL-able"
        assert col.default is None, f"{name}: NULL значит «не мерили», дефолта быть не должно"
        assert col.server_default is None, f"{name}: server_default сделал бы «не мерили» нулём"


def test_to_dict_exposes_metrics_as_none_when_unmeasured():
    row = CollectedPostAudit(lip="1_2", region_code="mi", decision="kept")
    d = row.to_dict()
    for name in METRIC_FIELDS:
        assert d[name] is None, f"{name} не должен превращаться в 0 при сериализации"
    assert d["published_at"] is None
