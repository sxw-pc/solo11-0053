from __future__ import annotations

import json

from click.testing import CliRunner

from gatesampler.cli import main
from gatesampler.config import load_run_config
from gatesampler.ledger import init_db, list_judgments


def test_cli_run_replay_and_query_end_to_end(demos):
    runner = CliRunner()
    run_result = runner.invoke(main, ["run", "--config", str(demos["config"])])
    assert run_result.exit_code == 0, run_result.output
    assert "batch_a" in run_result.output
    assert "decision=review" in run_result.output
    assert "完成：3 个批次" in run_result.output
    config = load_run_config(demos["config"])
    assert (config.out_dir / "reports" / "batch_a.json").exists()
    assert (config.out_dir / "reports" / "batch_b.md").exists()

    connection = init_db(config.ledger_path)
    try:
        rows = list_judgments(connection)
    finally:
        connection.close()
    run_id = next(row["run_id"] for row in rows if row["batch_id"] == "batch_bad")
    replay_result = runner.invoke(
        main,
        ["replay", "--ledger", str(config.ledger_path), "--run-id", run_id],
    )
    assert replay_result.exit_code == 0, replay_result.output
    assert "重放一致" in replay_result.output

    query_result = runner.invoke(
        main,
        ["query", "--ledger", str(config.ledger_path), "--decision", "review"],
    )
    assert query_result.exit_code == 0, query_result.output
    assert "batch_b" in query_result.output

    json_result = runner.invoke(
        main,
        ["query", "--ledger", str(config.ledger_path), "--json", "--batch-id", "batch_a"],
    )
    assert json_result.exit_code == 0
    assert '"batch_a"' in json_result.output


def test_cli_reports_stable_error_code(config_factory):
    config = config_factory(version="9.0")
    result = CliRunner().invoke(main, ["run", "--config", str(config)])
    assert result.exit_code == 2
    assert "GS-CFG-002" in result.output


def test_cli_query_multi_batch_time_range_and_cursor(demos):
    runner = CliRunner()
    assert runner.invoke(main, ["run", "--config", str(demos["config"])]).exit_code == 0
    config = load_run_config(demos["config"])

    first = runner.invoke(
        main,
        [
            "query",
            "--ledger", str(config.ledger_path),
            "--batch-id", "batch_a",
            "--batch-id", "batch_b",
            "--limit", "1",
        ],
    )
    assert first.exit_code == 0, first.output
    # Backward-compatible count line stays exactly as before, but now filtered.
    assert "决策计数: pass=1 block=0 review=1" in first.output
    assert "匹配总数: 2" in first.output
    assert "下一页游标: " in first.output
    cursor = first.output.split("下一页游标: ", 1)[1].splitlines()[0].strip()

    second = runner.invoke(
        main,
        [
            "query",
            "--ledger", str(config.ledger_path),
            "--batch-id", "batch_a",
            "--batch-id", "batch_b",
            "--limit", "1",
            "--cursor", cursor,
        ],
    )
    assert second.exit_code == 0, second.output
    assert "没有更多记录。" in second.output
    assert "batch_bad" not in second.output
    first_rows = {line.split()[1] for line in first.output.splitlines() if line.startswith("run-")}
    second_rows = {line.split()[1] for line in second.output.splitlines() if line.startswith("run-")}
    assert first_rows and second_rows and first_rows != second_rows

    ranged = runner.invoke(
        main,
        [
            "query",
            "--ledger", str(config.ledger_path),
            "--created-from", "2000-01-01",
            "--created-to", "2999-12-31",
            "--json",
        ],
    )
    assert ranged.exit_code == 0, ranged.output
    payload = json.loads(ranged.output)
    assert {row["batch_id"] for row in payload["judgments"]} == {"batch_a", "batch_b", "batch_bad"}
    assert payload["total"] == 3
    assert payload["next_cursor"] is None

    empty = runner.invoke(
        main,
        [
            "query",
            "--ledger", str(config.ledger_path),
            "--created-from", "2999-01-01",
            "--json",
        ],
    )
    assert empty.exit_code == 0
    assert json.loads(empty.output)["total"] == 0


def test_cli_query_rejects_bad_cursor_and_bad_time(demos):
    runner = CliRunner()
    assert runner.invoke(main, ["run", "--config", str(demos["config"])]).exit_code == 0
    config = load_run_config(demos["config"])
    bad_cursor = runner.invoke(
        main,
        ["query", "--ledger", str(config.ledger_path), "--cursor", "garbage"],
    )
    assert bad_cursor.exit_code == 2
    assert "游标无效" in bad_cursor.output
    bad_time = runner.invoke(
        main,
        ["query", "--ledger", str(config.ledger_path), "--created-from", "yesterday"],
    )
    assert bad_time.exit_code == 2
    assert "无法解析" in bad_time.output


def test_cli_replay_missing_record(demos):
    runner = CliRunner()
    result = runner.invoke(main, ["replay", "--ledger", str(demos["out"] / "none.sqlite3"), "--run-id", "run-nope"])
    assert result.exit_code == 2
    assert "GS-LGR-002" in result.output
