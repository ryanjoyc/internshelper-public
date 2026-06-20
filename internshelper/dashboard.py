"""Streamlit dashboard: Feed / Tracker / Health.

Run from the repo root:  streamlit run internshelper/dashboard.py
All data logic lives in (and is tested via) internshelper.store / internshelper.review;
this file is UI only.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from internshelper import config, db, display, review, store
from internshelper.dotenv import load_dotenv

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


def _feed_tab(conn) -> None:
    st.subheader("Feed")
    total, cand = store.pending_counts(conn)
    st.metric("Pending review", total, help=f"{cand} keyword-candidate, {total - cand} other")
    if total:
        st.caption("Open this repo in Claude Code and run **/review-internships** to classify them.")

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
        rows = review.list_pending(conn)
        st.caption(f"{len(rows)} pending (keyword-candidates first)")
        st.dataframe(
            [{"Posted": display.format_release(r["posted_at"], r["first_seen"]),
              "Company": r["company"], "Title": r["title"], "Location": r["location"],
              "Candidate": bool(r["is_cs_relevant"] or r["is_internship"] or r["is_newgrad"]),
              "Apply": r["url"]} for r in rows],
            width="stretch", hide_index=True,
            column_order=["Posted", "Company", "Title", "Location", "Candidate", "Apply"],
            column_config={"Apply": st.column_config.LinkColumn("Apply", display_text="open ↗")},
        )

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


def _health_tab(conn) -> None:
    st.subheader("Health")
    total, cand = store.pending_counts(conn)
    st.caption(f"Pending review: {total} ({cand} candidates)")
    rows = store.health(conn)
    if not rows:
        st.info("No runs recorded yet.")
        return
    st.dataframe(
        [{"Source": r["source_key"], "Last run": r["started_at"],
          "OK": "✅" if r["ok"] else "❌", "Count": r["count"], "Error": r["error"]}
         for r in rows],
        width="stretch", hide_index=True,
    )


def main() -> None:
    st.set_page_config(page_title="internsHELPer", page_icon="🎯", layout="wide")
    _inject_css()
    st.title("🎯 internsHELPer")
    conn = _open_conn()
    feed, tracker, health = st.tabs(["Feed", "Tracker", "Health"])
    with feed:
        _feed_tab(conn)
    with tracker:
        _tracker_tab(conn)
    with health:
        _health_tab(conn)


if __name__ == "__main__":
    main()
