package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestVerifierClientVerify(t *testing.T) {
	var gotReq VerifyRequest
	var gotPath, gotContentType string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotContentType = r.Header.Get("Content-Type")
		_ = json.NewDecoder(r.Body).Decode(&gotReq)

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(VerifyResponse{
			ReportID:   "rep-123",
			Score:      42.5,
			Verdict:    "outdated",
			ClaimCount: 2,
			StaleCount: 1,
			TTLSeconds: 900,
		})
	}))
	defer server.Close()

	c := NewVerifierClient(server.URL, 2*time.Second)
	res, err := c.Verify(context.Background(), VerifyRequest{
		RequestID: "req-1",
		Answer:    "Jack Dorsey is the CEO of Twitter.",
		CacheHit:  true,
		CacheID:   "cid-9",
		Mode:      "annotate",
	})
	if err != nil {
		t.Fatalf("Verify returned an error: %v", err)
	}

	if gotPath != "/v1/verify" {
		t.Errorf("path = %q, want /v1/verify", gotPath)
	}
	if gotContentType != "application/json" {
		t.Errorf("content type = %q", gotContentType)
	}
	if gotReq.Answer != "Jack Dorsey is the CEO of Twitter." {
		t.Errorf("answer not transmitted: %q", gotReq.Answer)
	}
	if !gotReq.CacheHit || gotReq.CacheID != "cid-9" {
		t.Errorf("cache context not transmitted: hit=%v id=%q", gotReq.CacheHit, gotReq.CacheID)
	}

	if res.ReportID != "rep-123" || res.Score != 42.5 || res.Verdict != "outdated" {
		t.Errorf("response decoded wrongly: %+v", res)
	}
	if res.TTLSeconds != 900 {
		t.Errorf("ttl = %d, want 900", res.TTLSeconds)
	}
}

func TestVerifierClientAsyncSetsFlagAndPath(t *testing.T) {
	var gotReq VerifyRequest
	var gotPath string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotReq)
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte(`{"report_id":"rep-async","verdict":"pending"}`))
	}))
	defer server.Close()

	c := NewVerifierClient(server.URL, 2*time.Second)
	res, err := c.VerifyAsync(context.Background(), VerifyRequest{Answer: "x"})
	if err != nil {
		t.Fatalf("VerifyAsync returned an error: %v", err)
	}
	if gotPath != "/v1/verify/async" {
		t.Errorf("path = %q", gotPath)
	}
	if !gotReq.Async {
		t.Error("async flag was not set on the request")
	}
	if res.Verdict != "pending" {
		t.Errorf("verdict = %q, want pending", res.Verdict)
	}
}

// TestVerifierClientDegradesOn5xx and the tests below cover the rule that
// matters most: Chronos must never break the gateway. Every verifier failure
// has to come back as a plain error so PostLLMHook can pass the response
// through as "unverifiable".
func TestVerifierClientDegradesOn5xx(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("extractor exploded"))
	}))
	defer server.Close()

	c := NewVerifierClient(server.URL, 2*time.Second)
	res, err := c.Verify(context.Background(), VerifyRequest{Answer: "x"})
	if err == nil {
		t.Fatal("expected an error for a 500 response")
	}
	if res != nil {
		t.Error("no response should be returned alongside an error")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("error should name the status, got %v", err)
	}
}

func TestVerifierClientDegradesOnMalformedJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("{not json at all"))
	}))
	defer server.Close()

	c := NewVerifierClient(server.URL, 2*time.Second)
	if _, err := c.Verify(context.Background(), VerifyRequest{Answer: "x"}); err == nil {
		t.Fatal("expected an error for malformed JSON")
	}
}

func TestVerifierClientDegradesOnTimeout(t *testing.T) {
	block := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		<-block
		w.WriteHeader(http.StatusOK)
	}))
	defer func() {
		close(block)
		server.Close()
	}()

	c := NewVerifierClient(server.URL, 100*time.Millisecond)

	start := time.Now()
	_, err := c.Verify(context.Background(), VerifyRequest{Answer: "x"})
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("expected a timeout error")
	}
	// The point of the deadline is that the gateway is not held up.
	if elapsed > 2*time.Second {
		t.Errorf("client waited %v despite a 100ms timeout", elapsed)
	}
}

func TestVerifierClientDegradesWhenUnreachable(t *testing.T) {
	c := NewVerifierClient("http://127.0.0.1:1", 500*time.Millisecond)
	if _, err := c.Verify(context.Background(), VerifyRequest{Answer: "x"}); err == nil {
		t.Fatal("expected an error when the verifier is unreachable")
	}
}

func TestVerifierClientHealth(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	c := NewVerifierClient(server.URL, time.Second)
	if err := c.Health(context.Background()); err != nil {
		t.Errorf("Health returned an error: %v", err)
	}
}
