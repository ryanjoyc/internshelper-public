"""Tiny stdlib `.env` loader — no third-party dependency.

Reads a `KEY=value` file at the repo root into `os.environ` using `setdefault`, so any
variable already present in the real environment (shell export, launchd plist) always wins.
A missing file is a silent no-op. Returns the pairs parsed from the file (regardless of
whether each was actually applied), which keeps the function easy to test.

The repo holds secrets (SMTP app password, your email) here; `.env` is gitignored.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = the directory containing the `internshelper` package.
_DEFAULT_ENV = Path(__file__).resolve().parent.parent / ".env"


_FALSEY = {"0", "false", "no", "off", ""}


def feature_enabled(name: str, default: bool = True) -> bool:
    """Read an INTERNSHELPER_FEATURE_<NAME> toggle. Unset -> `default`; 0/false/no/off -> False."""
    raw = os.environ.get(f"INTERNSHELPER_FEATURE_{name.upper()}")
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def _parse_value(raw: str) -> str:
    """Quote- and inline-comment-aware value parse.

    A quoted value keeps everything inside the quotes (incl. `#`) and drops any trailing text.
    An unquoted value is truncated at the first `#` preceded by whitespace (an inline comment);
    a `#` with no leading space (e.g. inside a token) is kept.
    """
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ("'", '"'):
        q = raw[0]
        end = raw.find(q, 1)
        return raw[1:end] if end != -1 else raw[1:].strip()
    out: list[str] = []
    for i, ch in enumerate(raw):
        if ch == "#" and i > 0 and raw[i - 1] in " \t":
            break
        out.append(ch)
    return "".join(out).strip()


def load_dotenv(path: str | Path | None = None) -> dict[str, str]:
    """Load `.env` into os.environ (never overriding existing vars). Returns parsed pairs."""
    path = Path(path) if path is not None else _DEFAULT_ENV
    if not path.exists():
        return {}

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _parse_value(value)
        parsed[key] = value
        os.environ.setdefault(key, value)
    return parsed
