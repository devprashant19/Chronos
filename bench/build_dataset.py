#!/usr/bin/env python3
"""Build bench/stalebench.json — 120 hand-labelled claims.

Why this is a script rather than a hand-edited JSON blob: the labels need
provenance, and the script is where that provenance is written down and
checked. Running it regenerates the dataset deterministically.

How the three groups are labelled, and how much to trust each:

  stale (40)
      Claims that were true once and are now wrong. These are the strongest
      labels in the set: each one turned false at a specific, publicly
      documented moment (a CEO resigned, a company was renamed, a newer
      release shipped), so "this is no longer true" does not depend on
      knowing today's value.

  current (40)
      Claims believed true as of DATASET_DATE. These are the weakest labels,
      and the honest caveat is that they are the ones that rot: a `current`
      label is a snapshot of public knowledge on the date below, not a
      permanent fact. Each carries a `confidence` field, and the runner
      reports label disagreements separately (see --audit) instead of
      silently scoring them as model errors. Treat a `current`-label
      disagreement as "re-check the label" first and "the system is wrong"
      second.

  timeless (40)
      Facts that do not change: physical constants, definitions, geography,
      settled history, arithmetic. These exist to measure the skip rate —
      the share of claims Chronos correctly declines to spend a Tavily
      credit on. They are the cheapest correctness win in the system.

`source` is an authoritative *domain or page* for the subject, not a
hand-invented deep link to an article that may not exist.
"""

from __future__ import annotations

import json
from pathlib import Path

DATASET_DATE = "2026-11-06"
DATASET_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# (claim text, class, subject, predicate, stated value, source, why it is stale)
# ---------------------------------------------------------------------------

