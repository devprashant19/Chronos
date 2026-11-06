"""Briefing mode: a meeting brief that shows its work.

The same engine, pointed at a different problem. Instead of checking an answer
someone else produced, Chronos produces the answer and then checks itself:

    4 parallel Tavily searches (news, funding, hires, launches, last 30 days)
      → a brief written through Bifrost
      → every line of that brief run back through the verifier
      → split into Verified (80+), Unconfirmed (under 80, with the reason),
        and Open questions (from contradictions and unconfirmed lines)

The point is the third section. A brief that silently drops what it could not
confirm is more dangerous than one that says "I could not confirm this" — the
reader cannot tell the difference between absence and ignorance. So every line
that fails verification survives into the output with its score and its reason
attached, and the contradictions become questions to ask in the room.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from .llm import LLMClient, ModelUnavailable
from .models import BriefLine, BriefRequest, BriefResponse, VerifyRequest
from .settings import Settings, get_settings
from .tavily_client import BudgetExhausted, TavilyClient
from .verify import Verifier

logger = logging.getLogger("chronos.briefing")

#: The four angles a pre-meeting brief actually needs.
SEARCH_ANGLES: tuple[tuple[str, str, str], ...] = (
    ("news", "{subject} news", "news"),
    ("funding", "{subject} funding round investment raise", "news"),
    ("hires", "{subject} appoints hires new executive leadership", "news"),
    ("launches", "{subject} launches announces new product release", "news"),
)

BRIEF_SYSTEM = """You write a short pre-meeting brief from search results.

Today is {today}.

Rules:
- Write 6 to 10 single-sentence bullet points, one fact each.
- Every bullet must be grounded in the supplied results. Do not add anything
  you were not given.
- Put the most decision-relevant facts first.
- State dates explicitly where the result gives one.
- No preamble, no conclusion, no headings. Output only the bullets, one per
  line, each starting with "- ".
