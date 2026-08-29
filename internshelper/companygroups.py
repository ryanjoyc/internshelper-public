"""User-controlled company priority groups for the Board.

This is deliberately separate from ``companies.py``: that module tracks whether a
company has a resolvable job board, while this module answers how the user wants to browse
postings from a company regardless of where those postings were collected.

Absent companies are ``unclassified``.  Discovery suggestions are proposals until
the user explicitly approves them; the ranker never assigns a company group.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from internshelper.config import ConfigError, default_path
from internshelper.dotenv import load_dotenv

GROUPS = ("top_target", "known", "discovery")
MANUAL_GROUPS = ("top_target", "known")
ALL_GROUPS = (*GROUPS, "unclassified")
GROUP_LABELS = {
    "top_target": "Top targets",
    "known": "Known companies",
    "discovery": "Worth discovering",
    "unclassified": "Unclassified",
}
GROUP_HINTS = {
    "top_target": "FAANG+ and companies you personally prioritize.",
    "known": "Established companies you recognize and want to browse.",
    "discovery": "Lesser-known companies with an approved reason to look closer.",
    "unclassified": "Not evaluated yet — neutral, not a negative judgment.",
}

_SUFFIX_TOKENS = frozenset(
    {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "plc", "gmbh"}
)
_PUNCT = str.maketrans({c: " " for c in ".,'\"&()/"})
_HEADER = (
    "# internsHELPer — company browsing groups. MACHINE-MANAGED: edit via the\n"
    "# `companygroups` CLI or the web UI; this file is regenerated on every write.\n"
    "# Groups: top_target | known | discovery. Missing companies are unclassified.\n"
)


def normalize_company(name: str) -> str:
    """Canonical company identity used by classification and Board grouping."""
    tokens = (name or "").lower().translate(_PUNCT).split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    merged: list[str] = []
    run: list[str] = []
    for token in tokens:
        if len(token) == 1:
            run.append(token)
        else:
            if run:
                merged.append("".join(run))
                run = []
            merged.append(token)
    if run:
        merged.append("".join(run))
    return " ".join(merged)


@dataclass
class CompanyGroupEntry:
    name: str
    group: str = ""
    aliases: list[str] = field(default_factory=list)
    reason: str = ""
    proposal: dict = field(default_factory=dict)


def company_groups_path() -> str:
    return default_path("INTERNSHELPER_COMPANY_GROUPS", "config/company-groups.yaml")


def _parse_entry(raw: object, seen: set[str]) -> CompanyGroupEntry:
    if not isinstance(raw, dict):
        raise ValueError("company group entry must be a mapping")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("company group entry requires a non-empty 'name'")
    identity = normalize_company(name)
    if identity in seen:
        raise ValueError(f"duplicate company identity: {name}")
    seen.add(identity)
    group = str(raw.get("group") or "")
    if group and group not in GROUPS:
        raise ValueError(f"unknown group {group!r} for {name}")
    aliases = raw.get("aliases") or []
    if not isinstance(aliases, list):
        raise ValueError(f"aliases for {name} must be a list")
    aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
    proposal = raw.get("proposal") or {}
    if not isinstance(proposal, dict):
        raise ValueError(f"proposal for {name} must be a mapping")
    if proposal:
        proposed_group = str(proposal.get("group") or "")
        if proposed_group != "discovery":
            raise ValueError(f"proposal for {name} must target discovery")
        if not str(proposal.get("reason") or "").strip():
            raise ValueError(f"proposal for {name} requires a reason")
        if not str(proposal.get("evidence") or "").strip():
            raise ValueError(f"proposal for {name} requires evidence")
    return CompanyGroupEntry(
        name=name,
        group=group,
        aliases=aliases,
        reason=str(raw.get("reason") or "").strip(),
        proposal=dict(proposal),
    )


def load_company_groups(path: str | Path | None = None) -> tuple[list[CompanyGroupEntry], list[dict]]:
    path = Path(path or company_groups_path())
    if not path.exists():
        return [], []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc
    raw_entries = data.get("companies") or []
    if not isinstance(raw_entries, list):
        raise ConfigError(f"{path}: 'companies' must be a list")
    entries: list[CompanyGroupEntry] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for raw in raw_entries:
        try:
            entries.append(_parse_entry(raw, seen))
        except ValueError as exc:
            errors.append({"raw": raw, "reason": str(exc)})
    return entries, errors


def save_company_groups(path: str | Path, entries: list[CompanyGroupEntry]) -> None:
    docs: list[dict[str, object]] = []
    for entry in entries:
        doc: dict[str, object] = {"name": entry.name}
        if entry.group:
            doc["group"] = entry.group
        if entry.aliases:
            doc["aliases"] = list(entry.aliases)
        if entry.reason:
            doc["reason"] = entry.reason
        if entry.proposal:
            doc["proposal"] = dict(entry.proposal)
        docs.append(doc)
    body = yaml.safe_dump(
        {"companies": docs}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    Path(path).write_text(_HEADER + body, encoding="utf-8")


def _load_or_raise(path: str | Path) -> list[CompanyGroupEntry]:
    entries, errors = load_company_groups(path)
    if errors:
        raise ConfigError(f"{path} has malformed entries: {errors}")
    return entries


def find(entries: list[CompanyGroupEntry], name: str) -> CompanyGroupEntry | None:
    identity = normalize_company(name)
    for entry in entries:
        names = [entry.name, *entry.aliases]
        if identity in {normalize_company(candidate) for candidate in names}:
            return entry
    return None


def group_map(entries: list[CompanyGroupEntry]) -> dict[str, CompanyGroupEntry]:
    result: dict[str, CompanyGroupEntry] = {}
    for entry in entries:
        for name in [entry.name, *entry.aliases]:
            identity = normalize_company(name)
            if identity:
                result[identity] = entry
    return result


def classify(name: str, mapping: dict[str, CompanyGroupEntry]) -> tuple[str, CompanyGroupEntry | None]:
    entry = mapping.get(normalize_company(name))
    return ((entry.group if entry and entry.group else "unclassified"), entry)


def set_group(
    path: str | Path, name: str, group: str, *, reason: str = "",
    allow_discovery: bool = False,
) -> CompanyGroupEntry:
    allowed = GROUPS if allow_discovery else MANUAL_GROUPS
    if group not in allowed:
        if group == "discovery" and not allow_discovery:
            raise ValueError("discovery requires a reason/evidence proposal and approval")
        raise ValueError(f"group must be one of {allowed}, got {group!r}")
    name = name.strip()
    if not name:
        raise ValueError("company name must be non-empty")
    entries = _load_or_raise(path)
    entry = find(entries, name)
    if entry is None:
        entry = CompanyGroupEntry(name=name)
        entries.append(entry)
    entry.group = group
    entry.reason = reason.strip()
    entry.proposal = {}
    save_company_groups(path, entries)
    return entry


def clear_group(path: str | Path, name: str) -> None:
    entries = _load_or_raise(path)
    entry = find(entries, name)
    if entry is None:
        return
    entry.group = ""
    entry.reason = ""
    if not entry.aliases and not entry.proposal:
        entries.remove(entry)
    save_company_groups(path, entries)


def propose_discovery(
    path: str | Path, name: str, *, reason: str, evidence: str
) -> CompanyGroupEntry:
    name, reason, evidence = name.strip(), reason.strip(), evidence.strip()
    if not name or not reason or not evidence:
        raise ValueError("company, reason, and evidence are required")
    entries = _load_or_raise(path)
    entry = find(entries, name)
    if entry is None:
        entry = CompanyGroupEntry(name=name)
        entries.append(entry)
    entry.proposal = {"group": "discovery", "reason": reason, "evidence": evidence}
    save_company_groups(path, entries)
    return entry


def approve_proposal(path: str | Path, name: str) -> CompanyGroupEntry:
    entries = _load_or_raise(path)
    entry = find(entries, name)
    if entry is None or not entry.proposal:
        raise ValueError(f"{name} has no pending company-group proposal")
    entry.group = "discovery"
    entry.reason = str(entry.proposal["reason"])
    entry.proposal = {}
    save_company_groups(path, entries)
    return entry


def reject_proposal(path: str | Path, name: str) -> None:
    entries = _load_or_raise(path)
    entry = find(entries, name)
    if entry is None or not entry.proposal:
        raise ValueError(f"{name} has no pending company-group proposal")
    entry.proposal = {}
    if not entry.group and not entry.aliases:
        entries.remove(entry)
    save_company_groups(path, entries)


def _cmd_list(path: str) -> int:
    entries, errors = load_company_groups(path)
    print(json.dumps([
        {"name": e.name, "group": e.group or "unclassified", "aliases": e.aliases,
         "reason": e.reason, "proposal": e.proposal}
        for e in entries
    ]))
    for error in errors:
        print(f"WARNING: skipped malformed entry: {error['reason']}")
    return 0


def _sync_posting_groups(path: str) -> None:
    """Keep the CLI's persisted compatibility tiers aligned with its config write."""
    from internshelper import db, tiers

    conn = db.connect(default_path("INTERNSHELPER_DB", "data/internshelper.db"))
    try:
        db.init_db(conn)
        tiers.retier_inbox(conn, tiers.load_tier_map(path))
    finally:
        conn.close()


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.companygroups")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("name")
    set_cmd.add_argument("group", choices=MANUAL_GROUPS)
    set_cmd.add_argument("--reason", default="")
    clear_cmd = sub.add_parser("clear")
    clear_cmd.add_argument("name")
    propose_cmd = sub.add_parser("propose")
    propose_cmd.add_argument("name")
    propose_cmd.add_argument("group", choices=("discovery",))
    propose_cmd.add_argument("--reason", required=True)
    propose_cmd.add_argument("--evidence", required=True)
    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("name")
    reject_cmd = sub.add_parser("reject")
    reject_cmd.add_argument("name")
    args = parser.parse_args(argv)
    path = company_groups_path()
    try:
        if args.cmd == "list":
            return _cmd_list(path)
        if args.cmd == "set":
            entry = set_group(path, args.name, args.group, reason=args.reason)
            _sync_posting_groups(path)
            print(f"{entry.name} -> {entry.group}")
        elif args.cmd == "clear":
            clear_group(path, args.name)
            _sync_posting_groups(path)
            print(f"{args.name} -> unclassified")
        elif args.cmd == "propose":
            propose_discovery(path, args.name, reason=args.reason, evidence=args.evidence)
            print(f"discovery proposal recorded for {args.name}")
        elif args.cmd == "approve":
            entry = approve_proposal(path, args.name)
            _sync_posting_groups(path)
            print(f"{entry.name} -> discovery")
        elif args.cmd == "reject":
            reject_proposal(path, args.name)
            print(f"rejected company-group proposal for {args.name}")
    except (ValueError, ConfigError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
