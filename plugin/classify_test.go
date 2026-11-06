package main

import "testing"

func TestClassifyPrompt(t *testing.T) {
	tests := []struct {
		name   string
		prompt string
		want   VolatilityClass
	}{
		// market
		{"stock price", "What is the stock price of NVDA today?", ClassMarket},
		{"bitcoin", "How much is bitcoin worth right now?", ClassMarket},
		{"market cap", "What is Apple's market cap?", ClassMarket},
		{"exchange rate", "What is the exchange rate for GBP to USD?", ClassMarket},

		// pricing
		{"token pricing", "How much does GPT-4o cost per million tokens?", ClassPricing},
		{"price of", "What is the price of a Notion team seat?", ClassPricing},
		{"plan cost", "What is the subscription price for Figma?", ClassPricing},

		// version
		{"latest version", "What is the latest version of Python?", ClassVersion},
		{"current version", "What is the current version of Postgres?", ClassVersion},
		{"deprecation", "Is the old API deprecated?", ClassVersion},

		// leadership
		{"who is ceo", "Who is the CEO of OpenAI?", ClassLeadership},
		{"ceo of", "Tell me about the CEO of Microsoft", ClassLeadership},
		{"current pm", "Who is the current prime minister of Japan?", ClassLeadership},

		// law
		{"regulation", "What does the GDPR require for data deletion?", ClassLaw},
		{"ai act", "When does the EU AI Act come into force?", ClassLaw},

		// events
		{"who won", "Who won the match last night?", ClassEvents},
		{"today", "What happened in the markets today?", ClassMarket}, // market wins: more volatile
		{"latest news", "Give me the latest news on the merger", ClassEvents},

		// timeless
		{"capital", "What is the capital of France?", ClassTimeless},
		{"write code", "Write a function that reverses a string", ClassTimeless},
		{"explain", "Explain the difference between TCP and UDP", ClassTimeless},
		{"arithmetic", "What is 17 * 23?", ClassTimeless},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := ClassifyPrompt(tc.prompt)
			if got.Class != tc.want {
				t.Errorf("ClassifyPrompt(%q).Class = %q, want %q (matched %v)",
					tc.prompt, got.Class, tc.want, got.Matched)
			}
		})
	}
}

func TestClassifyPromptPicksMostVolatile(t *testing.T) {
	// A prompt that reads as several classes must take the fastest-moving one,
	// because the cache TTL has to protect the fastest-moving fact.
	prompt := "What is the latest version of the Bloomberg terminal and its stock price?"
	got := ClassifyPrompt(prompt)
	if got.Class != ClassMarket {
		t.Errorf("mixed prompt classified as %q, want %q (matched %v)",
			got.Class, ClassMarket, got.Matched)
	}
}

func TestClassifyPromptUnmatchedIsNotTimeless(t *testing.T) {
	// The important negative case: an unrecognised prompt must NOT be treated
	// as timeless, or Chronos would hand it a week-long cache TTL and never
	// look at it again. `events` is the conservative default.
	got := ClassifyPrompt("Bletchley zorblax quantifier arrangement")
	if got.Class == ClassTimeless {
		t.Fatal("unmatched prompt was classified timeless; it must default to a short-TTL class")
	}
	if got.Class != ClassEvents {
		t.Errorf("unmatched prompt classified as %q, want %q", got.Class, ClassEvents)
	}
	if got.Confidence != "default" {
		t.Errorf("confidence = %q, want %q", got.Confidence, "default")
	}
}

func TestClassifyPromptEmpty(t *testing.T) {
	got := ClassifyPrompt("")
	if got.Class != ClassEvents {
		t.Errorf("empty prompt classified as %q, want %q", got.Class, ClassEvents)
	}
}
