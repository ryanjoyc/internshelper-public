"""Unit tests for the sources CLI (add/list/remove/test).

No network: the live fetch path (`build_connector`) is monkeypatched with a fake connector,
and writes go to a tmp `sources.yaml` pointed at by INTERNSHELPER_SOURCES. The key invariant
is that `add`/`remove` never destroy the file's hand-written comments / commented examples.
"""

import json

import pytest

from internshelper import config, sources
from internshelper.config import SourceEntry
from internshelper.models import Posting


class _FakeConnector:
    def __init__(self, posts=None, exc=None, warnings=None):
        self._posts = posts or []
        self._exc = exc
        # Push model: diagnostics are populated during fetch/parse and read off the
        # connector afterwards (matches the real Connector.diagnostics channel).
        self.diagnostics = list(warnings or [])

    def fetch(self):
        if self._exc:
            raise self._exc
        return self._posts


def _post(pid, title):
    return Posting(posting_id=pid, source_key="x", title=title, company="C", url=f"https://x/{pid}")


@pytest.fixture
def srcfile(tmp_path, monkeypatch):
    p = tmp_path / "sources.yaml"
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(p))
    return p


def _wire(monkeypatch, posts=None, exc=None, warnings=None):
    monkeypatch.setattr(sources, "build_connector",
                        lambda entry: _FakeConnector(posts=posts, exc=exc, warnings=warnings))


# ---------- pure helpers ----------

def test_entry_to_block_uses_id_field_per_type():
    gh = sources.entry_to_block(SourceEntry(type="greenhouse", token="stripe", label="Stripe"))
    assert "type: greenhouse" in gh and "token: stripe" in gh and "label: Stripe" in gh
    ash = sources.entry_to_block(SourceEntry(type="ashby", token="ramp"))
    assert "org: ramp" in ash and "label" not in ash
    md = sources.entry_to_block(SourceEntry(type="markdown", token="https://x/r.md"))
    assert "url:" in md and "https://x/r.md" in md


def test_entry_to_block_round_trips_through_load_sources(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n" + sources.entry_to_block(
        SourceEntry(type="greenhouse", token="stripe", label="Stripe",
                    title_must_match=["engineer", "intern"])))
    entries, errors = config.load_sources(p)
    assert errors == []
    assert entries[0].source_key == "greenhouse:stripe"
    assert entries[0].title_must_match == ["engineer", "intern"]


def test_append_source_preserves_comments_and_adds_entry(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "# my header comment\n"
        "sources:\n"
        "  # an example (commented)\n"
        "  # - type: lever\n"
        "  #   token: netflix\n"
        "  - type: greenhouse\n"
        "    token: stripe\n"
    )
    sources.append_source(p, SourceEntry(type="lever", token="netflix", label="Netflix"))
    text = p.read_text()
    assert "# my header comment" in text and "# an example (commented)" in text
    entries, errors = config.load_sources(p)
    assert errors == []
    assert [e.source_key for e in entries] == ["greenhouse:stripe", "lever:netflix"]


