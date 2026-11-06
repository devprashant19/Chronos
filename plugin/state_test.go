package main

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestResultStoreRoundTrip(t *testing.T) {
	s := newResultStore()
	want := result{Score: 87, Verdict: verdictCurrent, Claims: 3, Stale: 0, TTL: 900, ReportID: "rep-1"}

	s.put("req-1", want)
	got, ok := s.take("req-1")
	if !ok {
		t.Fatal("take() did not find the stored result")
	}
	if got != want {
		t.Errorf("got %+v, want %+v", got, want)
	}
}

func TestResultStoreTakeRemoves(t *testing.T) {
	// The headers hook collects a result exactly once per request; leaving it
	// behind would leak an entry for every request the gateway serves.
	s := newResultStore()
	s.put("req-1", result{Verdict: verdictCurrent})

	if _, ok := s.take("req-1"); !ok {
		t.Fatal("first take should succeed")
	}
	if _, ok := s.take("req-1"); ok {
		t.Error("second take should fail: take must remove the entry")
	}
	if s.size() != 0 {
		t.Errorf("store size = %d after take, want 0", s.size())
	}
}

func TestResultStoreMissingKey(t *testing.T) {
	s := newResultStore()
	if _, ok := s.take("nope"); ok {
		t.Error("take on a missing key should report false")
	}
	if _, ok := s.take(""); ok {
		t.Error("take on an empty key should report false")
	}
}

func TestResultStoreIgnoresEmptyKey(t *testing.T) {
	s := newResultStore()
	s.put("", result{Verdict: verdictCurrent})
	if s.size() != 0 {
		t.Error("an empty request id must not be stored")
	}
}

func TestResultStoreExpiry(t *testing.T) {
	s := newResultStore()
	s.ttl = 20 * time.Millisecond
	s.put("req-1", result{Verdict: verdictCurrent})

	time.Sleep(50 * time.Millisecond)
	if _, ok := s.take("req-1"); ok {
		t.Error("an expired entry should not be returned")
	}
}

// TestResultStoreBounded is the memory-safety property: a gateway whose
// responses never commit (so take() is never called) must not grow forever.
func TestResultStoreBounded(t *testing.T) {
	s := newResultStore()
	s.max = 64

	for i := 0; i < 500; i++ {
		s.put(fmt.Sprintf("req-%d", i), result{Verdict: verdictPending})
	}

	if size := s.size(); size > s.max {
		t.Errorf("store grew to %d entries, above the %d cap", size, s.max)
	}
}

func TestResultStoreConcurrent(t *testing.T) {
	// PostLLMHook and the headers hook run on different requests concurrently;
	// run under -race to make this meaningful.
	s := newResultStore()
	var wg sync.WaitGroup

	for i := 0; i < 50; i++ {
		wg.Add(2)
		id := fmt.Sprintf("req-%d", i)
		go func() {
			defer wg.Done()
			s.put(id, result{Score: 50, Verdict: verdictCheck})
		}()
		go func() {
			defer wg.Done()
			_, _ = s.take(id)
		}()
	}
	wg.Wait()
}
