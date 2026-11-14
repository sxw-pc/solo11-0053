from __future__ import annotations

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


def test_cli_replay_missing_record(demos):
    runner = CliRunner()
    result = runner.invoke(main, ["replay", "--ledger", str(demos["out"] / "none.sqlite3"), "--run-id", "run-nope"])
    assert result.exit_code == 2
    assert "GS-LGR-002" in result.output
