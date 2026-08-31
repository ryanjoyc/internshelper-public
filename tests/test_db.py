import sqlite3
from datetime import datetime, timedelta, timezone

from internshelper import db

V2_COLUMNS = {"payload_path", "review_status", "verdict", "verdict_reason", "reviewed_at", "posted_at"}


def _conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    return conn


def test_fresh_db_has_v2_columns(tmp_path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert V2_COLUMNS <= cols


def test_migration_adds_v2_columns_to_v1_db_without_data_loss(tmp_path):
    p = tmp_path / "v1.db"
    raw = sqlite3.connect(p)
    raw.execute(
        """CREATE TABLE postings (
            posting_id TEXT PRIMARY KEY, source_key TEXT NOT NULL, source_type TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT, location TEXT, url TEXT, description TEXT,
            is_internship INTEGER NOT NULL DEFAULT 0, is_newgrad INTEGER NOT NULL DEFAULT 0,
            is_cs_relevant INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1)"""
    )
    raw.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, first_seen, last_seen) "
        "VALUES ('greenhouse:1','greenhouse:stripe','greenhouse','Old Role','t','t')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(p)
    db.init_db(conn)  # should migrate in place

    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert V2_COLUMNS <= cols
    row = conn.execute(
        "SELECT title, review_status FROM postings WHERE posting_id='greenhouse:1'"
    ).fetchone()
    assert row["title"] == "Old Role"  # data preserved
    assert row["review_status"] == "pending"  # default applied to the migrated row
    tables = {
        item[0]
        for item in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"availability_state", "availability_evidence"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM availability_state").fetchone()[0] == 0


def test_fresh_db_has_v3_rank_columns(tmp_path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert {"rank_score", "rank_reasons"} <= cols


def test_migration_adds_v3_rank_columns_without_data_loss(tmp_path):
    p = tmp_path / "v2.db"
    raw = sqlite3.connect(p)
    raw.execute(
        """CREATE TABLE postings (
            posting_id TEXT PRIMARY KEY, source_key TEXT NOT NULL, source_type TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT, location TEXT, url TEXT, description TEXT,
            is_internship INTEGER NOT NULL DEFAULT 0, is_newgrad INTEGER NOT NULL DEFAULT 0,
            is_cs_relevant INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
            payload_path TEXT, review_status TEXT NOT NULL DEFAULT 'pending',
            verdict TEXT, verdict_reason TEXT, reviewed_at TEXT, posted_at TEXT)"""
    )
    raw.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, first_seen, last_seen) "
        "VALUES ('greenhouse:1','greenhouse:stripe','greenhouse','Old Role','t','t')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(p)
    db.init_db(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert {"rank_score", "rank_reasons"} <= cols
    row = conn.execute(
        "SELECT title, rank_score FROM postings WHERE posting_id='greenhouse:1'"
    ).fetchone()
    assert row["title"] == "Old Role"
    assert row["rank_score"] is None  # unscored until the first retrain


def test_fresh_db_runs_table_has_dropped_column(tmp_path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "dropped" in cols


def test_migration_adds_dropped_to_old_runs_table_without_data_loss(tmp_path):
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute(
        """CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            source_key TEXT NOT NULL, ok INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, error TEXT)"""
    )
    raw.execute(
        "INSERT INTO runs (started_at, source_key, ok, count) VALUES ('t','greenhouse:stripe',1,5)"
    )
    raw.commit()
    raw.close()

    conn = db.connect(p)
    db.init_db(conn)  # should add the dropped column in place

    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "dropped" in cols
    row = conn.execute("SELECT count, dropped FROM runs WHERE source_key='greenhouse:stripe'").fetchone()
    assert row["count"] == 5  # data preserved
    assert row["dropped"] == 0  # default back-filled


def test_init_db_creates_all_tables(tmp_path):
    conn = _conn(tmp_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"postings", "applications", "runs", "meta"} <= names


def test_migration_adds_replacement_authority_columns_idempotently(tmp_path):
    path = tmp_path / "legacy-availability.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """CREATE TABLE availability_state (
            posting_id TEXT PRIMARY KEY,
            source_authority TEXT NOT NULL,
            status TEXT,
            validation_completed INTEGER NOT NULL DEFAULT 0,
            last_attempt INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            next_check_at TEXT,
            pending_candidate_url TEXT,
            confirmed_url TEXT,
            replacement_confirmed_at TEXT,
            investigation_outcome TEXT NOT NULL DEFAULT 'not_run',
            investigation_stages TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )"""
    )
    raw.execute(
        "INSERT INTO availability_state (posting_id, source_authority, confirmed_url, "
        "updated_at) VALUES ('legacy:1', 'community_list', "
        "'https://jobs.example.test/replacement', 't')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(path)
    db.init_db(conn)
    db.init_db(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(availability_state)")}
    assert {"pending_candidate_authority", "confirmed_url_authority"} <= columns
    row = conn.execute(
        "SELECT confirmed_url, pending_candidate_authority, confirmed_url_authority "
        "FROM availability_state WHERE posting_id = 'legacy:1'"
    ).fetchone()
    assert row["confirmed_url"] == "https://jobs.example.test/replacement"
    assert row["pending_candidate_authority"] is None
    assert row["confirmed_url_authority"] is None


def test_replacement_authority_migration_backfills_from_candidate_evidence(tmp_path):
    conn = _conn(tmp_path)
    conn.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, first_seen, "
        "last_seen) VALUES ('legacy:1', 'greenhouse:original', 'greenhouse', "
        "'Software Intern', 't', 't')"
    )
    conn.execute(
        "INSERT INTO availability_state (posting_id, source_authority, confirmed_url, "
        "updated_at) VALUES ('legacy:1', 'first_party_ats', "
        "'https://lists.example.test/replacement', 't')"
    )
    conn.execute(
        """INSERT INTO availability_evidence (
            posting_id, record_key, sequence, observed_at, stage, channel, target,
            attempt, signal, candidate_url, authority, trustworthy, is_current
        ) VALUES (
            'legacy:1', 'legacy-record', 1, 't', 'user_investigation', 'source',
            'replacement_candidate', 1, 'replacement_candidate_present',
            'https://lists.example.test/replacement', 'community_list', 0, 1
        )"""
    )
    conn.commit()

    db.init_db(conn)
    db.init_db(conn)

    state = conn.execute(
        "SELECT source_authority, confirmed_url_authority FROM availability_state "
        "WHERE posting_id = 'legacy:1'"
    ).fetchone()
    assert state["source_authority"] == "first_party_ats"
    assert state["confirmed_url_authority"] == "community_list"


def test_connection_uses_wal(tmp_path):
    conn = _conn(tmp_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connection_usable_from_another_thread(tmp_path):
    # Streamlit reruns the script on different threads; the connection must not raise
    # sqlite3.ProgrammingError("created in a thread can only be used in that same thread").
    import threading

    conn = _conn(tmp_path)
    result = {}

    def query():
        try:
            result["rows"] = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
        except Exception as e:  # pragma: no cover - failure path
            result["error"] = e

    t = threading.Thread(target=query)
    t.start()
    t.join()
    assert "error" not in result, result.get("error")
    assert result["rows"] == 0


def test_meta_round_trip(tmp_path):
    conn = _conn(tmp_path)
    assert db.get_meta(conn, "last_digest_at") is None
    db.set_meta(conn, "last_digest_at", "2026-06-18T00:00:00+00:00")
    assert db.get_meta(conn, "last_digest_at") == "2026-06-18T00:00:00+00:00"
    db.set_meta(conn, "last_digest_at", "2026-06-19T00:00:00+00:00")
    assert db.get_meta(conn, "last_digest_at") == "2026-06-19T00:00:00+00:00"


def test_prune_runs_deletes_strictly_older_than_30_days(tmp_path):
    conn = _conn(tmp_path)
    now = datetime(2026, 6, 18, tzinfo=timezone.utc)

    def add(age_days):
        ts = (now - timedelta(days=age_days)).isoformat()
        conn.execute(
            "INSERT INTO runs (started_at, source_key, ok, count) VALUES (?,?,1,0)",
            (ts, f"src{age_days}"),
        )

    add(29)
    add(30)
    add(31)
    conn.commit()

    db.prune_runs(conn, now=now.isoformat(), days=30)

    survivors = {r[0] for r in conn.execute("SELECT source_key FROM runs")}
    assert survivors == {"src29", "src30"}  # 31d deleted; 30d boundary kept


def test_fresh_db_has_v4_tier_columns(tmp_path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert {"tier", "pinned_tier", "tier_before_pin", "pinned_at", "notified_at"} <= cols


def test_fresh_db_has_v5_flag_columns_and_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(postings)")}
    assert {"flagged_at", "flag_reason"} <= cols
    db.init_db(conn)  # re-running the migration is a no-op
    assert {r[1] for r in conn.execute("PRAGMA table_info(postings)")} == cols


def test_inbox_migration_stamps_notified_and_is_idempotent(tmp_path):
    p = tmp_path / "old.db"
    conn = db.connect(p)
    # simulate a pre-inbox DB: build schema, seed a row + the old nudge flag, clear the gate
    db.init_db(conn)
    conn.execute("DELETE FROM meta")
    conn.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, first_seen, last_seen) "
        "VALUES ('g:1','greenhouse:x','greenhouse','Old Role','t1','t2')"
    )
    db.set_meta(conn, "pending_notified", "1")

    db.init_db(conn)  # runs the meta-gated migration
    row = conn.execute("SELECT notified_at FROM postings WHERE posting_id='g:1'").fetchone()
    assert row["notified_at"] == "t2"  # stamped with the row's own last_seen — no digest blast
    assert db.get_meta(conn, "pending_notified") is None
    assert db.get_meta(conn, "inbox_migrated") == "1"

    # idempotent: a post-migration arrival keeps its NULL (it SHOULD be digested)
    conn.execute(
        "INSERT INTO postings (posting_id, source_key, source_type, title, first_seen, last_seen) "
        "VALUES ('g:2','greenhouse:x','greenhouse','New Role','t3','t3')"
    )
    conn.commit()
    db.init_db(conn)
    assert conn.execute("SELECT notified_at FROM postings WHERE posting_id='g:2'"
                        ).fetchone()["notified_at"] is None
