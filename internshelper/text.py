"""HTML -> plain text, using only the stdlib html.parser (no extra dependency).

Two flavours: `strip_html` (single line, for keyword/token matching) and
`html_to_text` (paragraph-preserving, for showing descriptions to a human).
Both survive double-encoded HTML — Greenhouse's `content` field arrives
HTML-escaped, so one decode pass just reveals the tags instead of removing them.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags that end a line of visible text. Closing one (or hitting <br>) starts a
# new line; a <li> additionally gets a bullet so lists survive flattening.
_BLOCK_TAGS = frozenset(
    ("p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br")
)
_NON_VISIBLE_TAGS = frozenset(("script", "style", "template"))

# A real markup tag (not a stray "a < b") left in the output after a parse pass
# means the input was escaped HTML — decode again.
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")

_MAX_DECODE_PASSES = 3


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True (default) means entities arrive already decoded.
        super().__init__()
        self._parts: list[str] = []
        self._suppressed_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _NON_VISIBLE_TAGS:
            self._suppressed_depth += 1
            return
        if self._suppressed_depth:
            return
        if tag == "br":
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n• ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _NON_VISIBLE_TAGS:
            self._suppressed_depth = max(0, self._suppressed_depth - 1)
            return
        if self._suppressed_depth:
            return
        # Paragraphs and headings read better with a blank line after them.
        # <li>/<br> already break on the start tag — a close-newline too would
        # put a blank line between every bullet.
        if tag == "p" or (len(tag) == 2 and tag[0] == "h" and tag[1].isdigit()):
            self._parts.append("\n\n")
        elif tag in _BLOCK_TAGS and tag not in ("li", "br"):
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _visible_text(s: str) -> str:
    """One decode+strip pass, repeated while the output still contains markup."""
    out = s
    for _ in range(_MAX_DECODE_PASSES):
        parser = _Stripper()
        parser.feed(out)
        parser.close()
        out = parser.text()
        if not _TAG_RE.search(out):
            break
    return out


def html_to_text(s: str | None) -> str:
    """Visible text of an HTML (or plain) string, paragraphs/lists kept as lines."""
    if not s:
        return ""
    lines = [" ".join(line.split()) for line in _visible_text(s).split("\n")]
    # Collapse runs of blank lines to a single paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


def strip_html(s: str | None) -> str:
    """Return the visible text of an HTML (or plain) string, whitespace-collapsed."""
    return " ".join(html_to_text(s).split())