STALE: list[tuple[str, str, str, str, str, str, str]] = [
    ("Jack Dorsey is the CEO of Twitter.", "leadership", "Twitter", "chief executive", "Jack Dorsey",
     "https://about.x.com/en/who-we-are/our-company", "Dorsey stepped down as CEO in November 2021."),
    ("Twitter is a publicly traded company on the New York Stock Exchange.", "leadership", "Twitter", "listing status", "publicly traded on the NYSE",
     "https://about.x.com/en/who-we-are/our-company", "Taken private after the October 2022 acquisition."),
    ("The company is called Facebook, Inc.", "leadership", "Facebook Inc", "company name", "Facebook, Inc.",
     "https://about.fb.com/company-info/", "Renamed Meta Platforms, Inc. in October 2021."),
    ("Larry Page is the CEO of Google.", "leadership", "Google", "chief executive", "Larry Page",
     "https://about.google/belonging/leadership/", "Sundar Pichai became Google CEO in 2015 and Alphabet CEO in 2019."),
    ("Steve Ballmer is the CEO of Microsoft.", "leadership", "Microsoft", "chief executive", "Steve Ballmer",
     "https://news.microsoft.com/leadership/", "Satya Nadella became CEO in February 2014."),
    ("Boris Johnson is the Prime Minister of the United Kingdom.", "leadership", "United Kingdom", "prime minister", "Boris Johnson",
     "https://www.gov.uk/government/ministers/prime-minister", "Resigned as PM in September 2022; the office has changed hands since."),
    ("Elizabeth II is the reigning monarch of the United Kingdom.", "leadership", "United Kingdom", "monarch", "Elizabeth II",
     "https://www.royal.uk/", "Queen Elizabeth II died in September 2022; Charles III acceded."),
    ("Angela Merkel is the Chancellor of Germany.", "leadership", "Germany", "chancellor", "Angela Merkel",
     "https://www.bundesregierung.de/breg-en", "Left office in December 2021."),
    ("Jeff Bezos is the CEO of Amazon.", "leadership", "Amazon", "chief executive", "Jeff Bezos",
     "https://www.aboutamazon.com/about-us/leadership", "Andy Jassy became CEO in July 2021."),
    ("Bob Iger has retired and is no longer involved with Disney.", "leadership", "Disney", "chief executive", "retired, not involved",
     "https://thewaltdisneycompany.com/leadership/", "Iger returned as CEO in November 2022."),
    ("Twitter's logo is a blue bird.", "version", "Twitter", "logo", "a blue bird",
     "https://about.x.com/en/who-we-are/brand-toolkit", "Rebranded to X with a new logo in July 2023."),
    ("The latest stable release of Python is 3.11.", "version", "Python", "latest stable release", "3.11",
     "https://www.python.org/downloads/", "Python ships a feature release every October; 3.11 is from October 2022."),
    ("Node.js 18 is the current LTS release.", "version", "Node.js", "current LTS release", "18",
     "https://nodejs.org/en/about/previous-releases", "Node.js promotes a new LTS line every year."),
    ("The current version of Java is Java 17.", "version", "Java", "current version", "17",
     "https://www.oracle.com/java/technologies/downloads/", "Java ships a release every six months; 17 is from September 2021."),
    ("GPT-4 is OpenAI's newest and most capable model.", "version", "OpenAI", "newest model", "GPT-4",
     "https://platform.openai.com/docs/models", "Newer model families have shipped since GPT-4's March 2023 release."),
    ("React 17 is the latest major version of React.", "version", "React", "latest major version", "17",
     "https://react.dev/versions", "React 18 shipped in March 2022, and later majors have followed."),
    ("PostgreSQL 14 is the newest major release.", "version", "PostgreSQL", "newest major release", "14",
     "https://www.postgresql.org/support/versioning/", "PostgreSQL ships a major release annually; 14 is from 2021."),
    ("Ubuntu 20.04 is the current long-term support release.", "version", "Ubuntu", "current LTS release", "20.04",
     "https://releases.ubuntu.com/", "Ubuntu publishes a new LTS every two years."),
    ("Kubernetes is at version 1.24.", "version", "Kubernetes", "current version", "1.24",
     "https://kubernetes.io/releases/", "Kubernetes ships roughly three minor releases a year; 1.24 is from 2022."),
    ("The newest iPhone is the iPhone 13.", "version", "iPhone", "newest model", "iPhone 13",
     "https://www.apple.com/iphone/", "Apple has released several newer iPhone generations since 2021."),
    ("Windows 10 is Microsoft's current desktop operating system.", "version", "Windows", "current desktop version", "Windows 10",
     "https://www.microsoft.com/windows", "Windows 11 shipped in October 2021."),
    ("TypeScript's latest release is 4.9.", "version", "TypeScript", "latest release", "4.9",
     "https://www.typescriptlang.org/download/", "TypeScript ships several minor releases a year; 4.9 is from November 2022."),
    ("The EU AI Act is still a proposal under negotiation with no agreed text.", "law", "EU AI Act", "legal status", "a proposal under negotiation",
     "https://eur-lex.europa.eu/eli/reg/2024/1689/oj", "Adopted as Regulation (EU) 2024/1689 and entered into force in August 2024."),
    ("The United Kingdom is a member state of the European Union.", "law", "United Kingdom", "EU membership", "a member state",
     "https://europa.eu/european-union/about-eu/countries_en", "The UK left the EU on 31 January 2020."),
    ("Roe v. Wade is settled law establishing a federal right to abortion in the United States.", "law", "Roe v. Wade", "legal status", "settled federal law",
     "https://www.supremecourt.gov/opinions/slipopinion/21", "Overturned by Dobbs v. Jackson in June 2022."),
    ("The GDPR has not yet come into effect.", "law", "GDPR", "effective status", "not yet in effect",
     "https://eur-lex.europa.eu/eli/reg/2016/679/oj", "The GDPR has applied since 25 May 2018."),
    ("Twitter's API has a generous free tier for developers.", "pricing", "Twitter API", "free tier", "a generous free tier",
     "https://developer.x.com/en/products/x-api", "Free API access was substantially restricted in 2023."),
    ("GPT-4o costs $5.00 per million input tokens.", "pricing", "OpenAI GPT-4o", "input token price", "$5.00 per million",
     "https://openai.com/api/pricing/", "That figure is from the 2024 launch rate card; pricing has been revised since."),
    ("GPT-4 costs $0.03 per 1K input tokens and $0.06 per 1K output tokens.", "pricing", "OpenAI GPT-4", "token price", "$0.03/1K input, $0.06/1K output",
     "https://openai.com/api/pricing/", "Original GPT-4 launch pricing, since superseded."),
    ("Heroku offers a free tier for hobby applications.", "pricing", "Heroku", "free tier", "a free tier for hobby apps",
     "https://www.heroku.com/pricing", "Free product tiers were discontinued in November 2022."),
    ("Docker Desktop is free for all commercial use.", "pricing", "Docker Desktop", "commercial licensing", "free for all commercial use",
     "https://www.docker.com/pricing/", "A paid subscription has been required for larger businesses since 2021."),
    ("Bitcoin is trading at about $43,200.", "market", "Bitcoin", "spot price", "about $43,200",
     "https://www.coinbase.com/price/bitcoin", "A spot price is stale within minutes of being written."),
    ("Bitcoin's all-time high is around $69,000.", "market", "Bitcoin", "all-time high", "around $69,000",
     "https://www.coinbase.com/price/bitcoin", "The November 2021 high was exceeded in 2024."),
    ("Ethereum uses proof-of-work consensus.", "version", "Ethereum", "consensus mechanism", "proof-of-work",
     "https://ethereum.org/en/roadmap/merge/", "Ethereum moved to proof-of-stake at the Merge in September 2022."),
    ("Apple's market capitalisation is about $2 trillion.", "market", "Apple", "market capitalisation", "about $2 trillion",
     "https://investor.apple.com/", "Apple's market capitalisation has moved well past $2tn since 2022."),
    ("The US federal funds rate is near zero.", "market", "United States", "federal funds rate", "near zero",
     "https://www.federalreserve.gov/monetarypolicy/openmarket.htm", "Rates were raised substantially from March 2022 onwards."),
    ("Tesla's stock trades around $1,200 per share.", "market", "Tesla", "share price", "around $1,200",
     "https://ir.tesla.com/", "Pre-dates the August 2022 three-for-one stock split, so the figure is not comparable."),
    ("The 2022 FIFA World Cup is scheduled to be played in the summer.", "events", "2022 FIFA World Cup", "schedule", "played in the summer",
     "https://www.fifa.com/", "It was played in November and December 2022."),
    ("Queen Elizabeth II is celebrating her Platinum Jubilee this year.", "events", "Elizabeth II", "current event", "celebrating her Platinum Jubilee",
     "https://www.royal.uk/", "The Platinum Jubilee was in 2022, and she died that September."),
    ("Stack Overflow is the primary way developers get programming answers, with no AI competition.", "events", "Stack Overflow", "competitive position", "no AI competition",
     "https://stackoverflow.co/", "AI coding assistants became mainstream from 2022 onwards."),
]

