"""The Phase 1.5 v1 learned ranker: tokenizer, label weights, NB math, persistence, eval."""

import json
import math

from internshelper import db, ranking, review, store
from internshelper.models import Posting

NOW = "2026-07-10T12:00:00+00:00"


def _conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    return c


def _row(title, verdict="match", reason="", company="Stripe", source_key="greenhouse:stripe",
         description="", posted_at=None, first_seen=NOW, posting_id="g:x"):
    """A gather_labels-shaped training row (label/weight derived like _signal does)."""
    return {
        "posting_id": posting_id, "title": title, "company": company,
        "description": description, "source_key": source_key, "posted_at": posted_at,
        "first_seen": first_seen, "verdict": verdict, "verdict_reason": reason,
        "label": 1 if verdict == "match" else 0, "weight": ranking.label_weight(reason),
    }


def _labels(n_match, n_no_match, match_title="Quant Intern", no_title="Sales Manager"):
    rows = []
    for i in range(n_match):
        rows.append(_row(match_title, "match", posting_id=f"m:{i}"))
    for i in range(n_no_match):
        rows.append(_row(no_title, "no_match", posting_id=f"n:{i}"))
    return rows


# ---------- tokenize ----------

def test_tokenize_lowercases_and_keeps_symbol_tokens():
    toks = ranking.tokenize("C++ Engineer, Co-op 2027 (C#)")
    assert {"c++", "engineer", "co-op", "2027", "c#"} <= toks


def test_tokenize_drops_stopwords_short_tokens_and_dedupes():
    toks = ranking.tokenize("The Intern and the Intern of A B")
    assert toks == {"intern"}


def test_tokenize_strips_html_from_description_and_caps_length():
    toks = ranking.tokenize("Role", "<p>Backend <b>systems</b></p>" + " filler" * 400)
    assert {"backend", "systems"} <= toks
    # tokens past the 1500-char cap never enter
    long_desc = "x" * 2000 + " uniquetoken"
    assert "uniquetoken" not in ranking.tokenize("Role", long_desc)


def test_tokenize_empty_description_is_title_only():
    assert ranking.tokenize("Quant Intern", None) == {"quant", "intern"}
    assert ranking.tokenize("Quant Intern", "") == {"quant", "intern"}


# ---------- label weights ----------

def test_label_weight_classes():
    assert ranking.label_weight("bulk: non-candidate") == ranking.WEAK_LABEL_WEIGHT
    assert ranking.label_weight("bulk: guard leak") == ranking.WEAK_LABEL_WEIGHT
    assert ranking.label_weight("borderline: page unreachable") == ranking.WEAK_LABEL_WEIGHT
    assert ranking.label_weight("Summer 2026 — deep-scan (open); wrong term") == 1.0
    assert ranking.label_weight("") == 1.0
    assert ranking.label_weight(None) == 1.0


# ---------- training ----------

def test_train_rows_cold_start_below_floor():
    assert ranking.train_rows(_labels(10, 10), NOW) is None  # < MIN_STRONG_LABELS
    # enough total but one class starved
    assert ranking.train_rows(_labels(28, 2), NOW) is None


def test_train_rows_counts_weak_labels_at_quarter_weight():
    rows = _labels(20, 15)
    rows.append(_row("Weak Clear Chef", "no_match", reason="bulk: non-candidate"))
    m = ranking.train_rows(rows, NOW)
    assert m is not None
    assert m.n_match == 20.0
    assert m.n_no_match == 15.0 + ranking.WEAK_LABEL_WEIGHT
    # the weak row's unique token carries its 0.25 weight but falls below TOKEN_MIN_DF
    assert "chef" not in m.tokens
    assert m.tokens["quant"] == [20.0, 0.0]


