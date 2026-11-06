package main

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"time"
)

// Mode decides how much Chronos is allowed to interfere with a response.
type Mode string

const (
	// ModeObserve verifies out of band and never alters the response.
	ModeObserve Mode = "observe"
	// ModeAnnotate verifies out of band and adds x-chronos-* response headers.
	ModeAnnotate Mode = "annotate"
	// ModeGuard verifies in line, within a deadline, and may regenerate or
	// block an answer whose score falls below GuardThreshold.
	ModeGuard Mode = "guard"
)

// Config is the plugin's `config` object from Bifrost's config.json.
// Every field has a safe default, and every field can be overridden by an
// environment variable so the same image works across deployments.
type Config struct {
	// VerifierURL is the base URL of the Chronos verifier service.
	VerifierURL string `json:"verifier_url"`

	// Mode is observe | annotate | guard.
	Mode Mode `json:"mode"`

	// GuardThreshold is the score below which guard mode intervenes (0-100).
	GuardThreshold int `json:"guard_threshold"`

	// VerifyTimeoutMS bounds an inline verification. Chronos always lets the
	// response through when the deadline passes; it never fails a request.
	VerifyTimeoutMS int `json:"verify_timeout_ms"`

	// SyncOnCacheHit verifies cache hits inline even outside guard mode.
	//
	// This is deliberate: a cache hit costs no provider latency and is the only
	// case that can serve a *stale* answer, so it is the one worth waiting for.
	// Fresh answers stay fully asynchronous, which keeps Chronos's added
	// latency on the expensive path near zero.
	SyncOnCacheHit *bool `json:"sync_on_cache_hit"`

	// EvictStale issues DELETE /api/cache/clear/{cache_id} when a cached answer
	// turns out to carry an outdated claim.
	EvictStale *bool `json:"evict_stale"`

	// BifrostAdminURL is where the cache-clear API lives (usually loopback).
	BifrostAdminURL string `json:"bifrost_admin_url"`

	// AdminAuthHeader/AdminAuthValue are sent with cache-clear calls when the
	// gateway enforces auth on its management API.
	AdminAuthHeader string `json:"admin_auth_header"`
	AdminAuthValue  string `json:"admin_auth_value"`

	// AutoCacheKey makes Chronos supply an x-bf-cache-key for requests that
	// omit one, so the semantic cache engages. Empty disables the behaviour.
	AutoCacheKey string `json:"auto_cache_key"`

	// TTLByClass maps a volatility class to the cache TTL Chronos requests.
	TTLByClass map[string]string `json:"ttl_by_class"`

	// DefaultTTL is used when no claim class is known (e.g. timeless answers).
	DefaultTTL string `json:"default_ttl"`

	// MaxAnswerChars caps how much answer text is shipped to the verifier.
	MaxAnswerChars int `json:"max_answer_chars"`

	// Paths restricts Chronos to inference routes; everything else is ignored.
	Paths []string `json:"paths"`

	// Debug logs a line per hook.
	Debug bool `json:"debug"`
}

// defaultTTLByClass encodes the "how fast does this kind of fact go stale"
// policy. These are the maximum cache lifetimes, not the re-check intervals;
// the verifier owns re-check scheduling.
func defaultTTLByClass() map[string]string {
	return map[string]string{
		"market":     "15m",
		"pricing":    "6h",
		"version":    "6h",
		"leadership": "24h",
		"law":        "24h",
		"events":     "12h",
		"timeless":   "168h",
	}
}

// DefaultConfig returns the configuration Chronos uses with an empty `config`.
func DefaultConfig() Config {
	t := true
	return Config{
		VerifierURL:     "http://verifier:8000",
		Mode:            ModeAnnotate,
		GuardThreshold:  50,
		VerifyTimeoutMS: 4000,
		SyncOnCacheHit:  &t,
		EvictStale:      &t,
		BifrostAdminURL: "http://127.0.0.1:8080",
		AutoCacheKey:    "",
		TTLByClass:      defaultTTLByClass(),
		DefaultTTL:      "24h",
		MaxAnswerChars:  12000,
		Paths:           []string{"/v1/chat/completions", "/v1/responses", "/openai/v1/chat/completions"},
		Debug:           false,
	}
}

