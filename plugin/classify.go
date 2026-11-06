package main

import (
	"regexp"
	"sort"
	"strings"
)

// VolatilityClass names how quickly the facts behind an answer go stale.
// The classes are ordered from fastest-moving to never-moving.
type VolatilityClass string

const (
	ClassMarket     VolatilityClass = "market"
	ClassPricing    VolatilityClass = "pricing"
	ClassVersion    VolatilityClass = "version"
	ClassLeadership VolatilityClass = "leadership"
	ClassLaw        VolatilityClass = "law"
	ClassEvents     VolatilityClass = "events"
	ClassTimeless   VolatilityClass = "timeless"
)

// classRank orders classes by volatility. A prompt that looks like several
// classes at once is assigned the most volatile of them, because the cache TTL
// has to protect the fastest-moving fact in the answer.
var classRank = map[VolatilityClass]int{
	ClassMarket:     0,
	ClassPricing:    1,
	ClassVersion:    2,
	ClassEvents:     3,
	ClassLeadership: 4,
	ClassLaw:        5,
	ClassTimeless:   6,
}

// classifier is a compiled rule: a class plus the patterns that select it.
type classifier struct {
	class    VolatilityClass
	patterns []*regexp.Regexp
}

// mustCompileAll compiles case-insensitive word-ish patterns.
func mustCompileAll(exprs ...string) []*regexp.Regexp {
	out := make([]*regexp.Regexp, 0, len(exprs))
	for _, e := range exprs {
		out = append(out, regexp.MustCompile(`(?i)`+e))
	}
	return out
}

// rules are evaluated cheaply on every request, before any model is involved.
// Rules-first is a deliberate choice: classification sits on the hot path, and
// a regex costs microseconds where a model call costs hundreds of milliseconds.
// The verifier re-derives per-claim classes properly once the answer exists;
// this pass only has to be good enough to pick a cache TTL.
var rules = []classifier{
	{
		class: ClassMarket,
		patterns: mustCompileAll(
			`\b(stock|share) price\b`, `\bmarket cap(italization)?\b`,
			`\bticker\b`, `\b(exchange|conversion) rate\b`,
			`\b(bitcoin|btc|ethereum|eth|crypto)\b.*\b(price|worth|value)\b`,
			`\b(price|worth|value)\b.*\b(bitcoin|btc|ethereum|eth)\b`,
			`\b(interest|inflation|repo) rate\b`, `\bbond yield\b`,
			`\btrading at\b`, `\bpre-?market\b`,
			// Market commentary tied to a moment. Without this, "what happened
			// in the markets today" falls through to `events` and gets a
			// 12-hour TTL, which is far too long for a moving market.
			`\b(markets?|stocks?|indices|index|shares?)\b[^.]{0,40}\b(today|now|this (morning|afternoon|week)|close[ds]?|open(ed|ing)?|up|down|rose|fell|rally|selloff)\b`,
			`\b(today|now|this morning)\b[^.]{0,40}\b(markets?|stocks?|indices)\b`,
		),
	},
	{
		class: ClassPricing,
		patterns: mustCompileAll(
			`\bhow much (does|do|is|are)\b.*\b(cost|charge)`,
			`\bprice (of|for|per)\b`, `\bpricing\b`, `\bcost per\b`,
			`\b(per|\/)\s?(1k|1m|million|thousand) tokens?\b`,
			`\b(subscription|plan|tier) (price|cost)\b`,
			`\bfree tier\b`, `\brate card\b`, `\bhow much is\b`,
		),
	},
	{
		class: ClassVersion,
		patterns: mustCompileAll(
			`\blatest (version|release|stable)\b`, `\bcurrent version\b`,
			`\bnewest version\b`, `\bmost recent (version|release)\b`,
			`\bwhat version\b`, `\brelease notes\b`, `\bchangelog\b`,
			`\b(lts|long[- ]term support)\b`, `\bdeprecat(ed|ion)\b`,
			`\bwhich model (is|are) (the )?(latest|newest|best|current)\b`,
		),
	},
	{
		class: ClassLeadership,
		patterns: mustCompileAll(
			`\bwho (is|are|runs|leads|heads)\b.*\b(ceo|cto|cfo|coo|president|prime minister|chair(man|person)?|director|head of)\b`,
			`\b(ceo|cto|cfo|coo|chair(man|person)?) of\b`,
			`\bcurrent (ceo|cto|cfo|president|prime minister|leader|manager|coach)\b`,
			`\bwho (is|are) the (current|new)\b`,
			`\b(appointed|stepped down|resigned|took over as)\b`,
		),
	},
	{
		class: ClassLaw,
		patterns: mustCompileAll(
			`\b(law|statute|regulation|directive|act|bill|ordinance)\b`,
			`\b(legal|compliance|regulatory) (requirement|deadline|status)\b`,
			`\b(gdpr|hipaa|ccpa|dma|dsa|ai act|sox|pci[- ]dss)\b`,
			`\bcomes? into (force|effect)\b`, `\btax rate\b`,
			`\bis it legal\b`, `\bbanned in\b`,
		),
	},
	{
		class: ClassEvents,
		patterns: mustCompileAll(
			`\b(who won|winner of|final score|results? of)\b`,
			`\b(today|yesterday|this (week|month|morning)|right now|currently|as of now)\b`,
			`\b(latest|recent|breaking) news\b`, `\bwhat happened\b`,
			`\b(schedule|fixture|lineup) for\b`,
			`\b(election|match|game|tournament|conference|launch event)\b`,
			`\bstill (open|running|available|happening)\b`,
		),
	},
	{
		class: ClassTimeless,
		patterns: mustCompileAll(
			`\bwhat is the (capital|chemical formula|boiling point|freezing point)\b`,
			`\bspeed of light\b`, `\bwho wrote\b`, `\bwho painted\b`,
			`\bdefine\b`, `\bdefinition of\b`, `\bexplain (the )?(concept|difference|theory)\b`,
			`\bhow does .* work\b`, `\bwrite (a|me|some)? ?(function|code|program|script|poem|story)\b`,
			`\brefactor\b`, `\btranslate\b`, `\bsummar(y|ise|ize)\b`,
			`\bprove that\b`, `\bderive\b`, `\bwhat is \d+ ?[-+*/x] ?\d+`,
		),
	},
}

