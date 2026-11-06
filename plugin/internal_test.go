package main

import (
	"strings"
	"testing"
)

// TestInternalHeaderNameMatchesVerifier pins the one string that has to be
// identical in two languages.
//
// The verifier sets this header on every model call it makes
// (verifier/chronos/llm.py: INTERNAL_HEADER) and the plugin skips any request
// carrying it. If the two drift, nothing fails loudly — instead Chronos starts
// verifying its own verification calls, each of which makes further model
// calls that are verified in turn. That loop amplifies until it exhausts the
// model's token budget; it was measured at 4,605 verifications and 37 minutes
// of rate-limit waiting from a single 18-claim benchmark run.
func TestInternalHeaderNameMatchesVerifier(t *testing.T) {
	const expected = "x-chronos-internal"
	if hdrInternal != expected {
		t.Fatalf("hdrInternal = %q, but the verifier sends %q; the loop guard is broken",
			hdrInternal, expected)
	}
}

func TestInternalHeaderIsLowercase(t *testing.T) {
	// Looked up case-insensitively, but kept lowercase for consistency with
	// the other header constants.
	if hdrInternal != strings.ToLower(hdrInternal) {
		t.Errorf("hdrInternal %q should be lowercase", hdrInternal)
	}
}

func TestInternalHeaderIsNotExposedToClients(t *testing.T) {
	// It is a request marker, not a response header: publishing it on
	// Access-Control-Expose-Headers would be meaningless noise.
	for _, h := range chronosHeaders {
		if h == hdrInternal {
			t.Errorf("%q should not be in the exposed response headers", hdrInternal)
		}
	}
}

// TestSkipKeyIsDistinct guards against a copy-paste collision between the
// context keys, which would make the skip flag overwrite a real value.
func TestSkipKeyIsDistinct(t *testing.T) {
	keys := []string{
		string(ctxKeyClass), string(ctxKeyTTL), string(ctxKeyPrompt),
		string(ctxKeyModel), string(ctxKeyCacheKy), string(ctxKeyResult),
		string(ctxKeyCorrID), string(ctxKeySkip),
	}
	seen := make(map[string]bool, len(keys))
	for _, k := range keys {
		if k == "" {
			t.Error("a context key is empty")
		}
		if seen[k] {
			t.Errorf("duplicate context key %q", k)
		}
		seen[k] = true
	}
}