# ---------------------------------------------------------------------------
# (claim, class, subject, predicate, value, source, confidence)
# ---------------------------------------------------------------------------

CURRENT: list[tuple[str, str, str, str, str, str, str]] = [
    ("Satya Nadella is the CEO of Microsoft.", "leadership", "Microsoft", "chief executive", "Satya Nadella",
     "https://news.microsoft.com/leadership/", "high"),
    ("Tim Cook is the CEO of Apple.", "leadership", "Apple", "chief executive", "Tim Cook",
     "https://www.apple.com/leadership/", "high"),
    ("Sundar Pichai is the CEO of Alphabet.", "leadership", "Alphabet", "chief executive", "Sundar Pichai",
     "https://abc.xyz/investor/management/", "high"),
    ("Jensen Huang is the CEO of NVIDIA.", "leadership", "NVIDIA", "chief executive", "Jensen Huang",
     "https://nvidianews.nvidia.com/bios", "high"),
    ("Andy Jassy is the CEO of Amazon.", "leadership", "Amazon", "chief executive", "Andy Jassy",
     "https://www.aboutamazon.com/about-us/leadership", "high"),
    ("Charles III is the reigning monarch of the United Kingdom.", "leadership", "United Kingdom", "monarch", "Charles III",
     "https://www.royal.uk/", "high"),
    ("Mark Zuckerberg is the CEO of Meta Platforms.", "leadership", "Meta Platforms", "chief executive", "Mark Zuckerberg",
     "https://about.meta.com/company-info/", "high"),
    ("Lisa Su is the CEO of AMD.", "leadership", "AMD", "chief executive", "Lisa Su",
     "https://www.amd.com/en/corporate/leadership.html", "high"),
    ("Sam Altman is the CEO of OpenAI.", "leadership", "OpenAI", "chief executive", "Sam Altman",
     "https://openai.com/about/", "medium"),
    ("Dario Amodei is the CEO of Anthropic.", "leadership", "Anthropic", "chief executive", "Dario Amodei",
     "https://www.anthropic.com/company", "high"),
    ("The company formerly known as Facebook is now called Meta Platforms.", "leadership", "Meta Platforms", "company name", "Meta Platforms",
     "https://about.fb.com/company-info/", "high"),
    ("Twitter has been rebranded as X.", "leadership", "X", "company name", "X",
     "https://about.x.com/", "high"),
    ("Python 3 is the current major version line of Python.", "version", "Python", "current major version", "3.x",
     "https://www.python.org/downloads/", "high"),
    ("Ethereum uses proof-of-stake consensus.", "version", "Ethereum", "consensus mechanism", "proof-of-stake",
     "https://ethereum.org/en/developers/docs/consensus-mechanisms/pos/", "high"),
    ("Windows 11 is a current Microsoft desktop operating system.", "version", "Windows", "current desktop version", "Windows 11",
     "https://www.microsoft.com/windows", "high"),
    ("React 18 introduced concurrent rendering.", "version", "React", "concurrent rendering release", "18",
     "https://react.dev/blog", "high"),
    ("HTTP/3 is a standardised version of HTTP built on QUIC.", "version", "HTTP/3", "transport protocol", "QUIC",
     "https://www.rfc-editor.org/rfc/rfc9114.html", "high"),
    ("Git is a distributed version control system created by Linus Torvalds.", "version", "Git", "creator", "Linus Torvalds",
     "https://git-scm.com/book/en/v2", "high"),
    ("Rust has a six-week release cadence.", "version", "Rust", "release cadence", "six weeks",
     "https://doc.rust-lang.org/book/appendix-07-nightly-rust.html", "high"),
    ("Kubernetes is governed by the Cloud Native Computing Foundation.", "version", "Kubernetes", "governance", "the CNCF",
     "https://kubernetes.io/community/", "high"),
    ("The EU AI Act entered into force in 2024.", "law", "EU AI Act", "entry into force", "2024",
     "https://eur-lex.europa.eu/eli/reg/2024/1689/oj", "high"),
    ("The GDPR has applied since May 2018.", "law", "GDPR", "application date", "25 May 2018",
     "https://eur-lex.europa.eu/eli/reg/2016/679/oj", "high"),
    ("The United Kingdom has left the European Union.", "law", "United Kingdom", "EU membership", "no longer a member",
     "https://www.gov.uk/government/collections/brexit-guidance", "high"),
    ("The US federal minimum wage is $7.25 per hour.", "law", "United States", "federal minimum wage", "$7.25 per hour",
     "https://www.dol.gov/agencies/whd/minimum-wage", "high"),
    ("California's CCPA gives consumers a right to opt out of the sale of personal information.", "law", "CCPA", "consumer right", "opt out of sale",
     "https://oag.ca.gov/privacy/ccpa", "high"),
    ("Dobbs v. Jackson overturned Roe v. Wade.", "law", "Dobbs v. Jackson", "effect on Roe", "overturned it",
     "https://www.supremecourt.gov/opinions/slipopinion/21", "high"),
    ("The Digital Markets Act regulates designated gatekeeper platforms in the EU.", "law", "Digital Markets Act", "regulated entities", "designated gatekeepers",
     "https://eur-lex.europa.eu/eli/reg/2022/1925/oj", "high"),
    ("HIPAA governs the privacy of health information in the United States.", "law", "HIPAA", "scope", "health information privacy",
     "https://www.hhs.gov/hipaa/index.html", "high"),
    ("Bitcoin has a maximum supply of 21 million coins.", "market", "Bitcoin", "maximum supply", "21 million",
     "https://bitcoin.org/bitcoin.pdf", "high"),
    ("Bitcoin's block reward halves roughly every four years.", "market", "Bitcoin", "halving interval", "roughly four years",
     "https://bitcoin.org/bitcoin.pdf", "high"),
    ("The S&P 500 is a market-capitalisation-weighted index of large US companies.", "market", "S&P 500", "index methodology", "market-cap weighted",
     "https://www.spglobal.com/spdji/en/indices/equity/sp-500/", "high"),
    ("The euro is the official currency of the eurozone.", "market", "eurozone", "official currency", "the euro",
     "https://www.ecb.europa.eu/euro/html/index.en.html", "high"),
    ("NASDAQ is a US stock exchange.", "market", "NASDAQ", "type of entity", "a US stock exchange",
     "https://www.nasdaq.com/about", "high"),
    ("Tesla completed a three-for-one stock split in 2022.", "market", "Tesla", "stock split", "three-for-one in 2022",
     "https://ir.tesla.com/", "high"),
    ("OpenAI offers an API priced per token.", "pricing", "OpenAI API", "pricing model", "per token",
     "https://openai.com/api/pricing/", "high"),
    ("AWS charges for EC2 on-demand instances by the second.", "pricing", "AWS EC2", "billing granularity", "per second",
     "https://aws.amazon.com/ec2/pricing/on-demand/", "high"),
    ("GitHub offers free private repositories.", "pricing", "GitHub", "private repositories", "free",
     "https://github.com/pricing", "high"),
    ("Cloudflare offers a free plan for its CDN.", "pricing", "Cloudflare", "free plan", "available",
     "https://www.cloudflare.com/plans/", "high"),
    ("Tavily charges for its search API on a credit basis.", "pricing", "Tavily", "pricing model", "credits",
     "https://docs.tavily.com/", "medium"),
    ("Bifrost is distributed under the Apache 2.0 licence.", "law", "Bifrost", "licence", "Apache 2.0",
     "https://github.com/maximhq/bifrost", "high"),
]