def test_train_rows_drops_rare_tokens_but_keeps_source_prior():
    rows = _labels(20, 15)
    rows.append(_row("Singleton Zebra Role", "match",
                     source_key="lever:oneoff", posting_id="m:z"))
    m = ranking.train_rows(rows, NOW)
    assert "zebra" not in m.tokens                 # weighted df 1.0 < TOKEN_MIN_DF
    assert "source:lever:oneoff" in m.sources      # source priors are exempt from the floor
    assert not hasattr(m, "companies")             # company prior is gone from the model


# ---------- scoring ----------

def test_score_hand_computed_contribution():
    m = ranking.train_rows(_labels(20, 15), NOW)
    expected = math.log((20 + 1) / (20 + 2)) - math.log((0 + 1) / (15 + 2))
    _, contribs = ranking.score(m, _row("Quant"), NOW, recency=False)
    quant = dict(contribs)["quant"]
    assert abs(quant - expected) < 1e-9


def test_score_unseen_token_contributes_zero():
    m = ranking.train_rows(_labels(20, 15), NOW)
    _, contribs = ranking.score(
        m, _row("Nonexistentword", company="", source_key=""), NOW, recency=False)
    assert contribs == []


def test_score_orders_match_leaning_above_no_match_leaning():
    m = ranking.train_rows(_labels(20, 15), NOW)
    hi, _ = ranking.score(m, _row("Quant Intern"), NOW, recency=False)
    lo, _ = ranking.score(m, _row("Sales Manager"), NOW, recency=False)
    assert 0.0 < lo < hi < 1.0


def test_score_source_prior_shifts_direction():
    rows = _labels(20, 5)
    rows += _labels(0, 10, no_title="Whatever Role")
    for r in rows[-10:]:
        r["source_key"] = "lever:bad"
    m = ranking.train_rows(rows, NOW)
    neutral, _ = ranking.score(m, _row("Zzz", source_key=""), NOW, recency=False)
    with_bad, _ = ranking.score(m, _row("Zzz", source_key="lever:bad"), NOW, recency=False)
    assert with_bad < neutral


def test_recency_orders_identical_rows_newest_first():
    m = ranking.train_rows(_labels(20, 15), NOW)
    fresh, _ = ranking.score(m, _row("Quant Intern", posted_at="2026-07-09"), NOW)
    stale, _ = ranking.score(m, _row("Quant Intern", posted_at="2026-04-01"), NOW)
    assert fresh > stale
    no_recency, _ = ranking.score(m, _row("Quant Intern", posted_at="2026-07-09"),
                                  NOW, recency=False)
    assert no_recency < fresh


def test_explanations_sorted_by_magnitude_and_json_round_trip():
    m = ranking.train_rows(_labels(20, 15), NOW)
    _, contribs = ranking.score(m, _row("Quant Sales Intern"), NOW, recency=False)
    mags = [abs(v) for _, v in contribs]
    assert mags == sorted(mags, reverse=True)
    parsed = json.loads(ranking.top_reasons(contribs, limit=2))
    assert len(parsed) == 2 and all(len(p) == 2 for p in parsed)


# ---------- gather_labels ----------

def _seed_one(c, pid, title="SWE Intern", company="Stripe"):
    store.upsert(c, Posting(posting_id=pid, source_key="greenhouse:stripe", title=title,
                            company=company, url=f"https://x/{pid}"), now=NOW)


def test_gather_labels_precedence_application_beats_verdict(tmp_path):
    c = _conn(tmp_path)
    _seed_one(c, "g:1")
    review.set_verdict(c, "g:1", "no_match", "dismissed: board", now=NOW)
    store.set_application_status(c, "g:1", "Applied", applied_date_if_empty="2026-07-11")
    (row,) = ranking.gather_labels(c)
    assert row["label"] == 1 and row["weight"] == 1.0     # applied wins over dismissed
    assert row["labeled_at"] == "2026-07-11"


