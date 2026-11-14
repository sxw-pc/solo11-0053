from __future__ import annotations

import html
import re

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


def test_index_filters_by_multiple_batches_and_time_range(demos):
    config = load_run_config(demos["config"])
    run_config(config)
    client = create_app(config.ledger_path).test_client()

    page = client.get("/?batch_id=batch_a&batch_id=batch_b")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "当前过滤条件匹配 <strong>2</strong> 条判定" in body
    assert "batch_bad" not in body
    # Count cards are filtered: one pass (batch_a), one review (batch_b), no block.
    assert '<strong class="pass">1</strong>' in body
    assert '<strong class="block">0</strong>' in body
    assert '<strong class="review">1</strong>' in body

    comma_form = client.get("/?batch_id=batch_a,batch_b")
    assert "当前过滤条件匹配 <strong>2</strong> 条判定" in comma_form.get_data(as_text=True)

    ranged = client.get("/?created_from=2000-01-01&created_to=2999-12-31")
    assert "当前过滤条件匹配 <strong>3</strong> 条判定" in ranged.get_data(as_text=True)

    empty = client.get("/?created_from=2999-01-01")
    assert "当前过滤条件匹配 <strong>0</strong> 条判定" in empty.get_data(as_text=True)

    bad = client.get("/?created_from=yesterday")
    assert bad.status_code == 400
    assert "无法解析" in bad.get_data(as_text=True)


def test_index_cursor_pagination_is_gap_free(demos, monkeypatch):
    config = load_run_config(demos["config"])
    run_config(config)
    monkeypatch.setattr("gatesampler.server.app.PAGE_SIZE", 2)
    client = create_app(config.ledger_path).test_client()

    seen: list[str] = []
    url = "/"
    for _ in range(5):
        response = client.get(url)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "当前过滤条件匹配 <strong>3</strong> 条判定" in body
        seen.extend(re.findall(r'href="/batch/(run-[^"]+)"', body))
        if "下一页 →" not in body:
            break
        href = re.search(r'class="pager"><a href="([^"]+)"', body).group(1)
        url = html.unescape(href)
        assert url.startswith("/")
    assert len(seen) == len(set(seen)) == 3
    connection = init_db(config.ledger_path)
    try:
        assert set(seen) == {row["run_id"] for row in list_judgments(connection)}
    finally:
        connection.close()
