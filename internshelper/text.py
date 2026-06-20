"""HTML -> plain text, using only the stdlib html.parser (no extra dependency).

Used to clean posting descriptions before keyword matching so tags and attribute
text (e.g. URLs) cannot produce false keyword hits.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        # convert_charrefs=True (default) means entities arrive already decoded.
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(s: str | None) -> str:
    """Return the visible text of an HTML (or plain) string, whitespace-collapsed."""
    if not s:
        return ""
    parser = _Stripper()
    parser.feed(s)
    parser.close()
    return " ".join(parser.text().split())
