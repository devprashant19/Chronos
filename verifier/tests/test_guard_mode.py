"""Guard mode, the verifier's half: rewrite a stale answer, or say it must be blocked.

The plugin substitutes `corrected_answer` or returns a 409 when `blocked` is
set (plugin/guard_test.go holds that half). Until 8 Nov neither half had a
test, though the README said they did. These drive the real `Verifier.verify`
with a stale answer, a contradicting page and a judge that agrees with it.
"""

from __future__ import annotations

import asyncio

import pytest

from chronos import verify as verify_module
from chronos.cache import ClaimCache
from chronos.models import Claim, ExtractionResult, VerifyRequest
from chronos.settings import Settings
from chronos.verify import Verifier

STALE = "The latest stable release of Python is 3.11."


class Budget:
    daily_limit = 400
    remaining = 400


class OnePage:
    budget = Budget()
    live = True

    async def search(self, query, **_kwargs):
        return {
            "credits": 1,
            "results": [{
                "url": "https://www.python.org/downloads/",
                "title": "Download Python",
                "content": "The latest stable release is Python 3.14.",
                "published_date": "2026-11-01",
            }],
        }

    async def extract(self, urls):
        return {"credits": 1, "pages": []}


class Judge:
    """Calls the page a contradiction, and rewrites when asked to."""

    def __init__(self, rewrite: str | None) -> None:
        self.rewrite = rewrite
        self.rewrite_prompt = ""

    async def complete_json(self, *, user, **_kwargs):
        return {
            "sources": [{"url": "https://www.python.org/downloads/", "call": "contradicts",
                         "quote": "The latest stable release is Python 3.14."}],
            "current_value": "3.14",
            "confidence": 0.95,
            "best_source_url": "https://www.python.org/downloads/",
            "publish_date": "2026-11-01",
        }

    async def complete_text(self, *, system, user, **_kwargs):
        self.rewrite_prompt = user
        if self.rewrite is None:
            raise verify_module.ModelUnavailable("no model")
        return self.rewrite

    async def aclose(self):
        pass


@pytest.fixture
def one_claim(monkeypatch):
    claim = Claim(
        text=STALE, span_start=0, span_end=len(STALE), subject="Python",
        predicate="latest stable release version", stated_value="3.11", **{"class": "version"},
    )

    async def fake_extract(answer, llm=None, settings=None):
        return ExtractionResult(claims=[claim], model_used="test")

    monkeypatch.setattr(verify_module, "extract_claims", fake_extract)


def run(judge: Judge, *, regenerate: bool, live_models: bool = True):
    settings = Settings(offline_demo=False, live_models_override="1" if live_models else "0")
    verifier = Verifier(settings, ClaimCache(settings), OnePage(), judge)  # type: ignore[arg-type]
    req = VerifyRequest(answer=STALE, mode="guard", regenerate=regenerate, threshold=50)
    return asyncio.run(verifier.verify(req))


def test_guard_rewrites_a_stale_answer_with_the_evidence(one_claim):
    judge = Judge(rewrite="The latest stable release of Python is 3.14 (https://www.python.org/downloads/).")
    report = run(judge, regenerate=True)

    assert report.verdict == "outdated"
    assert report.corrected_answer and "3.14" in report.corrected_answer
    assert report.blocked is False
    # The rewrite is told exactly what to change and where the evidence is.
    assert "'3.11'" in judge.rewrite_prompt and "'3.14'" in judge.rewrite_prompt
    assert "https://www.python.org/downloads/" in judge.rewrite_prompt


def test_guard_blocks_when_no_rewrite_is_possible(one_claim):
    report = run(Judge(rewrite=None), regenerate=True)
    assert report.verdict == "outdated"
    assert report.corrected_answer is None
    assert report.blocked is True


def test_guard_without_a_model_blocks_rather_than_serving_the_stale_answer(one_claim):
    report = run(Judge(rewrite="never used"), regenerate=True, live_models=False)
    assert report.blocked is True


def test_annotate_never_rewrites_or_blocks(one_claim):
    report = run(Judge(rewrite="never used"), regenerate=False)
    assert report.verdict == "outdated"
    assert report.corrected_answer is None
    assert report.blocked is False
