"""SQLite audit ledger for judgments, sampling seeds, and assertion evidence."""

from __future__ import annotations

import base64
import binascii
import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .util import canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    run_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    source TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    format TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    expected_rows INTEGER,
    sampling_json TEXT NOT NULL,
    seed INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('pass', 'block', 'review')),
    fail_count INTEGER NOT NULL,
    warn_count INTEGER NOT NULL,
    skip_count INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assertion_results (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    assertion_id TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    code TEXT NOT NULL,
    metric_json TEXT NOT NULL,
    threshold_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES judgments(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_judgments_batch ON judgments(batch_id);
CREATE INDEX IF NOT EXISTS idx_judgments_decision ON judgments(decision);
CREATE INDEX IF NOT EXISTS idx_judgments_gate ON judgments(gate_id);
CREATE INDEX IF NOT EXISTS idx_judgments_order ON judgments(created_at DESC, run_id DESC);
"""


def open_db(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(path: str | Path) -> sqlite3.Connection:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_db(ledger_path)
    connection.executescript(SCHEMA)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save_judgment(
    connection: sqlite3.Connection,
    report: dict[str, Any],
    *,
    config_snapshot: dict[str, Any],
    report_sha256: str,
    created_at: str | None = None,
) -> str:
    """Idempotently persist a content-addressed judgment and its assertions."""
    batch = report["batch"]
    decision = report["decision"]
    counts = decision["counts"]
    run_id = report["judgment_id"]
    values = (
        run_id,
        batch["batch_id"],
        report["gate"]["gate_id"],
        report["gate"]["contract_version"],
        batch["source"],
        batch["source_sha256"],
        batch["format"],
        batch["row_count"],
        batch["column_count"],
        batch["expected_rows"],
        canonical_json(report["sampling"]),
        int(report["sampling"]["seed"]),
        decision["outcome"],
        int(counts["fail"]),
        int(counts["warn"]),
        int(counts["skip"]),
        canonical_json(decision["reasons"]),
        report["config_fingerprint"],
        canonical_json(config_snapshot),
        report_sha256,
        created_at or _now(),
    )
    with connection:
        connection.execute("DELETE FROM assertion_results WHERE run_id = ?", (run_id,))
        connection.execute(
            """
            INSERT OR REPLACE INTO judgments (
                run_id, batch_id, gate_id, contract_version, source, source_sha256, format,
                row_count, column_count, expected_rows, sampling_json, seed, decision,
                fail_count, warn_count, skip_count, reasons_json, config_fingerprint,
                config_snapshot, report_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        for seq, assertion in enumerate(report["assertions"]):
            connection.execute(
                """
                INSERT INTO assertion_results (
                    run_id, seq, assertion_id, type, status, code,
                    metric_json, threshold_json, evidence_json, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    seq,
                    assertion["assertion_id"],
                    assertion["type"],
                    assertion["status"],
                    assertion["code"],
                    canonical_json(assertion["metric"]),
                    canonical_json(assertion["threshold"]),
                    canonical_json(assertion["evidence"]),
                    assertion["message"],
                ),
            )
    return run_id


def _decode_judgment(row: sqlite3.Row, include_snapshot: bool) -> dict[str, Any]:
    data = dict(row)
    data["sampling"] = json.loads(data.pop("sampling_json"))
    data["reasons"] = json.loads(data.pop("reasons_json"))
    snapshot = data.pop("config_snapshot")
    if include_snapshot:
        data["config_snapshot"] = json.loads(snapshot)
    return data


def list_assertions(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM assertion_results WHERE run_id = ? ORDER BY seq", (run_id,)
    ).fetchall()
    assertions = []
    for row in rows:
        item = dict(row)
        item["metric"] = json.loads(item.pop("metric_json"))
        item["threshold"] = json.loads(item.pop("threshold_json"))
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item.pop("seq", None)
        assertions.append(item)
    return assertions


def get_judgment(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM judgments WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    judgment = _decode_judgment(row, include_snapshot=True)
    judgment["assertions"] = list_assertions(connection, run_id)
    return judgment


def _parse_created_bound(value: str, *, upper: bool) -> str:
    """Normalize a user-supplied time bound to the ledger's UTC second format.

    Accepts ``YYYY-MM-DD`` (whole-day semantics), ISO datetimes with or without
    seconds, and a trailing ``Z``. Naive values are interpreted as UTC.
    """
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        clock = "23:59:59.999999" if upper else "00:00:00"
        return f"{day.isoformat()}T{clock}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"无法解析 created_at 时间边界: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    stored = parsed.astimezone(UTC)
    timespec = "microseconds" if stored.microsecond else "seconds"
    return stored.isoformat(timespec=timespec)


def _normalize_batch_ids(
    batch_id: str | None, batch_ids: Iterable[str] | None
) -> tuple[str, ...]:
    values: list[str] = []
    if batch_ids is not None:
        values.extend(str(item).strip() for item in batch_ids)
    if batch_id is not None:
        values.append(batch_id.strip())
    return tuple(dict.fromkeys(item for item in values if item))


def _filter_clauses(
    *,
    batch_id: str | None,
    batch_ids: Iterable[str] | None,
    decision: str | None,
    gate_id: str | None,
    created_from: str | None,
    created_to: str | None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    ids = _normalize_batch_ids(batch_id, batch_ids)
    if ids:
        clauses.append("batch_id IN (" + ", ".join("?" for _ in ids) + ")")
        params.extend(ids)
    if decision is not None:
        clauses.append("decision = ?")
        params.append(decision)
    if gate_id is not None:
        clauses.append("gate_id = ?")
        params.append(gate_id)
    if created_from is not None:
        clauses.append("created_at >= ?")
        params.append(_parse_created_bound(created_from, upper=False))
    if created_to is not None:
        clauses.append("created_at <= ?")
        params.append(_parse_created_bound(created_to, upper=True))
    return clauses, params


def encode_cursor(record: dict[str, Any]) -> str:
    """Encode a stable continuation cursor for ``(created_at, run_id)`` ordering."""
    payload = json.dumps(
        {"v": 1, "created_at": record["created_at"], "run_id": record["run_id"]},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode and validate a cursor produced by :func:`encode_cursor`."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("ascii"))
        created_at = payload["created_at"]
        run_id = payload["run_id"]
        if payload.get("v") != 1 or not isinstance(created_at, str) or not isinstance(run_id, str):
            raise ValueError("游标结构不合法")
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error) as error:
        raise ValueError(f"游标无效: {cursor}") from error
    return created_at, run_id


class JudgmentPage(list):
    """A page of judgment rows plus the cursor and counts for the same filter.

    It behaves like a plain list of rows (iteration, indexing, ``len``) so older
    callers keep working, while new callers can read ``records``, ``next_cursor``,
    ``total`` and ``counts``.
    """

    def __init__(
        self,
        records: Iterable[dict[str, Any]],
        *,
        next_cursor: str | None,
        counts: dict[str, int],
        total: int,
    ) -> None:
        super().__init__(records)
        self.records = list(self)
        self.next_cursor = next_cursor
        self.counts = counts
        self.total = total


def decision_counts(
    connection: sqlite3.Connection,
    *,
    batch_id: str | None = None,
    batch_ids: Iterable[str] | None = None,
    decision: str | None = None,
    gate_id: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> dict[str, int]:
    """Aggregate decisions for the rows matching the same filters as queries.

    With no filters this is the whole-ledger aggregate; with filters, counts describe
    only the matching subset (including the ``decision`` filter itself).
    """
    clauses, params = _filter_clauses(
        batch_id=batch_id,
        batch_ids=batch_ids,
        decision=decision,
        gate_id=gate_id,
        created_from=created_from,
        created_to=created_to,
    )
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT decision, COUNT(*) AS count FROM judgments{where} GROUP BY decision", params
    ).fetchall()
    result = {"pass": 0, "block": 0, "review": 0}
    result.update({row["decision"]: row["count"] for row in rows})
    return result


def list_judgments(
    connection: sqlite3.Connection,
    *,
    batch_id: str | None = None,
    batch_ids: Iterable[str] | None = None,
    decision: str | None = None,
    gate_id: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = 200,
    cursor: str | None = None,
) -> JudgmentPage:
    """Return one cursor-paginated page of judgments and its filtered counts.

    Rows are ordered by ``created_at DESC, run_id DESC``. ``run_id`` is the unique
    primary key, so records written in the same second still have a deterministic,
    gap-free order across pages. The cursor is an opaque token for the last row
    of this page; pass it back to fetch the next page. Counts and ``total`` always
    describe the full filtered result set, not just this page.
    """
    clauses, params = _filter_clauses(
        batch_id=batch_id,
        batch_ids=batch_ids,
        decision=decision,
        gate_id=gate_id,
        created_from=created_from,
        created_to=created_to,
    )
    if cursor is not None:
        try:
            cursor_at, cursor_run_id = decode_cursor(cursor)
        except ValueError as error:
            raise ValueError(str(error)) from error
        clauses.append("(created_at < ? OR (created_at = ? AND run_id < ?))")
        params.extend([cursor_at, cursor_at, cursor_run_id])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    page_size = max(1, min(int(limit), 1000))
    params.append(page_size + 1)
    rows = connection.execute(
        f"""
        SELECT * FROM judgments{where}
        ORDER BY created_at DESC, run_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    page_rows = rows[:page_size]
    records = [_decode_judgment(row, include_snapshot=False) for row in page_rows]
    next_cursor = encode_cursor(records[-1]) if len(rows) > page_size and records else None
    counts = decision_counts(
        connection,
        batch_id=batch_id,
        batch_ids=batch_ids,
        decision=decision,
        gate_id=gate_id,
        created_from=created_from,
        created_to=created_to,
    )
    return JudgmentPage(
        records,
        next_cursor=next_cursor,
        counts=counts,
        total=sum(counts.values()),
    )


def latest_judgment(connection: sqlite3.Connection, batch_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT run_id FROM judgments WHERE batch_id = ?
        ORDER BY created_at DESC, rowid DESC LIMIT 1
        """,
        (batch_id,),
    ).fetchone()
    return None if row is None else get_judgment(connection, row["run_id"])