// LoadConfig merges, in increasing order of precedence:
//
//	defaults  <  config.json `config` object  <  CHRONOS_* environment
//
// It never returns an error for a value it can simply ignore: a gateway must
// not fail to boot because one knob was misspelled.
func LoadConfig(raw any) (Config, []string) {
	cfg := DefaultConfig()
	var warnings []string

	if raw != nil {
		// Round-trip through JSON so we accept whatever shape the transport
		// handed us (map[string]any from config.json, or a typed struct).
		if b, err := json.Marshal(raw); err == nil && len(b) > 0 && string(b) != "null" {
			merged := cfg
			if err := json.Unmarshal(b, &merged); err != nil {
				warnings = append(warnings, "could not parse plugin config, using defaults: "+err.Error())
			} else {
				cfg = merged
			}
		}
	}

	// Environment overrides.
	if v := os.Getenv("CHRONOS_VERIFIER_URL"); v != "" {
		cfg.VerifierURL = v
	}
	if v := os.Getenv("CHRONOS_MODE"); v != "" {
		switch Mode(strings.ToLower(strings.TrimSpace(v))) {
		case ModeObserve, ModeAnnotate, ModeGuard:
			cfg.Mode = Mode(strings.ToLower(strings.TrimSpace(v)))
		default:
			warnings = append(warnings, "ignoring unknown CHRONOS_MODE="+v)
		}
	}
	if v := os.Getenv("CHRONOS_GUARD_THRESHOLD"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 && n <= 100 {
			cfg.GuardThreshold = n
		} else {
			warnings = append(warnings, "ignoring invalid CHRONOS_GUARD_THRESHOLD="+v)
		}
	}
	if v := os.Getenv("CHRONOS_VERIFY_TIMEOUT_MS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			cfg.VerifyTimeoutMS = n
		} else {
			warnings = append(warnings, "ignoring invalid CHRONOS_VERIFY_TIMEOUT_MS="+v)
		}
	}
	if v := os.Getenv("CHRONOS_BIFROST_ADMIN_URL"); v != "" {
		cfg.BifrostAdminURL = v
	}
	if v := os.Getenv("CHRONOS_AUTO_CACHE_KEY"); v != "" {
		cfg.AutoCacheKey = v
	}
	// The gateway's management API requires a setup token while dashboard auth
	// is inactive, and it is a secret, so it comes from the environment rather
	// than from config.json.
	if v := os.Getenv("CHRONOS_ADMIN_TOKEN"); v != "" {
		cfg.AdminAuthValue = v
		if cfg.AdminAuthHeader == "" {
			cfg.AdminAuthHeader = "X-Bifrost-Setup-Token"
		}
	}
	if v := os.Getenv("BIFROST_SETUP_TOKEN"); v != "" && cfg.AdminAuthValue == "" {
		cfg.AdminAuthValue = v
		if cfg.AdminAuthHeader == "" {
			cfg.AdminAuthHeader = "X-Bifrost-Setup-Token"
		}
	}
	if v := os.Getenv("CHRONOS_DEBUG"); v == "1" || strings.EqualFold(v, "true") {
		cfg.Debug = true
	}
	if v := os.Getenv("CHRONOS_SYNC_ON_CACHE_HIT"); v != "" {
		b := v == "1" || strings.EqualFold(v, "true")
		cfg.SyncOnCacheHit = &b
	}

	// Normalise.
	cfg.VerifierURL = strings.TrimRight(cfg.VerifierURL, "/")
	cfg.BifrostAdminURL = strings.TrimRight(cfg.BifrostAdminURL, "/")
	if cfg.TTLByClass == nil {
		cfg.TTLByClass = defaultTTLByClass()
	}
	if cfg.MaxAnswerChars <= 0 {
		cfg.MaxAnswerChars = 12000
	}
	if cfg.VerifyTimeoutMS <= 0 {
		cfg.VerifyTimeoutMS = 4000
	}

	return cfg, warnings
}

func (c Config) syncOnCacheHit() bool {
	return c.SyncOnCacheHit == nil || *c.SyncOnCacheHit
}

func (c Config) evictStale() bool {
	return c.EvictStale == nil || *c.EvictStale
}

func (c Config) verifyTimeout() time.Duration {
	return time.Duration(c.VerifyTimeoutMS) * time.Millisecond
}

// ttlForClass returns the cache TTL for a volatility class, falling back to
// DefaultTTL. Unparseable values fall back rather than propagating an error.
func (c Config) ttlForClass(class string) time.Duration {
	if s, ok := c.TTLByClass[class]; ok {
		if d, err := time.ParseDuration(s); err == nil && d > 0 {
			return d
		}
	}
	if d, err := time.ParseDuration(c.DefaultTTL); err == nil && d > 0 {
		return d
	}
	return 24 * time.Hour
}

// watchesPath reports whether Chronos should act on this transport path.
func (c Config) watchesPath(path string) bool {
	if len(c.Paths) == 0 {
		return true
	}
	for _, p := range c.Paths {
		if strings.HasSuffix(path, p) || strings.HasPrefix(path, p) {
			return true
		}
	}
	return false
}
