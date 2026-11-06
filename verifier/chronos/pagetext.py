"""Turning an extracted web page into text a judge can actually read.

Tavily's `extract` returns page content as markdown, and most of a real page
is not prose: breadcrumbs, nav bars, cookie notices, link markup. That matters
because the judge sees a *window* of each page, sized by a token budget, and
chrome crowds the budget.

Observed on Google's own newsroom while judging "Larry Page is the CEO of
Google", with a 1,100-character window:

    Breadcrumb  1. [Home](https://blog.google/ "News from Google")
    2. [Company News](https://blog.google/company-news/) 3. [Inside Google]...

The sentence naming the current CEO was further down the page and never made
it into the window, so the judge reported "insufficient" about a page that
answered the question outright.

Two functions, both pure so they can be tested without a network:

* `clean_page_text` strips the chrome and keeps the prose.
* `relevant_window` picks which part of the cleaned text to show, centred on
  the claim's PREDICATE rather than its stated value — see its docstring for
  why that ordering is the one that matters.
"""

from __future__ import annotations

import re

# --- markdown and navigation chrome ---------------------------------------

#: "[Sundar Pichai](/bio)" -> "Sundar Pichai". The link text carries the fact;
#: the href never does.
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

#: Bare image/footnote markers left behind by the above.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]")

_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)

#: Emphasis, code fences, blockquote and table pipes.
_MD_DECORATION = re.compile(r"[*_`>|]+")

#: Whole lines that are site furniture rather than content.
_NAV_LINE = re.compile(
    r"^\s*(breadcrumb|jump to content|skip to|table of contents|menu|search"
    r"|sign in|log in|subscribe|share this|follow us|cookie|privacy policy"
    r"|terms of (use|service)|all rights reserved|accept all)\b.*$",
    re.I | re.M,
)

#: A line of nothing but punctuation, bullets or numbering.
_NOISE_LINE = re.compile(r"^\s*[\d.\-•\s]*$", re.M)

_MULTISPACE = re.compile(r"[ \t]{2,}")
_MULTINEWLINE = re.compile(r"\n{2,}")


def clean_page_text(text: str) -> str:
    """Strip markdown and navigation chrome, keeping the prose.

    Purely a density improvement: the same token budget then buys sentences
    instead of markup.
    """
    if not text:
        return ""

    out = _MD_IMAGE.sub(" ", text)
    out = _MD_LINK.sub(r"\1", out)
    out = _MD_HEADING.sub("", out)
    out = _NAV_LINE.sub("", out)
    out = _MD_DECORATION.sub(" ", out)
    out = _NOISE_LINE.sub("", out)
    out = _MULTISPACE.sub(" ", out)
    out = _MULTINEWLINE.sub("\n", out)
    return out.strip()


# --- choosing which part of the page to show ------------------------------

#: Words in a predicate that locate nothing on a page.
_PREDICATE_STOPWORDS = frozenset(
    {
        "of", "the", "a", "an", "and", "for", "current", "holder", "role",
        "status", "value", "item", "statement", "recent", "timeless",
    }
)

_WORD = re.compile(r"[A-Za-z][A-Za-z-]{2,}")


def predicate_terms(predicate: str) -> list[str]:
    """The searchable words of a predicate, most useful first.

    "holder of role (CEO)" gives ["CEO"]; "input token price" gives
    ["price", "token", "input"]; "latest version" gives ["version", "latest"].

    Ordering is head-noun first, then longest. In an English compound noun the
    head is the last word — "input token *price*" — and the head is the word a
    page actually uses next to the value. Length alone cannot rank "input",
    "token" and "price": they are all five letters, and "input" appears on
    every page while "price" appears beside the number.
    """
    terms = [w for w in _WORD.findall(predicate or "") if w.lower() not in _PREDICATE_STOPWORDS]
    # Longest first; among equal lengths, the later word wins, because that is
    # the head of the compound. Stated as an explicit key rather than relying
    # on reversed() plus a stable sort, which reads like an accident.
    return [
        word
        for _, word in sorted(
            ((-len(w), -i), w) for i, w in enumerate(terms)
        )
    ]


def relevant_window(text: str, subject: str, predicate: str, stated_value: str, budget: int) -> str:
    """The most useful `budget` characters of a page for judging a claim.

    The ordering is the point, and getting it wrong cost the model path all of
    its recall.

    Centring on the claim's STATED value first seems obvious and is backwards
    for exactly the claims that matter. Judging "Larry Page is the CEO of
    Google" against Google's newsroom, "Larry Page" does not appear — the page
    says Sundar Pichai — so the search missed and the window fell back to the
    head of the page, which is navigation. The judge then said "insufficient"
    about a page that stated the answer.

    So the PREDICATE is tried first. A page that settles a role claim contains
    "CEO"; a page that settles a price contains "price". Finding the property
    locates the sentence carrying whatever the current value is, which is the
    thing the judge needs to see and cannot guess.
    """
    if not text:
        return ""
    if len(text) <= budget:
        return text

    low = text.lower()
    needles = [*predicate_terms(predicate), stated_value or "", subject or ""]

    for needle in needles:
        candidate = needle.strip().lower()
        if len(candidate) < 3:
            continue
        found = low.find(candidate)
        if found == -1:
            continue
        # Keep a third of the window ahead of the hit so the sentence it sits
        # in is not clipped at its start.
        start = max(0, found - budget // 3)
        return text[start : start + budget]

    return text[:budget]
