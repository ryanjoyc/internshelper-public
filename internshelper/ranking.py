"""Learned ranking: a transparent naive-Bayes fit-likelihood scorer.

Ranking only, never gating — the score orders the Inbox within its tiers; it never
hides, filters, or auto-decides. Training labels come from what the user actually
does (`gather_labels`, precedence application > tier pin > verdict): applying —
including later rejections — is positive, dismissing or demoting is negative, and
the historical match/no_match verdicts remain as seed data. Per-term weights over
title+description tokens, plus a per-source prior, are learned as a weighted
Bernoulli NB, with a fixed deterministic recency bonus on top. There is deliberately
no company prior — fit is judged on the role, and company preference lives in the
tier system — so dismissing one role type at a company never drags down its others.
Cold start
(< MIN_STRONG_LABELS full-weight labels, or < MIN_CLASS_LABELS per class) leaves
rank_score NULL and the Inbox falls back to the keyword candidates-first heuristic.

Scores are persisted onto inbox rows (`rank_score`, `rank_reasons`) at retrain
time so paging stays stable between requests; the model itself is stored in
meta['rank_model'] for inspection (`show` / `explain`), never read on the hot path.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone

from internshelper import clock, config, db, store, text
from internshelper.dotenv import load_dotenv

MIN_STRONG_LABELS = 30      # cold-start floor: full-weight verdicts needed to train
MIN_CLASS_LABELS = 5        # ...and at least this many of EACH class among them
WEAK_LABEL_WEIGHT = 0.25    # bulk-clear + "borderline:" no_matches
TOKEN_MIN_DF = 2.0          # weighted doc-frequency floor for a token to enter the model
ALPHA = 1.0                 # Laplace smoothing
RECENCY_WEIGHT = 0.5        # max logit bonus for a brand-new posting
RECENCY_HORIZON_DAYS = 60   # linear decay to 0 over this window
LIKELY_MATCH_THRESHOLD = 0.65  # UI badge cutoff (on the sigmoid output)
TIER_HIGH = 0.90            # review tier cutoffs (sigmoid output): >= HIGH is
TIER_LOW = 0.35             # "near-certain", < LOW is "probably not"
TOP_REASONS = 4             # contributors persisted per row

MODEL_META_KEY = "rank_model"

# Weak-label reason prefixes: mass keyword clears and term-verification "borderline"
# demotions are directionally useful but are NOT per-item preference judgments — at
# full weight they would drown the genuine signal (or poison good tokens).
_WEAK_PREFIXES = ("bulk:", "borderline:")

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#-]*")  # keeps c++, c#, co-op, 2027
_STOP = frozenset({
    "the", "and", "of", "for", "in", "to", "a", "at", "on", "with",
    "or", "an", "is", "are", "be", "as", "by", "we", "our", "you", "your",
})
_DESCRIPTION_CAP = 1500  # chars — one verbose JD must not inject hundreds of weak tokens


def tokenize(title: str, description: str | None = None) -> set[str]:
    """Whole-word presence set over lowercased title (+ HTML-stripped description).

    Bernoulli presence, not counts: with a few hundred labels, term frequency is
    noise, and presence makes the explanation ("↑ quant") honest. Empty description
    → title-only; we never read payload files here (training rows are almost all
    description-less, so payload-only tokens would have no learned weights).
    """
    toks = set(_TOKEN.findall((title or "").lower()))
    if description:
        toks |= set(_TOKEN.findall(text.strip_html(description).lower()[:_DESCRIPTION_CAP]))
    return {t for t in toks if len(t) >= 2 and t not in _STOP}


def label_weight(verdict_reason: str | None) -> float:
    reason = verdict_reason or ""
    if any(reason.startswith(p) for p in _WEAK_PREFIXES):
        return WEAK_LABEL_WEIGHT
    return 1.0


@dataclass
class Model:
    trained_at: str
    n_match: float = 0.0        # weighted class totals
    n_no_match: float = 0.0
    n_strong: int = 0           # raw full-weight label count (cold-start bookkeeping)
    tokens: dict = field(default_factory=dict)     # token -> [match_w, no_match_w]
    sources: dict = field(default_factory=dict)    # source_key -> [m, n]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Model":
        # Tolerate keys from older persisted models (e.g. the dropped company prior).
        d = json.loads(s)
        keep = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in keep})


def _row_features(row) -> list[tuple[str, str]]:
    """(table, key) feature pairs for one posting row (tokens + source prior).

    No company prior: fit is judged on the role (title/JD tokens), never the company.
    Company preference lives in the separate company-group system, so
    dismissing one role type at a company must not drag down its other roles.
    """
    feats = [("tokens", t) for t in tokenize(row["title"], row["description"] or "")]
    if row["source_key"]:
        feats.append(("sources", f"source:{row['source_key']}"))
    return feats


# Tier order for pin direction: pinning to a LOWER index than the tier the card sat
# in is a promotion (positive label); higher is a demotion (negative).
_TIER_RANK = {"top_target": 0, "known": 1, "discovery": 2, "unclassified": 3}


def _signal(r) -> tuple[int, float, str | None] | None:
    """(label, weight, labeled_at) for one joined posting row, or None if unlabeled.

    Precedence application > pin > verdict: a dismissed-then-applied posting counts
    positive, and a pin overrides the seed verdict it contradicts.
    """
    if r["app_status"] in store.PIPELINE_STATUSES:
        return 1, 1.0, r["applied_date"] or r["first_seen"]
    pin = _TIER_RANK.get(r["pinned_tier"] or "")
    before = _TIER_RANK.get(r["tier_before_pin"] or "")
    if pin is not None and before is not None and pin != before:
        return (1 if pin < before else 0), 1.0, r["pinned_at"]
    if r["verdict"] in ("match", "no_match"):
        return (
            1 if r["verdict"] == "match" else 0,
            label_weight(r["verdict_reason"]),
            r["reviewed_at"],
        )
    return None


def gather_labels(conn: sqlite3.Connection) -> list[dict]:
    """The unified training set: one row per labeled posting, ordered by labeled_at.

    Each row carries the posting's features plus `label` (1 = positive), `weight`,
    and `labeled_at` — the single source of truth for train() and evaluate().
    """
    rows = conn.execute(
        "SELECT p.posting_id, p.title, p.company, p.description, p.source_key, "
        "p.posted_at, p.first_seen, p.is_cs_relevant, p.is_internship, p.is_newgrad, "
        "p.verdict, p.verdict_reason, p.reviewed_at, "
        "p.pinned_tier, p.tier_before_pin, p.pinned_at, "
        "a.status AS app_status, a.applied_date "
        "FROM postings p LEFT JOIN applications a ON a.posting_id = p.posting_id"
    ).fetchall()
    out = []
    for r in rows:
        sig = _signal(r)
        if sig is None:
            continue
        label, weight, at = sig
        d = dict(r)
        d.update(label=label, weight=weight, labeled_at=at or r["first_seen"])
        out.append(d)
    out.sort(key=lambda d: (d["labeled_at"] or "", d["posting_id"]))
    return out


def train_rows(rows: list, now: str) -> Model | None:
    """Fit the weighted Bernoulli NB counts from `gather_labels`-shaped rows
    (each has `label` and `weight`); None = cold start (too few strong labels)."""
    strong_m = sum(1 for r in rows if r["weight"] == 1.0 and r["label"])
    strong_n = sum(1 for r in rows if r["weight"] == 1.0 and not r["label"])
    if strong_m + strong_n < MIN_STRONG_LABELS or min(strong_m, strong_n) < MIN_CLASS_LABELS:
        return None

    model = Model(trained_at=now, n_strong=strong_m + strong_n)
    for r in rows:
        w = r["weight"]
        cls = 0 if r["label"] else 1
        if cls == 0:
            model.n_match += w
        else:
            model.n_no_match += w
        for table, key in _row_features(r):
            counts = getattr(model, table).setdefault(key, [0.0, 0.0])
            counts[cls] += w
    # Rare tokens are noise — drop below the weighted doc-frequency floor. Source
    # priors are few and deliberate; they stay regardless of count.
    model.tokens = {
        t: c for t, c in model.tokens.items() if c[0] + c[1] >= TOKEN_MIN_DF
    }
    return model


def train(conn: sqlite3.Connection, now: str) -> Model | None:
    return train_rows(gather_labels(conn), now)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recency_bonus(row, now: str) -> float:
    """Fixed deterministic freshness bonus — not learned (learned recency would
    confound "new" with "unreviewed")."""
    ts = _parse_ts(row["posted_at"]) or _parse_ts(row["first_seen"])
    ref = _parse_ts(now)
    if ts is None or ref is None:
        return 0.0
    age_days = max((ref - ts).total_seconds() / 86400.0, 0.0)
    return RECENCY_WEIGHT * max(0.0, 1.0 - age_days / RECENCY_HORIZON_DAYS)


def _contribution(counts: list, n_match: float, n_no_match: float) -> float:
    c_m, c_n = counts
    return math.log((c_m + ALPHA) / (n_match + 2 * ALPHA)) - math.log(
        (c_n + ALPHA) / (n_no_match + 2 * ALPHA)
    )


def score(model: Model, row, now: str, recency: bool = True) -> tuple[float, list]:
    """(sigmoid(log-likelihood-ratio), contributions) for one posting row.

    Present-features-only (a ranking score, not a full Bernoulli likelihood):
    tokens absent from the model contribute exactly 0. Contributions are
    (label, value) pairs sorted by |value| descending — the explanation.
    """
    contribs: list[tuple[str, float]] = []
    for table, key in _row_features(row):
        counts = getattr(model, table).get(key)
        if counts:
            contribs.append((key, _contribution(counts, model.n_match, model.n_no_match)))
    if recency:
        bonus = _recency_bonus(row, now)
        if bonus:
            contribs.append(("recent", bonus))
    contribs.sort(key=lambda kv: abs(kv[1]), reverse=True)
    llr0 = math.log((model.n_match + 1) / (model.n_no_match + 1))
    llr = llr0 + sum(v for _, v in contribs)
    prob = 1.0 / (1.0 + math.exp(-max(min(llr, 50.0), -50.0)))
    return prob, contribs


def top_reasons(contribs: list, limit: int = TOP_REASONS) -> str:
    return json.dumps([[k, round(v, 3)] for k, v in contribs[:limit]])


# Every non-dismissed posting gets a score (pipeline rows ride along — harmless, and
# simpler than excluding them). Dismissed rows keep whatever score they had.
_INBOX_SQL = store.INBOX_SQL


def rescore_inbox(conn: sqlite3.Connection, now: str) -> int:
    """Retrain from scratch and persist a score onto every inbox row (one transaction).

    Cold start NULLs both columns on all inbox rows, so a DB that dips below the
    training floor cleanly reverts to the candidates-first heuristic. Returns the
    number of rows touched. Idempotent for fixed DB contents + `now`.
    """
    model = train(conn, now)
    if model is None:
        cur = conn.execute(
            "UPDATE postings SET rank_score = NULL, rank_reasons = NULL "
            f"WHERE {_INBOX_SQL}"
        )
        conn.commit()
        return cur.rowcount
    rows = conn.execute(
        "SELECT posting_id, title, company, description, source_key, posted_at, first_seen "
        f"FROM postings WHERE {_INBOX_SQL}"
    ).fetchall()
    updates = []
    for r in rows:
        prob, contribs = score(model, r, now)
        updates.append((prob, top_reasons(contribs), r["posting_id"]))
    conn.executemany(
        "UPDATE postings SET rank_score = ?, rank_reasons = ? WHERE posting_id = ?", updates
    )
    db.set_meta(conn, MODEL_META_KEY, model.to_json())  # commits
    return len(updates)


def _heuristic_key(row):
    """Sort key reproducing the pre-ranking queue order (the eval baseline)."""
    candidate = bool(row["is_cs_relevant"] or row["is_internship"] or row["is_newgrad"])
    return (
        not candidate,
        row["posted_at"] is None,
        _desc_str(row["posted_at"]),
        _desc_str(row["first_seen"]),
        row["posting_id"],
    )


class _desc_str(str):
    """A str that sorts descending inside an ascending tuple sort."""

    def __lt__(self, other):  # noqa: D105
        return str.__gt__(self, other)


def _auc(labeled: list) -> float | None:
    """Mann–Whitney AUC over (score, is_match) pairs; ties get average ranks."""
    pos = sum(1 for _, y in labeled if y)
    neg = len(labeled) - pos
    if not pos or not neg:
        return None
    ranked = sorted(labeled, key=lambda sy: sy[0])
    ranks: dict[int, float] = {}
    i = 0
    while i < len(ranked):
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based average rank across the tie run
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    rank_sum = sum(ranks[i] for i, (_, y) in enumerate(ranked) if y)
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def _precision_at(ordered_labels: list, ks) -> dict:
    return {
        f"precision_at_{k}": round(sum(ordered_labels[:k]) / k, 3)
        for k in ks
        if k <= len(ordered_labels)
    }


def evaluate(conn: sqlite3.Connection, holdout: float = 0.25, ks=(10, 25, 50)) -> dict:
    """Backtest: train on the older slice of the label history, grade on the newer one.

    Runs over the unified `gather_labels` set (applications + pins + verdicts), so
    the seed verdict history and the new behavioral signals grade together in one
    chronology. Time-ordered split (simulates deployment); weak labels may train but
    never grade; the recency bonus is disabled (evaluate the learned model, not the
    freshness prior). Reports precision@k + AUC vs the pre-ranking heuristic order
    as baseline. Deterministic: `now` is the max labeled_at in the data.
    """
    rows = gather_labels(conn)
    if not rows:
        return {"status": "no_data"}
    now = max((r["labeled_at"] for r in rows if r["labeled_at"]), default=None) or clock.now_iso()
    split = int(len(rows) * (1 - holdout))
    train_slice, test_slice = rows[:split], rows[split:]
    test_strong = [r for r in test_slice if r["weight"] == 1.0]
    base = {
        "train_size": len(train_slice),
        "test_size": len(test_strong),
        "test_weak_dropped": len(test_slice) - len(test_strong),
        "test_matches": sum(1 for r in test_strong if r["label"]),
    }
    model = train_rows(train_slice, now)
    if model is None:
        return {"status": "cold_start", **base}
    if not test_strong:
        return {"status": "empty_test", **base}

    scored = [(score(model, r, now, recency=False)[0], r) for r in test_strong]
    scored.sort(key=lambda sr: -sr[0])
    labels = [r["label"] for _, r in scored]
    baseline_rows = sorted(test_strong, key=_heuristic_key)
    baseline_labels = [r["label"] for r in baseline_rows]
    auc = _auc([(s, y) for (s, _), y in zip(scored, labels)])
    baseline_auc = _auc(
        [(-i, y) for i, y in enumerate(baseline_labels)]  # rank order as the "score"
    )
    return {
        "status": "ok",
        **base,
        "model_vocab": len(model.tokens),
        "trained_at": model.trained_at,
        **_precision_at(labels, ks),
        "auc": round(auc, 3) if auc is not None else None,
        **{f"baseline_{k}": v for k, v in _precision_at(baseline_labels, ks).items()},
        "baseline_auc": round(baseline_auc, 3) if baseline_auc is not None else None,
    }


def _open() -> sqlite3.Connection:
    conn = db.connect(config.default_path("INTERNSHELPER_DB", "data/internshelper.db"))
    db.init_db(conn)
    return conn


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="internshelper.ranking")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("retrain", help="retrain and rescore every inbox posting")
    sub.add_parser("show", help="print the stored model summary (JSON)")
    sub.add_parser("labels", help="the unified training labels (JSON) — debugging")
    ex = sub.add_parser("explain", help="score one posting and print its contributions")
    ex.add_argument("posting_id")
    ev = sub.add_parser("eval", help="backtest against verdict history (JSON)")
    ev.add_argument("--holdout", type=float, default=0.25)
    ev.add_argument("--k", default="10,25,50")

    args = parser.parse_args(argv)
    conn = _open()

    if args.cmd == "retrain":
        n = rescore_inbox(conn, now=clock.now_iso())
        print(f"rescored {n} inbox posting(s)")
    elif args.cmd == "labels":
        print(json.dumps([
            {"posting_id": r["posting_id"], "title": r["title"], "company": r["company"],
             "label": r["label"], "weight": r["weight"], "labeled_at": r["labeled_at"]}
            for r in gather_labels(conn)
        ], indent=2))
    elif args.cmd == "show":
        raw = db.get_meta(conn, MODEL_META_KEY)
        if not raw:
            print("no model stored — run `retrain` first (or still in cold start)")
            return 1
        m = json.loads(raw)
        print(json.dumps({
            "trained_at": m["trained_at"],
            "n_match": m["n_match"], "n_no_match": m["n_no_match"],
            "n_strong": m["n_strong"], "vocab": len(m["tokens"]),
            "sources": len(m["sources"]),
        }, indent=2))
    elif args.cmd == "explain":
        now = clock.now_iso()
        model = train(conn, now)
        if model is None:
            print("cold start — not enough strong verdicts to train")
            return 1
        row = conn.execute(
            "SELECT posting_id, title, company, description, source_key, posted_at, "
            "first_seen FROM postings WHERE posting_id = ?", (args.posting_id,)
        ).fetchone()
        if row is None:
            print(f"unknown posting {args.posting_id!r}")
            return 1
        prob, contribs = score(model, row, now)
        print(json.dumps({
            "posting_id": row["posting_id"], "title": row["title"],
            "score": round(prob, 3),
            "contributions": [[k, round(v, 3)] for k, v in contribs],
        }, indent=2))
    elif args.cmd == "eval":
        ks = tuple(int(k) for k in args.k.split(","))
        print(json.dumps(evaluate(conn, holdout=args.holdout, ks=ks), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
