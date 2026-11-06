package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// TestEvictorClearsCacheID checks the request Chronos actually sends to the
// gateway's cache-clear API, against a mock server standing in for Bifrost.
func TestEvictorClearsCacheID(t *testing.T) {
	var gotMethod, gotPath string
	var calls int32

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		gotMethod, gotPath = r.Method, r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "", "")
	if err := e.ClearCacheID(context.Background(), "550e8500-e29b-41d4-a725-446655440001"); err != nil {
		t.Fatalf("ClearCacheID returned an error: %v", err)
	}

	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("expected exactly 1 request, got %d", got)
	}
	if gotMethod != http.MethodDelete {
		t.Errorf("method = %s, want DELETE", gotMethod)
	}
	want := "/api/cache/clear/550e8500-e29b-41d4-a725-446655440001"
	if gotPath != want {
		t.Errorf("path = %q, want %q", gotPath, want)
	}
}

func TestEvictorEscapesCacheID(t *testing.T) {
	// A cache id is opaque; anything that needs escaping must survive the trip
	// rather than silently clearing the wrong entry.
	var gotRawPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotRawPath = r.URL.EscapedPath()
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "", "")
	if err := e.ClearCacheID(context.Background(), "tenant a/session:42"); err != nil {
		t.Fatalf("ClearCacheID returned an error: %v", err)
	}

	decoded, err := url.PathUnescape(strings.TrimPrefix(gotRawPath, "/api/cache/clear/"))
	if err != nil {
		t.Fatalf("server received an unparseable path %q: %v", gotRawPath, err)
	}
	if decoded != "tenant a/session:42" {
		t.Errorf("cache id round-tripped as %q", decoded)
	}
}

func TestEvictorClearByKey(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "", "")
	if err := e.ClearCacheKey(context.Background(), "support-session-456"); err != nil {
		t.Fatalf("ClearCacheKey returned an error: %v", err)
	}
	if gotPath != "/api/cache/clear-by-key/support-session-456" {
		t.Errorf("path = %q", gotPath)
	}
}

func TestEvictorSendsAuthHeader(t *testing.T) {
	var gotAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "Authorization", "Bearer sk-bf-admin")
	if err := e.ClearCacheID(context.Background(), "abc"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotAuth != "Bearer sk-bf-admin" {
		t.Errorf("Authorization header = %q", gotAuth)
	}
}

func TestEvictorRejectsEmptyID(t *testing.T) {
	var called int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt32(&called, 1)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "", "")
	if err := e.ClearCacheID(context.Background(), "   "); err == nil {
		t.Error("expected an error for an empty cache id")
	}
	if atomic.LoadInt32(&called) != 0 {
		t.Error("an empty cache id must not reach the gateway")
	}
}

func TestEvictorReportsServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("vector store unavailable"))
	}))
	defer server.Close()

	e := NewEvictor(server.URL, "", "")
	err := e.ClearCacheID(context.Background(), "abc")
	if err == nil {
		t.Fatal("expected an error for a 500 response")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("error should mention the status code, got: %v", err)
	}
}

// TestEvictorDegradesWhenGatewayUnreachable is the important failure case:
// eviction failing must surface as an error the caller can log, never a panic
// or a hang.
func TestEvictorDegradesWhenGatewayUnreachable(t *testing.T) {
	// Port 1 on loopback refuses connections promptly.
	e := NewEvictor("http://127.0.0.1:1", "", "")

	done := make(chan error, 1)
	go func() { done <- e.ClearCacheID(context.Background(), "abc") }()

	select {
	case err := <-done:
		if err == nil {
			t.Error("expected an error when the gateway is unreachable")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("ClearCacheID hung instead of failing fast")
	}
}

func TestEvictorHonoursContextCancellation(t *testing.T) {
	block := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		<-block
		w.WriteHeader(http.StatusOK)
	}))
	defer func() {
		close(block)
		server.Close()
	}()

	e := NewEvictor(server.URL, "", "")
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	if err := e.ClearCacheID(ctx, "abc"); err == nil {
		t.Error("expected a context deadline error")
	}
}
