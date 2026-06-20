"""Unit tests for the tiny stdlib .env loader.

`load_dotenv` reads a `KEY=value` file into os.environ WITHOUT overriding any variable
already set in the real environment (so a launchd/shell value always wins), and returns the
parsed pairs. Missing file is a silent no-op.
"""

import os

import pytest

from internshelper import dotenv


@pytest.fixture(autouse=True)
def _restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _write(p, text):
    p.write_text(text)
    return p


def test_missing_file_returns_empty_and_leaves_env_untouched(tmp_path):
    before = dict(os.environ)
    result = dotenv.load_dotenv(tmp_path / "nope.env")
    assert result == {}
    assert dict(os.environ) == before


def test_sets_key_into_environ(tmp_path):
    f = _write(tmp_path / ".env", "INTERNSHELPER_TEST_KEY=hello\n")
    os.environ.pop("INTERNSHELPER_TEST_KEY", None)
    result = dotenv.load_dotenv(f)
    assert os.environ["INTERNSHELPER_TEST_KEY"] == "hello"
    assert result["INTERNSHELPER_TEST_KEY"] == "hello"


def test_existing_env_var_wins(tmp_path):
    f = _write(tmp_path / ".env", "INTERNSHELPER_TEST_KEY=fromfile\n")
    os.environ["INTERNSHELPER_TEST_KEY"] = "preset"
    result = dotenv.load_dotenv(f)
    assert os.environ["INTERNSHELPER_TEST_KEY"] == "preset"  # not overwritten
    assert result["INTERNSHELPER_TEST_KEY"] == "fromfile"   # parsed value still returned


def test_ignores_comments_and_blank_lines(tmp_path):
    f = _write(tmp_path / ".env", "# a comment\n\n   \nKEY=value\n  # indented comment\n")
    result = dotenv.load_dotenv(f)
    assert result == {"KEY": "value"}


def test_trims_whitespace_around_key_and_value(tmp_path):
    f = _write(tmp_path / ".env", "  KEY  =   value  \n")
    result = dotenv.load_dotenv(f)
    assert result == {"KEY": "value"}


def test_strips_one_layer_of_surrounding_quotes(tmp_path):
    f = _write(tmp_path / ".env", 'A="double"\nB=\'single\'\n')
    result = dotenv.load_dotenv(f)
    assert result == {"A": "double", "B": "single"}


def test_splits_on_first_equals_only(tmp_path):
    f = _write(tmp_path / ".env", "URL=https://x/y?a=b&c=d\n")
    result = dotenv.load_dotenv(f)
    assert result["URL"] == "https://x/y?a=b&c=d"


def test_strips_inline_comment_after_whitespace(tmp_path):
    f = _write(tmp_path / ".env", "KEY=abc   # a note\n")
    assert dotenv.load_dotenv(f)["KEY"] == "abc"


def test_hash_without_preceding_space_is_kept(tmp_path):
    f = _write(tmp_path / ".env", "PW=a#b#c\n")
    assert dotenv.load_dotenv(f)["PW"] == "a#b#c"


def test_hash_inside_quotes_is_kept(tmp_path):
    f = _write(tmp_path / ".env", 'PW="a # b"\n')
    assert dotenv.load_dotenv(f)["PW"] == "a # b"


def test_inline_comment_after_quoted_value_stripped(tmp_path):
    f = _write(tmp_path / ".env", 'PW="abc"   # note\n')
    assert dotenv.load_dotenv(f)["PW"] == "abc"


def test_blank_key_line_is_skipped(tmp_path):
    f = _write(tmp_path / ".env", "=orphan\nKEY=v\n")
    assert dotenv.load_dotenv(f) == {"KEY": "v"}


def test_inline_comment_on_feature_toggle_still_disables(tmp_path, monkeypatch):
    monkeypatch.delenv("INTERNSHELPER_FEATURE_EMAIL", raising=False)
    f = _write(tmp_path / ".env", "INTERNSHELPER_FEATURE_EMAIL=0   # disabled\n")
    dotenv.load_dotenv(f)
    assert dotenv.feature_enabled("EMAIL") is False


def test_lines_without_equals_are_skipped(tmp_path):
    f = _write(tmp_path / ".env", "NOEQUALS\nKEY=value\n")
    result = dotenv.load_dotenv(f)
    assert result == {"KEY": "value"}


def test_returns_parsed_dict(tmp_path):
    f = _write(tmp_path / ".env", "ONE=1\nTWO=2\n")
    result = dotenv.load_dotenv(f)
    assert result == {"ONE": "1", "TWO": "2"}


def test_default_path_resolves_repo_root_without_crashing():
    # With no .env at the repo root, the default-path call is a no-op returning {}.
    result = dotenv.load_dotenv()
    assert isinstance(result, dict)


def test_feature_enabled_default_true_when_unset(monkeypatch):
    monkeypatch.delenv("INTERNSHELPER_FEATURE_COLLECT", raising=False)
    assert dotenv.feature_enabled("COLLECT") is True


def test_feature_enabled_default_can_be_false(monkeypatch):
    monkeypatch.delenv("INTERNSHELPER_FEATURE_FOO", raising=False)
    assert dotenv.feature_enabled("FOO", default=False) is False


def test_feature_enabled_falsey_values_disable(monkeypatch):
    for v in ("0", "false", "no", "off", "FALSE", ""):
        monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", v)
        assert dotenv.feature_enabled("EMAIL") is False


def test_feature_enabled_truthy_values_enable(monkeypatch):
    for v in ("1", "true", "yes", "on"):
        monkeypatch.setenv("INTERNSHELPER_FEATURE_EMAIL", v)
        assert dotenv.feature_enabled("EMAIL") is True
