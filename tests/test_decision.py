from __future__ import annotations

from gatesampler.assertions import GuardResult
from gatesampler.config import DecisionSpec
from gatesampler.decision import decide


def result(assertion_id: str, status: str, code: str = "GS-GATE-101") -> GuardResult:
    return GuardResult(
        assertion_id=assertion_id,
        type="test",
        status=status,
        code=code,
        metric=0,
        threshold=0,
        evidence={},
        message=f"{assertion_id} is {status}",
    )


def test_all_pass_is_pass():
    decision = decide([result("one", "pass"), result("two", "pass")])
    assert decision.outcome == "pass"
    assert decision.counts == {"pass": 2, "fail": 0, "warn": 0, "skip": 0}
    assert decision.reasons[0]["rule_code"] == "GS-DEC-003"


def test_any_fail_blocks_and_reason_names_assertion():
    decision = decide([result("domain", "fail", "GS-GATE-103"), result("nulls", "warn")])
    assert decision.outcome == "block"
    assert decision.counts["fail"] == 1
    assert any(reason.get("assertion_id") == "domain" for reason in decision.reasons)
    assert all(reason["outcome"] == "block" for reason in decision.reasons)


def test_warn_threshold_triggers_review():
    decision = decide(
        [result("one", "warn"), result("two", "warn")],
        review_on_warn_count=2,
    )
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-002"
    assert {reason.get("assertion_id") for reason in decision.reasons[1:]} == {"one", "two"}


def test_warn_below_threshold_passes():
    decision = decide([result("one", "warn")], review_on_warn_count=2)
    assert decision.outcome == "pass"


def test_fail_can_review_when_block_disabled():
    decision = decide([result("one", "fail")], block_on_fail=False)
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-004"


def test_skip_threshold_and_decision_spec_object():
    spec = DecisionSpec(block_on_fail=True, review_on_warn_count=2, review_on_skip_count=1)
    decision = decide([result("one", "skip"), result("two", "pass")], spec)
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-005"


def test_decision_is_independent_of_files_and_order_is_stable():
    inputs = [result("first", "warn"), result("second", "warn")]
    first = decide(inputs, review_on_warn_count=1)
    second = decide(inputs, review_on_warn_count=1)
    assert first.to_dict() == second.to_dict()
    assert first.reasons[1]["assertion_id"] == "first"
