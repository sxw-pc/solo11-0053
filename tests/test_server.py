from __future__ import annotations

from gatesampler.config import load_run_config
from gatesampler.ledger import init_db, list_judgments
from gatesampler.runner import run_config
from gatesampler.server.app import create_app


def test_local_read_only_pages_and_host_rejection(demos):
    config = load_run_config(demos["config"])
    run_config(config)
    app = create_app(config.ledger_path)
    client = app.test_client()

    index = client.get("/")
    assert index.status_code == 200
    assert "闸门抽样台" in index.get_data(as_text=True)
    assert "batch_a" in index.get_data(as_text=True)
    assert b"https://" not in index.data
    assert b"cdn." not in index.data
    assert "no-store" in index.headers["Cache-Control"]
    assert "default-src 'none'" in index.headers["Content-Security-Policy"]

    connection = init_db(config.ledger_path)
    try:
        run_id = next(row["run_id"] for row in list_judgments(connection) if row["batch_id"] == "batch_bad")
    finally:
        connection.close()
    detail = client.get(f"/batch/{run_id}")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert "决策理由链" in body
    assert "断言明细与证据" in body
    assert "GS-GATE-102" in body

    refused = client.get("/", base_url="http://not-local.example")
    assert refused.status_code == 403
    assert "仅限本机" in refused.get_data(as_text=True)
    assert client.post("/").status_code == 405
    assert client.get("/batch/run-missing").status_code == 404
