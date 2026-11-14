from __future__ import annotations

from gatesampler.config import load_run_config
from gatesampler.ledger import init_db, list_judgments
from gatesampler.replay import replay_judgment
from gatesampler.runner import run_config


def test_replay_matches_original_fixture_judgment(demos):
    config = load_run_config(demos["config"])
    outcomes = run_config(config)
    ledger = config.ledger_path
    connection = init_db(ledger)
    try:
        rows = list_judgments(connection)
    finally:
        connection.close()
    assert len(rows) == 3
    for row in rows:
        replay = replay_judgment(ledger, run_id=row["run_id"])
        assert replay.consistent, replay.mismatches
        assert replay.stored_report_sha256 == replay.recomputed_report_sha256
    review = replay_judgment(ledger, batch_id="batch_b")
    assert review.consistent
    assert review.judgment.decision.outcome == "review"
    assert outcomes[1].judgment.run_id == review.run_id


def test_replay_detects_changed_source(demos):
    config = load_run_config(demos["config"])
    run_config(config)
    source = demos["fixtures"] / "batches" / "batch_a.csv"
    source.write_bytes(source.read_bytes() + b"ord-tampered, cust-x, north, 1.0, paid, 2024-12-31\n")
    result = replay_judgment(config.ledger_path, batch_id="batch_a")
    assert not result.consistent
    assert any(item.startswith("source_sha256:") for item in result.mismatches)
