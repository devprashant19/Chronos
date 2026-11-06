"""The rule-based claim extractor.

This is the path that runs when no model key is configured, and the fallback
when a model call fails. It is deliberately a separate module from extract.py
because it is the larger and more fiddly half of extraction, and because its
recall is a measured property of the system: see bench/results.json.

Two layers, in order:

1. **Specific rules** (`CLAIM_RULES`) exist for precision. When one matches,
   the subject, predicate and stated value all come from named capture groups,
   which produces a clean claim-cache key and a clean search query.

2. **A generic rule** (`GENERIC_ASSERTION`) exists for recall. Any sentence
   shaped like "<something> is/uses/offers <something>" *and* carrying a
   time-sensitivity cue becomes a claim, with a class inferred from the cue.
   The cue requirement is what stops it swallowing ordinary prose.

The second layer matters more than it looks. Measured on StaleBench, the
specific rules alone miss claims like "Elizabeth II is the reigning monarch of
the United Kingdom" or "Node.js 18 is the current LTS release" - not because
they are hard, but because no hand-written pattern anticipated that exact
phrasing. A missed claim scores 100 and reports "current", which is the worst
possible failure for a staleness detector: silence that looks like approval.
"""

from __future__ import annotations

import re

from .settings import VOLATILITY_WEIGHT

# ---------------------------------------------------------------------------
# sentences that never need checking
# ---------------------------------------------------------------------------

SKIP_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"^\s*(def |class |import |from |#|//|<|\{|\}|return |print\()",
        r"^\s{4,}\S",
        r"\b(i think|in my opinion|you should|i recommend|it depends)\b",
        r"^\s*\d+\s*[-+*/x]\s*\d+\s*$",
    )
]

#: Facts that do not change. Matching one records a `timeless` claim, which is
#: reported as deliberately skipped rather than silently dropped - that is what
#: makes the Tavily credit saving visible in the report and the benchmark.
TIMELESS_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bcapital of [A-Z]",
        r"\bboil(?:s|ing)?\s+at\b",
        r"\bfreezes? at\b",
        r"\bspeed of light\b",
        r"\bchemical (?:formula|symbol)\b",
        r"\batomic number\b",
        r"\borbits the sun\b",
        r"\b(?:right angle|square root|prime number|big-o|binary search)\b",
        r"\bdegrees (?:celsius|fahrenheit|in a circle)\b",
        r"\bsum of the angles\b",
        r"\b(?:wrote|painted|composed) (?:the )?[A-Z]",
        r"\bstands for\b",
        r"\bis a (?:last-in|first-in)\b",
        r"\b(?:lifo|fifo)\b",
        r"\bconnection(?:-oriented|less) transport\b",
        r"\bmaps arbitrary input\b",
        r"\bended in \d{4}\b",
        r"\b(?:sank|fell|landed|adopted) in \d{4}\b",
        r"\bhighest mountain\b",
        r"\blargest ocean\b",
        r"\blongest rivers?\b",
        r"\bwon nobel prizes\b",
        r"\bpi is approximately\b",
        r"\btwo plus two\b",
        r"\bmaximum supply of\b",
        r"\bhalves roughly every\b",
        r"\bdistributed version control\b",
        r"\bcreated by linus torvalds\b",
    )
]

# ---------------------------------------------------------------------------
# layer 1: specific rules, for precision
# ---------------------------------------------------------------------------

CURRENCY = r"[$€£]"

