import pytest

from internshelper import config


@pytest.fixture(autouse=True)
def _clear_smtp_env(monkeypatch):
    # Isolate from any real INTERNSHELPER_SMTP_* vars on the dev machine.
    monkeypatch.delenv("INTERNSHELPER_SMTP_SENDER", raising=False)
    monkeypatch.delenv("INTERNSHELPER_SMTP_RECIPIENT", raising=False)


# ---------- sources ----------

def _write(p, text):
    p.write_text(text)
    return p


def test_load_sources_parses_each_type(tmp_path):
    f = _write(tmp_path / "sources.yaml", """
sources:
  - type: greenhouse
    token: stripe
    label: Stripe
  - type: lever
    token: netflix
  - type: ashby
    org: ramp
  - type: github
    url: https://example.com/listings.json
""")
    entries, errors = config.load_sources(f)
    assert errors == []
    keys = [e.source_key for e in entries]
    assert keys == [
        "greenhouse:stripe",
        "lever:netflix",
        "ashby:ramp",
        "github:https://example.com/listings.json",
    ]
    assert entries[0].label == "Stripe"
    assert entries[0].display == "Stripe"
    assert entries[1].display == "lever:netflix"  # falls back to source_key


def test_source_title_must_match_parses_and_filters(tmp_path):
    f = _write(tmp_path / "sources.yaml", """
sources:
  - type: greenhouse
    token: bigco
    title_must_match: [engineer, intern]
""")
    entries, errors = config.load_sources(f)
    e = entries[0]
    assert e.title_must_match == ["engineer", "intern"]
    assert e.accepts("Software Engineer") is True
    assert e.accepts("Backend INTERN") is True  # case-insensitive
    assert e.accepts("Cashier") is False


def test_markdown_source_parses_url_and_columns(tmp_path):
    f = _write(tmp_path / "sources.yaml", """
sources:
  - type: markdown
    url: https://raw.githubusercontent.com/x/y/main/README.md
    label: SomeList
    columns: {company: Org, title: Position}
""")
    e = config.load_sources(f)[0][0]
    assert e.type == "markdown"
    assert e.source_key == "markdown:https://raw.githubusercontent.com/x/y/main/README.md"
    assert e.columns == {"company": "Org", "title": "Position"}


def test_markdown_source_defaults_empty_columns(tmp_path):
    f = _write(tmp_path / "sources.yaml", "sources:\n  - type: markdown\n    url: https://x/r.md\n")
    e = config.load_sources(f)[0][0]
    assert e.columns == {}


def test_source_without_filter_accepts_all(tmp_path):
    f = _write(tmp_path / "sources.yaml", "sources:\n  - type: lever\n    token: x\n")
    e = config.load_sources(f)[0][0]
    assert e.title_must_match == []
    assert e.accepts("Anything goes") is True


def test_load_sources_missing_file_is_hard_error(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_sources(tmp_path / "nope.yaml")


def test_load_sources_empty_list_is_valid_zero_sources(tmp_path):
    f = _write(tmp_path / "sources.yaml", "sources:\n")
    entries, errors = config.load_sources(f)
    assert entries == []
    assert errors == []


def test_load_sources_skips_malformed_entry_but_keeps_valid(tmp_path):
    f = _write(tmp_path / "sources.yaml", """
sources:
  - type: greenhouse        # missing token
    label: Bad
  - type: martian           # unknown type
    token: x
  - type: lever
    token: good
""")
    entries, errors = config.load_sources(f)
    assert [e.source_key for e in entries] == ["lever:good"]
    assert len(errors) == 2


# ---------- settings ----------

VALID_SETTINGS = """
[smtp]
host = "smtp.gmail.com"
port = 587
sender = "me@example.com"
recipient = "me@example.com"

[filters]
require_cs = true
require_intern_or_newgrad = false

[keywords]
internship = ["intern"]
newgrad = ["new grad"]
cs = ["software"]
"""


def test_load_settings_reads_all_fields(tmp_path):
    f = _write(tmp_path / "settings.toml", VALID_SETTINGS)
    s = config.load_settings(f)
    assert s.smtp_host == "smtp.gmail.com"
    assert s.smtp_port == 587
    assert s.smtp_recipient == "me@example.com"
    assert s.require_cs is True
    assert s.require_intern_or_newgrad is False
    assert s.keywords["cs"] == ["software"]


def test_smtp_sender_recipient_from_env_override_toml(tmp_path, monkeypatch):
    f = _write(tmp_path / "settings.toml", VALID_SETTINGS)
    monkeypatch.setenv("INTERNSHELPER_SMTP_SENDER", "envsender@x.com")
    monkeypatch.setenv("INTERNSHELPER_SMTP_RECIPIENT", "envrecip@x.com")
    s = config.load_settings(f)
    assert s.smtp_sender == "envsender@x.com"
    assert s.smtp_recipient == "envrecip@x.com"


def test_smtp_sender_recipient_fall_back_to_toml(tmp_path):
    f = _write(tmp_path / "settings.toml", VALID_SETTINGS)
    s = config.load_settings(f)
    assert s.smtp_sender == "me@example.com"
    assert s.smtp_recipient == "me@example.com"


def test_missing_sender_recipient_defaults_empty_no_error(tmp_path):
    # Email may be disabled -> empty sender/recipient is allowed (no hard error).
    bad = (VALID_SETTINGS
           .replace('sender = "me@example.com"', "")
           .replace('recipient = "me@example.com"', ""))
    f = _write(tmp_path / "settings.toml", bad)
    s = config.load_settings(f)
    assert s.smtp_sender == ""
    assert s.smtp_recipient == ""


def test_load_settings_missing_file_is_hard_error(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load_settings(tmp_path / "nope.toml")


# ---------- default_path ----------

def test_default_path_uses_env_when_set(monkeypatch):
    monkeypatch.setenv("INTERNSHELPER_DB", "/custom/db.sqlite")
    assert config.default_path("INTERNSHELPER_DB", "data/x.db") == "/custom/db.sqlite"


def test_default_path_falls_back_to_repo_root_absolute(monkeypatch):
    from pathlib import Path
    monkeypatch.delenv("INTERNSHELPER_DB", raising=False)
    p = config.default_path("INTERNSHELPER_DB", "data/x.db")
    assert Path(p).is_absolute() and p.endswith("/data/x.db")