# ---------------------------------------------------------------------------
# (claim, subject, predicate, value, source)
# ---------------------------------------------------------------------------

TIMELESS: list[tuple[str, str, str, str, str]] = [
    ("The capital of France is Paris.", "France", "capital", "Paris", "https://www.britannica.com/place/Paris"),
    ("The capital of Japan is Tokyo.", "Japan", "capital", "Tokyo", "https://www.britannica.com/place/Tokyo"),
    ("The capital of Australia is Canberra.", "Australia", "capital", "Canberra", "https://www.britannica.com/place/Canberra"),
    ("The capital of Canada is Ottawa.", "Canada", "capital", "Ottawa", "https://www.britannica.com/place/Ottawa"),
    ("The capital of Brazil is Brasilia.", "Brazil", "capital", "Brasilia", "https://www.britannica.com/place/Brasilia"),
    ("Water boils at 100 degrees Celsius at standard atmospheric pressure.", "water", "boiling point", "100 C", "https://www.britannica.com/science/boiling-point"),
    ("Water freezes at 0 degrees Celsius at standard atmospheric pressure.", "water", "freezing point", "0 C", "https://www.britannica.com/science/freezing-point"),
    ("The speed of light in a vacuum is about 299,792,458 metres per second.", "light", "speed in vacuum", "299792458 m/s", "https://physics.nist.gov/cgi-bin/cuu/Value?c"),
    ("The chemical formula for water is H2O.", "water", "chemical formula", "H2O", "https://pubchem.ncbi.nlm.nih.gov/compound/Water"),
    ("The atomic number of carbon is 6.", "carbon", "atomic number", "6", "https://pubchem.ncbi.nlm.nih.gov/element/Carbon"),
    ("Gold has the chemical symbol Au.", "gold", "chemical symbol", "Au", "https://pubchem.ncbi.nlm.nih.gov/element/Gold"),
    ("The Earth orbits the Sun.", "Earth", "orbital relationship", "orbits the Sun", "https://science.nasa.gov/earth/"),
    ("A right angle measures 90 degrees.", "right angle", "measure", "90 degrees", "https://mathworld.wolfram.com/RightAngle.html"),
    ("The square root of 144 is 12.", "144", "square root", "12", "https://mathworld.wolfram.com/SquareRoot.html"),
    ("Pi is approximately 3.14159.", "pi", "approximate value", "3.14159", "https://mathworld.wolfram.com/Pi.html"),
    ("There are 360 degrees in a circle.", "circle", "degrees", "360", "https://mathworld.wolfram.com/Circle.html"),
    ("A prime number has exactly two distinct positive divisors.", "prime number", "definition", "two divisors", "https://mathworld.wolfram.com/PrimeNumber.html"),
    ("Seventeen is a prime number.", "17", "primality", "prime", "https://mathworld.wolfram.com/PrimeNumber.html"),
    ("The sum of the angles in a Euclidean triangle is 180 degrees.", "triangle", "angle sum", "180 degrees", "https://mathworld.wolfram.com/Triangle.html"),
    ("Two plus two equals four.", "2+2", "sum", "4", "https://mathworld.wolfram.com/Addition.html"),
    ("William Shakespeare wrote Hamlet.", "Hamlet", "author", "William Shakespeare", "https://www.folger.edu/explore/shakespeares-works/hamlet/"),
    ("Leonardo da Vinci painted the Mona Lisa.", "Mona Lisa", "painter", "Leonardo da Vinci", "https://www.louvre.fr/en/explore/the-palace/the-mona-lisa"),
    ("The Second World War ended in 1945.", "Second World War", "end year", "1945", "https://www.iwm.org.uk/history"),
    ("The Declaration of Independence was adopted in 1776.", "Declaration of Independence", "adoption year", "1776", "https://www.archives.gov/founding-docs/declaration"),
    ("Apollo 11 landed humans on the Moon in 1969.", "Apollo 11", "Moon landing year", "1969", "https://www.nasa.gov/mission/apollo-11/"),
    ("The Berlin Wall fell in 1989.", "Berlin Wall", "fall year", "1989", "https://www.britannica.com/topic/Berlin-Wall"),
    ("The Titanic sank in 1912.", "Titanic", "sinking year", "1912", "https://www.britannica.com/topic/Titanic"),
    ("Marie Curie won Nobel Prizes in both physics and chemistry.", "Marie Curie", "Nobel Prizes", "physics and chemistry", "https://www.nobelprize.org/prizes/physics/1903/marie-curie/facts/"),
    ("Mount Everest is the highest mountain above sea level.", "Mount Everest", "rank by height", "highest above sea level", "https://www.britannica.com/place/Mount-Everest"),
    ("The Pacific is the largest ocean on Earth.", "Pacific Ocean", "rank by size", "largest", "https://oceanservice.noaa.gov/facts/"),
    ("The Nile and the Amazon are the two longest rivers on Earth.", "Nile and Amazon", "rank by length", "two longest", "https://www.usgs.gov/faqs"),
    ("TCP is a connection-oriented transport protocol.", "TCP", "connection model", "connection-oriented", "https://www.rfc-editor.org/rfc/rfc9293.html"),
    ("UDP is a connectionless transport protocol.", "UDP", "connection model", "connectionless", "https://www.rfc-editor.org/rfc/rfc768.html"),
    ("A hash function maps arbitrary input to a fixed-size output.", "hash function", "definition", "fixed-size output", "https://csrc.nist.gov/glossary/term/hash_function"),
    ("Big-O notation describes the asymptotic upper bound of an algorithm's growth.", "big-O notation", "definition", "asymptotic upper bound", "https://mathworld.wolfram.com/Big-ONotation.html"),
    ("Binary search runs in logarithmic time on a sorted array.", "binary search", "time complexity", "O(log n)", "https://mathworld.wolfram.com/BinarySearch.html"),
    ("A stack is a last-in, first-out data structure.", "stack", "ordering", "LIFO", "https://xlinux.nist.gov/dads/HTML/stack.html"),
    ("A queue is a first-in, first-out data structure.", "queue", "ordering", "FIFO", "https://xlinux.nist.gov/dads/HTML/queue.html"),
    ("SQL stands for Structured Query Language.", "SQL", "expansion", "Structured Query Language", "https://www.iso.org/standard/76583.html"),
    ("JSON stands for JavaScript Object Notation.", "JSON", "expansion", "JavaScript Object Notation", "https://www.rfc-editor.org/rfc/rfc8259.html"),
]