def test_gather_labels_pin_direction_and_weights(tmp_path):
    c = _conn(tmp_path)
    for pid in ("g:promo", "g:demo", "g:weak", "g:silent"):
        _seed_one(c, pid)
    c.execute("UPDATE postings SET pinned_tier='top_target', tier_before_pin='unclassified',"
              " pinned_at='2026-07-11T09:00:00+00:00' WHERE posting_id='g:promo'")
    c.execute("UPDATE postings SET pinned_tier='unclassified', tier_before_pin='known',"
              " pinned_at='2026-07-11T09:01:00+00:00' WHERE posting_id='g:demo'")
    c.commit()
    review.set_verdict(c, "g:weak", "no_match", "bulk: guard leak", now=NOW)
    by_id = {r["posting_id"]: r for r in ranking.gather_labels(c)}
    assert "g:silent" not in by_id                        # unlabeled rows never train
    assert by_id["g:promo"]["label"] == 1 and by_id["g:promo"]["weight"] == 1.0
    assert by_id["g:demo"]["label"] == 0
    assert by_id["g:weak"]["weight"] == ranking.WEAK_LABEL_WEIGHT
    # Rejected is positive: the user chose to apply.
    store.set_application_status(c, "g:demo", "Rejected")
    by_id = {r["posting_id"]: r for r in ranking.gather_labels(c)}
    assert by_id["g:demo"]["label"] == 1


def test_gather_labels_sorted_by_labeled_at(tmp_path):
    c = _conn(tmp_path)
    _seed_one(c, "g:old")
    _seed_one(c, "g:new")
    review.set_verdict(c, "g:new", "match", "", now="2026-07-11T10:00:00+00:00")
    review.set_verdict(c, "g:old", "match", "", now="2026-07-01T10:00:00+00:00")
    assert [r["posting_id"] for r in ranking.gather_labels(c)] == ["g:old", "g:new"]


def test_gather_labels_flag_is_not_a_signal(tmp_path):
    c = _conn(tmp_path)
    _seed_one(c, "g:1")
    review.flag(c, "g:1", "link shows nothing", now="2026-07-11T10:00:00+00:00")
    assert ranking.gather_labels(c) == []  # suspect data never trains the ranker


# ---------- rescore_inbox ----------

def _seed_history(c, n_match=20, n_no_match=15):
    """Interleaved match/no_match history with increasing reviewed_at timestamps,
    so a time-ordered eval split keeps both classes in the test slice."""
    jobs = [("m", i, "match", "Quant Intern", "yes", True) for i in range(n_match)]
    jobs += [("n", i, "no_match", "Sales Manager", "not for me", False) for i in range(n_no_match)]
    jobs.sort(key=lambda j: (j[1], j[0]))  # m:0, n:0, m:1, n:1, ...
    for k, (kind, i, verdict, title, reason, cs) in enumerate(jobs):
        ts = f"2026-07-10T{k // 60:02d}:{k % 60:02d}:00+00:00"
        store.upsert(c, Posting(posting_id=f"{kind}:{i}", source_key="greenhouse:stripe",
                                title=title, company="Stripe", url=f"https://x/{kind}{i}",
                                is_cs_relevant=cs), now=NOW)
        review.set_verdict(c, f"{kind}:{i}", verdict, reason, now=ts)


def test_rescore_inbox_persists_scores_and_model(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c)  # 20 match + 15 no_match
    store.upsert(c, Posting(posting_id="p:1", source_key="greenhouse:stripe",
                            title="Quant Intern", company="Stripe", url="https://x/p1",
                            is_cs_relevant=True), now=NOW)
    n = ranking.rescore_inbox(c, NOW)
    assert n == 21  # everything not dismissed: 20 old matches + the new arrival
    row = c.execute("SELECT rank_score, rank_reasons FROM postings "
                    "WHERE posting_id='p:1'").fetchone()
    assert row["rank_score"] is not None and 0 < row["rank_score"] < 1
    assert "quant" in row["rank_reasons"]
    # old matches are inbox rows now — scored; dismissed rows never are
    assert c.execute("SELECT rank_score FROM postings WHERE posting_id='m:0'"
                     ).fetchone()["rank_score"] is not None
    assert c.execute("SELECT rank_score FROM postings WHERE posting_id='n:0'"
                     ).fetchone()["rank_score"] is None
    assert json.loads(db.get_meta(c, ranking.MODEL_META_KEY))["trained_at"] == NOW


