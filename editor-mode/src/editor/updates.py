"""Updates of the server installation, from the console.

The backoffice never touches Docker: it has no socket and no root. It reads
the latest release from GitHub (every hour from the worker, when the page
of updates is opened and on "Controlla adesso"), and when an operator asks for an update it leaves a
request in a folder shared with the host (EDITOR_UPDATES_DIR). On the host,
deploy/server/update.sh, run every minute by root's cron, takes the request,
makes a backup, pulls the images of that version, restarts and writes how it
went in the same folder; the console shows it.

The latest release is asked first to app.redazia.com (EDITOR_UPDATES_URL),
which answers with GitHub's latest release and counts the installation:
only a random identifier derived from this installation's secret key and the
installed version are sent, nothing about newsrooms or users.
EDITOR_TELEMETRY=off asks GitHub directly and sends nothing. If our address
does not answer, GitHub is asked.

Files in the folder: available.json (the latest release, written here),
request.json (written here, removed by the script), status.json (with the
step and a percentage, read by the console's bar every 2 seconds) and
update.log (written by the script). The script starts as soon as the request
is written (a systemd path unit), or within a minute from cron.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

RELEASES = "https://api.github.com/repos/eullm/eullm-agent/releases/latest"
LATEST = "https://app.redazia.com/api/public/latest"
VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
CHECK_EVERY = timedelta(minutes=50)  # the worker's hourly round always finds it due
ON_PAGE = timedelta(minutes=10)  # opening the page of updates checks again past this
STALE_REQUEST = timedelta(minutes=5)  # a request nobody took: the script is not installed
INSTALL_ID = re.compile(r"^[0-9a-f]{24}$")
_memory: dict = {}  # what check() knows on an installation without the shared folder


def install_id(seed: str) -> str:
    """A stable identifier of this installation that says nothing about it
    (seed: the secret key, or the database address)."""
    return hashlib.sha256(f"redazia-install:{seed}".encode()).hexdigest()[:24]


def this_install() -> str:
    env = os.environ
    return install_id(env.get("EDITOR_SECRET_KEY") or env.get("EDITOR_DATABASE_URL") or "")


def telemetry() -> bool:
    return os.environ.get("EDITOR_TELEMETRY", "on").strip().lower() not in ("0", "off", "false", "no")


def _release(client, installed: str, iid: str, fresh: bool = False) -> dict:
    """The latest release: from our address (which counts the installation)
    when allowed, otherwise or if it fails from GitHub."""
    if iid and telemetry():
        try:
            r = client.get(os.environ.get("EDITOR_UPDATES_URL", "").strip() or LATEST,
                           params={"v": installed, "id": iid, **({"fresh": 1} if fresh else {})})
            r.raise_for_status()
            rel = r.json()
            if VERSION.match(str(rel.get("version") or "")):
                return rel
            raise ValueError(f"unexpected answer {rel!r:.80}")
        except (httpx.HTTPError, ValueError) as e:
            log.info("latest release not read from %s, asking GitHub: %s", LATEST, e)
    r = client.get(RELEASES, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    rel = r.json()
    version = str(rel.get("tag_name") or "").removeprefix("v")
    if not VERSION.match(version):
        raise ValueError(f"unexpected tag {rel.get('tag_name')!r}")
    return {"version": version, "url": rel.get("html_url") or "", "published_at": rel.get("published_at")}


def folder(path: str) -> Path | None:
    """The shared folder, when this installation has one we can write in
    (Docker creates a missing one owned by root: update.sh install fixes it)."""
    if not path:
        return None
    p = Path(path)
    return p if p.is_dir() and os.access(p, os.W_OK) else None


def newer(a: str, b: str) -> bool:
    """Is version a newer than b?"""
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except ValueError:
        return False


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    part = path.with_suffix(".part")
    part.write_text(json.dumps(data), encoding="utf-8")
    os.replace(part, path)


def check(where: Path | None, http=None, now: datetime | None = None, force: bool = False,
          installed: str = "", iid: str = "", every: timedelta = CHECK_EVERY) -> dict | None:
    """The latest release, read at most once in `every` (or now, with force).
    A failed read keeps what was known and says why. Without the shared
    folder it is kept in memory: the installation is still counted."""
    now = now or datetime.now(UTC)
    path = where / "available.json" if where is not None else None
    known = (_read(path) if path else _memory.get("known")) or {}
    checked = known.get("checked_at")
    if not force and checked and now - datetime.fromisoformat(checked) < every:
        return known
    try:
        client = http or httpx.Client(timeout=15, follow_redirects=True)
        rel = _release(client, installed, iid, fresh=force)
        known = {"version": rel["version"], "url": rel.get("url") or "", "published_at": rel.get("published_at"),
                 "checked_at": now.isoformat(), "error": None}
    except (httpx.HTTPError, ValueError) as e:
        log.warning("latest release not read: %s", e)
        known = {**known, "checked_at": now.isoformat(), "error": str(e)[:200]}
    if path:
        _write(path, known)
    else:
        _memory["known"] = known
    return known


def refresh(where: Path, installed: str, iid: str, http=None) -> dict | None:
    """Opening the page of updates checks again when the last check is
    older than ten minutes, with a short wait: a release out an hour ago is
    shown without pressing anything."""
    if http is not None:
        return check(where, http, installed=installed, iid=iid, every=ON_PAGE)
    with httpx.Client(timeout=5, follow_redirects=True) as quick:
        return check(where, quick, installed=installed, iid=iid, every=ON_PAGE)


def request(where: Path, version: str, by: str, now: datetime | None = None) -> None:
    """Ask the host to move to this version (update.sh does it)."""
    if not VERSION.match(version):
        raise ValueError("version")
    _write(where / "request.json", {"version": version, "by": by, "at": (now or datetime.now(UTC)).isoformat()})


def percent(st: dict) -> int:
    """How far the update is, 0-100: the script's percentage (an older
    script writes none: the step gives one)."""
    status = st.get("status") or {}
    if st.get("pending") or status.get("state") != "running":
        return 100 if status.get("state") == "done" and not st.get("pending") else 0
    try:
        return max(0, min(100, int(status.get("percent"))))
    except (TypeError, ValueError):
        return {"backup": 2, "pull": 10, "restart": 80, "check": 90}.get(status.get("step"), 0)


HISTORY = 30  # updates kept in history.json


def history(where: Path, status: dict | None = None) -> list[dict]:
    """The updates done or failed, newest first. The script keeps only the
    last one in status.json: each new ending seen is added here."""
    past = (_read(where / "history.json") or {}).get("updates") or []
    st = status or {}
    if st.get("state") in ("done", "failed") and st.get("at"):
        entry = {k: st.get(k) for k in ("state", "from", "version", "step", "at")}
        if not any(x.get("at") == entry["at"] and x.get("version") == entry["version"] for x in past):
            past = ([entry] + past)[:HISTORY]
            try:
                _write(where / "history.json", {"updates": past})
            except OSError:
                pass  # shown anyway, kept next time
    return past


def state(where: Path | None, installed: str, now: datetime | None = None) -> dict:
    """What the console shows: installed and available versions, whether an
    update is asked or running, how the last one went."""
    out = {"enabled": where is not None, "installed": installed, "available": None, "newer": False,
           "pending": None, "stale": False, "status": None, "log": "", "busy": False, "mismatch": None}
    if where is None:
        return out
    now = now or datetime.now(UTC)
    out["available"] = _read(where / "available.json")
    v = (out["available"] or {}).get("version")
    out["newer"] = bool(v) and newer(v, installed)
    out["pending"] = _read(where / "request.json")
    if out["pending"] and out["pending"].get("at"):
        out["stale"] = now - datetime.fromisoformat(out["pending"]["at"]) > STALE_REQUEST
    out["status"] = _read(where / "status.json")
    st = out["status"] or {}
    out["busy"] = bool(out["pending"]) or st.get("state") == "running"
    # Done, yet the server still says an older version: the release's images
    # carry another version. Asking again would only do the same.
    if st.get("state") == "done" and VERSION.match(str(st.get("version") or "")) and newer(st["version"], installed):
        out["mismatch"] = st["version"]
        if v == st["version"]:
            out["newer"] = False
    out["percent"] = percent(out)
    out["history"] = history(where, st)
    try:
        out["log"] = "\n".join((where / "update.log").read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
    except OSError:
        pass
    return out
