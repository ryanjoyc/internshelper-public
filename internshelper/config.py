"""Load + validate sources.yaml and settings.toml.

Failure policy:
- A missing or unparseable config file is a hard error (ConfigError) — never run blind.
- An empty / all-commented `sources` list is valid (zero sources; the run still fires a
  heartbeat and sends nothing).
- A single malformed source entry is skipped and returned in the `errors` list so the
  caller can record it; the remaining valid entries still load.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """Raised for a missing/unparseable config file or missing required settings."""


# Repo root = the directory containing the `internshelper` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def default_path(env_var: str, rel: str) -> str:
    """The env var if set, else `rel` resolved against the repo root.

    Makes the collector cwd-independent: a scheduled job (launchd/cron/systemd) started from
    $HOME or / still finds config/ and data/ without an explicit env override.
    """
    return os.environ.get(env_var) or str(_REPO_ROOT / rel)


# Which per-entry field carries the identifying token for each source type.
ID_FIELD = {
    "greenhouse": "token",
    "lever": "token",
    "ashby": "org",
    "github": "url",
    "markdown": "url",  # raw README.md URL of a Markdown-table internship list
}


@dataclass
class SourceEntry:
    type: str
    token: str  # the identifying value (token / org / url, normalized)
    label: str = ""
    # Optional coarse flood guard: if set, only postings whose title contains one of
    # these substrings (case-insensitive) are ingested from this source.
    title_must_match: list[str] = field(default_factory=list)
    # Markdown connector only: optional column override (field -> exact header name).
    columns: dict[str, str] = field(default_factory=dict)

    @property
    def source_key(self) -> str:
        return f"{self.type}:{self.token}"

    @property
    def display(self) -> str:
        return self.label or self.source_key

    def accepts(self, title: str) -> bool:
        """True if this source has no title filter, or `title` matches one of its substrings."""
        if not self.title_must_match:
            return True
        low = (title or "").lower()
        return any(sub.lower() in low for sub in self.title_must_match)


@dataclass
class Settings:
    smtp_host: str
    smtp_port: int
    smtp_sender: str
    smtp_recipient: str
    require_cs: bool
    require_intern_or_newgrad: bool
    keywords: dict[str, list[str]] = field(default_factory=dict)
    # v2: send the review nudge once >= this many postings are pending.
    notify_threshold: int = 10
    # v3 term classifier: the default target term (season + year) to grade against.
    term_season: str = "summer"
    term_year: int = 2027
    # Opt-in, METERED in-app LLM upgrade. Off by default — the free path is the
    # /classify-terms skill run in an interactive Claude Code session.
    term_llm_enabled: bool = False
    term_llm_model: str = "claude-opus-4-8"


def load_sources(path: str | Path) -> tuple[list[SourceEntry], list[dict]]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"sources file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"could not parse {path}: {e}") from e

    if data is None:
        # Entirely empty file.
        return [], []
    if not isinstance(data, dict) or "sources" not in data:
        raise ConfigError(f"{path} must contain a top-level 'sources:' key")

    raw_list = data["sources"]
    if raw_list is None:
        return [], []
    if not isinstance(raw_list, list):
        raise ConfigError(f"{path}: 'sources' must be a list")

    entries: list[SourceEntry] = []
    errors: list[dict] = []
    for raw in raw_list:
        try:
            entries.append(_parse_source(raw))
        except ValueError as e:
            errors.append({"raw": raw, "reason": str(e)})
    return entries, errors


def _parse_source(raw: object) -> SourceEntry:
    if not isinstance(raw, dict):
        raise ValueError("source entry must be a mapping")
    stype = raw.get("type")
    if stype not in ID_FIELD:
        raise ValueError(f"unknown or missing source type: {stype!r}")
    id_field = ID_FIELD[stype]
    token = raw.get(id_field)
    if not token or not str(token).strip():
        raise ValueError(f"{stype} source requires a non-empty '{id_field}'")
    tmm = raw.get("title_must_match") or []
    if not isinstance(tmm, list):
        raise ValueError("title_must_match must be a list of strings")
    cols = raw.get("columns") or {}
    if not isinstance(cols, dict):
        raise ValueError("columns must be a mapping of field -> header name")
    return SourceEntry(
        type=stype,
        token=str(token).strip(),
        label=str(raw.get("label", "") or ""),
        title_must_match=[str(s) for s in tmm],
        columns={str(k): str(v) for k, v in cols.items()},
    )


def load_settings(path: str | Path) -> Settings:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"settings file not found: {path}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"could not parse {path}: {e}") from e

    smtp = data.get("smtp", {})
    if not smtp.get("host"):
        raise ConfigError(f"{path}: missing required [smtp] host")
    # sender/recipient are per-user PII: prefer the gitignored .env, fall back to settings.toml,
    # and allow empty (email may be disabled). The "email on but creds missing" check lives in
    # notify.send_nudge, gated by the EMAIL feature flag.

    filters = data.get("filters", {})
    keywords = data.get("keywords", {})
    review = data.get("review", {})
    term = data.get("term", {})
    term_llm = term.get("llm", {})
    for key in ("internship", "newgrad", "cs"):
        if not keywords.get(key):
            raise ConfigError(f"{path}: missing required [keywords] {key} list")

    return Settings(
        smtp_host=smtp["host"],
        smtp_port=int(smtp.get("port", 587)),
        smtp_sender=os.environ.get("INTERNSHELPER_SMTP_SENDER") or smtp.get("sender") or "",
        smtp_recipient=os.environ.get("INTERNSHELPER_SMTP_RECIPIENT") or smtp.get("recipient") or "",
        require_cs=bool(filters.get("require_cs", True)),
        require_intern_or_newgrad=bool(filters.get("require_intern_or_newgrad", True)),
        keywords={k: list(keywords[k]) for k in ("internship", "newgrad", "cs")},
        notify_threshold=int(review.get("notify_threshold", 10)),
        term_season=str(term.get("default_target_season", "summer")).lower(),
        term_year=int(term.get("default_target_year", 2027)),
        term_llm_enabled=bool(term_llm.get("enabled", False)),
        term_llm_model=str(term_llm.get("model", "claude-opus-4-8")),
    )