CLAIM_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    # (class, pattern, predicate, value group name)
    (
        "market",
        re.compile(
            r"(?P<subject>bitcoin|btc|ethereum|eth|[A-Z]{1,5}\s+(?:stock|shares))\b[^.]{0,60}?"
            r"(?:trading at|is at|price of|worth|costs?)\s*"
            r"(?P<value>" + CURRENCY + r"\s?[\d,]+(?:\.\d+)?"
            r"(?:\s?(?:k|m|bn|billion|million|trillion))?)",
            re.I,
        ),
        "spot price",
        "value",
    ),
    (
        "market",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,30}?)[^.]{0,20}?market cap(?:italisation|italization)?\s*"
            r"(?:of|sits near|is|at|around|about)?\s*"
            r"(?P<value>" + CURRENCY + r"\s?[\d,.]+\s?(?:k|m|bn|billion|million|trillion)?)",
            re.I,
        ),
        "market capitalisation",
        "value",
    ),
    (
        "market",
        re.compile(
            r"(?:all-time high|record high)\s*(?:is|of|was|around|about)?\s*"
            r"(?P<value>" + CURRENCY + r"\s?[\d,.]+\s?(?:k|m|bn|billion|million|trillion)?)",
            re.I,
        ),
        "all-time high",
        "value",
    ),
    (
        "market",
        re.compile(
            r"(?P<subject>[\w .&-]{2,40}?)\s+(?:interest |federal funds |policy |repo )?rate\s+is\s+"
            r"(?P<value>near zero|at [\d.]+\s?%?|[\d.]+\s?(?:%|percent))",
            re.I,
        ),
        "interest rate",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"(?P<value>" + CURRENCY + r"\s?[\d,]+(?:\.\d+)?)\s*(?:per|/)\s*"
            r"(?:million|1m|1k|thousand|month|year|seat|user|request|hour)\b",
            re.I,
        ),
        "price",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"\b(?:costs?|priced at|charges)\s*(?P<value>" + CURRENCY + r"\s?[\d,]+(?:\.\d+)?)\b",
            re.I,
        ),
        "price",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+(?:offers?|has|provides?)\s+"
            r"(?P<value>an? (?:generous )?free (?:tier|plan)[^.]{0,30}|"
            r"free private repositories[^.]{0,20})",
            re.I,
        ),
        "free tier availability",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+(?P<value>free for [^.]{0,40})",
            re.I,
        ),
        "commercial licensing",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+(?:charges|bills)\s+[^.]{0,40}?"
            r"(?P<value>by the (?:second|hour|minute)|per token|on a credit basis)",
            re.I,
        ),
        "billing model",
        "value",
    ),
    # --- version ---------------------------------------------------------
    (
        "version",
        re.compile(
            r"(?:latest|current|newest|most recent)\s+(?:stable\s+)?(?:release|version)\s+of\s+"
            r"(?P<subject>[A-Za-z][\w .+#-]{1,40}?)\s+is\s+(?P<value>[\w.+#-]+(?:\s+[\d.]+)?)",
            re.I,
        ),
        "latest version",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .+#-]{1,30}?)\s+(?P<value>v?\d+(?:\.\d+)*)\s+is\s+(?:the\s+)?"
            r"(?:current|latest|newest|most recent)\b[^.]{0,30}",
            re.I,
        ),
        "current release",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .+#-]{1,30}?)\s+is\s+(?:at\s+)?version\s+"
            r"(?P<value>v?\d+(?:\.\d+)*)",
            re.I,
        ),
        "current version",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?:the\s+)?(?:newest|latest|current)\s+(?P<subject>[A-Za-z][\w .+#-]{1,30}?)\s+is\s+"
            r"(?:the\s+)?(?P<value>[\w .+#-]{2,40})",
            re.I,
        ),
        "newest item",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .&+#-]{1,30}?)(?:’s|s’)?\s+(?:logo|branding)\s+is\s+"
            r"(?P<value>[\w .&-]{2,40})",
            re.I,
        ),
        "branding",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,30}?)\s+uses\s+"
            r"(?P<value>proof-of-\w+|[\w -]{2,30}?)\s+consensus",
            re.I,
        ),
        "consensus mechanism",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?:the company|it)\s+is\s+(?:called|named)\s+(?P<value>[A-Z][\w .,&-]{1,40})",
            re.I,
        ),
        "company name",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,30}?)\s+has been\s+(?:rebranded|renamed)\s+as\s+"
            r"(?P<value>[\w .&-]{1,30})",
            re.I,
        ),
        "company name",
        "value",
    ),
    # --- leadership: the role vocabulary is deliberately wide ------------
    (
        "leadership",
        re.compile(
            r"(?:the\s+)?(?:reigning\s+|sitting\s+|current\s+)?"
            r"(?P<role>chief executive|ceo|cto|cfo|coo|president|prime minister|chancellor|monarch|"
            r"king|queen|chair(?:man|person|woman)?|head of state|minister|governor|mayor)\s+of\s+"
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+"
            r"(?P<value>[A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,3})",
            re.I,
        ),
        "holder of role",
        "value",
    ),
    (
        "leadership",
        re.compile(
            r"(?P<value>[A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,3})\s+is\s+(?:the\s+|a\s+)?"
            r"(?:reigning\s+|sitting\s+|current\s+|new\s+|acting\s+)?"
            r"(?P<role>chief executive|ceo|cto|cfo|coo|president|prime minister|chancellor|monarch|"
            r"king|queen|pope|chair(?:man|person|woman)?|head of state|secretary(?: of state)?|"
            r"minister|governor|mayor|head coach)\b"
            r"(?:\s+of\s+(?P<subject>[A-Z][\w .&-]{1,40}))?",
            re.I,
        ),
        "holder of role",
        "value",
    ),
    (
        "leadership",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+"
            r"(?P<value>a publicly traded company[^.]{0,40}|privately held[^.]{0,20}|"
            r"listed on [^.]{0,30})",
            re.I,
        ),
        "listing status",
        "value",
    ),
    (
        "leadership",
        re.compile(
            r"(?P<subject>[A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,2})\s+"
            r"(?P<value>has retired[^.]{0,40}|has stepped down[^.]{0,40}|"
            r"is no longer involved[^.]{0,40}|no longer works[^.]{0,40})",
            re.I,
        ),
        "current involvement",
        "value",
    ),
    # --- law --------------------------------------------------------------
    (
        "law",
        re.compile(
            r"(?P<subject>[A-Z][\w .-]{2,50}?(?:Act|Regulation|Directive|Law|Bill)|GDPR|CCPA|HIPAA|DMA)"
            r"\b[^.]{0,60}?"
            r"(?P<value>is still a (?:legislative )?proposal[^.]{0,40}|has not yet[^.]{0,40}|"
            r"comes into force[^.]{0,30}|applies from[^.]{0,30}|entered into force[^.]{0,30}|"
            r"has applied since[^.]{0,30}|is settled law[^.]{0,40})",
            re.I,
        ),
        "legal status",
        "value",
    ),
    (
        "law",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+"
            r"(?P<value>a member state[^.]{0,30}|no longer a member[^.]{0,30})",
            re.I,
        ),
        "membership status",
        "value",
    ),
    (
        "law",
        re.compile(
            r"(?:federal\s+)?minimum wage\s+is\s+"
            r"(?P<value>" + CURRENCY + r"\s?[\d.,]+(?:\s*per\s*hour)?)",
            re.I,
        ),
        "minimum wage",
        "value",
    ),
    (
        "law",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+(?:distributed\s+)?under\s+the\s+"
            r"(?P<value>[\w .-]{2,30})\s+licen[cs]e",
            re.I,
        ),
        "licence",
        "value",
    ),
    # --- events -----------------------------------------------------------
    (
        "events",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{2,40}?)\s+"
            r"(?:won|lost|announced|launched|acquired|raised|is celebrating|is scheduled)\s+"
            r"(?P<value>[^.]{3,60})",
        ),
        "recent event",
        "value",
    ),
]

