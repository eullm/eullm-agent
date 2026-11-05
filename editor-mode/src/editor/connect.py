"""Connecting a site: what the guided path checks before the first article.

A person adds a domain, says what the site is made with (WordPress, a
GitHub repository that Vercel, Cloudflare or Netlify publish, or anything
else) and gives the credential. Here the credential is tried for real,
before anything is saved: the site answers, the credential is accepted, it
may publish and upload a cover, and what the site already knows is read
(WordPress: the categories, so the newsroom knows what it has to fill;
GitHub: where the articles live and how a post is written there).

Every failure is a :class:`ConnectError` with a code; the dashboard says
what to do in each case, in the person's language."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlparse

import httpx

from . import publishing, sitegit
from .publishing import PublishError, check_public_url

GITHUB_API = publishing.GITHUB_API
# A category every WordPress has: it says nothing about the site.
DEFAULT_CATEGORIES = {"uncategorized", "senza-categoria", "sin-categoria", "non-classe", "nicht-kategorisiert",
                      "uncategorised"}
ARTICLE_FOLDERS = ("content/blog", "src/content/blog", "src/content/articles", "src/content/posts",
                   "src/content/news", "content/posts", "content/articles", "content/news", "posts", "_posts",
                   "blog", "src/pages/blog", "src/data/blog")
HOSTS = ("vercel", "cloudflare", "netlify", "github_pages", "other")


class ConnectError(Exception):
    """A connection that did not work; `code` says why, `info` carries the details."""

    def __init__(self, code: str, **info):
        super().__init__(code)
        self.code, self.info = code, info


# --- WordPress ----------------------------------------------------------------------


def wordpress_base(address: str) -> str:
    """The site's address as the REST API is reached from it: https, no
    admin or API part, no closing slash (a site in a subfolder keeps it)."""
    raw = (address or "").strip()
    if not raw:
        raise ConnectError("address")
    if "://" not in raw:
        raw = "https://" + raw
    u = urlparse(raw)
    if not u.hostname or any(c.isspace() for c in raw):
        raise ConnectError("address")
    if u.scheme != "https":
        raise ConnectError("https")
    path = re.split(r"/(?:wp-admin|wp-json|wp-login\.php|wp-content)(?:/|$)", u.path)[0].rstrip("/")
    return f"https://{u.netloc}{path}"


def _get(http: httpx.Client, url: str, base: str, resolver, **kw) -> httpx.Response:
    try:
        r = http.get(url, **kw)
    except httpx.TimeoutException:
        raise ConnectError("timeout") from None
    except httpx.HTTPError:
        raise ConnectError("unreachable") from None
    try:
        publishing._ensure_no_redirect(r, base, resolver)
    except PublishError:
        raise ConnectError("redirect") from None
    return r


def wordpress(address: str, user: str, password: str, *, http: httpx.Client | None = None, resolver=None) -> dict:
    """Try a WordPress site with the application password of `user`.

    Returns {base, name, user, can_publish, can_upload, categories: [{id,
    name, slug, count}], sections: [names worth filling]}; raises ConnectError.
    """
    base = wordpress_base(address)
    user, password = (user or "").strip(), (password or "").strip()
    if not user or not password:
        raise ConnectError("credentials_missing")
    try:
        check_public_url(base, resolver)
    except PublishError:
        raise ConnectError("private") from None
    own = http is None
    http = http or publishing.pinned_client(resolver)
    try:
        auth = (user, password)
        # Without pretty permalinks (or with a server that ignores .htaccess) /wp-json/ is a 404:
        # every call then goes by ?rest_route= (wp.aitoday.it, 2026-11-05).
        root = _get(http, publishing.wp_url(base, ""), base, resolver)
        plain = publishing.not_rest(root)
        if plain:
            root = _get(http, publishing.wp_url(base, "", plain=True), base, resolver)

        def api(route: str, query: str = "", **kw) -> httpx.Response:
            return _get(http, publishing.wp_url(base, route, query, plain=plain), base, resolver, **kw)

        info = {}
        if root.status_code == 200:
            try:
                info = root.json()
            except ValueError:
                info = {}
        if root.status_code != 200 or not isinstance(info, dict) or "namespaces" not in info:
            raise ConnectError("not_wordpress", status=root.status_code)
        me = api("wp/v2/users/me", "context=edit", auth=auth)
        if me.status_code in (401, 403):
            raise ConnectError("auth", status=me.status_code)
        if me.status_code >= 400:
            raise ConnectError("rest", status=me.status_code)
        who = me.json()
        caps = who.get("capabilities") or {}
        can_publish = bool(caps.get("publish_posts") or caps.get("edit_published_posts"))
        can_upload = bool(caps.get("upload_files"))
        if caps and not caps.get("edit_posts"):
            raise ConnectError("role", roles=who.get("roles") or [])
        categories = []
        for page in (1, 2, 3):
            r = api("wp/v2/categories", f"per_page=100&page={page}&orderby=count&order=desc"
                    "&hide_empty=false&_fields=id,name,slug,count,parent", auth=auth)
            if r.status_code >= 400:
                break
            rows = r.json()
            if not isinstance(rows, list):
                break
            categories += [{"id": int(x["id"]), "name": html.unescape(str(x.get("name") or "")).strip(),
                            "slug": str(x.get("slug") or ""), "count": int(x.get("count") or 0)}
                           for x in rows if x.get("id") and str(x.get("name") or "").strip()]
            if len(rows) < 100:
                break
        sections = [c["name"] for c in categories if c["slug"] not in DEFAULT_CATEGORIES][:30]
        return {"base": base, "name": html.unescape(str(info.get("name") or "")).strip(),
                "description": html.unescape(str(info.get("description") or "")).strip(),
                "user": user, "display_name": who.get("name") or user,
                "can_publish": can_publish if caps else True, "can_upload": can_upload if caps else True,
                "categories": categories, "sections": sections}
    finally:
        if own:
            http.close()


# --- GitHub ------------------------------------------------------------------------------

REPO_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$")


def github_repo(text: str) -> str:
    """`owner/name` from what a person pastes: the name or the page's address."""
    raw = (text or "").strip()
    found = REPO_URL.match(raw)
    repo = f"{found.group(1)}/{found.group(2)}" if found else raw.strip("/")
    if not publishing.GITHUB_REPO.fullmatch(repo) or ".." in repo:
        raise ConnectError("repo_name")
    return repo


