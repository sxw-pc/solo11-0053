from __future__ import annotations

import hashlib

from conftest import ROOT

from gatesampler.config import load_run_config
from gatesampler.report import render_json, render_markdown, write_reports
from gatesampler.runner import execute_batch

# Frozen golden hashes: extending the drift metric family must not change the
# bytes produced by legacy max_share_delta contracts.
GOLDEN_REPORT_SHA256 = {
    "demo": {
        "batch_a": "bc71e1d87c8e7448fdad66278f7343da4f3d0950942b5b57b4da1840697707c4",
        "batch_b": "1a6b9e56fdc58ee36f75e164b13cb3076381bd5acee1322803207660dfde5e03",
        "batch_bad": "77b5eddac3ca889befb61488851274449b1ba52c1fea1d9e9a64a35d00644a67",
    },
    "strict": {
        "batch_a": "9843702d122b3fe60bb581b0b17cc514fc4e02b7f99161d44e66fff514f778ac",
        "batch_b": "f83af39e80d39473fc8b50b6a5507d417b5561b42fbb4eb5555075ef7c3cd392",
    },
}


def test_json_report_bytes_are_stable_across_repeated_execution(demos):
    config = load_run_config(demos["config"])
    first = execute_batch(config.contract, config.batches[0])
    second = execute_batch(config.contract, config.batches[0])
    first_bytes = render_json(first.report)
    second_bytes = render_json(second.report)
    assert hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()
    assert b'"report_version": "1.0"' in first_bytes
    assert b"timestamp" not in first_bytes.lower()


def test_written_report_hashes_are_stable_and_markdown_matches(demos, tmp_path):
    config = load_run_config(demos["config"])
    judgment = execute_batch(config.contract, config.batches[0])
    first = write_reports(judgment.report, tmp_path)
    second = write_reports(judgment.report, tmp_path)
    assert first.json_sha256 == second.json_sha256
    assert first.markdown_sha256 == second.markdown_sha256
    assert first.json_path.read_bytes() == second.json_path.read_bytes()
    markdown = render_markdown(judgment.report)
    assert markdown == first.markdown_path.read_text(encoding="utf-8")
    assert "## 决策理由链" in markdown
    assert judgment.report["sampling"]["seed"] == 20240117


def test_legacy_max_share_delta_report_bytes_remain_unchanged():
    for config_name, golden in GOLDEN_REPORT_SHA256.items():
        config = load_run_config(ROOT / "configs" / f"{config_name}.yaml")
        strict = config_name == "strict"
        for batch_spec in config.batches:
            judgment = execute_batch(config.contract, batch_spec, strict=strict)
            rendered = render_json(judgment.report)
            assert hashlib.sha256(rendered).hexdigest() == golden[batch_spec.batch_id]
            drift = next(
                item for item in judgment.report["assertions"] if item["type"] == "drift"
            )
            assert drift["evidence"]["metric"] == "max_absolute_share_delta"
            on_disk = ROOT / config.out_dir / "reports" / f"{batch_spec.batch_id}.json"
            if on_disk.exists():
                assert on_disk.read_bytes() == rendered


def test_legacy_max_share_delta_markdown_bytes_remain_unchanged():
    # Markdown only renders non-pass assertions; guard the full demo set.
    config = load_run_config(ROOT / "configs" / "demo.yaml")
    for batch_spec in config.batches:
        judgment = execute_batch(config.contract, batch_spec)
        golden = ROOT / "out" / "reports" / f"{batch_spec.batch_id}.md"
        if golden.exists():
            assert golden.read_text(encoding="utf-8") == render_markdown(judgment.report)
