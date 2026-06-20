"""Add / list / remove / test job-board sources from the CLI.

The deterministic core under the `/add-source` agent skill: paste a job-board URL, it's
detected (`sourceurl`), live fetch-tested, and — only if it works — appended to
`config/sources.yaml`. Writes are append-only so the file's hand-written comments and
commented examples are never clobbered.

    python -m internshelper.sources add https://boards.greenhouse.io/stripe
    python -m internshelper.sources list [--json]
    python -m internshelper.sources remove greenhouse:stripe
    python -m internshelper.sources test <url-or-source_key>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

import yaml

from internshelper.config import ID_FIELD, ConfigError, SourceEntry, default_path, load_sources
from internshelper.connectors import build_connector
from internshelper.dotenv import load_dotenv
from internshelper.sourceurl import SourceDetectionError, detect_source


def _sources_path() -> str:
    return default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")


# ---------- YAML writing (append-only; never rewrites existing lines) ----------

def entry_to_block(entry: SourceEntry) -> str:
    """Render one source as a 2-space-indented YAML list item, ordered type -> id -> extras."""
    d: dict[str, object] = {"type": entry.type, ID_FIELD[entry.type]: entry.token}
    if entry.label:
        d["label"] = entry.label
    if entry.title_must_match:
        d["title_must_match"] = list(entry.title_must_match)
    if entry.columns:
        d["columns"] = dict(entry.columns)
    text = yaml.safe_dump([d], sort_keys=False, default_flow_style=False, allow_unicode=True)
    return textwrap.indent(text, "  ")


def _has_sources_key(text: str) -> bool:
    return any(
        ln.lstrip().startswith("sources:") and not ln.lstrip().startswith("#")
        for ln in text.splitlines()
    )


def append_source(path: str | Path, entry: SourceEntry) -> None:
    """Append a source entry at EOF under `sources:`, preserving all existing lines."""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not _has_sources_key(text):
        if text and not text.endswith("\n"):
            text += "\n"
        text += "sources:\n"
    elif text and not text.endswith("\n"):
        text += "\n"
    text += entry_to_block(entry)
    path.write_text(text, encoding="utf-8")


def _leading_spaces(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _block_matches(block: list[str], stype: str, id_field: str, token: str) -> bool:
    head = block[0].strip()
    m = re.match(r"-\s*type:\s*(.+)$", head)
    if not m or m.group(1).strip().strip("\"'") != stype:
        return False
    for ln in block:
        s = ln.strip()
        if s.startswith("#"):
            continue
        m2 = re.match(rf"{re.escape(id_field)}:\s*(.+)$", s)
        if m2 and m2.group(1).strip().strip("\"'") == token:
            return True
    return False


def remove_source(path: str | Path, source_key: str) -> bool:
    """Delete the active (uncommented) list item matching `type:token`. Returns success."""
    path = Path(path)
    if not path.exists():
        return False
    stype, _, token = source_key.partition(":")
    id_field = ID_FIELD.get(stype)
    if id_field is None:
        return False
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    i, n = 0, 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _leading_spaces(line) == 2 and line.strip().startswith("- "):
            j = i + 1
            while j < n:
                nxt = lines[j]
                if nxt.strip() == "":
                    break
                # Any line indented deeper than the 2-space dash is a continuation of this
                # item — including nested block-sequence items (e.g. title_must_match) that
                # start with '- ' at 4 spaces. Only a line at indent <= 2 ends the block.
                if _leading_spaces(nxt) >= 4:
                    j += 1
                else:
                    break
            if _block_matches(lines[i:j], stype, id_field, token):
                del lines[i:j]
                path.write_text("".join(lines), encoding="utf-8")
                return True
            i = j
        else:
            i += 1
    return False


def find_duplicate(entries: list[SourceEntry], key: str) -> bool:
    return any(e.source_key == key for e in entries)


def fetch_test(entry: SourceEntry, limit: int = 5) -> tuple[int, list[str]]:
    postings = build_connector(entry).fetch()
    return len(postings), [p.title for p in postings[:limit]]


def _parse_kv(spec: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _load_existing(path: str) -> list[SourceEntry]:
    try:
        return load_sources(path)[0]
    except ConfigError:
        return []


# ---------- subcommands ----------

def _cmd_add(args) -> int:
    path = _sources_path()
    try:
        entry = detect_source(args.url, label=args.label)
    except SourceDetectionError as e:
        print(f"could not detect a source from {args.url!r}: {e}")
        return 2
    if args.title_must_match:
        entry.title_must_match = [s.strip() for s in args.title_must_match.split(",") if s.strip()]
    if args.columns:
        entry.columns = _parse_kv(args.columns)

    if find_duplicate(_load_existing(path), entry.source_key):
        print(f"already present: {entry.source_key} (skipped)")
        return 0

    if not args.no_test:
        try:
            count, titles = fetch_test(entry, limit=args.limit)
        except Exception as e:
            print(f"fetch failed for {entry.source_key}: {type(e).__name__}: {e}")
            print("not written.")
            return 1
        print(f"fetched {count} postings from {entry.source_key}")
        for t in titles:
            print(f"  - {t}")
        if count == 0 and not args.yes:
            print("0 postings — the token may be wrong. Re-run with --yes to add anyway. Not written.")
            return 1

    if not args.yes:
        suffix = " (untested)" if args.no_test else ""
        if not _confirm(f"add {entry.source_key}{suffix}?"):
            print("aborted; not written.")
            return 1

    append_source(path, entry)
    print(f"added {entry.source_key}")
    return 0


def _cmd_list(args) -> int:
    path = _sources_path()
    entries, errors = load_sources(path)
    if args.json:
        print(json.dumps([{
            "source_key": e.source_key, "type": e.type, "token": e.token, "label": e.label,
            "title_must_match": e.title_must_match, "columns": e.columns,
        } for e in entries], indent=2))
    else:
        if not entries:
            print("(no sources)")
        for e in entries:
            extra = f"  filter={e.title_must_match}" if e.title_must_match else ""
            print(f"{e.source_key}  ({e.display}){extra}")
    for err in errors:
        print(f"! malformed entry skipped: {err['reason']}", file=sys.stderr)
    return 0


def _cmd_remove(args) -> int:
    if remove_source(_sources_path(), args.source_key):
        print(f"removed {args.source_key}")
        return 0
    print(f"not found: {args.source_key}")
    return 1


def _cmd_test(args) -> int:
    target = args.target
    stype = target.split(":", 1)[0] if ":" in target else ""
    if stype in ID_FIELD:
        entry = SourceEntry(type=stype, token=target.split(":", 1)[1])
    else:
        try:
            entry = detect_source(target)
        except SourceDetectionError as e:
            print(f"could not detect a source from {target!r}: {e}")
            return 2
    try:
        count, titles = fetch_test(entry, limit=args.limit)
    except Exception as e:
        print(f"fetch failed for {entry.source_key}: {type(e).__name__}: {e}")
        return 1
    print(f"{entry.source_key}: {count} postings")
    for t in titles:
        print(f"  - {t}")
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.sources")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add a job-board source from its URL")
    a.add_argument("url")
    a.add_argument("--label", default=None)
    a.add_argument("--title-must-match", default=None, help="comma-separated title substrings")
    a.add_argument("--columns", default=None, help="comma-separated k=v overrides (markdown only)")
    a.add_argument("--yes", action="store_true", help="skip the confirm prompt")
    a.add_argument("--no-test", action="store_true", help="skip the live fetch-test")
    a.add_argument("--limit", type=int, default=5)

    lp = sub.add_parser("list", help="list configured sources")
    lp.add_argument("--json", action="store_true")

    rm = sub.add_parser("remove", help="remove a source by its source_key (type:token)")
    rm.add_argument("source_key")

    t = sub.add_parser("test", help="fetch a URL or existing source_key and report (no write)")
    t.add_argument("target")
    t.add_argument("--limit", type=int, default=5)

    args = parser.parse_args(argv)
    return {
        "add": _cmd_add, "list": _cmd_list, "remove": _cmd_remove, "test": _cmd_test,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