def _detect_host(names: set[str], workflows: dict[str, str]) -> str | None:
    if "vercel.json" in names or ".vercel" in names:
        return "vercel"
    if "netlify.toml" in names:
        return "netlify"
    if names & {"wrangler.toml", "wrangler.json", "wrangler.jsonc"}:
        return "cloudflare"
    for text in workflows.values():
        low = text.lower()
        if "deploy-pages" in low or "upload-pages-artifact" in low:
            return "github_pages"
        if "wrangler" in low or "cloudflare/pages-action" in low:
            return "cloudflare"
    return None


def _detect_generator(names: set[str], package: dict | None) -> str | None:
    deps = {}
    if package:
        deps = {**(package.get("devDependencies") or {}), **(package.get("dependencies") or {})}
    for key, label in (("astro", "Astro"), ("next", "Next.js"), ("nuxt", "Nuxt"), ("gatsby", "Gatsby"),
                       ("@11ty/eleventy", "Eleventy"), ("@sveltejs/kit", "SvelteKit"), ("vitepress", "VitePress"),
                       ("@docusaurus/core", "Docusaurus")):
        if key in deps:
            return label
    if names & {"hugo.toml", "hugo.yaml", "hugo.json", "config.toml"} and ("content" in names or "archetypes" in names):
        return "Hugo"
    if "_config.yml" in names:
        return "Jekyll"
    return None


