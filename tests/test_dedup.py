"""Cross-source de-duplication: URL keying, auto-collapse, fuzzy suggestions, review actions."""

from internshelper import db, dedup, review, store
from internshelper.models import Posting

NOW = "2026-07-15T12:00:00+00:00"


def _db(tmp_path):
    c = db.connect(tmp_path / "d.db")
    db.init_db(c)
    return c


def _add(c, pid, *, url, company="SIG", title="SWE Intern", source="markdown:a"):
    store.upsert(
        c,
        Posting(posting_id=pid, source_key=source, title=title, company=company, url=url,
                is_internship=True, is_cs_relevant=True),
        now=NOW,
    )


# ---------- url_dedup_key ----------

def test_url_key_collapses_sig_variants_ignoring_prefix_and_query():
    k = dedup.url_dedup_key
    a = k("https://careers.sig.com/jobs/10838")
    b = k("https://careers.sig.com/intern-co-op-technology/jobs/10838?lang=en-us&utm_source=x")
    c = k("https://careers.sig.com/intern-co-op/jobs/10838")
    assert a == b == c == "careers.sig.com#10838"


def test_url_key_distinguishes_different_job_ids_and_strips_www():
    k = dedup.url_dedup_key
    assert k("https://careers.sig.com/job/10717/Quant-Trader-Summer-2027") == "careers.sig.com#10717"
    assert k("https://www.careers.sig.com/jobs/10838") == "careers.sig.com#10838"
    assert k("https://careers.sig.com/jobs/10838") != k("https://careers.sig.com/jobs/10717")


def test_url_key_uses_query_id_for_generic_paths():
    # Greenhouse hosted URLs share the path `/jobs/search` and carry the id in `gh_jid` —
    # distinct jobs MUST stay distinct (regression: they were all merging on the shared path).
    k = dedup.url_dedup_key
    a = k("https://stripe.com/jobs/search?gh_jid=7718947")
    b = k("https://stripe.com/jobs/search?gh_jid=7532806")
    assert a == "stripe.com#7718947"
    assert b == "stripe.com#7532806"
    assert a != b
    # same job, id in query, different tracking wrapper -> still one key
    assert k("https://stripe.com/jobs/search?gh_jid=7718947&utm_source=x") == a


def test_url_key_drops_tracking_params_but_keeps_identity_query():
    k = dedup.url_dedup_key
    base = "https://capitalone.wd12.myworkdayjobs.com/Capital_One/job/McLean-VA/Prod_R246020-1"
    # R246020-1 isn't a >=5-digit pure-numeric segment, so this falls to canonical host/path;
    # a utm wrapper must not defeat the match.
    assert k(base) == k(base + "?utm_source=github-vansh-ouckah")


def test_url_key_ignores_four_digit_year_and_falls_back_to_canonical_path():
    k = dedup.url_dedup_key
    # "2027" (4 digits) is not treated as an ATS id; no id -> canonical host/path.
    assert k("https://boards.example.com/summer/2027") == "boards.example.com/summer/2027"
    # a non-numeric id (Lever UUID) also falls back to exact canonical URL...
    uuid = "https://jobs.lever.co/palantir/abc-def"
    assert k(uuid) == "jobs.lever.co/palantir/abc-def"
    assert k(uuid + "?utm_source=x") == k(uuid + "/")  # tracking query + trailing slash ignored
    assert k("") is None and k(None) is None


# ---------- collapse_url_duplicates ----------