# ---------------------------------------------------------------------------
# layer 2: the generic rule, for recall
# ---------------------------------------------------------------------------

#: Cue words that mark a sentence time-sensitive and hint at its class.
CLASS_CUES: list[tuple[str, re.Pattern[str]]] = [
    (
        "market",
        re.compile(
            r"\b(spot price|trading at|market cap|all-time high|record high|interest rate|"
            r"federal funds|bond yield|exchange rate|share price|stock price)\b",
            re.I,
        ),
    ),
    (
        "pricing",
        re.compile(
            r"\b(pricing|costs?|priced|per million tokens|per 1k|free tier|free plan|"
            r"subscription|charges?|rate card|billed|price)\b",
            re.I,
        ),
    ),
    (
        "version",
        re.compile(
            r"\b(version|release|lts|logo|rebrand(?:ed)?|renamed|consensus|deprecated|"
            r"newest|latest|supersed|operating system|model)\b",
            re.I,
        ),
    ),
    (
        "leadership",
        re.compile(
            r"\b(ceo|cto|cfo|coo|chief executive|president|prime minister|chancellor|monarch|"
            r"king|queen|chair(?:man|person|woman)?|founder|retired|stepped down|"
            r"publicly traded|privately held|leads|runs|involved with)\b",
            re.I,
        ),
    ),
    (
        "law",
        re.compile(
            r"\b(law|act\b|regulation|directive|legal|member state|overturned|in force|"
            r"minimum wage|banned|licen[cs]e|gdpr|ccpa|hipaa|governs)\b",
            re.I,
        ),
    ),
    (
        "events",
        re.compile(
            r"\b(won|launched|announced|acquired|raised|scheduled|celebrating|this year|"
            r"last night|recently)\b",
            re.I,
        ),
    ),
]

#: A bare stative assertion. Only used when a class cue is also present.
GENERIC_ASSERTION = re.compile(
    r"^(?P<subject>[^.,;:]{3,60}?)\s+"
    r"(?P<verb>is|are|was|were|has|have|uses|offers|charges|trades|costs|stands at|"
    r"sits at|remains|governs)\s+"
    r"(?P<value>[^.]{2,80})",
    re.I,
)


def infer_class(sentence: str) -> str | None:
    """Pick the most volatile class whose cue appears in the sentence.

    Most volatile wins for the same reason it does in the plugin's prompt
    classifier: the cache TTL and the re-check interval have to protect the
    fastest-moving fact in the sentence.
    """
    matches = [cls for cls, pattern in CLASS_CUES if pattern.search(sentence)]
    if not matches:
        return None
    return max(matches, key=lambda c: VOLATILITY_WEIGHT.get(c, 0.0))