def build() -> dict:
    claims: list[dict] = []
    seq = 0

    for text, cls, subject, predicate, value, source, why in STALE:
        seq += 1
        claims.append(
            {
                "id": f"stale-{seq:03d}",
                "group": "stale",
                "claim": text,
                "class": cls,
                "subject": subject,
                "predicate": predicate,
                "stated_value": value,
                "expected_verdict": "outdated",
                "source": source,
                "label_basis": why,
                "label_confidence": "high",
            }
        )

    seq = 0
    for text, cls, subject, predicate, value, source, confidence in CURRENT:
        seq += 1
        claims.append(
            {
                "id": f"current-{seq:03d}",
                "group": "current",
                "claim": text,
                "class": cls,
                "subject": subject,
                "predicate": predicate,
                "stated_value": value,
                "expected_verdict": "current",
                "source": source,
                "label_basis": f"Believed true as of {DATASET_DATE}.",
                "label_confidence": confidence,
            }
        )

    seq = 0
    for text, subject, predicate, value, source in TIMELESS:
        seq += 1
        claims.append(
            {
                "id": f"timeless-{seq:03d}",
                "group": "timeless",
                "claim": text,
                "class": "timeless",
                "subject": subject,
                "predicate": predicate,
                "stated_value": value,
                "expected_verdict": "current",
                "expected_skipped": True,
                "source": source,
                "label_basis": "Does not change over time.",
                "label_confidence": "high",
            }
        )

    return {
        "name": "StaleBench",
        "version": DATASET_VERSION,
        "dataset_date": DATASET_DATE,
        "counts": {
            "stale": len(STALE),
            "current": len(CURRENT),
            "timeless": len(TIMELESS),
            "total": len(claims),
        },
        "labelling": {
            "stale": (
                "Claims that were true once and are now wrong, each tied to a "
                "publicly documented change. The strongest labels in the set."
            ),
            "current": (
                "Claims believed true as of dataset_date. These labels decay: a "
                "disagreement here means re-check the label before concluding the "
                "system is wrong. See label_confidence and the runner's --audit mode."
            ),
            "timeless": (
                "Facts that do not change. Present to measure the skip rate, i.e. "
                "how often Chronos correctly declines to spend a Tavily credit."
            ),
            "source_policy": (
                "source is an authoritative domain or page for the subject, not a "
                "hand-invented deep link."
            ),
        },
        "claims": claims,
    }


def main() -> None:
    data = build()
    out = Path(__file__).parent / "stalebench.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    total = data["counts"]["total"]
    print(f"wrote {out} with {total} claims")
    for group, n in data["counts"].items():
        if group != "total":
            print(f"  {group:<9} {n}")

    assert data["counts"]["stale"] == 40, data["counts"]
    assert data["counts"]["current"] == 40, data["counts"]
    assert data["counts"]["timeless"] == 40, data["counts"]
    assert total == 120, total

    ids = [c["id"] for c in data["claims"]]
    assert len(ids) == len(set(ids)), "duplicate claim ids"
    print("dataset checks passed")


if __name__ == "__main__":
    main()