def test_collapse_hides_losers_and_is_idempotent(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://careers.sig.com/jobs/10838", company="SIG", title="SWE")
    _add(c, "markdown:2", url="https://careers.sig.com/intern-co-op/jobs/10838?utm=x",
         company="Susquehanna", title="Quant Strategy Developer Intern")
    _add(c, "markdown:3", url="https://careers.sig.com/jobs/99999", company="SIG", title="QR")

    n = dedup.collapse_url_duplicates(c)
    assert n == 1  # two rows share 10838 -> one collapsed

    survivors = c.execute(
        "SELECT posting_id FROM postings WHERE duplicate_of IS NULL ORDER BY posting_id"
    ).fetchall()
    losers = c.execute(
        "SELECT posting_id, duplicate_of FROM postings WHERE duplicate_of IS NOT NULL"
    ).fetchall()
    assert len(survivors) == 2               # one of the 10838 pair + the 99999 row
    assert len(losers) == 1
    assert losers[0]["duplicate_of"] in ("markdown:1", "markdown:2")

    assert dedup.collapse_url_duplicates(c) == 0  # idempotent second run


def test_url_collapse_preserves_single_curated_application(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:applied", url="https://careers.sig.com/jobs/10838")
    _add(c, "markdown:ranked", url="https://careers.sig.com/jobs/10838?utm_source=x")
    c.execute(
        "UPDATE postings SET rank_score = 0, description = '' WHERE posting_id = ?",
        ("markdown:applied",),
    )
    c.execute(
        "UPDATE postings SET rank_score = 1, description = 'richer' WHERE posting_id = ?",
        ("markdown:ranked",),
    )
    store.set_application(
        c, "markdown:applied", status="Applied", notes="do not lose",
        applied_date="2026-08-20",
    )

    assert dedup.collapse_url_duplicates(c) == 1
    assert c.execute(
        "SELECT duplicate_of FROM postings WHERE posting_id = 'markdown:ranked'"
    ).fetchone()["duplicate_of"] == "markdown:applied"


def test_url_collapse_leaves_multiple_curated_applications_visible(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://careers.sig.com/jobs/10838")
    _add(c, "markdown:2", url="https://careers.sig.com/jobs/10838?utm_source=x")
    store.set_application(c, "markdown:1", status="Applied", notes="first")
    store.set_application(c, "markdown:2", status="Interviewing", notes="second")

    assert dedup.collapse_url_duplicates(c) == 0
    assert c.execute(
        "SELECT count(*) FROM postings WHERE duplicate_of IS NULL"
    ).fetchone()[0] == 2


def test_confirmed_duplicate_is_excluded_by_inbox_sql(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://careers.sig.com/jobs/10838")
    _add(c, "markdown:2", url="https://careers.sig.com/x/jobs/10838")
    dedup.collapse_url_duplicates(c)
    inbox_ids = {
        r["posting_id"]
        for r in c.execute(f"SELECT posting_id FROM postings WHERE {store.INBOX_SQL}")
    }
    assert len(inbox_ids) == 1  # the loser is hidden from the Inbox


# ---------- find_possible_duplicates (fuzzy) ----------

def test_fuzzy_groups_by_company_and_title_and_respects_keep(tmp_path):
    c = _db(tmp_path)
    # Same company+title, DIFFERENT hosts -> URL key won't collapse; fuzzy should suggest.
    _add(c, "markdown:1", url="https://ashbyhq.com/circleback/aaa", company="Circleback",
         title="Software Engineering Intern (Summer 2027) 🛂")
    _add(c, "markdown:2", url="https://ycombinator.com/circleback/bbb", company="Circleback",
         title="Software Engineering Intern - Summer 2027")

    groups = dedup.find_possible_duplicates(c)
    # titles differ only by punctuation/emoji/whitespace -> same title_norm bucket
    assert len(groups) == 1
    assert {r["posting_id"] for r in groups[0]["rows"]} == {"markdown:1", "markdown:2"}

    review.keep_separate(c, ["markdown:1", "markdown:2"])
    assert dedup.find_possible_duplicates(c) == []  # keep-separate silences the suggestion

    # A later arrival must be compared with the prior decision, not hidden forever.
    _add(c, "markdown:3", url="https://example.com/circleback/ccc", company="Circleback",
         title="Software Engineering Intern Summer 2027")
    groups = dedup.find_possible_duplicates(c)
    assert len(groups) == 1
    assert {r["posting_id"] for r in groups[0]["rows"]} == {
        "markdown:1", "markdown:2", "markdown:3",
    }
    review.confirm_duplicates(
        c, groups[0]["survivor_id"], [r["posting_id"] for r in groups[0]["rows"]]
    )
    assert dedup.find_kept_separate(c) == []
    losers = [
        r["posting_id"] for r in groups[0]["rows"]
        if r["posting_id"] != groups[0]["survivor_id"]
    ]
    review.undo_duplicates(c, losers, survivor_id=groups[0]["survivor_id"])
    assert dedup.find_possible_duplicates(c)
    assert {
        row["posting_id"]
        for group in dedup.find_kept_separate(c)
        for row in group["rows"]
    } == {"markdown:1", "markdown:2"}


def test_kept_separate_history_retains_rows_after_title_drift(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://a.com/x", company="Acme", title="Data Intern")
    _add(c, "markdown:2", url="https://b.com/y", company="Acme", title="Data Intern")
    review.keep_separate(c, ["markdown:1", "markdown:2"])
    c.execute("UPDATE postings SET title = 'Data Science Intern' WHERE posting_id = 'markdown:2'")
    c.commit()

    history_ids = {
        row["posting_id"]
        for group in dedup.find_kept_separate(c)
        for row in group["rows"]
    }
    assert history_ids == {"markdown:1", "markdown:2"}


def test_possible_duplicate_order_is_stable_across_pagination_slices(tmp_path):
    c = _db(tmp_path)
    for index in reversed(range(27)):
        for variant in ("a", "b"):
            _add(
                c,
                f"markdown:{index:02d}:{variant}",
                url=f"https://{variant}.example/{index}",
                company=f"Company {index:02d}",
                title="Software Intern",
            )

    first = dedup.find_possible_duplicates(c)
    second = dedup.find_possible_duplicates(c)
    first_ids = [group["rows"][0]["posting_id"] for group in first]
    assert first_ids == [group["rows"][0]["posting_id"] for group in second]
    assert len(first_ids) == 27
    assert set(first_ids[:25]).isdisjoint(first_ids[25:])


def test_confirm_then_undo_roundtrip(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://a.com/x", company="Acme", title="Data Intern")
    _add(c, "markdown:2", url="https://b.com/y", company="Acme", title="Data Intern")

    n = review.confirm_duplicates(c, "markdown:1", ["markdown:1", "markdown:2"])
    assert n == 1  # survivor not pointed at itself
    assert c.execute("SELECT duplicate_of FROM postings WHERE posting_id='markdown:2'"
                     ).fetchone()["duplicate_of"] == "markdown:1"

    review.undo_duplicate(c, "markdown:2")
    assert c.execute("SELECT duplicate_of FROM postings WHERE posting_id='markdown:2'"
                     ).fetchone()["duplicate_of"] is None


def test_fuzzy_merge_preserves_one_application_and_blocks_two(tmp_path):
    c = _db(tmp_path)
    _add(c, "markdown:1", url="https://a.com/x", company="Acme", title="Data Intern")
    _add(c, "markdown:2", url="https://b.com/y", company="Acme", title="Data Intern")
    store.set_application(c, "markdown:2", status="Applied", notes="curated")

    group = dedup.find_possible_duplicates(c)[0]
    assert group["survivor_id"] == "markdown:2"
    assert group["merge_blocked"] is False

    store.set_application(c, "markdown:1", status="Interviewing", notes="also curated")
    group = dedup.find_possible_duplicates(c)[0]
    assert group["merge_blocked"] is True
    try:
        review.confirm_duplicates(
            c, group["survivor_id"], [r["posting_id"] for r in group["rows"]]
        )
    except ValueError as exc:
        assert "saved application records cannot be merged safely" in str(exc)
    else:
        raise AssertionError("merge should reject multiple curated applications")
    assert review.keep_separate(c, ["markdown:1", "markdown:2"]) == 2
