"""Page-text tests.

Both functions here exist because of one measured failure: judging "Larry Page
is the CEO of Google" against Google's own newsroom, the judge was shown
breadcrumbs and a cookie notice while the sentence naming the current CEO sat
further down the page. It reported "insufficient" about a page that answered
the question, and the model path measured 0.00 recall.
"""

from __future__ import annotations

from chronos.pagetext import clean_page_text, predicate_terms, relevant_window

# The shape Tavily's extract actually returns.
NEWSROOM = (
    "Breadcrumb  1. [Home](https://blog.google/ \"News from Google\") "
    "2. [Company News](https://blog.google/company-news/) "
    "3. [Inside Google](https://blog.google/inside/)\n\n"
    "# A message from our CEO\n\n"
    "**Sundar Pichai**, the _CEO_ of Google and Alphabet, said the company "
    "will invest further in AI.\n\n"
    "Cookie notice: we use cookies.\n"
    "Privacy policy | Terms of use\n"
)


# ---------------------------------------------------------------------------
# clean_page_text
# ---------------------------------------------------------------------------


def test_link_text_is_kept_and_urls_dropped():
    """"[Sundar Pichai](/bio)" carries the fact; the href never does."""
    out = clean_page_text("See [Sundar Pichai](https://example.com/bio) for details.")
    assert "Sundar Pichai" in out
    assert "example.com" not in out


def test_navigation_and_decoration_are_stripped():
    out = clean_page_text(NEWSROOM)
    assert "Breadcrumb" not in out
    assert "blog.google" not in out
    assert "**" not in out
    assert "Cookie notice" not in out
    # The actual content survives.
    assert "Sundar Pichai" in out
    assert "CEO of Google" in out


def test_cleaning_shrinks_the_page():
    out = clean_page_text(NEWSROOM)
    assert len(out) < len(NEWSROOM)


def test_cleaning_handles_empty_input():
    assert clean_page_text("") == ""
    assert clean_page_text(None) == ""  # type: ignore[arg-type]


def test_prose_is_left_alone():
    prose = "Python ships a new feature release every October."
    assert clean_page_text(prose) == prose


# ---------------------------------------------------------------------------
# predicate_terms
# ---------------------------------------------------------------------------


def test_predicate_terms_drops_noise_and_leads_with_the_head_noun():
    assert predicate_terms("holder of role (CEO)") == ["CEO"]
    # Head noun first: "input", "token" and "price" are all five letters, and
    # "price" is the one that appears beside the number on a page.
    assert predicate_terms("input token price")[0] == "price"
    assert predicate_terms("latest version")[0] == "version"
    assert predicate_terms("current value") == []


# ---------------------------------------------------------------------------
# relevant_window
# ---------------------------------------------------------------------------


def test_window_centres_on_the_predicate_not_the_stated_value():
    """The exact regression. "Larry Page" is absent from a page about Sundar
    Pichai, so centring on the stated value misses and falls back to the page
    head — which is navigation."""
    page = clean_page_text(("Google Products About Careers Press " * 30) + NEWSROOM)

    window = relevant_window(page, "Google", "holder of role (CEO)", "Larry Page", 300)
    assert "Sundar Pichai" in window, "the window must contain the current holder"

    # A head-of-page slice of the same size would not.
    assert "Sundar Pichai" not in page[:300]


def test_window_falls_back_to_the_stated_value():
    page = "filler " * 200 + "The price is $2.50 per million tokens."
    window = relevant_window(page, "GPT-4o", "unknown property", "$2.50", 200)
    assert "$2.50" in window


def test_window_falls_back_to_the_subject():
    page = "filler " * 200 + "Kestrel Robotics announced a funding round."
    window = relevant_window(page, "Kestrel Robotics", "zzz", "qqq", 200)
    assert "Kestrel Robotics" in window


def test_short_pages_are_returned_whole():
    page = "Sundar Pichai is the CEO of Google."
    assert relevant_window(page, "Google", "CEO", "Larry Page", 500) == page


def test_window_respects_the_budget():
    page = "x" * 5000
    assert len(relevant_window(page, "s", "p", "v", 300)) <= 300


def test_window_keeps_context_before_the_hit():
    """A third of the window sits ahead of the match so the sentence it is in
    is not clipped at its start."""
    page = ("a" * 1000) + "The CEO is Sundar Pichai." + ("b" * 1000)
    window = relevant_window(page, "Google", "CEO", "Larry Page", 300)
    assert "The CEO is Sundar Pichai." in window
    assert window.startswith("a")


def test_window_handles_empty_input():
    assert relevant_window("", "s", "p", "v", 100) == ""
