"""Procrastinate wiring: one periodic task, once an hour. The job queue lives
in PostgreSQL (no Redis); run the worker with `editor worker`."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import procrastinate

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(
        conninfo=os.environ.get("EDITOR_QUEUE_DATABASE_URL", os.environ.get("EDITOR_ADMIN_DATABASE_URL", ""))
    ),
)


@app.periodic(cron="0 * * * *")
@app.task(queueing_lock="editor-tick", pass_context=False)
def tick(timestamp: int) -> dict:
    from .runtime import runner

    report = runner().tick(datetime.fromtimestamp(timestamp, UTC))
    return {"tenants": report.tenants, "errors": report.errors}