// Classification is what the pre-hook learns about a request.
type Classification struct {
	Class      VolatilityClass `json:"class"`
	Matched    []string        `json:"matched"`
	Confidence string          `json:"confidence"` // "rule" | "default"
}

// ClassifyPrompt assigns a volatility class to a user prompt using rules only.
//
// Unmatched prompts are deliberately NOT treated as timeless. An answer we
// cannot classify might still contain a fast-moving fact, so it gets the
// conservative `events` class and a short TTL; the verifier then decides, per
// claim, whether anything time-sensitive was actually said. Guessing
// "timeless" here would be the one mistake that silently serves stale answers.
func ClassifyPrompt(prompt string) Classification {
	p := strings.TrimSpace(prompt)
	if p == "" {
		return Classification{Class: ClassEvents, Confidence: "default"}
	}

	var hits []Classification
	for _, r := range rules {
		for _, re := range r.patterns {
			if m := re.FindString(p); m != "" {
				hits = append(hits, Classification{
					Class:      r.class,
					Matched:    []string{strings.ToLower(strings.TrimSpace(m))},
					Confidence: "rule",
				})
				break
			}
		}
	}

	if len(hits) == 0 {
		return Classification{Class: ClassEvents, Confidence: "default"}
	}

	// Most volatile class wins.
	sort.SliceStable(hits, func(i, j int) bool {
		return classRank[hits[i].Class] < classRank[hits[j].Class]
	})

	out := hits[0]
	for _, h := range hits[1:] {
		out.Matched = append(out.Matched, h.Matched...)
	}
	return out
}
