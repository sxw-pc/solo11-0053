"""Replay compatibility for the CSV record-number semantics upgrade.

旧台账（记录号=数据行序号）在新代码下重放时，断言证据与报告哈希必然变化。
这些测试验证重放路径能把“记录号语义升级的预期签名变化”与“真实数据漂移”区分开。
"""

from __future__ import annotations

from gatesampler.config import load_run_config
from gatesampler.ledger import get_judgment, init_db, save_judgment
from gatesampler.replay import replay_judgment
from gatesampler.report import render_json
from gatesampler.runner import run_config
from gatesampler.util import sha256_bytes

# 断言证据中承载记录号语义的键，与 replay.py 中的定义保持一致。
_RECORD_NO_KEYS = ("record_no", "rows", "null_rows")


def _downgrade_record_number(value):
    """把升级后的物理行号结构映射回修复前的“数据行序号”（CSV 表头偏移 1 行）。"""
    if isinstance(value, dict):
        return {key: _downgrade_record_number(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_downgrade_record_number(item) for item in value]
    if isinstance(value, int) and value > 1:
        return value - 1
    return value


def _downgrade_evidence(evidence):
    if not isinstance(evidence, dict):
        return evidence
    result = {}
    for key, value in evidence.items():
        if key in _RECORD_NO_KEYS:
            result[key] = _downgrade_record_number(value)
        else:
            result[key] = _downgrade_evidence(value)
    return result


def _persist_legacy_csv_ledger(config):
    """用新代码跑出判定，再把记录号回退为旧语义后重算报告哈希并落账。"""
    outcomes = run_config(config)
    csv_outcomes = [
        outcome
        for outcome in outcomes
        if outcome.judgment.batch.format == "csv"
    ]
    assert csv_outcomes, "测试配置至少需要一个 CSV 批次"
    connection = init_db(config.ledger_path)
    try:
        for outcome in csv_outcomes:
            report = outcome.judgment.report
            legacy_report = {
                key: value for key, value in report.items()
            }
            legacy_report["assertions"] = [
                {**assertion, "evidence": _downgrade_evidence(assertion["evidence"])}
                for assertion in report["assertions"]
            ]
            sampling = dict(legacy_report["sampling"])
            # 旧语义：sample.indices 从 0 开始，旧记录号即数据行序号（indices + 1）
            sampling["sample_record_numbers"] = [
                index + 1 for index in sampling["indices"]
            ]
            legacy_report["sampling"] = sampling
            legacy_report_sha = sha256_bytes(render_json(legacy_report))
            save_judgment(
                connection,
                legacy_report,
                config_snapshot=outcome.judgment.config_snapshot,
                report_sha256=legacy_report_sha,
            )
    finally:
        connection.close()
    return csv_outcomes


def test_legacy_csv_ledger_replay_classified_as_record_number_upgrade(demos):
    config = load_run_config(demos["config"])
    csv_outcomes = _persist_legacy_csv_ledger(config)

    for outcome in csv_outcomes:
        run_id = outcome.judgment.run_id
        result = replay_judgment(config.ledger_path, run_id=run_id)
        # 严格意义上签名确实变化，consistent 仍为 False，避免静默掩盖差异
        assert not result.consistent
        assert result.record_number_upgrade is True
        # 预期差异只允许出现在断言证据与报告哈希上
        labels = {mismatch.split(":", 1)[0] for mismatch in result.mismatches}
        assert labels <= {"assertions", "report_sha256"}
        assert any("物理行号" in note for note in result.upgrade_notes)
        # indices 与 sample_fingerprint 完全不变
        current_sample = result.judgment.sample
        connection = init_db(config.ledger_path)
        try:
            stored = get_judgment(connection, run_id)
        finally:
            connection.close()
        assert stored["sampling"]["sample_fingerprint"] == current_sample.sample_fingerprint
        assert stored["sampling"]["indices"] == list(current_sample.indices)


def test_real_drift_is_not_misclassified_as_upgrade(demos):
    config = load_run_config(demos["config"])
    _persist_legacy_csv_ledger(config)
    # 真实数据漂移：篡改源文件，source_sha256 必然变化
    source = demos["fixtures"] / "batches" / "batch_a.csv"
    source.write_bytes(
        source.read_bytes() + b"ord-tampered, cust-x, north, 1.0, paid, 2024-12-31\n"
    )
    result = replay_judgment(config.ledger_path, batch_id="batch_a")
    assert not result.consistent
    assert result.record_number_upgrade is False
    assert result.upgrade_notes == ()
    assert any(item.startswith("source_sha256:") for item in result.mismatches)


def test_evidence_change_beyond_record_numbers_is_not_misclassified(demos):
    """记录号之外的证据变化（此处模拟真实漂移只改证据内容）不得归入升级。"""
    config = load_run_config(demos["config"])
    outcomes = run_config(config)
    connection = init_db(config.ledger_path)
    try:
        target = next(o for o in outcomes if o.judgment.batch.format == "csv")
        report = target.judgment.report
        tampered_report = {key: value for key, value in report.items()}
        tampered_assertions = []
        for assertion in report["assertions"]:
            evidence = dict(assertion["evidence"])
            evidence["checked"] = int(evidence.get("checked", 0)) + 1
            tampered_assertions.append({**assertion, "evidence": evidence})
        tampered_report["assertions"] = tampered_assertions
        save_judgment(
            connection,
            tampered_report,
            config_snapshot=target.judgment.config_snapshot,
            report_sha256=sha256_bytes(render_json(tampered_report)),
        )
        run_id = target.judgment.run_id
    finally:
        connection.close()
    result = replay_judgment(config.ledger_path, run_id=run_id)
    assert not result.consistent
    assert result.record_number_upgrade is False
    assert any(item.startswith("assertions:") for item in result.mismatches)


def test_jsonl_ledger_replay_stays_consistent_after_upgrade(demos):
    config = load_run_config(demos["config"])
    run_config(config)
    # batch_b 是 JSONL：记录号语义未改动，重放必须完全一致
    result = replay_judgment(config.ledger_path, batch_id="batch_b")
    assert result.consistent, result.mismatches
    assert result.record_number_upgrade is False
    assert result.stored_report_sha256 == result.recomputed_report_sha256


def test_sampling_fingerprint_stable_across_repeated_runs(demos):
    config = load_run_config(demos["config"])
    first = run_config(config)[0].judgment
    second = run_config(config)[0].judgment
    assert first.sample.indices == second.sample.indices
    assert first.sample.sample_fingerprint == second.sample.sample_fingerprint
    # 报告中的样本记录号与断言证据命中行使用同一套物理行号
    report = second.report
    expected_numbers = [
        second.batch.record_numbers[index] for index in second.sample.indices
    ]
    assert report["sampling"]["sample_record_numbers"] == expected_numbers
    for assertion in report["assertions"]:
        evidence_rows = assertion["evidence"].get("rows")
        if evidence_rows:
            assert set(evidence_rows) <= set(second.batch.record_numbers)