def github(repo_text: str, token: str, *, branch: str = "", path: str = "", api: str = GITHUB_API,
           http: httpx.Client | None = None, resolver=None, sleep=None) -> dict:
    """Try a GitHub repository with the token: it exists, the token may
    write to it, and what is in it — where the articles go, how a post is
    written there, what publishes the site.

    Returns {repo, branch, private, generator, host, articles_path,
    articles_found, format: {...}, has_llms, has_sitemap}; raises ConnectError.
    """
    repo = github_repo(repo_text)
    token = (token or "").strip()
    if not token:
        raise ConnectError("credentials_missing")
    api = api.rstrip("/")
    own = http is None
    http = http or publishing.pinned_client(resolver)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "eullm-editor"}

    def call(method: str, p: str, **kw) -> httpx.Response:
        try:
            r = http.request(method, f"{api}{p}", headers={**headers, **kw.pop("headers", {})}, **kw)
        except httpx.TimeoutException:
            raise ConnectError("timeout") from None
        except httpx.HTTPError:
            raise ConnectError("unreachable") from None
        try:
            publishing._ensure_no_redirect(r, api, resolver)
        except PublishError:
            raise ConnectError("redirect") from None
        return r

    try:
        info = call("GET", f"/repos/{repo}")
        if info.status_code == 401:
            raise ConnectError("token_bad")
        if info.status_code in (403, 404):
            # GitHub answers 404 for a repository the token cannot see.
            raise ConnectError("repo_missing" if info.status_code == 404 else "token_forbidden", repo=repo)
        info.raise_for_status()
        body = info.json()
        perms = body.get("permissions")
        if isinstance(perms, dict) and not (perms.get("push") or perms.get("admin") or perms.get("maintain")):
            raise ConnectError("no_write", repo=repo)
        base = branch.strip() or body.get("default_branch") or "main"
        gh = sitegit.Repo(call, repo, sleep=sleep or (lambda _s: None))
        try:
            root = gh.listing("", base)
            if not root and not gh.file("README.md", base) and branch.strip():
                raise ConnectError("branch_missing", branch=base)
            names = {e.get("name", "") for e in root}
            package = None
            raw = gh.file("package.json", base) if "package.json" in names else None
            if raw:
                try:
                    package = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    package = None
            workflows = {}
            if ".github" in names:
                for e in gh.listing(".github/workflows", base)[:6]:
                    if e.get("type") == "file":
                        text = gh.file(e["path"], base)
                        workflows[e["path"]] = (text or b"").decode("utf-8", "replace")[:6000]
            found = path.strip().strip("/")
            candidates = [found] if found else list(ARTICLE_FOLDERS)
            articles, posts = None, []
            for folder in candidates:
                got = gh.posts(folder, base, limit=sitegit.SAMPLES)
                if got:
                    articles, posts = folder, got
                    break
            fmt = sitegit.learn(posts) if posts else None
            texts = {p: gh.file(p, base) for p in ("public/llms.txt", "static/llms.txt", "llms.txt",
                                                    "public/sitemap.xml", "static/sitemap.xml")}
        except sitegit.GitHubError as e:
            raise ConnectError("github", detail=str(e)) from None
        return {
            "repo": repo, "branch": base, "private": bool(body.get("private")),
            "generator": _detect_generator(names, package), "host": _detect_host(names, workflows),
            "articles_path": articles, "articles_found": len(posts),
            "format": ({"ext": fmt.ext, "folder": fmt.folder, "keys": fmt.keys[:14], "learned": fmt.learned}
                       if fmt else None),
            "has_llms": any(v and p.endswith("llms.txt") for p, v in texts.items()),
            "has_sitemap": any(v and p.endswith("sitemap.xml") for p, v in texts.items()),
        }
    finally:
        if own:
            http.close()


# --- saving the connection ---------------------------------------------------------------


def known_categories(s, site_id: int) -> dict[str, int]:
    """The categories the site's CMS already has, as {name: articles}: what
    the analysis takes as the sections to fill. Empty without a WordPress."""
    from sqlalchemy import select

    from . import models as m

    out: dict[str, int] = {}
    for config in s.execute(select(m.publish_targets.c.config).where(
            m.publish_targets.c.site_id == site_id, m.publish_targets.c.kind == "wordpress")).scalars():
        for c in (config or {}).get("categories") or []:
            if c.get("slug") not in DEFAULT_CATEGORIES and c.get("name"):
                out.setdefault(c["name"], int(c.get("count") or 0))
    return out


def category_id(config: dict | None, section: str | None) -> int | None:
    """The WordPress category of an article's section, by name or slug, None
    when the site has no such category (the post stays in the default one)."""
    if not section:
        return None
    want = section.strip().lower()
    for c in (config or {}).get("categories") or []:
        if want in (str(c.get("name", "")).lower(), str(c.get("slug", "")).lower()):
            return int(c["id"])
    return None


def secret_name(prefix: str, domain: str) -> str:
    """The name the credential is stored under: WP_ESEMPIO_IT."""
    body = re.sub(r"[^A-Z0-9]+", "_", domain.upper()).strip("_")
    return f"{prefix}_{body}"[:40].rstrip("_")


def save_target(db, tenant_id: str, site_id: int, kind: str, name: str, config: dict, secret: str,
                prefix: str, domain: str) -> tuple[int, bool]:
    """The verified connection as the site's destination, with its
    credential stored encrypted. A destination of the same kind and address
    is updated instead of added twice. Returns (id, was_new)."""
    from sqlalchemy import select, update

    from . import models as m
    from . import vault

    key = "repo" if kind == "github" else "url"
    with db.tenant(tenant_id) as s:
        rows = s.execute(select(m.publish_targets).where(
            m.publish_targets.c.site_id == site_id, m.publish_targets.c.kind == kind)).all()
    same = next((r for r in rows if str((r.config or {}).get(key, "")).rstrip("/").lower()
                 == str(config.get(key, "")).rstrip("/").lower()), None)
    if same is not None:
        env_name = same.secret_env or secret_name(prefix, domain)
        vault.put(db, tenant_id, env_name, secret)
        merged = {**(same.config or {}), **config}
        with db.tenant(tenant_id) as s:
            s.execute(update(m.publish_targets).where(m.publish_targets.c.id == same.id)
                      .values(config=merged, secret_env=env_name, enabled=True))
        return same.id, False
    env_name = secret_name(prefix, domain)
    vault.put(db, tenant_id, env_name, secret)
    return publishing.add_target(db, tenant_id, site_id, kind, name[:100], config, env_name), True


# The name resolver of the probes: None in production (the real one), a stand-in in tests.
resolver = None
