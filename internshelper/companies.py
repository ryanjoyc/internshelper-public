"""The approved-companies index: the user's master list of companies to track.

`config/companies.yaml` holds intent + resolution state; the actual scan config for a
resolved company's board lives in `config/sources.yaml` (`board` is a pointer — a
source_key). Unlike sources.yaml (hand-editable, append-only writes), this file is
MACHINE-MANAGED: every write regenerates it under a fixed header, because entries change
state in place (pending -> resolved / no-board).

States: pending (awaiting resolve) | resolved (board set) | no-board (checked, nothing
public — remembered so we don't re-hunt). A pending entry may carry a `proposal`
({url, count, evidence}) written by the resolve-companies skill, awaiting Approval A
(the user's yes/no on the web Companies page or `companies approve`).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from internshelper import sources as sources_mod
from internshelper.config import ConfigError, default_path, load_sources
from internshelper.dotenv import load_dotenv

STATUSES = ("pending", "resolved", "no-board")

# Flood guard applied to boards created by approving a proposal (whole-word matching:
# `intern` and `internship` are separate terms on purpose).
DEFAULT_GUARD = ["intern", "internship", "new grad", "university", "graduate",
                 "early career", "campus"]

_HEADER = (
    "# internsHELPer — approved-companies index. MACHINE-MANAGED: edit via the\n"
    "# `companies` CLI or the web Companies page; this file is regenerated on every\n"
    "# write (hand comments are not preserved).\n"
    "# States: pending | resolved (board -> source_key in sources.yaml) | no-board.\n"
)


@dataclass
class CompanyEntry:
    name: str
    status: str = "pending"
    board: str = ""    # source_key in sources.yaml when resolved
    notes: str = ""
    proposal: dict = field(default_factory=dict)  # {url, count, evidence} awaiting approval


def companies_path() -> str:
    return default_path("INTERNSHELPER_COMPANIES", "config/companies.yaml")


def load_companies(path) -> tuple[list[CompanyEntry], list[dict]]:
    """(entries, per-entry errors). A missing file is an empty index, not an error."""
    path = Path(path)
    if not path.exists():
        return [], []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"could not parse {path}: {e}") from e
    raw_list = data.get("companies") or []
    if not isinstance(raw_list, list):
        raise ConfigError(f"{path}: 'companies' must be a list")
    entries: list[CompanyEntry] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for raw in raw_list:
        try:
            entries.append(_parse_company(raw, seen))
        except ValueError as e:
            errors.append({"raw": raw, "reason": str(e)})
    return entries, errors


def _parse_company(raw: object, seen: set[str]) -> CompanyEntry:
    if not isinstance(raw, dict):
        raise ValueError("company entry must be a mapping")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("company requires a non-empty 'name'")
    if name.lower() in seen:
        raise ValueError(f"duplicate company: {name}")
    seen.add(name.lower())
    status = str(raw.get("status") or "pending")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r} for {name}")
    board = str(raw.get("board") or "").strip()
    if status == "resolved" and not board:
        raise ValueError(f"resolved company {name} requires a 'board' source_key")
    proposal = raw.get("proposal") or {}
    if not isinstance(proposal, dict):
        raise ValueError(f"proposal for {name} must be a mapping")
    return CompanyEntry(name=name, status=status, board=board,
                        notes=str(raw.get("notes") or ""), proposal=dict(proposal))


def save_companies(path, entries: list[CompanyEntry]) -> None:
    docs = []
    for e in entries:
        d: dict[str, object] = {"name": e.name, "status": e.status}
        if e.board:
            d["board"] = e.board
        if e.notes:
            d["notes"] = e.notes
        if e.proposal:
            d["proposal"] = dict(e.proposal)
        docs.append(d)
    body = yaml.safe_dump({"companies": docs}, sort_keys=False, allow_unicode=True,
                          default_flow_style=False)
    Path(path).write_text(_HEADER + body)


def find(entries: list[CompanyEntry], name: str) -> CompanyEntry | None:
    low = name.strip().lower()
    return next((e for e in entries if e.name.lower() == low), None)


# ---------- mutations (each loads, edits, regenerates the file) ----------

def _load_or_raise(path) -> list[CompanyEntry]:
    entries, errors = load_companies(path)
    if errors:
        raise ConfigError(f"{path} has malformed entries: {errors}")
    return entries


def _get(entries: list[CompanyEntry], name: str) -> CompanyEntry:
    e = find(entries, name)
    if e is None:
        raise ValueError(f"unknown company: {name}")
    return e


def add_company(path, name: str) -> CompanyEntry:
    name = name.strip()
    if not name:
        raise ValueError("company name must be non-empty")
    entries = _load_or_raise(path)
    if find(entries, name):
        raise ValueError(f"{name} is already in the index")
    entry = CompanyEntry(name=name)
    entries.append(entry)
    save_companies(path, entries)
    return entry


def set_proposal(path, name: str, *, url: str, count: int, evidence: str) -> None:
    entries = _load_or_raise(path)
    e = _get(entries, name)
    if e.status != "pending":
        raise ValueError(f"{e.name} is {e.status}; proposals only apply to pending companies")
    e.proposal = {"url": url, "count": count, "evidence": evidence}
    save_companies(path, entries)


def approve(path, sources_path, name: str):
    """Approval A: turn the pending proposal into a real source + resolved pointer."""
    entries = _load_or_raise(path)
    e = _get(entries, name)
    if e.status != "pending" or not e.proposal.get("url"):
        raise ValueError(f"{e.name} has no pending proposal to approve")
    src = sources_mod.resolve_entry(e.proposal["url"], label=e.name,
                                    title_must_match=list(DEFAULT_GUARD))
    if src.type == "workday" and not src.search:
        src.search = "intern"  # bank-sized tenants refuse full crawls without it
    if not sources_mod.is_duplicate(sources_path, src):
        sources_mod.append_source(sources_path, src)
    e.status, e.board, e.proposal = "resolved", src.source_key, {}
    save_companies(path, entries)
    return src


def reject(path, name: str, notes: str = "") -> None:
    entries = _load_or_raise(path)
    e = _get(entries, name)
    e.status, e.proposal = "no-board", {}
    if notes:
        e.notes = notes
    save_companies(path, entries)


def resolve_write(path, sources_path, name: str, source_key: str) -> None:
    """Manually link a company to an already-registered source."""
    known = {s.source_key for s in load_sources(sources_path)[0]}
    if source_key not in known:
        raise ValueError(f"{source_key} is not in sources.yaml — add the board first")
    entries = _load_or_raise(path)
    e = _get(entries, name)
    e.status, e.board, e.proposal = "resolved", source_key, {}
    save_companies(path, entries)


def mark_no_board(path, name: str, notes: str = "") -> None:
    reject(path, name, notes=notes)


def dangling(entries: list[CompanyEntry], source_keys: set[str]) -> list[str]:
    """Names of resolved companies whose board pointer is missing from sources.yaml."""
    return [e.name for e in entries
            if e.status == "resolved" and e.board not in source_keys]


# ---------- CLI ----------

def _cmd_list(args) -> int:
    entries, errors = load_companies(companies_path())
    if args.json:
        print(json.dumps([{"name": e.name, "status": e.status, "board": e.board,
                           "notes": e.notes, "proposal": e.proposal} for e in entries]))
    else:
        if not entries:
            print("(no companies)")
        for e in entries:
            extra = f" -> {e.board}" if e.board else (" [proposal]" if e.proposal else "")
            print(f"{e.name}  ({e.status}){extra}")
    for err in errors:
        print(f"WARNING: skipped malformed entry: {err['reason']}")
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.companies")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a company to the index (pending)")
    a.add_argument("name")
    lp = sub.add_parser("list", help="the index with states + board pointers")
    lp.add_argument("--json", action="store_true")
    pr = sub.add_parser("propose", help="record an agent-found board proposal")
    pr.add_argument("name")
    pr.add_argument("--url", required=True)
    pr.add_argument("--count", type=int, default=0)
    pr.add_argument("--evidence", default="")
    ap = sub.add_parser("approve", help="Approval A: proposal -> source + resolved")
    ap.add_argument("name")
    rj = sub.add_parser("reject", help="reject the proposal -> no-board")
    rj.add_argument("name")
    rj.add_argument("--notes", default="")
    rw = sub.add_parser("resolve-write", help="link to an already-registered source_key")
    rw.add_argument("name")
    rw.add_argument("source_key")
    nb = sub.add_parser("mark-no-board", help="no public board found; remember that")
    nb.add_argument("name")
    nb.add_argument("--notes", default="")

    args = parser.parse_args(argv)
    cpath = companies_path()
    spath = default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")
    try:
        if args.cmd == "add":
            e = add_company(cpath, args.name)
            print(f"added {e.name} (pending)")
        elif args.cmd == "list":
            return _cmd_list(args)
        elif args.cmd == "propose":
            set_proposal(cpath, args.name, url=args.url, count=args.count,
                         evidence=args.evidence)
            print(f"proposal recorded for {args.name} — approve on the Companies page "
                  "or with `companies approve`")
        elif args.cmd == "approve":
            src = approve(cpath, spath, args.name)
            print(f"{args.name} resolved -> {src.source_key}")
        elif args.cmd == "reject":
            reject(cpath, args.name, notes=args.notes)
            print(f"{args.name} -> no-board")
        elif args.cmd == "resolve-write":
            resolve_write(cpath, spath, args.name, args.source_key)
            print(f"{args.name} resolved -> {args.source_key}")
        elif args.cmd == "mark-no-board":
            mark_no_board(cpath, args.name, notes=args.notes)
            print(f"{args.name} -> no-board")
    except (ValueError, ConfigError) as e:
        print(str(e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
