from __future__ import annotations

from gatesampler.ledger import (
    decision_counts,
    get_judgment,
    init_db,
    latest_judgment,
    list_judgments,
    save_judgment,
)


def make_report(run_id: str, batch_id: str, decision: str, gate: str = "orders_gate") -> dict:
    counts = {"pass": 1, "fail": 0, "warn": 0, "skip": 0}
    if decision == "block":
        counts = {"pass": 0, "fail": 1, "warn": 0, "skip": 0}
    elif decision == "review":
        counts = {"pass": 0, "fail": 0, "warn": 1, "skip": 0}
    status = "pass" if decision == "pass" else ("fail" if decision == "block" else "warn")
    return {
        "report_version": "1.0",
        "judgment_id": run_id,
        "config_fingerprint": "f" * 64,
        "gate": {"gate_id": gate, "contract_version": "1.0", "description": "demo"},
        "batch": {
            "batch_id": batch_id,
            "source": f"fixtures/{batch_id}.csv",
            "format": "csv",
            "encoding": "utf-8-sig",
            "row_count": 10,
            "column_count": 4,
            "columns": ["id"],
            "expected_rows": 10,
            "source_sha256": "a" * 64,
            "load_warning_count": 0,
            "load_warnings": [],
            "strict": False,
        },
        "sampling": {
            "method": "systematic",
            "seed": 42,
            "size_requested": 5,
            "size_actual": 5,
            "indices": [0, 2, 4, 6, 8],
            "sample_record_numbers": [1, 3, 5, 7, 9],
            "sample_fingerprint": "b" * 64,
            "details": {},
        },
        "assertions": [
            {
                "assertion_id": "one",
                "type": "count",
                "status": status,
                "code": "GS-GATE-101",
                "metric": 10,
                "threshold": 10,
                "evidence": {"rows": [1], "text": "证据"},
                "message": "消息",
            }
        ],
        "decision": {"outcome": decision, "reasons": [{"rule_code": "GS-DEC-003"}], "counts": counts},
        "error_codes": {"load": {}, "assertions": {}, "decision": {}},
    }


def save(connection, report, run_id: str | None = None):
    return save_judgment(
        connection,
        report,
        config_snapshot={"strict": False, "batch": {"batch_id": report["batch"]["batch_id"]}},
        report_sha256="c" * 64,
        created_at=run_id or "2026-01-01T00:00:00+00:00",
    )


def test_ledger_crud_and_queries(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        save(connection, make_report("run-a", "batch_a", "pass"))
        save(connection, make_report("run-b", "batch_b", "block"))
        save(connection, make_report("run-c", "batch_c", "review", gate="other_gate"))
        assert len(list_judgments(connection)) == 3
        assert [row["run_id"] for row in list_judgments(connection, batch_id="batch_a")] == ["run-a"]
        assert [row["run_id"] for row in list_judgments(connection, decision="block")] == ["run-b"]
        assert [row["run_id"] for row in list_judgments(connection, gate_id="other_gate")] == ["run-c"]
        assert decision_counts(connection) == {"pass": 1, "block": 1, "review": 1}

        record = get_judgment(connection, "run-b")
        assert record["decision"] == "block"
        assert record["sampling"]["seed"] == 42
        assert record["assertions"][0]["evidence"]["text"] == "证据"
        assert record["config_snapshot"]["strict"] is False
        assert latest_judgment(connection, "batch_b")["run_id"] == "run-b"
    finally:
        connection.close()


def test_save_is_idempotent_and_assertion_rows_are_replaced(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        report = make_report("run-a", "batch_a", "pass")
        save(connection, report)
        report["decision"] = {"outcome": "block", "reasons": [], "counts": report["decision"]["counts"]}
        report["assertions"][0]["evidence"] = {"rows": [2]}
        save(connection, report)
        assert len(list_judgments(connection)) == 1
        record = get_judgment(connection, "run-a")
        assert record["assertions"][0]["evidence"]["rows"] == [2]
    finally:
        connection.close()


def test_missing_record_returns_none(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        assert get_judgment(connection, "absent") is None
        assert latest_judgment(connection, "absent") is None
        assert list_judgments(connection) == []
    finally:
        connection.close()
