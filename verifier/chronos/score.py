"""Scoring a claim 0-100.

    S = 100 * (0.45A + 0.25R + 0.15Q + 0.15C) * (1 - 0.5 * V * D)

    A  agreement   share of sources that support the claim; a contradicting
                   source counts -1, and the result is floored at 0
    R  recency     1.0 when the best source is under 7 days old, falling
                   linearly to 0.0 at 365 days
    Q  quality     best source tier: official 1.0, major news 0.7, other 0.4
    C  confidence  the judge's own confidence in its call
    V  volatility  how fast this class of fact moves (market 1 ... timeless 0)
    D  decay       time since the last check over the class's re-check
                   interval, capped at 1

The decay term is what makes this more than a fact-check: a claim can be
perfectly supported by evidence and still lose points because the evidence is
old relative to how fast that kind of fact changes. A stock price verified an
hour ago is worth much less than a tax rate verified an hour ago, and the
`0.5 * V * D` factor is where that is expressed. The 0.5 ceiling means decay
can at most halve a score; it can never on its own make a well-supported claim
look outdated.

Verdicts: 80+ current, 50-79 check, below 50 outdated, no evidence at all is
unverifiable rather than outdated — Chronos does not punish a claim for being
hard to look up.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from .models import Evidence, ScoreBreakdown, Verdict
from .settings import (
    MAJOR_NEWS_DOMAINS,
    OFFICIAL_DOMAIN_HINTS,
    OFFICIAL_TLDS,
    get_settings,
    recheck_interval,
    volatility_weight,
)

# Weights. They sum to 1.0; the assertion below is a guard against a careless
# edit, since a non-normalised set would silently rescale every score.
W_AGREEMENT = 0.45
W_RECENCY = 0.25
W_QUALITY = 0.15
W_CONFIDENCE = 0.15
assert abs((W_AGREEMENT + W_RECENCY + W_QUALITY + W_CONFIDENCE) - 1.0) < 1e-9

#: Decay can remove at most half of a score.
DECAY_CEILING = 0.5

RECENCY_FULL_DAYS = 7
RECENCY_ZERO_DAYS = 365

QUALITY_BY_TIER = {"official": 1.0, "news": 0.7, "other": 0.4}


# ---------------------------------------------------------------------------
# source grading
# ---------------------------------------------------------------------------


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def classify_source(url: str, subject: str = "") -> str:
    """Grade a source as official, news or other.

    `official` means the primary party for this subject: the vendor's own
    pricing page for a price, a government domain for a law. It is deliberately
    narrow, because treating a blog as official is how a fact-checker ends up
    confidently wrong.
    """
    domain = domain_of(url)
    if not domain:
        return "other"

    if any(domain.endswith(tld) for tld in OFFICIAL_TLDS):
        return "official"

    subject_low = subject.lower()
    for key, domains in OFFICIAL_DOMAIN_HINTS.items():
        if key in subject_low and any(
            domain == d or domain.endswith("." + d) for d in domains
        ):
            return "official"

    if domain in MAJOR_NEWS_DOMAINS or any(
        domain.endswith("." + d) for d in MAJOR_NEWS_DOMAINS
    ):
        return "news"

    return "other"


#: News URLs very often carry the publication date in the path, e.g.
#: fortune.com/2026/10/15/... or theguardian.com/2026-10-15/...
_URL_DATE = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:[/-]|$)")


def date_from_url(url: str) -> datetime | None:
    """Recover a publication date from a URL path.

    Worth doing because Tavily frequently returns no `published_date` for
    general-topic searches, and an undated source scores R = 0.4 — which caps
    a well-supported claim at around 76, below the `current` threshold. The
    date is usually sitting in the URL.

    Only accepts plausible dates, and never a future one: a path segment that
    happens to look like a date should not be able to invent fresher evidence
    than exists.
    """
    m = _URL_DATE.search(url or "")
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        parsed = datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None
    if parsed > datetime.now(UTC):
        return None
    return parsed


def parse_date(raw: str | None) -> datetime | None:
    """Parse the assorted date shapes search APIs return."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None

    # ISO-8601, with or without a Z.
    try:
        cleaned = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# the terms
# ---------------------------------------------------------------------------


def agreement(evidence: list[Evidence]) -> tuple[float, int, int, int]:
    """A: net supporting share of the sources that ADDRESSED the claim.

    Returns (A, supporting, contradicting, insufficient).

    The denominator is `supports + contradicts`, deliberately excluding the
    sources that said nothing either way. Dividing by every retrieved result
    instead punishes a claim for the search engine's aim: judging "Satya
    Nadella is the CEO of Microsoft" against one confirming page and four
    irrelevant ones gave A = 0.2 and a score of 44.5, i.e. `outdated` for a
    claim that a source had just confirmed and none had disputed.

    An insufficient source carries no information about the claim, so it
    belongs in the breakdown for transparency but not in the ratio. Having no
    addressing source at all is handled upstream as `unverifiable`, not as
    disagreement.
    """
    supporting = sum(1 for e in evidence if e.call == "supports")
    contradicting = sum(1 for e in evidence if e.call == "contradicts")
    insufficient = sum(1 for e in evidence if e.call == "insufficient")

    addressing = supporting + contradicting
    if addressing == 0:
        return 0.0, supporting, contradicting, insufficient

    net = supporting - contradicting
    return max(0.0, net / addressing), supporting, contradicting, insufficient


