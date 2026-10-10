"""Markdown to HTML for the drafts Editor Mode writes (headings, paragraphs,
emphasis, links, footnotes). The input is our own constrained output, and
every piece of text is escaped."""

from __future__ import annotations

import html
import re

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_FOOTREF = re.compile(r"\[\^(\d+)\]")
_EM = re.compile(r"(?<![\w*])_([^_]+)_(?![\w*])")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" rel="noopener">{m.group(1)}</a>', out)
    out = _FOOTREF.sub(lambda m: f'<sup><a href="#fn{m.group(1)}">{m.group(1)}</a></sup>', out)
    return _EM.sub(r"<em>\1</em>", out)


def to_html(md: str) -> str:
    parts, notes = [], []
    for block in re.split(r"\n\s*\n", md.strip()):
        block = block.strip()
        if not block:
            continue
        foot = re.match(r"\[\^(\d+)\]:\s*(.*)", block, re.S)
        if foot:
            notes.append(f'<li id="fn{foot.group(1)}">{_inline(foot.group(2))}</li>')
        elif block.startswith("## "):
            parts.append(f"<h2>{_inline(block[3:])}</h2>")
        elif block.startswith("# "):
            parts.append(f"<h1>{_inline(block[2:])}</h1>")
        else:
            parts.append(f"<p>{_inline(' '.join(block.splitlines()))}</p>")
    if notes:
        parts.append("<ol class=\"sources\">" + "".join(notes) + "</ol>")
    return "\n".join(parts)