def test_append_into_null_sources_file(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n")
    sources.append_source(p, SourceEntry(type="greenhouse", token="stripe"))
    assert [e.source_key for e in config.load_sources(p)[0]] == ["greenhouse:stripe"]


def test_append_into_file_without_sources_key(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("")
    sources.append_source(p, SourceEntry(type="lever", token="x"))
    assert [e.source_key for e in config.load_sources(p)[0]] == ["lever:x"]


def test_sources_path_honors_env(tmp_path, monkeypatch):
    p = tmp_path / "custom.yaml"
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(p))
    assert sources._sources_path() == str(p)


# ---------- add ----------

def test_add_no_test_writes(srcfile):
    srcfile.write_text("sources:\n")
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe", "--yes", "--no-test"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].source_key == "greenhouse:stripe"


def test_add_yes_writes_after_fetch_test(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern"), _post("2", "Data Intern")])
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe", "--yes"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].source_key == "greenhouse:stripe"


def test_add_duplicate_is_noop(srcfile, monkeypatch):
    srcfile.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    before = srcfile.read_text()
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe", "--yes"])
    assert rc == 0
    assert srcfile.read_text() == before


def test_add_fetch_error_does_not_write(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, exc=RuntimeError("boom"))
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe", "--yes"])
    assert rc == 1
    assert config.load_sources(srcfile)[0] == []


def test_add_zero_count_without_yes_refuses(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[])
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe"])
    assert rc == 1
    assert config.load_sources(srcfile)[0] == []


_MD_URL = "https://raw.githubusercontent.com/foo/bar/main/README.md"


def test_add_structural_warning_without_yes_refuses(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[], warnings=["required column(s) not mapped"])
    rc = sources.main(["add", _MD_URL])
    assert rc == 1
    assert config.load_sources(srcfile)[0] == []


def test_add_structural_warning_with_yes_writes(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[], warnings=["required column(s) not mapped"])
    rc = sources.main(["add", _MD_URL, "--yes"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].source_key == f"markdown:{_MD_URL}"


def test_add_unknown_host_exit_2(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    monkeypatch.setattr(sources.sniffer, "sniff_careers_page", lambda url, label=None: [])
    rc = sources.main(["add", "https://mycorp.com/careers", "--yes"])
    assert rc == 2  # page sniffed, no embedded board found
    assert config.load_sources(srcfile)[0] == []


def test_add_unknown_host_falls_back_to_sniffer(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    monkeypatch.setattr(sources.sniffer, "sniff_careers_page",
                        lambda url, label=None: [SourceEntry(type="greenhouse", token="stripe")])
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    rc = sources.main(["add", "https://mycorp.com/careers", "--yes"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].source_key == "greenhouse:stripe"


def test_add_sniffer_multiple_candidates_exit_2(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    monkeypatch.setattr(sources.sniffer, "sniff_careers_page",
                        lambda url, label=None: [SourceEntry(type="greenhouse", token="a"),
                                                 SourceEntry(type="lever", token="b")])
    rc = sources.main(["add", "https://mycorp.com/careers", "--yes"])
    assert rc == 2  # ambiguous — won't guess
    assert config.load_sources(srcfile)[0] == []


def test_add_zero_count_with_yes_writes(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[])
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe", "--yes"])
    assert rc == 0  # --yes is the documented "add anyway" escape hatch
    assert config.load_sources(srcfile)[0][0].source_key == "greenhouse:stripe"


def test_add_with_columns_flag(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "X")])
    rc = sources.main(["add", "https://raw.githubusercontent.com/x/y/main/README.md",
                       "--yes", "--columns", "company=Org,title=Position"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].columns == {"company": "Org", "title": "Position"}


def test_parse_kv_drops_segments_without_equals():
    assert sources.parse_kv("a=1, b=2 , bad") == {"a": "1", "b": "2"}


def test_parse_kv_private_alias_kept_for_compat():
    assert sources._parse_kv is sources.parse_kv


# ---------- reusable add-source core (resolve_entry / is_duplicate) ----------

def test_resolve_entry_applies_title_must_match_and_columns():
    entry = sources.resolve_entry(
        "https://boards.greenhouse.io/stripe",
        label="Stripe",
        title_must_match=["engineer", "intern"],
        columns={"company": "Org"},
    )
    assert entry.source_key == "greenhouse:stripe"
    assert entry.label == "Stripe"
    assert entry.title_must_match == ["engineer", "intern"]
    assert entry.columns == {"company": "Org"}


def test_resolve_entry_unknown_host_raises():
    from internshelper.sourceurl import SourceDetectionError
    with pytest.raises(SourceDetectionError):
        sources.resolve_entry("https://mycorp.com/careers")


def test_is_duplicate_true_false(srcfile):
    srcfile.write_text("sources:\n  - type: greenhouse\n    token: stripe\n")
    dup = SourceEntry(type="greenhouse", token="stripe")
    fresh = SourceEntry(type="lever", token="netflix")
    assert sources.is_duplicate(srcfile, dup) is True
    assert sources.is_duplicate(srcfile, fresh) is False


def test_add_with_title_must_match_flag(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    rc = sources.main(["add", "https://boards.greenhouse.io/bigco", "--yes",
                       "--title-must-match", "engineer,intern"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].title_must_match == ["engineer", "intern"]


def test_add_prompt_yes_writes(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    monkeypatch.setattr(sources.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe"])
    assert rc == 0
    assert config.load_sources(srcfile)[0][0].source_key == "greenhouse:stripe"


def test_add_prompt_no_aborts(srcfile, monkeypatch):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    monkeypatch.setattr(sources.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    rc = sources.main(["add", "https://boards.greenhouse.io/stripe"])
    assert rc == 1
    assert config.load_sources(srcfile)[0] == []


# ---------- list / remove / test ----------

def test_list_json(srcfile, capsys):
    srcfile.write_text("sources:\n  - type: greenhouse\n    token: stripe\n    label: Stripe\n")
    rc = sources.main(["list", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["source_key"] == "greenhouse:stripe" and out[0]["label"] == "Stripe"


def test_remove_deletes_entry_keeps_siblings_and_comments(srcfile):
    srcfile.write_text(
        "# header\n"
        "sources:\n"
        "  # commented example\n"
        "  - type: greenhouse\n"
        "    token: stripe\n"
        "    label: Stripe\n"
        "  - type: lever\n"
        "    token: netflix\n"
    )
    rc = sources.main(["remove", "greenhouse:stripe"])
    assert rc == 0
    text = srcfile.read_text()
    assert "# header" in text and "# commented example" in text
    entries, errors = config.load_sources(srcfile)
    assert errors == []
    assert [e.source_key for e in entries] == ["lever:netflix"]


def test_remove_entry_with_title_must_match_keeps_file_valid(srcfile, monkeypatch):
    # Regression: a title_must_match block sequence must not break the remove block-scan.
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    sources.main(["add", "https://boards.greenhouse.io/bigco", "--yes",
                  "--title-must-match", "engineer,intern"])
    sources.main(["add", "https://jobs.lever.co/netflix", "--yes", "--no-test"])
    assert sources.main(["remove", "greenhouse:bigco"]) == 0
    entries, errors = config.load_sources(srcfile)
    assert errors == []  # file still valid YAML, not orphaned sequence items
    assert [e.source_key for e in entries] == ["lever:netflix"]  # sibling survives


def test_remove_url_source_key(srcfile):
    url = "https://raw.githubusercontent.com/x/y/main/README.md"
    srcfile.write_text(f"sources:\n  - type: markdown\n    url: {url}\n")
    rc = sources.main(["remove", f"markdown:{url}"])
    assert rc == 0
    assert config.load_sources(srcfile)[0] == []


def test_remove_missing_key_exit_1(srcfile):
    srcfile.write_text("sources:\n  - type: lever\n    token: netflix\n")
    assert sources.main(["remove", "greenhouse:stripe"]) == 1


def test_remove_unknown_type_exit_1(srcfile):
    srcfile.write_text("sources:\n  - type: lever\n    token: netflix\n")
    assert sources.main(["remove", "martian:x"]) == 1


def test_remove_missing_file_exit_1(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_SOURCES", str(tmp_path / "absent.yaml"))
    assert sources.main(["remove", "greenhouse:stripe"]) == 1


def test_list_plain_output_shows_key_and_filter(srcfile, capsys):
    srcfile.write_text("sources:\n  - type: greenhouse\n    token: bigco\n    title_must_match: [engineer]\n")
    assert sources.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "greenhouse:bigco" in out and "filter=" in out


def test_list_plain_empty(srcfile, capsys):
    srcfile.write_text("sources:\n")
    sources.main(["list"])
    assert "(no sources)" in capsys.readouterr().out


def test_test_command_unknown_host_exit_2(srcfile):
    srcfile.write_text("sources:\n")
    assert sources.main(["test", "https://mycorp.com/careers"]) == 2


def test_test_command_fetch_error_exit_1(srcfile, monkeypatch, capsys):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, exc=RuntimeError("boom"))
    rc = sources.main(["test", "https://boards.greenhouse.io/stripe"])
    assert rc == 1
    assert "fetch failed" in capsys.readouterr().out


def test_test_command_detects_url_and_reports(srcfile, monkeypatch, capsys):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern")])
    rc = sources.main(["test", "https://boards.greenhouse.io/stripe"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "greenhouse:stripe" in out and "1 postings" in out
    assert config.load_sources(srcfile)[0] == []  # no write


def test_test_command_existing_source_key(srcfile, monkeypatch, capsys):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern"), _post("2", "Data")])
    rc = sources.main(["test", "greenhouse:stripe"])
    assert rc == 0
    assert "2 postings" in capsys.readouterr().out


def test_test_command_json_dumps_full_list(srcfile, monkeypatch, capsys):
    # --json emits EVERY parsed posting (not just sample titles) with the stable posting_id
    # and url the deep-scan-source skill needs to enumerate links + map back to DB rows.
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, posts=[_post("1", "SWE Intern"), _post("2", "Data Intern")],
          warnings=["heads up: a table was degenerate"])
    rc = sources.main(["test", "https://boards.greenhouse.io/stripe", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source_key"] == "greenhouse:stripe"
    assert out["count"] == 2
    assert [p["posting_id"] for p in out["postings"]] == ["1", "2"]
    assert all(p["url"] for p in out["postings"])  # every row carries its link
    assert out["postings"][0]["title"] == "SWE Intern"
    assert out["diagnostics"] == ["heads up: a table was degenerate"]
    assert config.load_sources(srcfile)[0] == []  # still no write


def test_test_command_json_fetch_error_exit_1(srcfile, monkeypatch, capsys):
    srcfile.write_text("sources:\n")
    _wire(monkeypatch, exc=RuntimeError("boom"))
    rc = sources.main(["test", "https://boards.greenhouse.io/stripe", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["source_key"] == "greenhouse:stripe" and "boom" in out["error"]
