"""Pure URL -> SourceEntry detection (no network).

Maps a pasted job-board URL to a ready-to-write `SourceEntry` purely by hostname + path:
greenhouse / lever / ashby ATS boards, plus GitHub aggregator lists (structured
`listings.json` -> github, README `*.md` -> markdown). Anything that needs a network call to
resolve (bare repo with no branch) or that isn't a known host (a company's own careers page,
a bare company name) raises `SourceDetectionError` — that's the `/add-source` agent skill's job.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from internshelper.config import SourceEntry
from internshelper.connectors.workday import parse_board_url

_GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
_LEVER_HOST = "jobs.lever.co"
_ASHBY_HOST = "jobs.ashbyhq.com"
_GITHUB_HOST = "github.com"
_RAW_HOST = "raw.githubusercontent.com"
_WORKDAY_HOST_SUFFIX = ".myworkdayjobs.com"
_AMAZON_HOSTS = {"amazon.jobs", "www.amazon.jobs"}

_SUPPORTED = (
    "boards.greenhouse.io/<token>, job-boards.greenhouse.io/<token>, "
    "jobs.lever.co/<token>, jobs.ashbyhq.com/<org>, "
    "<tenant>.wd<N>.myworkdayjobs.com/<site>, "
    "amazon.jobs/en/search?base_query=<query>, "
    "github.com/<u>/<r>/blob/<branch>/<file>, raw.githubusercontent.com/.../<file>"
)


class SourceDetectionError(ValueError):
    """Raised when a URL can't be resolved to a known job-board source."""


def _label(text: str) -> str:
    return text.replace("-", " ").replace("_", " ").title()


def _classify_github_file(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".json"):
        return "github"
    if low.endswith(".md") or low.endswith(".markdown"):
        return "markdown"
    raise SourceDetectionError(
        f"can't tell whether {filename!r} is a structured listings.json (github) or a "
        "README markdown list (markdown) — point me at a .json or .md file."
    )


def detect_source(url: str, *, label: str | None = None) -> SourceEntry:
    """Resolve a job-board URL to a SourceEntry, or raise SourceDetectionError."""
    if not url or not url.strip():
        raise SourceDetectionError("empty URL")
    url = url.strip()
    if "://" not in url:
        url = "https://" + url

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    if not host:
        raise SourceDetectionError(f"no host in URL {url!r}. Supported: {_SUPPORTED}")

    def _make(stype: str, token: str) -> SourceEntry:
        return SourceEntry(type=stype, token=token, label=label or _label(token))

    if host in _GREENHOUSE_HOSTS:
        if not segs:
            raise SourceDetectionError("greenhouse URL needs a board slug: boards.greenhouse.io/<token>")
        return _make("greenhouse", segs[0])

    if host == _LEVER_HOST:
        if not segs:
            raise SourceDetectionError("lever URL needs a company slug: jobs.lever.co/<token>")
        return _make("lever", segs[0])

    if host == _ASHBY_HOST:
        if not segs:
            raise SourceDetectionError("ashby URL needs an org slug: jobs.ashbyhq.com/<org>")
        return _make("ashby", segs[0])  # Ashby orgs are case-sensitive: preserve case

    if host in _AMAZON_HOSTS:
        # Amazon has no enumerable board — the "source" is a search query. Take it from
        # ?base_query=… (default "intern"); scope is US-only in the connector.
        query = (parse_qs(parts.query).get("base_query") or ["intern"])[0].strip() or "intern"
        return SourceEntry(type="amazon", token=query, label=label or "Amazon")

    if host.endswith(_WORKDAY_HOST_SUFFIX):
        # Canonicalize any board / locale-variant / single-job link to the bare board URL,
        # so duplicates collide on one source_key.
        try:
            board = parse_board_url(url)
        except ValueError as e:
            raise SourceDetectionError(str(e)) from e
        return SourceEntry(
            type="workday", token=board.base_url, label=label or _label(board.tenant)
        )

    if host == _RAW_HOST:
        if len(segs) < 4:
            raise SourceDetectionError(f"not a recognizable raw GitHub file URL: {url!r}")
        stype = _classify_github_file(segs[-1])
        # Rebuild from cleaned segments (drops trailing slash / query / fragment) so the stored
        # fetch URL is consistent with the github.com/blob branch and never 404s on a stray slash.
        raw = "https://raw.githubusercontent.com/" + "/".join(segs)
        return SourceEntry(type=stype, token=raw, label=label or _label(f"{segs[0]} {segs[1]}"))

    if host == _GITHUB_HOST:
        if "blob" not in segs:
            raise SourceDetectionError(
                f"point me at the file inside the repo (the raw README.md or listings.json, or a "
                f"github.com/.../blob/<branch>/<file> URL), not the bare repo {url!r}. "
                "The /add-source skill can resolve a bare repo for you."
            )
        bi = segs.index("blob")
        if bi < 2 or bi + 1 >= len(segs):
            raise SourceDetectionError(f"unrecognized github blob URL: {url!r}")
        user, repo, branch, rest = segs[0], segs[1], segs[bi + 1], segs[bi + 2:]
        if not rest:
            raise SourceDetectionError(f"github blob URL has no file path: {url!r}")
        stype = _classify_github_file(rest[-1])
        raw = "https://raw.githubusercontent.com/" + "/".join([user, repo, branch, *rest])
        return SourceEntry(type=stype, token=raw, label=label or _label(f"{user} {repo}"))

    raise SourceDetectionError(
        f"unsupported host {host!r}. URL-add supports: {_SUPPORTED}. For a company name, careers "
        "page, or single job link, use the /add-source skill."
    )
