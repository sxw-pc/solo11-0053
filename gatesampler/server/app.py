"""Local-only, read-only Flask viewer over the judgment ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, g, redirect, render_template, request, url_for

from ..ledger import get_judgment, init_db, list_judgments

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
PAGE_SIZE = 200
LOCAL_REFUSAL = "GateSampler 是仅限本机的只读审计界面。\n请求 Host 不是回环地址，已拒绝访问。\n"


def _hostname() -> str | None:
    raw_host = request.headers.get("Host", "")
    if not raw_host:
        return None
    try:
        return urlsplit(f"//{raw_host}").hostname
    except ValueError:
        return None


def create_app(
    ledger_path: str | Path, *, public_url: str = "http://127.0.0.1:5000/"
) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["LEDGER_PATH"] = Path(ledger_path)
    init_db(app.config["LEDGER_PATH"]).close()
    refusal = f"{LOCAL_REFUSAL}请通过 {public_url} 访问。\n"

    @app.before_request
    def enforce_local_read_only():
        if _hostname() not in ALLOWED_HOSTS:
            return Response(refusal, status=403, mimetype="text/plain")
        if request.method not in {"GET", "HEAD"}:
            return Response("只读服务，不接受写请求。\n", status=405, mimetype="text/plain")
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    def db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = init_db(app.config["LEDGER_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_error=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def index():
        batch_values: list[str] = []
        for raw in request.args.getlist("batch_id"):
            batch_values.extend(part.strip() for part in raw.split(","))
        batch_ids = tuple(dict.fromkeys(item for item in batch_values if item))
        gate_id = request.args.get("gate_id") or None
        decision = request.args.get("decision") or None
        if decision not in {None, "pass", "block", "review"}:
            decision = None
        created_from = request.args.get("created_from") or None
        created_to = request.args.get("created_to") or None
        cursor = request.args.get("cursor") or None
        filters = {
            "batch_id": ", ".join(batch_ids),
            "gate_id": gate_id or "",
            "decision": decision or "",
            "created_from": created_from or "",
            "created_to": created_to or "",
        }
        try:
            page = list_judgments(
                db(),
                batch_ids=batch_ids,
                gate_id=gate_id,
                decision=decision,
                created_from=created_from,
                created_to=created_to,
                limit=PAGE_SIZE,
                cursor=cursor,
            )
        except ValueError as error:
            return Response(f"{error}\n", status=400, mimetype="text/plain")
        next_url = None
        if page.next_cursor is not None:
            next_args = {
                "batch_id": filters["batch_id"],
                "gate_id": gate_id,
                "decision": decision,
                "created_from": created_from,
                "created_to": created_to,
                "cursor": page.next_cursor,
            }
            next_url = url_for(
                "index", **{key: value for key, value in next_args.items() if value}
            )
        return render_template(
            "index.html",
            records=page.records,
            counts=page.counts,
            total=page.total,
            next_url=next_url,
            filters=filters,
            ledger=app.config["LEDGER_PATH"],
        )

    @app.get("/batch/<run_id>")
    def batch_detail(run_id: str):
        record = get_judgment(db(), run_id)
        if record is None:
            return Response("台账中没有该判定。\n", status=404, mimetype="text/plain")
        return render_template(
            "batch.html",
            record=record,
            ledger=app.config["LEDGER_PATH"],
        )

    @app.get("/latest")
    def latest():
        rows = list_judgments(db(), limit=1)
        if not rows:
            return redirect(url_for("index"))
        return redirect(url_for("batch_detail", run_id=rows[0]["run_id"]))

    return app
