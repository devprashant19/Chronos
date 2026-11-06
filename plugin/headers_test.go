package main

import (
	"strings"
	"testing"

	"github.com/maximhq/bifrost/core/schemas"
)

func TestExposeChronosHeadersWhenListIsEmpty(t *testing.T) {
	resp := &schemas.HTTPResponseMetadata{Headers: map[string]string{}}

	exposeChronosHeaders(resp)

	got := resp.Header("Access-Control-Expose-Headers")
	for _, h := range chronosHeaders {
		if !strings.Contains(got, h) {
			t.Errorf("expose list %q is missing %q", got, h)
		}
	}
}

// TestExposeChronosHeadersPreservesExisting is the important one: the gateway
// publishes its own x-bifrost-* headers on this list, and clobbering it would
// break every client that reads them.
func TestExposeChronosHeadersPreservesExisting(t *testing.T) {
	resp := &schemas.HTTPResponseMetadata{
		Headers: map[string]string{
			"Access-Control-Expose-Headers": "x-bifrost-cache-hit, x-bifrost-cache-id",
		},
	}

	exposeChronosHeaders(resp)

	got := resp.Header("Access-Control-Expose-Headers")
	for _, h := range []string{"x-bifrost-cache-hit", "x-bifrost-cache-id"} {
		if !strings.Contains(got, h) {
			t.Errorf("expose list %q dropped the gateway's header %q", got, h)
		}
	}
	if !strings.Contains(got, hdrVerdict) {
		t.Errorf("expose list %q is missing %q", got, hdrVerdict)
	}
}

func TestExposeChronosHeadersIsIdempotent(t *testing.T) {
	resp := &schemas.HTTPResponseMetadata{Headers: map[string]string{}}

	exposeChronosHeaders(resp)
	first := resp.Header("Access-Control-Expose-Headers")
	exposeChronosHeaders(resp)
	second := resp.Header("Access-Control-Expose-Headers")

	if first != second {
		t.Errorf("second call changed the list:\n first:  %q\n second: %q", first, second)
	}
	if n := strings.Count(second, hdrVerdict); n != 1 {
		t.Errorf("%q appears %d times, want 1", hdrVerdict, n)
	}
}

func TestChronosHeadersAreAllLowercase(t *testing.T) {
	// Header names are compared case-insensitively, but the expose list is
	// matched against lowercased text, so a capitalised constant would make
	// the duplicate check fail and the entry be appended repeatedly.
	for _, h := range chronosHeaders {
		if h != strings.ToLower(h) {
			t.Errorf("header constant %q should be lowercase", h)
		}
	}
}

func TestResponseMetadataSetHeaderRoundTrip(t *testing.T) {
	// Guards the assumption the headers hook relies on: SetHeader then Header
	// returns the value, case-insensitively.
	resp := &schemas.HTTPResponseMetadata{Headers: map[string]string{}}
	resp.SetHeader(hdrVerdict, verdictOutdated)

	if got := resp.Header(hdrVerdict); got != verdictOutdated {
		t.Errorf("Header(%q) = %q, want %q", hdrVerdict, got, verdictOutdated)
	}
	if got := resp.Header(strings.ToUpper(hdrVerdict)); got != verdictOutdated {
		t.Errorf("header lookup should be case-insensitive, got %q", got)
	}
}
