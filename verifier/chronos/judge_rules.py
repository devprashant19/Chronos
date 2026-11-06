"""Rule-based judging: deciding whether evidence supports or contradicts a claim.

This runs when no model key is configured, when a model call fails, and in
offline mode. It is the component whose precision the benchmark measures.

The design problem it solves
---------------------------
The naive version — "find a number in the page, compare it to the claim's
value, call a mismatch a contradiction" — produces false positives constantly.
Judging "Satya Nadella is the CEO of Microsoft" against a leadership page, it
finds some unrelated figure in the text, sees it differs from "Satya Nadella",
and reports a contradiction. Measured on StaleBench, that alone held precision
near 0.5 while recall was 0.87: the detector was crying wolf.

So comparison here is *typed*. A claim's stated value has a kind — money, a
version number, a percentage, a person's name, or free text — and evidence can
only contradict it with a value of the same kind. A page full of numbers says
nothing about who runs a company.

For names and phrases, where there is no number to compare, presence is the
signal: the name appearing in an authoritative page supports the claim, and its
absence from an authoritative page is evidence against. Absence from a page of
unknown quality is treated as insufficient rather than as a contradiction,
because "I did not find it" and "it is false" are different statements.
"""

from __future__ import annotations

import re

from .models import Claim, Evidence

# ---------------------------------------------------------------------------
# value kinds
# ---------------------------------------------------------------------------

MONEY = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn|billion|million|trillion))?", re.I)
VERSION = re.compile(r"\bv?\d+(?:\.\d+)+\b")
BARE_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")
# No trailing \b: "%" is not a word character, so \b cannot match after it at
# the end of a string, and "7.25%" fell through to the version pattern.
PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|percent\b)", re.I)
NAME = re.compile(r"^[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,3}$")

#: Predicates whose value is a version even when written as a bare integer,
#: e.g. "Node.js 18 is the current LTS release".
VERSIONISH_PREDICATE = re.compile(r"version|release|lts", re.I)


def value_kind(value: str, predicate: str = "") -> str:
    """Classify a stated value so it is only compared with its own kind."""
    v = (value or "").strip()
    if not v:
        return "empty"
    if MONEY.search(v):
        return "money"
    if PERCENT.search(v):
        return "percent"
    if VERSION.search(v):
        return "version"
    if VERSIONISH_PREDICATE.search(predicate) and BARE_NUMBER.search(v):
        return "version"
    if NAME.match(v):
        return "name"
    return "phrase"


