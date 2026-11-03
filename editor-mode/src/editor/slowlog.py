"""A page slower than it should be says so in the log: how long it took,
how much of it was the database and the statements that took longest, so a
slow page is found from `docker compose logs`, not guessed."""

import contextvars
import logging
import time

from sqlalchemy import event

log = logging.getLogger("editor.slow")
SLOW_SECONDS = 1.5
_current: contextvars.ContextVar[list | None] = contextvars.ContextVar("editor_statements", default=None)
_hooked: set[int] = set()


def watch(engine) -> None:
    """Record the statements run during a request (once per engine)."""
    if id(engine) in _hooked:
        return
    _hooked.add(id(engine))

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        context._started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        seen = _current.get()
        if seen is not None:
            seen.append((time.perf_counter() - context._started, " ".join(statement.split())[:160]))


def start() -> contextvars.Token:
    return _current.set([])


def finish(token: contextvars.Token, started: float, what: str) -> None:
    seen = _current.get() or []
    _current.reset(token)
    total = time.perf_counter() - started
    if total < SLOW_SECONDS:
        return
    spent = sum(d for d, _ in seen)
    worst = "; ".join(f"{d * 1000:.0f} ms {q}" for d, q in sorted(seen, reverse=True)[:3])
    log.warning("slow page %s: %.1f s, %.1f s in %d queries; slowest: %s", what, total, spent, len(seen), worst)
