package main

import (
	"sync"
	"time"
)

// resultStore carries a verification result from PostLLMHook to
// HTTPTransportResponseHeadersHook.
//
// Why this exists rather than just using the request context: Bifrost's
// context values propagate *downward*. A value set in HTTPTransportPreHook is
// visible in PreLLMHook and PostLLMHook, which is how the TTL and the prompt
// class travel. The reverse is not true — PostLLMHook runs inside the LLM
// pipeline, and a value it sets is not visible to the transport-level hook
// that commits the response headers. Verified empirically: the hooks ran and
// the verdict was computed, but ctx.Value in the headers hook came back empty.
//
// So the verdict is parked here under the request id, which both hooks can
// read from the context, and collected by the headers hook.
//
// The store is bounded in two ways, because a gateway must not grow a map
// forever: every entry has a deadline, and a write past maxEntries triggers a
// sweep of expired entries (then, if still over, drops the oldest). Losing a
// header is a cosmetic failure; leaking memory in a gateway is not.
type resultStore struct {
	mu      sync.Mutex
	entries map[string]storedResult
	ttl     time.Duration
	max     int
}

type storedResult struct {
	result  result
	expires time.Time
}

const (
	resultTTL        = 60 * time.Second
	maxResultEntries = 4096
)

func newResultStore() *resultStore {
	return &resultStore{
		entries: make(map[string]storedResult, 256),
		ttl:     resultTTL,
		max:     maxResultEntries,
	}
}

// put parks a result for this request id.
func (s *resultStore) put(requestID string, res result) {
	if requestID == "" {
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if len(s.entries) >= s.max {
		s.evictLocked()
	}
	s.entries[requestID] = storedResult{result: res, expires: time.Now().Add(s.ttl)}
}

// take returns and removes the result for this request id.
func (s *resultStore) take(requestID string) (result, bool) {
	if requestID == "" {
		return result{}, false
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	entry, ok := s.entries[requestID]
	if !ok {
		return result{}, false
	}
	delete(s.entries, requestID)

	if time.Now().After(entry.expires) {
		return result{}, false
	}
	return entry.result, true
}

// evictLocked drops expired entries, then oldest-first if still at capacity.
// Caller holds the lock.
func (s *resultStore) evictLocked() {
	now := time.Now()
	for k, v := range s.entries {
		if now.After(v.expires) {
			delete(s.entries, k)
		}
	}

	// Still full: a response was never committed for these, so drop the
	// entries closest to expiry.
	for len(s.entries) >= s.max {
		var oldestKey string
		var oldest time.Time
		first := true
		for k, v := range s.entries {
			if first || v.expires.Before(oldest) {
				oldestKey, oldest, first = k, v.expires, false
			}
		}
		if oldestKey == "" {
			return
		}
		delete(s.entries, oldestKey)
	}
}

func (s *resultStore) size() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.entries)
}