def extract_values(text: str, kind: str, predicate: str = "") -> list[str]:
    """Pull every value of one kind out of a page."""
    window = text[:6000]
    if kind == "money":
        return [m.group(0) for m in MONEY.finditer(window)]
    if kind == "percent":
        return [m.group(0) for m in PERCENT.finditer(window)]
    if kind == "version":
        found = [m.group(0) for m in VERSION.finditer(window)]
        if not found and VERSIONISH_PREDICATE.search(predicate):
            found = [m.group(0) for m in BARE_NUMBER.finditer(window)]
        return found
    return []


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_in(value: str) -> list[float]:
    out: list[float] = []
    for raw in _NUM.findall(value or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def values_agree(stated: str, found: str, tolerance: float = 0.01) -> bool:
    """Whether two same-kind values say the same thing.

    Numeric comparison, because "$5.00" and "5 dollars" are the same price and
    a string compare would call them a contradiction.
    """
    s, f = (stated or "").strip().lower(), (found or "").strip().lower()
    if not s or not f:
        return True
    if s == f:
        return True

    s_nums, f_nums = numbers_in(s), numbers_in(f)
    if s_nums and f_nums:
        for a in s_nums:
            for b in f_nums:
                if a == b:
                    return True
                scale = max(abs(a), abs(b), 1e-9)
                if abs(a - b) / scale <= tolerance:
                    return True
        return False

    return s in f or f in s


def _sentence_with(text: str, needle: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+", text[:6000]):
        if needle and needle.lower() in sentence.lower():
            return sentence.strip()[:400]
    return ""


def _mentions(text: str, value: str) -> bool:
    """Case-insensitive presence, tolerant of surname-only mentions."""
    low = (text or "").lower()
    v = (value or "").strip().lower()
    if not v:
        return False
    if v in low:
        return True
    # "Satya Nadella" also counts as present if the page says "Nadella".
    parts = [p for p in v.split() if len(p) > 3]
    return bool(parts) and parts[-1] in low


#: Role words, pulled from a predicate like "holder of role (CEO)".
_ROLE_WORDS = re.compile(
    r"\b(chief executive|ceo|cto|cfo|coo|president|prime minister|chancellor|monarch|king|queen|"
    r"chair(?:man|person|woman)?|head of state|secretary|minister|governor|mayor|founder|"
    r"head coach|coach|director)\b",
    re.I,
)

#: A person-shaped name: two or three capitalised words.
_PERSON = r"[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z.]{1,15}){1,2}"

_ROLE_ALT = (
    r"(?:chief executive|CEO|CTO|CFO|COO|president|prime minister|chancellor|monarch|"
    r"king|queen|chair(?:man|person|woman)?)"
)

#: Grammatical constructions that actually *assert* who holds a role. Nothing
#: looser is admissible: a ±160-character window around the word "CEO" picked
#: up section headings ("World Politics"), product names ("Meta Quest") and
#: journalist bylines ("Sebastian Herrera") as rival role-holders, which held
#: precision at 0.47 on StaleBench.
_ROLE_ASSERTIONS = [
    # "CEO Tim Cook", "new Apple CEO John Ternus", "chief executive is Lisa Su"
    re.compile(_ROLE_ALT + r"\s+(?:of\s+[\w .&-]{1,30}\s+)?(?:is\s+)?(?P<name>" + _PERSON + r")\b"),
    # "Tim Cook, the CEO of Apple", "Lisa Su, CEO"
    re.compile(r"(?P<name>" + _PERSON + r"),?\s+(?:the\s+|who\s+is\s+)?" + _ROLE_ALT + r"\b"),
    # "Tim Cook was named CEO", "Lisa Su became chief executive"
    re.compile(
        r"(?P<name>" + _PERSON + r")\s+(?:was named|became|takes over as|has been appointed)\s+"
        r"(?:the\s+)?" + _ROLE_ALT + r"\b"
    ),
]

#: Words that disqualify a match as a person's name.
_NOT_A_PERSON = re.compile(
    r"\b(World|Politics|Business|Technology|Markets|Opinion|News|Latest|Breaking|Big Tech|"
    r"Quest|Studio|Cloud|Store|Center|Centre|Report|Update|Read|More|Share|Follow|"
    r"Subscribe|Sign|Menu|Home|About|Contact|Privacy|Terms)\b"
)


#: Place and organisation names that match the two-capitalised-words shape of a
#: person but are not people. "United Kingdom" was reported as a rival monarch.
_NOT_A_PERSON_PHRASE = re.compile(
    r"\b(United (?:Kingdom|States|Nations|Arab)|New (?:York|Zealand|Delhi)|South (?:Korea|Africa)|"
    r"North (?:Korea|America)|Hong Kong|Saudi Arabia|European Union|White House|Silicon Valley|"
    r"Wall Street|Big Tech|Supreme Court|Prime Minister|Chief Executive)\b"
)


def role_assertions(text: str, predicate: str, stated: str) -> tuple[bool, list[str]]:
    """Who this page *asserts* holds the role.

    Returns (asserts_stated, other_names).

    Both halves come from the same grammatical patterns, which is what makes
    the judgement symmetric: the stated name is only "supported" if the page
    asserts it in the role, not merely mentions it. That distinction matters —
    a page reporting "Dorsey stepped down in 2021" mentions Dorsey while
    saying the opposite of "Dorsey is the CEO".
    """
    if not (_ROLE_WORDS.search(predicate) or _ROLE_WORDS.search(stated)):
        return False, []

    stated_tokens = {t.lower() for t in stated.split() if len(t) > 2}
    window_text = text[:6000]

    asserts_stated = False
    others: list[str] = []

    for pattern in _ROLE_ASSERTIONS:
        for m in pattern.finditer(window_text):
            person = m.group("name").strip()
            tokens = {t.lower() for t in person.split() if len(t) > 2}
            if not tokens:
                continue
            if tokens & stated_tokens:
                asserts_stated = True
                continue
            if (
                _looks_like_org(person)
                or _NOT_A_PERSON.search(person)
                or _NOT_A_PERSON_PHRASE.search(person)
            ):
                continue
            others.append(person)

    return asserts_stated, others


_ORG_HINT = re.compile(
    r"\b(Inc|Ltd|LLC|Corp|Company|Group|Holdings|Platforms|Technologies|University|"
    r"Institute|Foundation|Department|Ministry|Office|Board|Council|News|Times|Post|"
    r"Journal|Reuters|Press|Media|Research|Labs?|Systems|Solutions|Partners)\b"
)


def _looks_like_org(phrase: str) -> bool:
    return bool(_ORG_HINT.search(phrase))


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse a version into comparable integer components.

    Pads nothing: (3,) < (3, 15) under tuple comparison, which is the ordering
    we want — "3" is not newer than "3.15".
    """
    parts = re.findall(r"\d+", value or "")
    return tuple(int(p) for p in parts[:4]) if parts else (0,)


def _versions_compatible(stated: str, found: str) -> bool:
    """Whether two version strings describe the same line.

    "Python 3" and "3.15" agree: 3 is the major line 3.15 belongs to. So do
    "3.11" and "3.11.9". Comparing them numerically would call 3 and 3.15 a
    4% mismatch and report a contradiction, which is how "Python 3 is the
    current major version line" came back outdated.
    """
    s_parts = re.findall(r"\d+", stated or "")
    f_parts = re.findall(r"\d+", found or "")
    if not s_parts or not f_parts:
        return False
    # Agree when the shorter is a component-wise prefix of the longer.
    shorter, longer = (s_parts, f_parts) if len(s_parts) <= len(f_parts) else (f_parts, s_parts)
    return longer[: len(shorter)] == shorter


def judge(
    claim: Claim, evidence: list[Evidence], recorded: dict[str, dict] | None = None
) -> dict:
    """Judge evidence for one claim. Mirrors the model judge's output shape."""
    recorded = recorded or {}
    kind = value_kind(claim.stated_value, claim.predicate)

    sources: list[dict] = []
    current_value = ""
    confident = False

    # Role claims are adjudicated across all sources at once, because a single
    # page's parse is not enough to condemn a claim and a direct confirmation
    # anywhere outweighs silence elsewhere. See _judge_roles.
    role_calls: dict[str, tuple[str, str]] | None = None
    if kind == "name":
        role_calls, current_value = _judge_roles(claim, evidence, recorded)

    for e in evidence:
        note = recorded.get(e.url) or {}

        # A recorded fixture carries its own judgement; read it back.
        raw_call = str(note.get("call") or "").lower()
        if raw_call in {"supports", "contradicts", "insufficient"}:
            if note.get("current_value"):
                current_value = current_value or str(note["current_value"])
            sources.append(
                {"url": e.url, "call": raw_call, "quote": str(note.get("quote") or "")[:600]}
            )
            if raw_call != "insufficient":
                confident = True
            continue

        if role_calls is not None:
            call, quote = role_calls.get(e.url, ("insufficient", ""))
        else:
            call, quote, found = _judge_one(claim, e, kind)
            if found and not current_value:
                current_value = found

        if call != "insufficient":
            confident = True
        sources.append({"url": e.url, "call": call, "quote": quote[:600]})

    if not current_value and not any(s["call"] == "contradicts" for s in sources):
        # Nothing contradicted it, so the stated value stands as current.
        current_value = claim.stated_value

    best = next((s["url"] for s in sources if s["call"] == "contradicts"), None) or next(
        (s["url"] for s in sources if s["call"] == "supports"), None
    )

    return {
        "sources": sources,
        "current_value": current_value,
        # Confidence is modest by design: this is a rule engine, not a reader.
        "confidence": 0.7 if confident else 0.2,
        "best_source_url": best or (evidence[0].url if evidence else ""),
        "publish_date": next((e.published_date for e in evidence if e.published_date), None),
    }


#: How many independent sources must assert the same rival role-holder before
#: Chronos will call a role claim outdated.
ROLE_CORROBORATION = 2


def _judge_roles(
    claim: Claim, evidence: list[Evidence], recorded: dict[str, dict]
) -> tuple[dict[str, tuple[str, str]], str]:
    """Adjudicate a role claim across every source at once.

    Two rules, both there to stop a noisy page condemning a correct claim:

    1. A page that asserts the stated person in the role *supports* it, and any
       such confirmation blocks a contradiction. Direct evidence beats absence.
    2. A rival name must be asserted by at least ROLE_CORROBORATION separate
       sources to count. One page naming someone else is as likely to be a
       parsing artefact — a byline, a heading, a quoted third party — as a
       genuine change.

    Returns ({url: (call, quote)}, current_value).
    """
    per_source: dict[str, tuple[bool, list[str]]] = {}
    for e in evidence:
        if e.url in recorded:
            continue
        per_source[e.url] = role_assertions(e.snippet or "", claim.predicate, claim.stated_value)

    supporting_urls = [u for u, (asserts, _) in per_source.items() if asserts]

    # Count rival names by surname, so "Linda Yaccarino" and "Yaccarino" agree.
    tally: dict[str, int] = {}
    display: dict[str, str] = {}
    for _url, (_asserts, others) in per_source.items():
        seen_here: set[str] = set()
        for name in others:
            key = name.split()[-1].lower()
            if key in seen_here:
                continue
            seen_here.add(key)
            tally[key] = tally.get(key, 0) + 1
            display.setdefault(key, name)

    rival_key = ""
    if tally:
        rival_key = max(tally, key=lambda k: tally[k])

    corroborated = bool(rival_key) and tally.get(rival_key, 0) >= ROLE_CORROBORATION
    contradict = corroborated and not supporting_urls

    calls: dict[str, tuple[str, str]] = {}
    for url, (asserts, others) in per_source.items():
        if asserts:
            calls[url] = ("supports", _sentence_with_name(evidence, url, claim.stated_value))
        elif contradict and any(n.split()[-1].lower() == rival_key for n in others):
            calls[url] = ("contradicts", _sentence_with_name(evidence, url, display[rival_key]))
        else:
            calls[url] = ("insufficient", "")

    if contradict:
        return calls, display[rival_key]
    if supporting_urls:
        return calls, claim.stated_value
    return calls, ""


def _sentence_with_name(evidence: list[Evidence], url: str, name: str) -> str:
    for e in evidence:
        if e.url == url:
            needle = name.split()[-1] if name else ""
            return _sentence_with(e.snippet or "", needle)
    return ""


def _judge_one(claim: Claim, e: Evidence, kind: str) -> tuple[str, str, str]:
    """Judge one source. Returns (call, quote, found_value)."""
    text = e.snippet or ""
    if not text:
        return "insufficient", "", ""

    # Typed comparison: only a value of the same kind can contradict.
    if kind in {"money", "percent", "version"}:
        candidates = extract_values(text, kind, claim.predicate)
        if not candidates:
            return "insufficient", "", ""

        # A "latest version" claim is a claim about the MAXIMUM, so the page's
        # highest version is the candidate current value — not merely any
        # version it happens to mention. Matching on mere presence counted
        # "Python 3.11 reached end of life" as support for "3.11 is the latest
        # release", which is the opposite of what the page says.
        if kind == "version" and VERSIONISH_PREDICATE.search(claim.predicate):
            highest = max(candidates, key=_version_tuple)
            stated_t = _version_tuple(claim.stated_value)
            highest_t = _version_tuple(highest)

            if stated_t >= highest_t or _versions_compatible(claim.stated_value, highest):
                return "supports", _sentence_with(text, highest), highest
            return "contradicts", _sentence_with(text, highest), highest

        for cand in candidates:
            if values_agree(claim.stated_value, cand):
                return "supports", _sentence_with(text, cand), cand
            if kind == "version" and _versions_compatible(claim.stated_value, cand):
                return "supports", _sentence_with(text, cand), cand

        # Same kind of value present, none of them matching: a real conflict.
        return "contradicts", _sentence_with(text, candidates[0]), candidates[0]

    # kind == "name" never reaches here. Role claims are adjudicated across all
    # sources at once in _judge_roles, because the decision needs corroboration
    # that a single source cannot provide, and because absence from one page is
    # not evidence against a claim.

    # Free text: presence supports; absence is not proof of the negative.
    if kind == "phrase":
        key_tokens = re.findall(r"[a-z]{4,}", claim.stated_value.lower())[:4]
        if key_tokens and all(t in text.lower() for t in key_tokens):
            return "supports", _sentence_with(text, key_tokens[0]), claim.stated_value
        return "insufficient", "", ""

    return "insufficient", "", ""
