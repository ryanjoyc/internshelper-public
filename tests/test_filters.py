from internshelper import filters
from internshelper.models import Posting


def _p(cs, intern, newgrad):
    return Posting(
        posting_id="t:1", source_key="greenhouse:x", title="T", company="C", url="u",
        is_cs_relevant=cs, is_internship=intern, is_newgrad=newgrad,
    )


def test_default_filter_passes_cs_intern():
    assert filters.passes(_p(cs=True, intern=True, newgrad=False)) is True


def test_default_filter_passes_cs_newgrad():
    assert filters.passes(_p(cs=True, intern=False, newgrad=True)) is True


def test_default_filter_rejects_cs_only():
    # CS but neither intern nor new-grad -> excluded by require_intern_or_newgrad.
    assert filters.passes(_p(cs=True, intern=False, newgrad=False)) is False


def test_default_filter_rejects_non_cs_intern():
    assert filters.passes(_p(cs=False, intern=True, newgrad=False)) is False


def test_both_requirements_off_passes_everything():
    p = _p(cs=False, intern=False, newgrad=False)
    assert filters.passes(p, require_cs=False, require_intern_or_newgrad=False) is True
