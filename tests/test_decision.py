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


def test_empty_results_still_pass_via_all_clear():
    decision = decide([])
    assert decision.outcome == "pass"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-003"
    assert list(decision.counts) == ["pass", "fail", "warn", "skip"]
    assert decision.counts == {"pass": 0, "fail": 0, "warn": 0, "skip": 0}


def test_counts_keys_are_stable_under_bucketing():
    decision = decide(
        [
            result("one", "fail"),
            result("two", "warn"),
            result("three", "skip"),
            result("four", "pass"),
            result("five", "weird-status"),
        ]
    )
    # 未知状态不产生新键，四个键的集合与顺序固定。
    assert list(decision.counts.keys()) == ["pass", "fail", "warn", "skip"]
    assert decision.counts == {"pass": 1, "fail": 1, "warn": 1, "skip": 1}


def test_fail_count_threshold_blocks_instead_of_review():
    decision = decide(
        [result("one", "fail"), result("two", "fail"), result("three", "warn")],
        block_on_fail=False,
        block_on_fail_count=2,
    )
    assert decision.outcome == "block"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-006"
    assert decision.reasons[0]["rule"] == "fail_count_blocks"
    assert decision.reasons[0]["outcome"] == "block"
    # summary 在前，其后逐条 fail 断言理由，保持既有理由链形状。
    assert [reason.get("assertion_id") for reason in decision.reasons[1:]] == ["one", "two"]
    assert all(reason["outcome"] == "block" for reason in decision.reasons)
    assert decision.counts == {"pass": 0, "fail": 2, "warn": 1, "skip": 0}


def test_fail_count_threshold_uses_greater_or_equal():
    decision = decide(
        [result("one", "fail"), result("two", "fail"), result("three", "fail")],
        block_on_fail=False,
        block_on_fail_count=3,
    )
    assert decision.outcome == "block"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-006"


def test_fail_below_threshold_falls_back_to_review_with_warns_merged():
    decision = decide(
        [result("one", "fail"), result("two", "warn")],
        block_on_fail=False,
        block_on_fail_count=2,
    )
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-004"
    # fail-review 分支仍把 warn 以 GS-DEC-002 并入同一条理由链。
    tail_codes = [reason["rule_code"] for reason in decision.reasons[1:]]
    assert tail_codes == ["GS-DEC-004", "GS-DEC-002"]
    assert [reason.get("assertion_id") for reason in decision.reasons[1:]] == ["one", "two"]


def test_hard_block_takes_priority_over_fail_count_threshold():
    decision = decide(
        [result("one", "fail")],
        block_on_fail=True,
        block_on_fail_count=5,
    )
    assert decision.outcome == "block"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-001"


def test_undeclared_threshold_keeps_legacy_review_behavior():
    decision = decide([result("one", "fail")], block_on_fail=False)
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-004"


def test_fail_count_threshold_from_decision_spec_object():
    spec = DecisionSpec(block_on_fail=False, block_on_fail_count=1)
    decision = decide([result("one", "fail"), result("two", "fail")], spec)
    assert decision.outcome == "block"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-006"


def test_fail_count_threshold_from_dict_spec():
    spec = {"block_on_fail": False, "block_on_fail_count": 2}
    decision = decide([result("one", "fail")], spec)
    assert decision.outcome == "review"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-004"
    decision = decide([result("one", "fail"), result("two", "fail")], spec)
    assert decision.outcome == "block"
    assert decision.reasons[0]["rule_code"] == "GS-DEC-006"


def test_legacy_rule_codes_remain_unchanged():
    cases = [
        ([result("a", "fail")], {}, "GS-DEC-001"),
        ([result("a", "fail")], {"block_on_fail": False}, "GS-DEC-004"),
        ([result("a", "warn")], {"review_on_warn_count": 1}, "GS-DEC-002"),
        (
            [result("a", "skip")],
            {"review_on_warn_count": 2, "review_on_skip_count": 1},
            "GS-DEC-005",
        ),
        ([result("a", "pass")], {}, "GS-DEC-003"),
    ]
    for items, kwargs, expected_code in cases:
        assert decide(items, **kwargs).reasons[0]["rule_code"] == expected_code
