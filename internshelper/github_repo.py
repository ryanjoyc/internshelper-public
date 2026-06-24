"""Networked fallback that resolves a *bare* GitHub repo URL to its job-list files.

`sourceurl.detect_source` is pure (host+path only) and deliberately refuses a bare repo URL —
it can't know the default branch or which files are lists without a network call. This module
is that network call, mirroring how `sniffer.sniff_careers_page` is the add-time fallback for a
careers page: paste `github.com/<owner>/<repo>` (or `.../tree/<branch>`) and it lists the repo's
root job-list files as `SourceEntry` candidates, one per `*.md`/`*.json`, for the user to pick.

Two GitHub API GETs: one for the default branch, one for the root contents. If `GITHUB_TOKEN`
is set it's sent as a bearer (lifts the 60/hr unauth rate limit) — optional, env-only.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

from internshelper.config import SourceEntry
from internshelper.connectors.base import HEADERS, TIMEOUT

_GITHUB_HOST = "github.com"
_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"

# Root files that are repo scaffolding, never a job list — excluded by stem (compared upper).
_NON_LIST_STEMS = {"LICENSE", "CONTRIBUTING", "CODE_OF_CONDUCT", "SECURITY", "CHANGELOG"}
_LIST_EXTS = {".md": "markdown", ".markdown": "markdown", ".json": "github"}


class GitHubRepoError(Exception):
    """The URL isn't a github.com repo, or the GitHub API couldn't be reached."""


def _label(text: str) -> str:
    return text.replace("-", " ").replace("_", " ").title()


def _parse_repo(url: str) -> tuple[str, str, str | None]:
    """(owner, repo, branch-or-None) from a github.com repo / `/tree/<branch>` URL."""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != _GITHUB_HOST:
        raise GitHubRepoError(f"not a github.com repo URL: {url!r}")
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        raise GitHubRepoError(f"not a github.com repo URL (need owner/repo): {url!r}")
    owner, repo = segs[0], segs[1]
    branch = None
    if "tree" in segs[2:]:
        ti = segs.index("tree")
        if ti + 1 < len(segs):
            branch = segs[ti + 1]
    return owner, repo, branch


def _gh_get(url: str):
    headers = dict(HEADERS, Accept="application/vnd.github+json")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(url, timeout=TIMEOUT, headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise GitHubRepoError(f"GitHub API request failed for {url!r}: "
                              f"{type(e).__name__}: {e}") from e


def _is_list_file(name: str) -> str | None:
    """The source type for a root filename if it's a job list, else None."""
    stem, _, ext = name.rpartition(".")
    if not ext:
        return None
    stype = _LIST_EXTS.get("." + ext.lower())
    if stype is None:
        return None
    upper = stem.upper()
    if upper in _NON_LIST_STEMS or "TEMPLATE" in upper:
        return None
    return stype


def resolve_repo(url: str, *, label: str | None = None) -> list[SourceEntry]:
    """Bare github.com repo (or `/tree/<branch>`) URL -> one SourceEntry per job-list file in the
    repo root. Markdown files -> type 'markdown'; *.json -> type 'github'. Raises GitHubRepoError
    on a network failure or a non-repo URL, so callers distinguish 'couldn't load' from 'no lists'.
    """
    owner, repo, branch = _parse_repo(url)
    if branch is None:
        info = _gh_get(f"{_API}/repos/{owner}/{repo}")
        branch = info.get("default_branch") or "main"

    contents = _gh_get(f"{_API}/repos/{owner}/{repo}/contents")
    if not isinstance(contents, list):
        raise GitHubRepoError(f"unexpected contents response for {owner}/{repo}")

    out: list[SourceEntry] = []
    for item in contents:
        if item.get("type") != "file":
            continue
        name = item.get("name", "")
        stype = _is_list_file(name)
        if stype is None:
            continue
        token = f"{_RAW}/{owner}/{repo}/{branch}/{name}"
        out.append(SourceEntry(
            type=stype, token=token,
            label=label or _label(f"{owner} {repo} {name.rpartition('.')[0]}"),
        ))
    return out
