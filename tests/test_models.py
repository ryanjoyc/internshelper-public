from internshelper.models import Posting


def _posting(**kw):
    base = dict(
        posting_id="greenhouse:123",
        source_key="greenhouse:stripe",
        title="Software Engineer Intern",
        company="Stripe",
        url="https://example.com/job/123",
    )
    base.update(kw)
    return Posting(**base)


def test_source_type_is_derived_from_source_key():
    assert _posting().source_type == "greenhouse"


def test_source_type_handles_github_url_token():
    p = _posting(source_key="github:https://raw.githubusercontent.com/x/y/listings.json")
    assert p.source_type == "github"


def test_classification_flags_default_to_false():
    p = _posting()
    assert (p.is_internship, p.is_newgrad, p.is_cs_relevant) == (False, False, False)


def test_optional_fields_default_empty():
    p = _posting()
    assert p.location == ""
    assert p.description == ""
