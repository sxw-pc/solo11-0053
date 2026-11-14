from __future__ import annotations

import hashlib

from gatesampler.config import load_run_config
from gatesampler.report import render_json, render_markdown, write_reports
from gatesampler.runner import execute_batch


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