"""


class BriefingService:
    def __init__(
        self,
        verifier: Verifier,
        tavily: TavilyClient | None = None,
        llm: LLMClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.verifier = verifier
        self.settings = settings or get_settings()
        self.tavily = tavily or verifier.tavily
        self.llm = llm or verifier.llm

    async def brief(self, req: BriefRequest) -> BriefResponse:
        subject = req.subject.strip()
        today = datetime.now(UTC).date().isoformat()

        degraded = False
        reason: str | None = None
        credits = 0

        # 1. Four searches, in parallel.
        try:
            searches = await asyncio.gather(
                *[
                    self.tavily.search(
                        query.format(subject=subject),
                        topic=topic,
                        search_depth="advanced",
                        max_results=5,
                    )
                    for _angle, query, topic in SEARCH_ANGLES
                ],
                return_exceptions=True,
            )
        except BudgetExhausted as exc:
            return BriefResponse(
                subject=subject,
                meeting_time=req.meeting_time,
                degraded=True,
                reason=str(exc),
            )

        results: list[dict] = []
        for angle, outcome in zip([a[0] for a in SEARCH_ANGLES], searches, strict=False):
            if isinstance(outcome, BudgetExhausted):
                degraded = True
                reason = str(outcome)
                continue
            if isinstance(outcome, BaseException):
                logger.warning("briefing search %s failed: %s", angle, outcome)
                continue
            credits += int(outcome.get("credits", 0))
            for item in outcome.get("results", []):
                item = dict(item)
                item["angle"] = angle
                results.append(item)

        if not results:
            return BriefResponse(
                subject=subject,
                meeting_time=req.meeting_time,
                degraded=True,
                reason=reason or "no recent sources found for this subject",
                tavily_credits=credits,
            )

        # 2. Write the brief.
        lines = await self._write(subject, today, results)
        if not lines:
            return BriefResponse(
                subject=subject,
                meeting_time=req.meeting_time,
                degraded=True,
                reason="could not draft a brief (model unavailable)",
                tavily_credits=credits,
            )

        # 3. Verify every line, then sort by what survived.
        verified: list[BriefLine] = []
        unconfirmed: list[BriefLine] = []
        open_questions: list[str] = []

        reports = await asyncio.gather(
            *[
                self.verifier.verify(
                    VerifyRequest(
                        answer=line,
                        prompt=f"Pre-meeting brief on {subject}",
                        mode="observe",
                        request_id=f"brief-{subject[:20]}",
                    )
                )
                for line in lines
            ],
            return_exceptions=True,
        )

        for line, report in zip(lines, reports, strict=False):
            if isinstance(report, BaseException):
                unconfirmed.append(
                    BriefLine(
                        text=line,
                        verdict="unverifiable",
                        score=0.0,
                        reason=f"verification failed: {report}",
                    )
                )
                continue

            credits += report.tavily_credits
            claim = report.claims[0] if report.claims else None
            entry = BriefLine(
                text=line,
                verdict=report.verdict,
                score=report.score,
                source_url=claim.source_url if claim else None,
                publish_date=claim.publish_date if claim else None,
            )

            # A line with no extractable claim must NOT be presented as
            # verified. It scores 100 and reports "current" only because there
            # was nothing in it to check — the same silent-approval failure
            # that makes a staleness detector dangerous. "Nothing to check" is
            # unconfirmed, and the reason says so.
            confirmed = (
                report.verdict == "current"
                and report.score >= self.settings.current_threshold
                and report.claim_count > 0
            )

            if confirmed:
                verified.append(entry)
            else:
                entry.reason = (
                    "no checkable claim in this line, so nothing was confirmed"
                    if report.claim_count == 0
                    else self._why(report)
                )
                unconfirmed.append(entry)

            for c in report.claims:
                if c.verdict == "outdated" and c.current_value:
                    open_questions.append(
                        f"The brief says {c.subject} {c.predicate} is {c.stated_value or 'as stated'}, "
                        f"but {c.source_url or 'a source'} says {c.current_value}. Which is right?"
                    )

        # Every unconfirmed line becomes something to ask, not just the ones
        # that failed verification outright. A line nobody could check is
        # exactly the line to raise in the room, and leaving it out of this
        # section is how a brief ends up looking more certain than it is.
        for entry in unconfirmed:
            text = entry.text.rstrip(".")
            if entry.verdict == "outdated":
                continue  # already covered by the contradiction questions above
            open_questions.append(f"Could not confirm: {text}. Worth asking directly.")

        # Keep the questions distinct and bounded.
        seen: set[str] = set()
        deduped = []
        for q in open_questions:
            key = q.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(q)

        return BriefResponse(
            subject=subject,
            meeting_time=req.meeting_time,
            verified=verified,
            unconfirmed=unconfirmed,
            open_questions=deduped[:8],
            tavily_credits=credits,
            degraded=degraded,
            reason=reason,
        )

    # ------------------------------------------------------------------

    async def _write(self, subject: str, today: str, results: list[dict]) -> list[str]:
        rendered = "\n\n".join(
            f"[{r.get('angle', 'news')}] {r.get('title', '')}\n"
            f"url: {r.get('url', '')}\n"
            f"published: {r.get('published_date') or 'unknown'}\n"
            f"{(r.get('content') or '')[:800]}"
            for r in results[:16]
        )

        if not self.settings.live_models:
            return _draft_offline(subject, results)

        try:
            text = await self.llm.complete_text(
                system=BRIEF_SYSTEM.format(today=today),
                user=f"Subject: {subject}\n\nSearch results:\n{rendered}",
                model=self.settings.judge_model,
                max_tokens=900,
            )
        except ModelUnavailable as exc:
            logger.warning("brief drafting unavailable: %s", exc)
            return _draft_offline(subject, results)

        return _bullets(text)

    @staticmethod
    def _why(report) -> str:
        """A human reason a line did not make the Verified section."""
        if report.verdict == "unverifiable":
            return report.reason or "no source could confirm this"
        stale = [c for c in report.claims if c.verdict == "outdated"]
        if stale:
            c = stale[0]
            return (
                f"a source dated {c.publish_date or 'recently'} gives "
                f"{c.current_value or 'a different value'}"
            )
        weak = [c for c in report.claims if c.verdict == "check"]
        if weak:
            c = weak[0]
            bd = c.breakdown
            if bd and bd.recency < 0.5:
                return "the only sources found are old relative to how fast this changes"
            if bd and bd.quality < 0.7:
                return "no official or major-news source confirmed it"
            return f"scored {c.score:.0f}, below the confirmation threshold"
        return f"scored {report.score:.0f}, below the confirmation threshold"


_BULLET_RE = re.compile(r"^\s*[-*•]\s*(.+?)\s*$")


def _bullets(text: str) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        m = _BULLET_RE.match(raw)
        if m:
            out.append(m.group(1))
        elif raw.strip() and len(out) == 0 and len(raw.strip()) > 30:
            out.append(raw.strip())
    return out[:10]


def _draft_offline(subject: str, results: list[dict]) -> list[str]:
    """Build a brief straight from result titles, with no model.

    Deliberately plain: it states what each source says and dates it, which is
    all a brief needs to be useful, and it keeps briefing mode demonstrable
    offline.
    """
    lines: list[str] = []
    seen: set[str] = set()

    for r in results[:12]:
        title = _clean_title(r.get("title") or "")
        if not title or len(title) < 20:
            continue
        key = title.lower()[:50]
        if key in seen:
            continue
        seen.add(key)

        published = r.get("published_date")
        when = f" (reported {published})" if published else ""
        lines.append(f"{title}{when}.")
        if len(lines) >= 8:
            break

    if not lines:
        lines.append(f"No recent coverage of {subject} was found in the search window.")
    return lines


def _clean_title(title: str) -> str:
    """Trim the site furniture search results carry.

    Titles arrive as "Real headline | Site Name" or with social-post noise
    appended. Left alone they make brief lines that read like scraped markup,
    and they give the extractor nothing to work with.
    """
    text = title.strip()

    # Drop a trailing " | Publisher" or " - Publisher" segment.
    for separator in (" | ", " — ", " – ", " - "):
        if separator in text:
            head, _, tail = text.rpartition(separator)
            if head and len(head) >= 20 and len(tail) <= 40:
                text = head

    # Social-post scaffolding that is never part of a claim.
    text = re.sub(
        r"\s*\|\s*(posted on the topic|LinkedIn|X \(formerly Twitter\)).*$", "", text, flags=re.I
    )
    return text.strip().rstrip(".").strip()
