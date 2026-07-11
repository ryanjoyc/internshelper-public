import html

from internshelper.text import html_to_text, strip_html


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


def test_escaped_html_is_decoded_then_stripped():
    # Greenhouse's `content` field arrives HTML-escaped: one decode pass just
    # reveals the tags. Both flavours must strip them, not display them.
    escaped = html.escape('<div class="intro"><p>Ready to build &amp; ship?</p></div>')
    assert strip_html(escaped) == "Ready to build & ship?"
    assert "<" not in html_to_text(escaped)


def test_stray_angle_brackets_survive():
    # "a < b" is not markup — the re-decode guard must leave it alone.
    assert strip_html("<p>needs a &lt; b</p>") == "needs a < b"


def test_html_to_text_keeps_paragraphs():
    out = html_to_text("<p>First para.</p><p>Second para.</p>")
    assert out == "First para.\n\nSecond para."


def test_html_to_text_bullets_lists_and_breaks():
    out = html_to_text("<p>Perks:</p><ul><li>Snacks</li><li>Pay</li></ul>done<br>bye")
    assert "• Snacks" in out and "• Pay" in out
    assert out.splitlines()[-2:] == ["done", "bye"]


def test_html_to_text_collapses_blank_runs_and_nbsp():
    out = html_to_text("<div><p>a</p></div><div></div><div><p>b&nbsp;c</p></div>")
    assert "\n\n\n" not in out
    assert "b c" in out


def test_html_to_text_empty_and_plain():
    assert html_to_text(None) == ""
    assert html_to_text("plain text") == "plain text"