def test_rescore_inbox_cold_start_nulls_scores(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c, n_match=3, n_no_match=3)  # below the floor
    store.upsert(c, Posting(posting_id="p:1", source_key="greenhouse:stripe",
                            title="Quant Intern", company="Stripe", url="https://x/p1"),
                 now=NOW)
    c.execute("UPDATE postings SET rank_score=0.9, rank_reasons='[]' WHERE posting_id='p:1'")
    c.commit()
    ranking.rescore_inbox(c, NOW)
    row = c.execute("SELECT rank_score, rank_reasons FROM postings "
                    "WHERE posting_id='p:1'").fetchone()
    assert row["rank_score"] is None and row["rank_reasons"] is None


def test_rescore_inbox_is_idempotent(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c)
    store.upsert(c, Posting(posting_id="p:1", source_key="greenhouse:stripe",
                            title="Quant Intern", company="Stripe", url="https://x/p1"),
                 now=NOW)
    ranking.rescore_inbox(c, NOW)
    first = c.execute("SELECT rank_score, rank_reasons FROM postings "
                      "WHERE posting_id='p:1'").fetchone()
    ranking.rescore_inbox(c, NOW)
    second = c.execute("SELECT rank_score, rank_reasons FROM postings "
                       "WHERE posting_id='p:1'").fetchone()
    assert tuple(first) == tuple(second)


# ---------- evaluate ----------

def test_evaluate_reports_metrics_and_beats_nothing_up_its_sleeve(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c, n_match=30, n_no_match=30)
    out = ranking.evaluate(c, holdout=0.25, ks=(5,))
    assert out["status"] == "ok"
    assert out["train_size"] == 45 and out["test_size"] == 15
    assert "precision_at_5" in out and "baseline_precision_at_5" in out
    assert out["auc"] is not None
    out2 = ranking.evaluate(c, holdout=0.25, ks=(5,))
    assert out == out2  # deterministic


def test_evaluate_excludes_weak_labels_from_test(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c, n_match=30, n_no_match=30)
    # newest verdicts (the test slice) include a weak bulk row
    store.upsert(c, Posting(posting_id="w:1", source_key="greenhouse:stripe",
                            title="Chef", company="Bistro", url="https://x/w1"),
                 now="2026-07-10T13:00:00+00:00")
    review.set_verdict(c, "w:1", "no_match", "bulk: non-candidate",
                       now="2026-07-10T13:00:00+00:00")
    out = ranking.evaluate(c, holdout=0.25, ks=(5,))
    assert out["test_weak_dropped"] >= 1


def test_evaluate_cold_start_shape(tmp_path):
    c = _conn(tmp_path)
    _seed_history(c, n_match=4, n_no_match=4)
    out = ranking.evaluate(c)
    assert out["status"] == "cold_start"


def test_evaluate_auc_bounds():
    # perfectly separable -> 1.0; constant score -> 0.5
    assert ranking._auc([(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]) == 1.0
    assert ranking._auc([(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]) == 0.5


# ---------- CLI ----------

def test_cli_retrain_show_eval(tmp_path, capsys, monkeypatch):
    c = _conn(tmp_path)
    _seed_history(c, n_match=30, n_no_match=30)
    store.upsert(c, Posting(posting_id="p:1", source_key="greenhouse:stripe",
                            title="Quant Intern", company="Stripe", url="https://x/p1"),
                 now=NOW)
    c.close()
    monkeypatch.setenv("INTERNSHELPER_DB", str(tmp_path / "t.db"))

    assert ranking.main(["retrain"]) == 0
    assert "rescored 31" in capsys.readouterr().out  # 30 matches + the pending row
    assert ranking.main(["show"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_strong"] == 60
    assert ranking.main(["explain", "p:1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] > 0.5 and out["contributions"]
    assert ranking.main(["eval", "--k", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and "precision_at_5" in out
