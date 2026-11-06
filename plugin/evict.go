package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Evictor removes entries from Bifrost's semantic cache through the gateway's
// own management API, which is the supported way to invalidate a single entry.
type Evictor struct {
	adminURL   string
	authHeader string
	authValue  string
	http       *http.Client
}

// NewEvictor builds an evictor against the gateway's admin base URL.
func NewEvictor(adminURL, authHeader, authValue string) *Evictor {
	return &Evictor{
		adminURL:   strings.TrimRight(adminURL, "/"),
		authHeader: authHeader,
		authValue:  authValue,
		http:       &http.Client{Timeout: 3 * time.Second},
	}
}

// ClearCacheID issues DELETE /api/cache/clear/{cacheId}.
//
// Clearing an id that does not exist, or that has already expired, succeeds on
// every vector store, so this is safe to call optimistically.
func (e *Evictor) ClearCacheID(ctx context.Context, cacheID string) error {
	if strings.TrimSpace(cacheID) == "" {
		return fmt.Errorf("empty cache id")
	}
	return e.do(ctx, e.adminURL+"/api/cache/clear/"+url.PathEscape(cacheID))
}

// ClearCacheKey issues DELETE /api/cache/clear-by-key/{cacheKey}, removing the
// entries of every identity under that key. Used by the sweeper, not the hot
// path.
func (e *Evictor) ClearCacheKey(ctx context.Context, cacheKey string) error {
	if strings.TrimSpace(cacheKey) == "" {
		return fmt.Errorf("empty cache key")
	}
	return e.do(ctx, e.adminURL+"/api/cache/clear-by-key/"+url.PathEscape(cacheKey))
}

func (e *Evictor) do(ctx context.Context, endpoint string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, endpoint, nil)
	if err != nil {
		return err
	}
	if e.authHeader != "" && e.authValue != "" {
		req.Header.Set(e.authHeader, e.authValue)
	}

	resp, err := e.http.Do(req)
	if err != nil {
		return fmt.Errorf("cache clear request failed: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, drainLimitBytes))
		_ = resp.Body.Close()
	}()

	if resp.StatusCode >= 300 {
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		return fmt.Errorf("cache clear returned %d: %s", resp.StatusCode, strings.TrimSpace(string(snippet)))
	}
	return nil
}
