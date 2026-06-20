from internshelper.text import strip_html


def test_removes_tags():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_unescapes_entities():
    assert strip_html("a &amp; b") == "a & b"


def test_drops_attribute_text_like_urls():
    # The href (containing 'ai-jobs') must not survive into the text.
    out = strip_html('<a href="https://ai-jobs.example.com/apply">Apply here</a>')
    assert out == "Apply here"
    assert "ai-jobs" not in out


def test_collapses_whitespace():
    assert strip_html("<div>line one\n\n   line two</div>") == "line one line two"


def test_empty_and_plain():
    assert strip_html("") == ""
    assert strip_html("plain text") == "plain text"
