"""Command-line entry points for GateSampler."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click

from . import __version__
from .config import load_run_config
from .errors import GateSamplerError
from .ledger import decision_counts, init_db, list_judgments
from .replay import replay_judgment
from .runner import run_config

DEFAULT_LEDGER = Path("out/gate_sampler.sqlite3")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _fail(ctx: click.Context, error: GateSamplerError, exit_code: int = 2) -> None:
    location = f" [{error.location}]" if error.location else ""
    click.echo(f"{error.code}{location}: {error.message}", err=True)
    ctx.exit(exit_code)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="gatesampler")
def main() -> None:
    """离线、确定性的入库前批次抽样闸门。"""


@main.command("run")
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="运行配置 YAML 路径。",
)
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path), help="覆盖报告输出目录。")
@click.option("--ledger", type=click.Path(dir_okay=False, path_type=Path), help="覆盖 SQLite 台账路径。")
@click.option("--strict/--no-strict", default=None, help="覆盖配置中的加载严格模式。")
@click.pass_context
def run_command(
    ctx: click.Context,
    config_path: Path,
    out_dir: Path | None,
    ledger: Path | None,
    strict: bool | None,
) -> None:
    """加载契约，执行每批抽样与断言，写出报告和台账。"""
    try:
        config = load_run_config(config_path)
        outcomes = run_config(config, out_dir=out_dir, ledger_path=ledger, strict=strict)
    except GateSamplerError as error:
        _fail(ctx, error)
        return
    for outcome in outcomes:
        judgment = outcome.judgment
        batch = judgment.batch
        click.echo(
            f"[{batch.batch_id}] rows={batch.row_count} cols={batch.column_count} "
            f"sha256={batch.source_sha256[:16]}… "
            f"sample={judgment.sample.method}/{judgment.sample.size_actual} "
            f"seed={judgment.sample.seed} decision={judgment.decision.outcome}"
        )
        click.echo(f"  json={outcome.artifacts.json_path} sha256={outcome.artifacts.json_sha256}")
        click.echo(f"  md={outcome.artifacts.markdown_path}")
    effective_ledger = ledger if ledger is not None else config.ledger_path
    click.echo(f"ledger={effective_ledger}")
    click.echo(f"完成：{len(outcomes)} 个批次判定已落账。")


@main.command("replay")
@click.option(
    "--ledger",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LEDGER,
    show_default=True,
    help="SQLite 台账路径。",
)
@click.option("--run-id", help="按判定 ID 重放。")
@click.option("--batch-id", help="按批次 ID 取最新判定重放。")
@click.pass_context
def replay_command(
    ctx: click.Context, ledger: Path, run_id: str | None, batch_id: str | None
) -> None:
    """从台账快照重放判定，并核对报告哈希、样本与断言。"""
    if run_id and batch_id:
        raise click.UsageError("--run-id 与 --batch-id 只能提供一个")
    try:
        result = replay_judgment(ledger, run_id=run_id, batch_id=batch_id)
    except GateSamplerError as error:
        _fail(ctx, error)
        return
    if result.consistent:
        click.echo(f"重放一致: {result.run_id}")
        click.echo(
            f"  batch={result.judgment.batch_spec.batch_id} "
            f"decision={result.judgment.decision.outcome} "
            f"sample={result.judgment.sample.sample_fingerprint}"
        )
        click.echo(f"  report_sha256={result.recomputed_report_sha256}")
    elif result.record_number_upgrade:
        click.echo(f"重放差异（记录号语义升级，非数据漂移）: {result.run_id}")
        for note in result.upgrade_notes:
            click.echo(f"  - {note}")
        for mismatch in result.mismatches:
            click.echo(f"  - {mismatch}")
    else:
        click.echo(f"重放不一致: {result.run_id}", err=True)
        for mismatch in result.mismatches:
            click.echo(f"  - {mismatch}", err=True)
        ctx.exit(1)


@main.command("query")
@click.option(
    "--ledger",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LEDGER,
    show_default=True,
    help="SQLite 台账路径。",
)
@click.option("--batch-id", help="按批次 ID 过滤。")
@click.option("--gate-id", help="按闸门 ID 过滤。")
@click.option("--decision", type=click.Choice(["pass", "block", "review"]), help="按决策过滤。")
@click.option("--limit", type=click.IntRange(1, 1000), default=50, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="输出 JSON。")
@click.pass_context
def query_command(
    ctx: click.Context,
    ledger: Path,
    batch_id: str | None,
    gate_id: str | None,
    decision: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """查询台账中的判定记录。"""
    try:
        connection = init_db(ledger)
        try:
            rows = list_judgments(
                connection,
                batch_id=batch_id,
                gate_id=gate_id,
                decision=decision,
                limit=limit,
            )
            totals = decision_counts(connection)
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as error:  # pragma: no cover - defensive
        _fail(ctx, GateSamplerError("GS-LGR-004", f"无法查询台账: {error}", str(ledger)))
        return
    if as_json:
        click.echo(json.dumps({"judgments": rows, "counts": totals}, ensure_ascii=False, indent=2, default=str))
        return
    click.echo(f"决策计数: pass={totals['pass']} block={totals['block']} review={totals['review']}")
    if not rows:
        click.echo("没有匹配记录。")
        return
    click.echo(f"{'run_id':<28} {'batch_id':<18} {'decision':<8} f/w/s  report_sha256")
    for row in rows:
        click.echo(
            f"{row['run_id']:<28} {row['batch_id']:<18} {row['decision']:<8} "
            f"{row['fail_count']}/{row['warn_count']}/{row['skip_count']}  "
            f"{row['report_sha256'][:16]}"
        )


@main.command("serve")
@click.option(
    "--ledger",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LEDGER,
    show_default=True,
    help="SQLite 台账路径。",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="仅允许本机回环地址。")
@click.option("--port", type=click.IntRange(1, 65535), default=5000, show_default=True)
@click.pass_context
def serve_command(ctx: click.Context, ledger: Path, host: str, port: int) -> None:
    """启动只读、本机访问的 Flask 判定浏览器。"""
    if host not in LOOPBACK_HOSTS:
        _fail(ctx, GateSamplerError("GS-SRV-001", "只允许监听本机回环地址", host))
        return
    try:
        from .server.app import create_app

        app = create_app(ledger, public_url=f"http://{host}:{port}/")
    except (OSError, GateSamplerError) as error:
        _fail(ctx, GateSamplerError("GS-SRV-002", f"无法启动服务: {error}", str(ledger)))
        return
    click.echo(f"GateSampler 只读服务: http://{host}:{port}/  (ledger={ledger})")
    click.echo("按 Ctrl+C 停止；非本机 Host 请求会被拒绝。")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
