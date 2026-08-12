"""Дистилляция правок оператора в правила — звено 7 петли обучения (D-024).

Правило пишется по нескольким правкам, а действует на весь поток 26 районов.
Цена ошибки несимметрична, поэтому тесты здесь стерегут не «модель предложила»,
а **что именно не должно доехать до оператора**: пересказ одного поста вместо
обобщения, ссылки на посты вне сырья, обобщение по двум случаям.
"""

from __future__ import annotations

import json

from modules.classifier import distill

CORRECTIONS = [
    {
        "lip": f"1_{i}",
        "region_code": "mi",
        "verdict_type": "action",
        "ai_value": "publish",
        "operator_value": "delete",
        "post_text": f"Продам автомобиль номер {i}, недорого, звоните по телефону 8900000000{i}",
    }
    for i in range(12)
]


def _chat(monkeypatch, payload, *, model="deepseek-chat", usage=None):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        if isinstance(payload, dict) and payload.get("__fail__"):
            return {"ok": False, "reason": payload["__fail__"]}
        return {
            "ok": True,
            "content": json.dumps(payload, ensure_ascii=False),
            "model": model,
            "usage": usage or {"total_tokens": 500},
        }

    monkeypatch.setattr(distill, "chat", fake_chat)
    return calls


def _proposal(text, **kw):
    out = {"rule_text": text, "rationale": "повторяется в правках", "evidence_lips": ["1_0", "1_1"]}
    out.update(kw)
    return out


# ───────── порог сырья ─────────


def test_below_threshold_costs_nothing(monkeypatch):
    """Правило из двух случаев — не обобщение, а запись частного случая в общий
    закон. Такие потом приходится выводить из обращения."""
    calls = _chat(monkeypatch, {"proposals": [_proposal("что угодно")]})
    run = distill.distill(CORRECTIONS[:3], postulates="ПРАВИЛА")
    assert run["reason"] == "not-enough-material"
    assert run["proposals"] == []
    assert run["tokens"] == 0
    assert calls == []  # ни одного вызова модели


def test_threshold_is_configurable(monkeypatch):
    _chat(monkeypatch, {"proposals": []})
    run = distill.distill(CORRECTIONS[:3], postulates="x", min_corrections=2)
    assert run["reason"] == "distilled"


def test_empty_proposals_are_a_normal_outcome(monkeypatch):
    """Нет устойчивого паттерна → пустой список. Это полезный ответ, не сбой."""
    _chat(monkeypatch, {"proposals": []})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["ok"] is True
    assert run["proposals"] == []


# ───────── качество предложений ─────────


def test_retelling_of_a_post_is_rejected(monkeypatch):
    """Модель охотно переписывает пост в «правило». Это не обобщение."""
    quoted = CORRECTIONS[0]["post_text"][:60]
    _chat(monkeypatch, {"proposals": [_proposal(quoted)]})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["proposals"] == []
    assert any("пересказ" in r for r in run["rejected"])


def test_generalised_rule_survives(monkeypatch):
    _chat(
        monkeypatch,
        {"proposals": [_proposal("Частные объявления о продаже автомобилей → delete")]},
    )
    run = distill.distill(CORRECTIONS, postulates="x")
    assert len(run["proposals"]) == 1
    assert run["proposals"][0].rule_text.startswith("Частные объявления")


def test_evidence_outside_the_material_is_stripped(monkeypatch):
    """Правило, чьи «доказательства» ссылаются на неизвестные посты, оператор
    проверить не сможет — значит и оценить тоже."""
    _chat(
        monkeypatch,
        {"proposals": [_proposal("Объявления о технике → delete", evidence_lips=["1_0", "9_99"])]},
    )
    run = distill.distill(CORRECTIONS, postulates="x")
    assert [e["lip"] for e in run["proposals"][0].evidence] == ["1_0"]


def test_too_short_rule_is_rejected(monkeypatch):
    _chat(monkeypatch, {"proposals": [_proposal("нет")]})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["proposals"] == []
    assert run["rejected"]


def test_proposal_count_is_capped(monkeypatch):
    many = [_proposal(f"Обобщённое правило номер {i} про категорию контента") for i in range(20)]
    _chat(monkeypatch, {"proposals": many})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert len(run["proposals"]) <= distill.MAX_PROPOSALS


def test_region_code_is_carried_through(monkeypatch):
    _chat(
        monkeypatch, {"proposals": [_proposal("Районное правило про объявления", region_code="mi")]}
    )
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["proposals"][0].region_code == "mi"


def test_null_region_means_global_rule(monkeypatch):
    _chat(monkeypatch, {"proposals": [_proposal("Общее правило про объявления", region_code=None)]})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["proposals"][0].region_code is None


# ───────── форма запроса и отказы ─────────


def test_current_rules_go_to_system_prompt(monkeypatch):
    """Модель обязана видеть действующие правила — иначе предложит уже имеющееся."""
    calls = _chat(monkeypatch, {"proposals": []})
    distill.distill(CORRECTIONS, postulates="ДЕЙСТВУЮЩИЕ-МАРКЕР")
    assert "ДЕЙСТВУЮЩИЕ-МАРКЕР" in calls[0]["system"]
    assert calls[0]["json_object"] is True


def test_corrections_show_both_values(monkeypatch):
    """В промпте должно быть видно И решение ИИ, И правку оператора — иначе
    паттерн «из чего во что» не выводится."""
    calls = _chat(monkeypatch, {"proposals": []})
    distill.distill(CORRECTIONS, postulates="x")
    assert "publish" in calls[0]["user"] and "delete" in calls[0]["user"]


def test_material_is_capped(monkeypatch):
    calls = _chat(monkeypatch, {"proposals": []})
    distill.distill(CORRECTIONS * 40, postulates="x")
    assert calls[0]["user"].count("### Правка") == distill.MAX_CORRECTIONS


def test_api_failure_is_reported_not_raised(monkeypatch):
    _chat(monkeypatch, {"__fail__": "http_429"})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["ok"] is False
    assert run["reason"] == "http_429"


def test_unparseable_answer_is_reported(monkeypatch):
    def fake_chat(**_kw):
        return {"ok": True, "content": "извини, не смог", "model": "m", "usage": None}

    monkeypatch.setattr(distill, "chat", fake_chat)
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["reason"] == "llm_unparseable"


def test_answer_without_proposals_list_is_reported(monkeypatch):
    _chat(monkeypatch, {"oops": []})
    run = distill.distill(CORRECTIONS, postulates="x")
    assert run["proposals"] == []
    assert run["rejected"]
