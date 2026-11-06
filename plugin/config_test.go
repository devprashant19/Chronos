package main

import (
	"testing"
	"time"
)

func TestTTLForClass(t *testing.T) {
	cfg := DefaultConfig()

	// The policy table: how long an answer of each class may be cached.
	tests := []struct {
		class string
		want  time.Duration
	}{
		{"market", 15 * time.Minute},
		{"pricing", 6 * time.Hour},
		{"version", 6 * time.Hour},
		{"leadership", 24 * time.Hour},
		{"law", 24 * time.Hour},
		{"events", 12 * time.Hour},
		{"timeless", 168 * time.Hour},
	}

	for _, tc := range tests {
		t.Run(tc.class, func(t *testing.T) {
			if got := cfg.ttlForClass(tc.class); got != tc.want {
				t.Errorf("ttlForClass(%q) = %v, want %v", tc.class, got, tc.want)
			}
		})
	}
}

func TestTTLForClassOrdering(t *testing.T) {
	// The whole premise is that volatile facts get shorter TTLs. Assert the
	// ordering rather than only the values, so a careless edit to the table
	// that inverts the policy fails here.
	cfg := DefaultConfig()
	order := []string{"market", "pricing", "leadership", "timeless"}
	for i := 1; i < len(order); i++ {
		prev, cur := cfg.ttlForClass(order[i-1]), cfg.ttlForClass(order[i])
		if prev >= cur {
			t.Errorf("TTL for %s (%v) should be shorter than for %s (%v)",
				order[i-1], prev, order[i], cur)
		}
	}
}

func TestTTLForClassUnknownFallsBackToDefault(t *testing.T) {
	cfg := DefaultConfig()
	if got := cfg.ttlForClass("no-such-class"); got != 24*time.Hour {
		t.Errorf("unknown class TTL = %v, want the 24h default", got)
	}
}

func TestTTLForClassIgnoresUnparseableValue(t *testing.T) {
	cfg := DefaultConfig()
	cfg.TTLByClass = map[string]string{"market": "not-a-duration"}
	// Must fall through to DefaultTTL rather than returning zero, which would
	// make the cache treat the entry as already expired.
	if got := cfg.ttlForClass("market"); got != 24*time.Hour {
		t.Errorf("unparseable TTL gave %v, want the 24h default", got)
	}
}

func TestLoadConfigDefaults(t *testing.T) {
	cfg, warnings := LoadConfig(nil)
	if len(warnings) != 0 {
		t.Errorf("nil config produced warnings: %v", warnings)
	}
	if cfg.Mode != ModeAnnotate {
		t.Errorf("default mode = %q, want %q", cfg.Mode, ModeAnnotate)
	}
	if cfg.GuardThreshold != 50 {
		t.Errorf("default guard threshold = %d, want 50", cfg.GuardThreshold)
	}
	if !cfg.syncOnCacheHit() {
		t.Error("sync_on_cache_hit should default to true: cache hits are the stale ones")
	}
	if !cfg.evictStale() {
		t.Error("evict_stale should default to true")
	}
}

func TestLoadConfigFromMap(t *testing.T) {
	raw := map[string]any{
		"verifier_url":      "http://verifier.internal:9000/",
		"mode":              "guard",
		"guard_threshold":   70,
		"verify_timeout_ms": 1500,
		"sync_on_cache_hit": false,
		"evict_stale":       false,
		"ttl_by_class":      map[string]any{"market": "30s"},
		"auto_cache_key":    "tenant-a",
	}

	cfg, warnings := LoadConfig(raw)
	if len(warnings) != 0 {
		t.Errorf("unexpected warnings: %v", warnings)
	}
	if cfg.VerifierURL != "http://verifier.internal:9000" {
		t.Errorf("verifier URL = %q, want the trailing slash stripped", cfg.VerifierURL)
	}
	if cfg.Mode != ModeGuard {
		t.Errorf("mode = %q, want guard", cfg.Mode)
	}
	if cfg.GuardThreshold != 70 {
		t.Errorf("guard threshold = %d, want 70", cfg.GuardThreshold)
	}
	if cfg.verifyTimeout() != 1500*time.Millisecond {
		t.Errorf("timeout = %v, want 1.5s", cfg.verifyTimeout())
	}
	if cfg.syncOnCacheHit() {
		t.Error("sync_on_cache_hit=false was not honoured")
	}
	if cfg.evictStale() {
		t.Error("evict_stale=false was not honoured")
	}
	if got := cfg.ttlForClass("market"); got != 30*time.Second {
		t.Errorf("overridden market TTL = %v, want 30s", got)
	}
	if cfg.AutoCacheKey != "tenant-a" {
		t.Errorf("auto cache key = %q", cfg.AutoCacheKey)
	}
}

func TestLoadConfigNeverFailsOnBadInput(t *testing.T) {
	// A gateway must not refuse to boot because a knob was misspelled. Bad
	// input produces defaults plus a warning, never an error.
	for _, raw := range []any{
		"a string where an object belongs",
		[]int{1, 2, 3},
		map[string]any{"guard_threshold": "seventy"},
		map[string]any{"mode": 42},
	} {
		cfg, _ := LoadConfig(raw)
		if cfg.VerifierURL == "" {
			t.Errorf("config from %#v left VerifierURL empty", raw)
		}
		if cfg.verifyTimeout() <= 0 {
			t.Errorf("config from %#v left a non-positive timeout", raw)
		}
	}
}

func TestWatchesPath(t *testing.T) {
	cfg := DefaultConfig()

	watched := []string{
		"/v1/chat/completions",
		"/openai/v1/chat/completions",
		"/v1/responses",
	}
	for _, p := range watched {
		if !cfg.watchesPath(p) {
			t.Errorf("watchesPath(%q) = false, want true", p)
		}
	}

	ignored := []string{"/metrics", "/health", "/api/cache/clear/abc", "/v1/embeddings"}
	for _, p := range ignored {
		if cfg.watchesPath(p) {
			t.Errorf("watchesPath(%q) = true, want false", p)
		}
	}
}

func TestWatchesPathEmptyListWatchesEverything(t *testing.T) {
	cfg := DefaultConfig()
	cfg.Paths = nil
	if !cfg.watchesPath("/anything") {
		t.Error("an empty path list should watch everything")
	}
}

func TestFormatDuration(t *testing.T) {
	tests := []struct {
		in   time.Duration
		want string
	}{
		{15 * time.Minute, "15m"},
		{6 * time.Hour, "6h"},
		{90 * time.Second, "90s"},
		{0, "0s"},
		{-5 * time.Second, "0s"},
		{168 * time.Hour, "168h"},
	}
	for _, tc := range tests {
		if got := formatDuration(tc.in); got != tc.want {
			t.Errorf("formatDuration(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormaliseVerdict(t *testing.T) {
	tests := map[string]string{
		"current":       verdictCurrent,
		"CURRENT":       verdictCurrent,
		" check ":       verdictCheck,
		"outdated":      verdictOutdated,
		"pending":       verdictPending,
		"unverifiable":  verdictUnverifiable,
		"":              verdictUnverifiable,
		"something-new": verdictUnverifiable,
	}
	for in, want := range tests {
		if got := normaliseVerdict(in); got != want {
			t.Errorf("normaliseVerdict(%q) = %q, want %q", in, got, want)
		}
	}
}
