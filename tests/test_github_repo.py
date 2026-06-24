"""Unit tests for the bare-repo resolver.

The two GitHub API GETs (default-branch + root contents) are monkeypatched — no real
requests — mirroring how `tests/test_sniffer.py` isolates the network.
"""

import httpx
import pytest

from internshelper import github_repo


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# A repo whose root has two real lists, a structured json list, and assorted scaffolding.
_CONTENTS = [
    {"type": "file", "name": "README.md"},
    {"type": "file", "name": "NEW_GRAD_USA.md"},
    {"type": "file", "name": "listings.json"},
    {"type": "file", "name": "LICENSE"},
    {"type": "file", "name": "CONTRIBUTING.md"},
    {"type": "file", "name": "ISSUE_TEMPLATE.md"},
    {"type": "dir", "name": "scripts"},
]


def _fake_get(monkeypatch, contents=_CONTENTS, branch="main"):
    def _get(url, *a, **k):
        if url.endswith("/contents"):
            return _Resp(contents)
        return _Resp({"default_branch": branch})
    monkeypatch.setattr(github_repo.httpx, "get", _get)


def _by_token(entries):
    return {e.token: e for e in entries}


def test_resolve_repo_lists_markdown_and_json_excludes_scaffolding(monkeypatch):
    _fake_get(monkeypatch)
    entries = github_repo.resolve_repo("https://github.com/speedyapply/2026-SWE-College-Jobs")
    tokens = _by_token(entries)
    base = "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/"
    assert set(tokens) == {
        base + "README.md", base + "NEW_GRAD_USA.md", base + "listings.json",
    }
    # type mapping: .md -> markdown, .json -> github
    assert tokens[base + "README.md"].type == "markdown"
    assert tokens[base + "NEW_GRAD_USA.md"].type == "markdown"
    assert tokens[base + "listings.json"].type == "github"
    # excluded: LICENSE, CONTRIBUTING.md, *TEMPLATE*, and the directory
    assert not any("LICENSE" in t or "CONTRIBUTING" in t or "TEMPLATE" in t for t in tokens)


def test_resolve_repo_honors_explicit_tree_branch_without_default_branch_call(monkeypatch):
    calls = []

    def _get(url, *a, **k):
        calls.append(url)
        return _Resp(_CONTENTS)  # only the contents call should happen
    monkeypatch.setattr(github_repo.httpx, "get", _get)

    entries = github_repo.resolve_repo("https://github.com/o/r/tree/dev")
    assert all(e.token.startswith("https://raw.githubusercontent.com/o/r/dev/") for e in entries)
    assert all(url.endswith("/contents") for url in calls)  # no /repos/o/r branch lookup


def test_resolve_repo_non_github_url_raises():
    with pytest.raises(github_repo.GitHubRepoError):
        github_repo.resolve_repo("https://example.com/foo/bar")


def test_resolve_repo_bare_owner_only_raises():
    with pytest.raises(github_repo.GitHubRepoError):
        github_repo.resolve_repo("https://github.com/speedyapply")


def test_resolve_repo_network_failure_raises(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(github_repo.httpx, "get", _boom)
    with pytest.raises(github_repo.GitHubRepoError):
        github_repo.resolve_repo("https://github.com/o/r")


def test_resolve_repo_empty_of_lists_returns_empty(monkeypatch):
    _fake_get(monkeypatch, contents=[{"type": "file", "name": "LICENSE"},
                                     {"type": "dir", "name": "src"}])
    assert github_repo.resolve_repo("https://github.com/o/r") == []
