from __future__ import annotations

import pytest

from gatesampler.ledger import (
    decision_counts,
    encode_cursor,
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


def save(connection, report, run_id: str | None = None, *, created_at: str | None = None):
    return save_judgment(
        connection,
        report,
        config_snapshot={"strict": False, "batch": {"batch_id": report["batch"]["batch_id"]}},
        report_sha256="c" * 64,
        created_at=created_at or run_id or "2026-01-01T00:00:00+00:00",
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


def test_multiple_batch_filter_and_filtered_counts(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        save(connection, make_report("run-a1", "batch_a", "pass"))
        save(connection, make_report("run-a2", "batch_a", "block"))
        save(connection, make_report("run-b1", "batch_b", "review"))
        save(connection, make_report("run-c1", "batch_c", "pass", gate="other_gate"))

        page = list_judgments(connection, batch_ids=["batch_a", "batch_b"])
        assert {row["run_id"] for row in page.records} == {"run-a1", "run-a2", "run-b1"}
        assert page.counts == {"pass": 1, "block": 1, "review": 1}
        assert page.total == 3
        # A single batch_id still works, and explicit batch_ids wins by union.
        assert len(list_judgments(connection, batch_id="batch_a")) == 2
        assert decision_counts(connection) == {"pass": 2, "block": 1, "review": 1}
        assert decision_counts(connection, batch_ids=["batch_a"]) == {
            "pass": 1,
            "block": 1,
            "review": 0,
        }
        # Filtered counts follow the decision filter too.
        assert decision_counts(connection, decision="pass") == {"pass": 2, "block": 0, "review": 0}
        # Unknown batch ids simply match nothing.
        assert list_judgments(connection, batch_ids=["absent"]).total == 0
    finally:
        connection.close()


def test_created_at_range_and_boundary_semantics(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        save(connection, make_report("run-early", "batch_a", "pass"), created_at="2026-01-01T00:00:00+00:00")
        save(connection, make_report("run-mid", "batch_b", "block"), created_at="2026-01-15T12:00:00+00:00")
        save(connection, make_report("run-late", "batch_c", "review"), created_at="2026-02-01T00:00:00+00:00")

        inclusive = list_judgments(connection, created_from="2026-01-01", created_to="2026-01-31")
        assert [row["run_id"] for row in inclusive] == ["run-mid", "run-early"]
        # Bare dates cover the whole day on both ends.
        assert [row["run_id"] for row in list_judgments(connection, created_from="2026-02-01")] == ["run-late"]
        assert [row["run_id"] for row in list_judgments(connection, created_to="2026-01-01")] == ["run-early"]
        # Datetime bounds with Z and second precision are accepted and inclusive.
        assert [
            row["run_id"]
            for row in list_judgments(connection, created_from="2026-01-15T12:00:00Z")
        ] == ["run-late", "run-mid"]
        assert decision_counts(connection, created_from="2026-01-15T12:00:00Z") == {
            "pass": 0,
            "block": 1,
            "review": 1,
        }
        with pytest.raises(ValueError):
            list_judgments(connection, created_from="not-a-date")
    finally:
        connection.close()


def test_cursor_pagination_is_stable_for_same_second_rows(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        same_second = "2026-01-01T00:00:00+00:00"
        for index, run_id in enumerate(["run-1", "run-2", "run-3", "run-4"]):
            save(connection, make_report(run_id, "batch_a", "pass"), created_at=same_second)
        save(connection, make_report("run-5", "batch_b", "block"), created_at="2025-12-31T00:00:00+00:00")

        first = list_judgments(connection, limit=2)
        assert len(first.records) == 2
        assert first.total == 5
        assert first.counts == {"pass": 4, "block": 1, "review": 0}
        assert first.next_cursor is not None
        # Cursor is round-trippable and carries both ordering keys.
        assert first.next_cursor == encode_cursor(first.records[-1])

        # Walk to the end, collecting rows, and ensure no duplicates or gaps.
        seen: list[str] = []
        cursor = first.next_cursor
        page = first
        while cursor is not None:
            page = list_judgments(connection, limit=2, cursor=cursor)
            seen.extend(row["run_id"] for row in page.records)
            cursor = page.next_cursor
        all_ids = [row["run_id"] for row in first.records] + seen
        assert len(all_ids) == len(set(all_ids)) == 5
        assert set(all_ids) == {"run-1", "run-2", "run-3", "run-4", "run-5"}
        # Same-second rows keep one deterministic order even though created_at ties.
        assert all_ids[:4] == ["run-4", "run-3", "run-2", "run-1"]
        assert page.total == 5
        assert page.next_cursor is None

        # The last page exactly fills the limit and must not invent a cursor.
        exact = list_judgments(connection, limit=5)
        assert exact.next_cursor is None

        with pytest.raises(ValueError):
            list_judgments(connection, cursor="not-a-cursor")
    finally:
        connection.close()


def test_cursor_pagination_respects_filters(tmp_path):
    connection = init_db(tmp_path / "audit.sqlite3")
    try:
        save(connection, make_report("run-a", "batch_a", "pass"), created_at="2026-01-02T00:00:00+00:00")
        save(connection, make_report("run-b", "batch_b", "pass"), created_at="2026-01-01T00:00:00+00:00")
        save(connection, make_report("run-c", "batch_c", "block"), created_at="2026-01-01T00:00:00+00:00")
        page = list_judgments(
            connection, batch_ids=["batch_a", "batch_b"], decision="pass", limit=1
        )
        assert [row["run_id"] for row in page.records] == ["run-a"]
        assert page.total == 2
        assert page.counts == {"pass": 2, "block": 0, "review": 0}
        second = list_judgments(
            connection, batch_ids=["batch_a", "batch_b"], decision="pass", limit=1,
            cursor=page.next_cursor,
        )
        assert [row["run_id"] for row in second.records] == ["run-b"]
        assert second.next_cursor is None
        assert second.total == 2
    finally:
        connection.close()
