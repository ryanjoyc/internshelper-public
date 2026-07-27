"""Streamlit dashboard: Feed / Tracker / Health.

Run from the repo root:  streamlit run internshelper/dashboard.py
All data logic lives in (and is tested via) internshelper.store / internshelper.review;
this file is UI only.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from urllib.parse import urlsplit

from internshelper import (
    clock, config, db, display, github_repo, review, sniffer, sources, store, term, text,
)
from internshelper.dotenv import load_dotenv
from internshelper.sourceurl import SourceDetectionError

load_dotenv()  # pick up INTERNSHELPER_* from the gitignored .env (real env still wins)

STATUS_OPTIONS = ["Untracked", "Interested", "Applied", "Interviewing", "Rejected", "Offer"]

_CSS_PATH = Path(__file__).parent / "assets" / "dashboard.css"


def _inject_css() -> None:
    # Layer-2 polish CSS (planTracker dark look). Global colors/radii/fonts come from
    # .streamlit/config.toml; this only adds what the theme API can't reach.
    css = _CSS_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _open_conn():
    # Open per script-run (NOT cached): Streamlit reruns on different threads, and a cached
    # SQLite connection used cross-thread raises. Opening here keeps create+use on one thread.
    conn = db.connect(config.default_path("INTERNSHELPER_DB", "data/internshelper.db"))
    db.init_db(conn)
    return conn


def _apply_form(conn, posting_id: str) -> None:
    app = conn.execute(
        "SELECT status, notes, applied_date FROM applications WHERE posting_id = ?",
        (posting_id,),
    ).fetchone()
    cur = (app["status"] if app else None) or "Untracked"
    with st.form(f"app-{posting_id}"):
        status = st.selectbox("Status", STATUS_OPTIONS,
                              index=STATUS_OPTIONS.index(cur) if cur in STATUS_OPTIONS else 0)
        notes = st.text_area("Notes", value=(app["notes"] if app else "") or "")
        applied = st.text_input("Applied date (YYYY-MM-DD)",
                                value=(app["applied_date"] if app else "") or "")
        if st.form_submit_button("Save"):
            store.set_application(conn, posting_id, status=status, notes=notes,
                                  applied_date=applied or None)
            st.success("Saved.")


def _payload_peek(payload_path) -> None:
    """Render the archived raw payload so the user can judge a pending posting."""
    if not payload_path:
        st.caption("No saved payload — judging by title only.")
        return
    try:
        raw = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    except Exception:
        st.caption("Payload file unreadable.")
        return
    desc = ""
    if isinstance(raw, dict):
        for k in ("content", "description", "descriptionPlain", "descriptionHtml", "plain"):
            if raw.get(k):
                desc = text.strip_html(str(raw[k]))
                break
    if desc:
        st.write(desc[:2000])
    st.json(raw, expanded=False)  # full payload — nothing hidden


def _record_verdict(conn, posting_id: str, verdict: str) -> None:
    reason = st.session_state.get(f"reason-{posting_id}", "")
    review.set_verdict(conn, posting_id, verdict, reason, now=clock.now_iso())
    review.finish(conn)  # resets the nudge flag the moment the queue empties
    st.rerun()


def _bulk_clear_non_candidates(conn) -> None:
    now = clock.now_iso()
    for r in review.list_pending(conn):
        if not (r["is_cs_relevant"] or r["is_internship"] or r["is_newgrad"]):
            review.set_verdict(conn, r["posting_id"], "no_match", "bulk: non-candidate", now=now)
    review.finish(conn)


def _pending_row(conn, r) -> None:
    pid = r["posting_id"]
    released = display.format_release(r["posted_at"], r["first_seen"])
    is_cand = bool(r["is_cs_relevant"] or r["is_internship"] or r["is_newgrad"])
    badge = "⭐ " if is_cand else ""
    st.markdown(f"{badge}**{released}** · {r['company']} — {r['title']} · {r['location'] or '—'}")
    c1, c2, c3, c4 = st.columns([1, 1, 3, 1])
    if c1.button("✅ Match", key=f"match-{pid}"):
        _record_verdict(conn, pid, "match")
    if c2.button("✖ No match", key=f"nomatch-{pid}"):
        _record_verdict(conn, pid, "no_match")
    c3.text_input("reason", key=f"reason-{pid}", label_visibility="collapsed",
                  placeholder="reason (optional)")
    c4.markdown(f"[open ↗]({r['url']})")
    with st.expander("Peek payload"):
        _payload_peek(r["payload_path"])


def _pending_review(conn) -> None:
    total, cand = store.pending_counts(conn)
    if total == 0:
        st.success("Nothing pending — the queue is clear.")
        return
    st.caption(f"{total} pending · {cand} keyword-candidates first")

    with st.expander("Bulk actions"):
        st.caption("Clear obvious non-matches fast, then skim the candidates that remain.")
        if st.session_state.get("bulk_confirm"):
            st.warning("Mark every NON-candidate pending posting as no_match?")
            b1, b2 = st.columns(2)
            if b1.button("Yes, clear them", key="bulk-yes"):
                _bulk_clear_non_candidates(conn)
                st.session_state.pop("bulk_confirm", None)
                st.rerun()
            if b2.button("Cancel", key="bulk-no"):
                st.session_state.pop("bulk_confirm", None)
                st.rerun()
        elif st.button("Mark all non-candidates as no_match", key="bulk-start"):
            st.session_state["bulk_confirm"] = True
            st.rerun()

    page_size = st.selectbox("Per page", [25, 50, 100], index=0, key="review-pagesize")
    total_pages = (total + page_size - 1) // page_size
    page = min(st.session_state.get("review_page", 0), total_pages - 1)
    rows = review.list_pending(conn, limit=page_size * (page + 1))
    for r in rows[page * page_size:(page + 1) * page_size]:
        _pending_row(conn, r)

    if total_pages > 1:
        c1, c2, c3 = st.columns([1, 2, 1])
        if c1.button("‹ Prev", key="review-prev", disabled=page <= 0):
            st.session_state["review_page"] = page - 1
            st.rerun()
        c2.caption(f"Page {page + 1} of {total_pages}")
        if c3.button("Next ›", key="review-next", disabled=page + 1 >= total_pages):
            st.session_state["review_page"] = page + 1
            st.rerun()


def _feed_tab(conn) -> None:
    st.subheader("Feed")
    total, cand = store.pending_counts(conn)
    st.metric("Pending review", total, help=f"{cand} keyword-candidate, {total - cand} other")
    if total:
        st.caption("Triage them in the **Pending review** view below — or run "
                   "**/review-internships** in Claude Code for the agent-assisted pass.")

    view = st.radio("View", ["Confirmed matches", "Pending review", "All postings"],
                    horizontal=True)

    if view == "Confirmed matches":
        rows = review.summary(conn)
        st.caption(f"{len(rows)} confirmed matches")
        for r in rows:
            released = display.format_release(r["posted_at"], r["first_seen"])
            with st.expander(
                f"**{released}**  ·  {r['company']} — {r['title']}  ·  {r['location'] or '—'}"
            ):
                st.write(f"[{r['url']}]({r['url']})")
                st.caption(f"verdict reason: {r['verdict_reason'] or '—'}")
                _apply_form(conn, r["posting_id"])

    elif view == "Pending review":
        _pending_review(conn)

    else:  # All postings
        search = st.text_input("Search title / company", "")
        rows = store.feed(conn, require_cs=False, require_intern_or_newgrad=False,
                          include_all=True, include_closed=True, search=search)
        st.caption(f"{len(rows)} postings")
        st.dataframe(
            [{"Posted": display.format_release(r["posted_at"], r["first_seen"]),
              "Company": r["company"], "Title": r["title"], "Review": r["review_status"],
              "Verdict": r["verdict"] or "—", "Active": bool(r["is_active"]), "Apply": r["url"]}
             for r in rows],
            width="stretch", hide_index=True,
            column_order=["Posted", "Company", "Title", "Review", "Verdict", "Active", "Apply"],
            column_config={"Apply": st.column_config.LinkColumn("Apply", display_text="open ↗")},
        )


def _tracker_tab(conn) -> None:
    st.subheader("Tracker")
    rows = store.tracker(conn)
    if not rows:
        st.info("No applications tracked yet. Set a status on a confirmed match in the Feed tab.")
        return
    st.dataframe(
        [{"Status": r["status"], "Title": r["title"], "Company": r["company"],
          "Applied": r["applied_date"], "Notes": r["notes"], "URL": r["url"]} for r in rows],
        width="stretch", hide_index=True,
    )


_TERM_VERDICT_HELP = {
    "EXPLICIT": "text literally states the target term",
    "LIKELY": "strong implicit cue for the target cohort",
    "POSSIBLE": "internship, no term stated — not excluded, not shown",
    "NOT": "an explicit different term, or a new-grad/full-time role",
    "UNREADABLE": "no readable description — honest non-answer",
}


def _terms_tab(conn) -> None:
    """Graded term classifier surface — 'which postings are my target term (e.g. Summer 2027)?'.

    Display-only (no silent drops). The app NEVER calls a model here: ambiguous rows are handed
    off to the free /classify-terms skill (interactive Claude Code, subscription). An in-app
    metered upgrade hint appears only when [term.llm] is enabled in settings.
    """
    st.subheader("Terms")
    try:
        settings = config.load_settings(
            config.default_path("INTERNSHELPER_SETTINGS", "config/settings.toml"))
    except config.ConfigError as e:
        st.error(f"Couldn't read settings: {e}")
        return
    target = (settings.term_season, settings.term_year)
    st.caption(f"Target term: **{target[0].title()} {target[1]}** "
               f"(set in `[term]`; override per-run with `term classify --target`).")

    counts = term.summary(conn, target)["counts"]
    if not counts:
        st.info("No postings classified yet. Run "
                "`.venv/bin/python -m internshelper.term classify` to grade the queue "
                "(enriches description-less rows, then applies the free heuristic).")
        return

    cols = st.columns(len(term.TERM_VERDICTS))
    for col, v in zip(cols, term.TERM_VERDICTS):
        col.metric(v.title(), counts.get(v, 0), help=_TERM_VERDICT_HELP[v])

    chosen = st.multiselect("Show verdicts", list(term.TERM_VERDICTS),
                            default=["EXPLICIT", "LIKELY", "POSSIBLE"], key="term-facet")
    rows = conn.execute(
        "SELECT company, title, location, url, term_verdict, term_season, term_year, "
        "term_evidence, term_source, posted_at, first_seen FROM postings "
        "WHERE is_active = 1 AND term_verdict IS NOT NULL "
        "ORDER BY first_seen DESC, company"
    ).fetchall()
    rows = [r for r in rows if r["term_verdict"] in chosen] if chosen else []
    st.caption(f"{len(rows)} posting(s)")
    st.dataframe(
        [{"Posted": display.format_release(r["posted_at"], r["first_seen"]),
          "Verdict": r["term_verdict"],
          "Term": (f"{(r['term_season'] or '').title()} {r['term_year']}".strip()
                   if r["term_year"] else "—"),
          "Company": r["company"], "Title": r["title"], "Location": r["location"] or "—",
          "Evidence": r["term_evidence"] or "—", "Src": r["term_source"], "Apply": r["url"]}
         for r in rows],
        width="stretch", hide_index=True,
        column_order=["Posted", "Verdict", "Term", "Company", "Title", "Location",
                      "Evidence", "Src", "Apply"],
        column_config={"Apply": st.column_config.LinkColumn("Apply", display_text="open ↗")},
    )

    gray = counts.get("POSSIBLE", 0) + counts.get("UNREADABLE", 0)
    if gray:
        st.divider()
        st.markdown(f"**{gray} posting(s) need a judgment call** (POSSIBLE / UNREADABLE).")
        st.caption("Upgrade them for **free** in an interactive Claude Code session — run "
                   "**/classify-terms** (uses your subscription, no per-token charge):")
        st.code(".venv/bin/python -m internshelper.term list-candidates", language="bash")
        if settings.term_llm_enabled:
            st.warning("In-app metered upgrade is **enabled** (`[term.llm]`). "
                       "`term classify --llm` bills your Anthropic API account per token.")
        else:
            st.caption("In-app LLM upgrade is off (`[term.llm] enabled = false`). It would be "
                       "pay-per-token; the free skill above is preferred.")


def _health_tab(conn) -> None:
    st.subheader("Health")
    total, cand = store.pending_counts(conn)
    st.caption(f"Pending review: {total} ({cand} candidates)")
    rows = store.source_health(conn)
    if not rows:
        st.info("No runs recorded yet.")
        return
    quiet = [r for r in rows if r["quiet"]]
    if quiet:
        st.warning(
            "⚠️ Source(s) went quiet — "
            + " · ".join(f"**{r['source_key']}** ({r['quiet_reason']})" for r in quiet)
        )
    st.dataframe(
        [{"Source": r["source_key"], "Last run": r["started_at"],
          "OK": "✅" if r["ok"] else "❌", "Quiet": "⚠️" if r["quiet"] else "",
          "Count": r["count"], "Dropped": r["dropped"], "Error": r["error"]}
         for r in rows],
        width="stretch", hide_index=True,
    )


@st.cache_data
def _eval_scorecard() -> list[dict]:
    """Run the offline accuracy harness once and cache it (deterministic, sub-second). The
    Re-run button clears this cache so it always reflects the current parser code."""
    from internshelper.eval import score_all

    def pct(v):
        return "—" if v is None else f"{v * 100:.0f}%"

    out = []
    for r in score_all():
        out.append({
            "Connector": r.connector, "Case": r.case,
            "Exp": r.expected_n, "Got": r.parsed_n,
            "Recall": pct(r.recall), "Precision": pct(r.precision),
            "Title": pct(r.field_acc("title")), "Company": pct(r.field_acc("company")),
            "Loc": pct(r.field_acc("location")), "URL": pct(r.field_acc("url")),
            "Date": pct(r.field_acc("posted_at")), "Desc": pct(r.field_acc("description_present")),
            "Storage": "✅" if r.storage_ok else "❌",
            "_perfect": r.is_perfect(), "_recall": r.recall, "_precision": r.precision,
        })
    return out


def _eval_tab(conn) -> None:  # conn unused — the harness is self-contained — kept for tab symmetry
    st.subheader("Accuracy")
    st.caption("Offline extraction-accuracy harness: each connector parses saved fixture pages, "
               "scored against hand-labeled goldens — role recall/precision + per-field correctness.")
    if st.button("Re-run harness", key="eval-rerun"):
        _eval_scorecard.clear()
        st.rerun()
    try:
        rows = _eval_scorecard()
    except Exception as e:  # never let a harness error blank the tab
        st.error(f"Eval harness failed to run: {e}")
        return
    if not rows:
        st.info("No eval fixtures found under tests/fixtures/eval/.")
        return

    n = len(rows)
    perfect = sum(1 for r in rows if r["_perfect"])
    macro_recall = sum(r["_recall"] for r in rows) / n
    macro_prec = sum(r["_precision"] for r in rows) / n
    c1, c2, c3 = st.columns(3)
    c1.metric("Fixtures perfect", f"{perfect}/{n}")
    c2.metric("Recall", f"{macro_recall:.0%}")
    c3.metric("Precision", f"{macro_prec:.0%}")

    failing = [r for r in rows if not r["_perfect"]]
    if failing:
        st.warning("⚠️ Accuracy regressions — "
                   + " · ".join(f"**{r['Connector']}/{r['Case']}**" for r in failing))
    else:
        st.success("All fixtures at 100%.")

    st.dataframe(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        width="stretch", hide_index=True,
    )


def _sources_path() -> str:
    return config.default_path("INTERNSHELPER_SOURCES", "config/sources.yaml")


def _stage_preview(entry) -> None:
    """Live fetch-test an entry and stash the result as a pending preview."""
    try:
        count, titles, warnings = sources.fetch_test(entry, limit=8)
        fetch_error = None
    except Exception as e:  # network/HTTP failure — surface, don't crash the tab
        count, titles, warnings, fetch_error = 0, [], [], f"{type(e).__name__}: {e}"
    st.session_state["src_preview"] = {
        "entry": entry, "count": count, "titles": titles,
        "warnings": warnings, "fetch_error": fetch_error,
    }


def _add_source_form(path: str) -> None:
    """Step 1: detect (or sniff) + live fetch-test, then stage a preview in session_state."""
    with st.form("src-add"):
        url = st.text_input("Board URL", key="src-url",
                            placeholder="https://boards.greenhouse.io/stripe")
        label = st.text_input("Label (optional)", key="src-label")
        tmm_raw = st.text_input("title_must_match (comma-separated, optional)", key="src-tmm")
        cols_raw = st.text_input("columns k=v (markdown only, optional)", key="src-cols")
        submitted = st.form_submit_button("Detect & test")
    if not submitted:
        return

    tmm = [s.strip() for s in tmm_raw.split(",") if s.strip()] or None
    cols = sources._parse_kv(cols_raw) or None
    try:
        entry = sources.resolve_entry(url, label=label or None, title_must_match=tmm, columns=cols)
    except SourceDetectionError as e:
        # A bare github.com repo resolves to its job-list files (multi-select); any other
        # unrecognized URL falls through to the careers-page sniffer (single-pick).
        host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
        if host == "github.com":
            try:
                files = github_repo.resolve_repo(url, label=label or None)
            except github_repo.GitHubRepoError as ge:
                st.error(f"Couldn't resolve that repo: {ge}")
                return
            if files:
                st.session_state["src_candidates"] = {
                    "kind": "repo", "candidates": files, "tmm": tmm, "cols": cols}
                st.rerun()
            st.error(f"No job-list files (.md/.json) found in that repo.")
            return
        try:
            candidates = sniffer.sniff_careers_page(url, label=label or None)
        except sniffer.SnifferError:
            candidates = []
        if candidates:
            st.session_state["src_candidates"] = {
                "kind": "sniffer", "candidates": candidates, "tmm": tmm, "cols": cols}
            st.rerun()
        st.error(f"Couldn't detect a source from that URL: {e}")
        return

    if sources.is_duplicate(path, entry):
        st.warning(f"Already present: {entry.source_key}")
        return
    _stage_preview(entry)
    st.rerun()


def _add_source_candidates(path: str) -> None:
    """Resolve a multi-candidate URL: a github repo (multi-select its list files) or a careers
    page (single-pick an embedded board)."""
    data = st.session_state["src_candidates"]
    if data.get("kind") == "repo":
        _add_repo_candidates(path, data)
    else:
        _add_sniffer_candidate(path, data)


def _apply_extras(entry, data) -> None:
    if data["tmm"]:
        entry.title_must_match = list(data["tmm"])
    if data["cols"]:
        entry.columns = dict(data["cols"])


def _add_repo_candidates(path: str, data: dict) -> None:
    """Bare-repo fallback: the repo's list files, multi-selectable so the user adds only the ones
    they want (the "ask which, don't flood" UX). Each chosen file is fetch-tested + appended."""
    cands = data["candidates"]
    st.info(f"That repo has {len(cands)} job-list file(s) — pick the ones to add.")
    labels = {f"{c.label}  ({c.type})  {c.token}": c for c in cands}
    chosen = st.multiselect("Add which list file(s)?", list(labels), key="src-repo-pick")
    c1, c2 = st.columns(2)
    if c1.button("Add selected", key="src-repo-add"):
        if not chosen:
            st.warning("Nothing selected.")
            return
        added, skipped, failed = [], [], []
        for lbl in chosen:
            entry = labels[lbl]
            _apply_extras(entry, data)
            if sources.is_duplicate(path, entry):
                skipped.append(entry.source_key)
                continue
            try:
                count, _titles, _warnings = sources.fetch_test(entry, limit=1)
            except Exception as e:  # network/HTTP failure — record, keep going
                failed.append(f"{entry.source_key}: {type(e).__name__}: {e}")
                continue
            sources.append_source(path, entry)
            added.append(f"{entry.source_key} ({count} postings)")
        if added:
            st.success("Added: " + ", ".join(added))
        if skipped:
            st.info("Already present: " + ", ".join(skipped))
        if failed:
            st.error("Failed: " + "; ".join(failed))
        st.session_state.pop("src_candidates", None)
        if added or skipped:
            st.rerun()
    if c2.button("Cancel", key="src-repo-cancel"):
        st.session_state.pop("src_candidates", None)
        st.rerun()


def _add_sniffer_candidate(path: str, data: dict) -> None:
    """Sniffer fallback: let the user pick one of the embedded boards found on the page."""
    cands = data["candidates"]
    st.info(f"No direct match — found {len(cands)} embedded board(s) on that page.")
    choice = st.radio("Use which board?", [c.source_key for c in cands], key="src-cand-choice")
    c1, c2 = st.columns(2)
    if c1.button("Use this board", key="src-cand-use"):
        entry = next(c for c in cands if c.source_key == choice)
        _apply_extras(entry, data)
        if sources.is_duplicate(path, entry):
            st.warning(f"Already present: {entry.source_key}")
        else:
            _stage_preview(entry)
            st.session_state.pop("src_candidates", None)
            st.rerun()
    if c2.button("Cancel", key="src-cand-cancel"):
        st.session_state.pop("src_candidates", None)
        st.rerun()


def _add_source_preview(path: str, preview: dict) -> None:
    """Step 2: show the fetch-test result and a confirm/cancel decision."""
    entry = preview["entry"]
    st.markdown(f"Detected: `{entry.source_key}`")
    if preview["fetch_error"]:
        st.error(f"Fetch failed: {preview['fetch_error']}")
    else:
        st.write(f"Fetched **{preview['count']}** postings.")
        for t in preview["titles"]:
            st.write(f"- {t}")
    for w in preview["warnings"]:
        st.warning(w)

    # Mirror the CLI's --yes gate: an empty/degenerate fetch needs an explicit "Add anyway".
    needs_force = preview["fetch_error"] is None and (preview["count"] == 0 or preview["warnings"])
    confirm_label = "Add anyway" if needs_force else "Confirm & add"
    c1, c2 = st.columns(2)
    if c1.button(confirm_label, key="src-confirm", disabled=preview["fetch_error"] is not None):
        sources.append_source(path, entry)
        st.session_state.pop("src_preview", None)
        st.success(f"Added {entry.source_key}")
        st.rerun()
    if c2.button("Cancel", key="src-cancel"):
        st.session_state.pop("src_preview", None)
        st.rerun()


def _sources_tab(conn) -> None:
    st.subheader("Sources")
    path = _sources_path()

    try:
        entries, errors = config.load_sources(path)
    except config.ConfigError as e:
        st.error(f"Couldn't read the sources file: {e}")
        entries, errors = [], []
    for err in errors:
        st.warning(f"Malformed entry skipped: {err['reason']}")

    if entries:
        st.dataframe(
            [{"Source key": e.source_key, "Type": e.type, "Label": e.display,
              "Filter": ", ".join(e.title_must_match),
              "Columns": ", ".join(f"{k}={v}" for k, v in e.columns.items())} for e in entries],
            width="stretch", hide_index=True,
        )
        c1, c2 = st.columns([3, 1])
        key = c1.selectbox("Remove a source", [e.source_key for e in entries],
                           key="src-remove-select")
        if c2.button("Remove", key="src-remove-btn"):
            if sources.remove_source(path, key):
                st.success(f"Removed {key}")
            else:
                st.error(f"Could not remove {key}")
            st.rerun()
    else:
        st.caption("No sources configured yet. Add one below.")

    st.divider()
    st.markdown("**Add a source**")
    if st.session_state.get("src_preview") is not None:
        _add_source_preview(path, st.session_state["src_preview"])
    elif st.session_state.get("src_candidates") is not None:
        _add_source_candidates(path)
    else:
        _add_source_form(path)


def main() -> None:
    st.set_page_config(page_title="internsHELPer", page_icon="🎯", layout="wide")
    _inject_css()
    st.title("🎯 internsHELPer")
    conn = _open_conn()
    feed, terms, tracker, health, srcs, accuracy = st.tabs(
        ["Feed", "Terms", "Tracker", "Health", "Sources", "Accuracy"])
    with feed:
        _feed_tab(conn)
    with terms:
        _terms_tab(conn)
    with tracker:
        _tracker_tab(conn)
    with health:
        _health_tab(conn)
    with srcs:
        _sources_tab(conn)
    with accuracy:
        _eval_tab(conn)


if __name__ == "__main__":
    main()
