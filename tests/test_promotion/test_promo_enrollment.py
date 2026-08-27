"""Зачисление в раскрутку: гистерезис и обращение с отсутствующим снимком."""

from modules.promotion.enrollment import (
    EnrollmentDecision,
    RegionState,
    evaluate_enrollment,
    summarize,
)


def state(**overrides) -> RegionState:
    """Базовый годный район; в тесте меняем ровно один факт."""
    base = dict(
        region_id=1,
        code="suna",
        kind="raion",
        is_active=True,
        has_group=True,
        members=10,
        enrollment_status=None,
    )
    base.update(overrides)
    return RegionState(**base)


def actions(decisions):
    return {d.code: d.action for d in decisions}


class TestEligibility:
    def test_weak_district_is_enrolled(self):
        assert actions(evaluate_enrollment([state()])) == {"suna": "enroll"}

    def test_oblast_is_not_enrolled(self):
        assert evaluate_enrollment([state(kind="oblast")]) == []

    def test_inactive_is_not_enrolled(self):
        assert evaluate_enrollment([state(is_active=False)]) == []

    def test_region_without_group_is_not_enrolled(self):
        assert evaluate_enrollment([state(has_group=False)]) == []

    def test_allowlist_filters_out_others(self):
        decisions = evaluate_enrollment([state()], allowlist=["mi"])
        assert decisions == []

    def test_allowlist_keeps_listed(self):
        decisions = evaluate_enrollment([state()], allowlist=["suna"])
        assert actions(decisions) == {"suna": "enroll"}


class TestMissingSnapshot:
    def test_region_without_snapshot_is_enrolled(self):
        # Суна, Кумёны и Зуевка активированы вчера — снимка нет. Прочитать None
        # как «подписчиков много» значило бы пропустить именно тех, ради кого
        # модуль и написан.
        decisions = evaluate_enrollment([state(members=None)])
        assert actions(decisions) == {"suna": "enroll"}
        assert decisions[0].members is None

    def test_enrolled_without_snapshot_is_kept(self):
        decisions = evaluate_enrollment([state(members=None, enrollment_status="active")])
        assert actions(decisions) == {"suna": "keep"}


class TestHysteresis:
    def test_stays_between_thresholds(self):
        decisions = evaluate_enrollment(
            [state(members=350, enrollment_status="active")],
            threshold_members=300,
            graduate_members=400,
        )
        assert actions(decisions) == {"suna": "keep"}

    def test_graduates_at_upper_threshold(self):
        decisions = evaluate_enrollment(
            [state(members=400, enrollment_status="active")],
            threshold_members=300,
            graduate_members=400,
        )
        assert actions(decisions) == {"suna": "graduate"}

    def test_not_enrolled_between_thresholds(self):
        # 350 больше порога входа — новый район в этой зоне не зачисляется,
        # хотя уже зачисленный в ней остаётся. Это и есть гистерезис.
        assert evaluate_enrollment([state(members=350)]) == []

    def test_inverted_thresholds_do_not_cause_flapping(self):
        # Порог выхода ниже порога входа задан по ошибке: район 250 иначе
        # зачислялся бы и тут же выпускался на каждом прогоне.
        decisions = evaluate_enrollment(
            [state(members=250, enrollment_status="active")],
            threshold_members=300,
            graduate_members=100,
        )
        assert actions(decisions) == {"suna": "keep"}


class TestGraduatedIsSticky:
    def test_graduated_region_is_not_re_enrolled(self):
        # Выпустился и просел обратно — возвращает владелец, а не автоматика:
        # иначе десяток отписок вернул бы район в раскрутку молча.
        assert evaluate_enrollment([state(members=5, enrollment_status="graduated")]) == []

    def test_paused_region_is_left_alone(self):
        assert evaluate_enrollment([state(members=5, enrollment_status="paused")]) == []

    def test_active_region_that_lost_eligibility_graduates(self):
        decisions = evaluate_enrollment([state(is_active=False, enrollment_status="active")])
        assert actions(decisions) == {"suna": "graduate"}


class TestSummarize:
    def test_counts_by_action(self):
        decisions = [
            EnrollmentDecision(1, "a", "enroll", 1, ""),
            EnrollmentDecision(2, "b", "enroll", 2, ""),
            EnrollmentDecision(3, "c", "graduate", 500, ""),
            EnrollmentDecision(4, "d", "keep", 10, ""),
        ]
        assert summarize(decisions) == {"enroll": 2, "graduate": 1, "keep": 1}
