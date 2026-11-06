package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// maxVerifyResponseBytes bounds how much of a verifier reply we will decode.
const (
	maxVerifyResponseBytes = 4 << 20
	drainLimitBytes        = 64 << 10
)

// VerifyRequest is the payload Chronos sends to the verifier.
type VerifyRequest struct {
	RequestID  string `json:"request_id"`
	Answer     string `json:"answer"`
	Prompt     string `json:"prompt,omitempty"`
	Model      string `json:"model,omitempty"`
	Provider   string `json:"provider,omitempty"`
	VirtualKey string `json:"virtual_key,omitempty"`

	// Cache context, so the verifier can attribute and evict.
	CacheHit   bool    `json:"cache_hit"`
	CacheID    string  `json:"cache_id,omitempty"`
	CacheKey   string  `json:"cache_key,omitempty"`
	HitType    string  `json:"hit_type,omitempty"`
	Similarity float64 `json:"similarity,omitempty"`

	// PromptClass is the pre-hook's cheap guess, passed along as a hint.
	PromptClass string `json:"prompt_class,omitempty"`

	// Mode tells the verifier how much work to do.
	Mode string `json:"mode"`

	// Regenerate asks for a corrected answer when the score falls below
	// Threshold. Guard mode only.
	Regenerate bool `json:"regenerate,omitempty"`
	Threshold  int  `json:"threshold,omitempty"`

	// Async requests a 202 plus a report id instead of a full inline report.
	Async bool `json:"async,omitempty"`
}

// ClaimReport is one verified claim.
type ClaimReport struct {
	Text         string  `json:"text"`
	SpanStart    int     `json:"span_start"`
	SpanEnd      int     `json:"span_end"`
	Subject      string  `json:"subject"`
	Predicate    string  `json:"predicate"`
	StatedValue  string  `json:"stated_value"`
	Class        string  `json:"class"`
	Verdict      string  `json:"verdict"`
	Score        float64 `json:"score"`
	CurrentValue string  `json:"current_value,omitempty"`
	SourceURL    string  `json:"source_url,omitempty"`
	PublishDate  string  `json:"publish_date,omitempty"`
	Quote        string  `json:"quote,omitempty"`
}

// VerifyResponse is the verifier's report on one answer.
type VerifyResponse struct {
	ReportID string `json:"report_id"`

	// Score is the answer-level score, 0-100.
	Score float64 `json:"score"`

	// Verdict is one of: current, check, outdated, unverifiable, pending.
	Verdict string `json:"verdict"`

	Claims     []ClaimReport `json:"claims"`
	ClaimCount int           `json:"claim_count"`
	StaleCount int           `json:"stale_count"`
	Skipped    int           `json:"skipped"`

	// TTLSeconds is the cache lifetime the verifier recommends for this answer,
	// taken as the minimum re-check interval across its claims.
	TTLSeconds int `json:"ttl_seconds"`

	// CorrectedAnswer is set only when regeneration was asked for and happened.
	CorrectedAnswer string `json:"corrected_answer,omitempty"`

	// Blocked asks the gateway to withhold the answer. Guard mode only.
	Blocked bool `json:"blocked,omitempty"`

	// TavilyCredits spent producing this report.
	TavilyCredits int `json:"tavily_credits"`

	// Degraded is set when the verifier ran in a reduced mode, for example
	// because its Tavily budget was spent.
	Degraded bool   `json:"degraded,omitempty"`
	Reason   string `json:"reason,omitempty"`

	LatencyMS int64 `json:"latency_ms"`
}

// VerifierClient talks to the Chronos verifier over HTTP.
//
// Every method here is best-effort by construction. Callers treat any error as
// "unverifiable" rather than as a request failure: Chronos must never be the
// reason a gateway request fails.
type VerifierClient struct {
	baseURL string
	http    *http.Client
}

// NewVerifierClient builds a client with a connection pool sized for gateway
// concurrency.
func NewVerifierClient(baseURL string, timeout time.Duration) *VerifierClient {
	transport := &http.Transport{
		MaxIdleConns:        64,
		MaxIdleConnsPerHost: 64,
		IdleConnTimeout:     90 * time.Second,
	}
	return &VerifierClient{
		baseURL: strings.TrimRight(baseURL, "/"),
		http: &http.Client{
			Timeout:   timeout,
			Transport: transport,
		},
	}
}

// Verify performs an inline verification bounded by ctx.
func (c *VerifierClient) Verify(ctx context.Context, req VerifyRequest) (*VerifyResponse, error) {
	return c.post(ctx, "/v1/verify", req)
}

// VerifyAsync hands the answer off and returns as soon as the verifier accepts
// it. The report is retrievable later by id, and the verifier streams the
// outcome to any console listening on /v1/stream.
func (c *VerifierClient) VerifyAsync(ctx context.Context, req VerifyRequest) (*VerifyResponse, error) {
	req.Async = true
	return c.post(ctx, "/v1/verify/async", req)
}

func (c *VerifierClient) post(ctx context.Context, path string, payload any) (*VerifyResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal verify request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build verify request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("User-Agent", "chronos-plugin/"+Version)

	resp, err := c.http.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("verifier unreachable: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, drainLimitBytes))
		_ = resp.Body.Close()
	}()

	if resp.StatusCode >= 300 {
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("verifier returned %d: %s", resp.StatusCode, strings.TrimSpace(string(snippet)))
	}

	var out VerifyResponse
	if err := json.NewDecoder(io.LimitReader(resp.Body, maxVerifyResponseBytes)).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode verify response: %w", err)
	}
	return &out, nil
}

// Health reports whether the verifier is reachable. Used for a startup log
// line only: a down verifier is a degraded mode, never a boot failure.
func (c *VerifierClient) Health(ctx context.Context) error {
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/health", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(httpReq)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("verifier health returned %d", resp.StatusCode)
	}
	return nil
}