def recency(evidence: list[Evidence], now: datetime | None = None) -> float:
    """R: freshness of the best-dated source.

    Undated sources score 0.4 rather than 0: plenty of authoritative pages
    carry no publication date, and penalising them as though they were a year
    old would bias the score against primary sources.
    """
    now = now or datetime.now(UTC)

    ages: list[float] = []
    for e in evidence:
        dt = parse_date(e.published_date)
        if dt is None:
            continue
        ages.append(max(0.0, (now - dt).total_seconds() / 86400.0))

    if not ages:
        return 0.4

    age = min(ages)
    if age <= RECENCY_FULL_DAYS:
        return 1.0
    if age >= RECENCY_ZERO_DAYS:
        return 0.0
    span = RECENCY_ZERO_DAYS - RECENCY_FULL_DAYS
    return max(0.0, 1.0 - (age - RECENCY_FULL_DAYS) / span)


def quality(evidence: list[Evidence]) -> float:
    """Q: the best source tier available."""
    if not evidence:
        return 0.0
    return max(QUALITY_BY_TIER.get(e.tier, 0.4) for e in evidence)


def decay(claim_class: str, last_checked: datetime | None, now: datetime | None = None) -> float:
    """D: age since last check over the class's re-check interval, capped at 1.

    A claim verified just now has D = 0. A timeless claim always has D = 0,
    because it has no re-check interval.
    """
    interval = recheck_interval(claim_class)
    if interval is None or last_checked is None:
        return 0.0

    now = now or datetime.now(UTC)
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=UTC)

    elapsed = (now - last_checked).total_seconds()
    if elapsed <= 0:
        return 0.0
    return min(1.0, elapsed / interval.total_seconds())


# ---------------------------------------------------------------------------
# the score
# ---------------------------------------------------------------------------


def score_claim(
    claim_class: str,
    evidence: list[Evidence],
    confidence: float,
    last_checked: datetime | None = None,
    now: datetime | None = None,
) -> ScoreBreakdown:
    """Score one claim and return every term that produced the number."""
    now = now or datetime.now(UTC)

    a, supporting, contradicting, insufficient = agreement(evidence)
    r = recency(evidence, now=now)
    q = quality(evidence)
    c = max(0.0, min(1.0, float(confidence)))
    v = volatility_weight(claim_class)
    d = decay(claim_class, last_checked, now=now)

    base = W_AGREEMENT * a + W_RECENCY * r + W_QUALITY * q + W_CONFIDENCE * c
    multiplier = max(0.0, 1.0 - DECAY_CEILING * v * d)
    score = round(100.0 * base * multiplier, 1)

    return ScoreBreakdown(
        agreement=round(a, 4),
        recency=round(r, 4),
        quality=round(q, 4),
        confidence=round(c, 4),
        volatility=round(v, 4),
        decay=round(d, 4),
        base=round(base, 4),
        decay_multiplier=round(multiplier, 4),
        score=score,
        supporting=supporting,
        contradicting=contradicting,
        insufficient=insufficient,
    )


def verdict_for(score: float, has_evidence: bool, contradicted: bool = False) -> Verdict:
    """Map a score — and the direction of the evidence — to a verdict.

    Why direction is a separate input, rather than being left to the score:

    The score measures how *strong* the evidence is. It cannot, on its own,
    express that the evidence points the other way. Work through a claim that
    every source contradicts: A floors at 0, but R, Q and C keep contributing,
    so S peaks at 100*(0.25 + 0.15 + 0.15) = 55 — comfortably inside the
    "check" band. A claim flatly refuted by two official, same-week sources
    would be reported as "worth a look".

    That is backwards. A well-sourced contradiction is *strong* evidence the
    claim is wrong, not weak evidence it is right. So when more sources
    contradict than support, the verdict is `outdated` whatever S says, and S
    is still reported in full so the number can be inspected.

    No evidence at all is `unverifiable`, never `outdated`: a claim Chronos
    could not check is a different thing from one it checked and found wrong,
    and conflating them would make both the headers and the eviction decision
    untrustworthy.
    """
    if not has_evidence:
        return "unverifiable"
    if contradicted:
        return "outdated"

    settings = get_settings()
    if score >= settings.current_threshold:
        return "current"
    if score >= settings.check_threshold:
        return "check"
    return "outdated"


def is_contradicted(breakdown: ScoreBreakdown) -> bool:
    """True when the evidence points against the claim on balance."""
    return breakdown.contradicting > breakdown.supporting


def answer_score(claim_scores: list[float]) -> float:
    """Reduce per-claim scores to one answer-level score.

    The minimum, not the mean. One outdated sentence makes an answer unsafe to
    act on, and averaging would let nine timeless sentences hide it.
    """
    return min(claim_scores) if claim_scores else 0.0


def answer_ttl_seconds(claim_classes: list[str], default_seconds: int = 86400) -> int:
    """An answer may be cached for as long as its fastest-moving claim allows.

    Timeless claims impose no ceiling, so an answer made only of timeless
    claims keeps the caller's default.
    """
    from .settings import max_answer_ttl

    ceilings: list[int] = []
    for cls in claim_classes:
        ttl = max_answer_ttl(cls)
        if ttl is not None:
            ceilings.append(int(ttl.total_seconds()))

    return min(ceilings) if ceilings else default_seconds


def tier_for_evidence(url: str, subject: str) -> str:
    """Convenience wrapper so callers do not import classify_source directly."""
    return classify_source(url, subject)


def staleness_window(claim_class: str) -> timedelta | None:
    """The re-check interval, exposed for the claim cache's TTL."""
    return recheck_interval(claim_class)
