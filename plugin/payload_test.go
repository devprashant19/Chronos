package main

import (
	"strings"
	"testing"
)

func TestPromptFromRequestBody(t *testing.T) {
	tests := []struct {
		name       string
		body       string
		wantModel  string
		wantPrompt string
		wantStream bool
	}{
		{
			name:       "simple chat",
			body:       `{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"What does GPT-4o cost?"}]}`,
			wantModel:  "openai/gpt-4o-mini",
			wantPrompt: "What does GPT-4o cost?",
		},
		{
			name: "last user turn wins",
			body: `{"model":"m","messages":[
				{"role":"system","content":"be nice"},
				{"role":"user","content":"first question"},
				{"role":"assistant","content":"an answer"},
				{"role":"user","content":"second question"}]}`,
			wantModel:  "m",
			wantPrompt: "second question",
		},
		{
			name:       "content blocks",
			body:       `{"model":"m","messages":[{"role":"user","content":[{"type":"text","text":"line one"},{"type":"text","text":"line two"}]}]}`,
			wantModel:  "m",
			wantPrompt: "line one\nline two",
		},
		{
			name:       "stream flag",
			body:       `{"model":"m","stream":true,"messages":[{"role":"user","content":"hi"}]}`,
			wantModel:  "m",
			wantPrompt: "hi",
			wantStream: true,
		},
		{
			name:       "responses api string input",
			body:       `{"model":"m","input":"what is the latest version of Go?"}`,
			wantModel:  "m",
			wantPrompt: "what is the latest version of Go?",
		},
		{
			name:       "no user turn falls back to last message",
			body:       `{"model":"m","messages":[{"role":"system","content":"only a system turn"}]}`,
			wantModel:  "m",
			wantPrompt: "only a system turn",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			model, prompt, stream := PromptFromRequestBody([]byte(tc.body))
			if model != tc.wantModel {
				t.Errorf("model = %q, want %q", model, tc.wantModel)
			}
			if prompt != tc.wantPrompt {
				t.Errorf("prompt = %q, want %q", prompt, tc.wantPrompt)
			}
			if stream != tc.wantStream {
				t.Errorf("stream = %v, want %v", stream, tc.wantStream)
			}
		})
	}
}

// TestPromptFromRequestBodyNeverPanics matters because this runs in the
// transport pre-hook on every single request, including malformed ones.
func TestPromptFromRequestBodyNeverPanics(t *testing.T) {
	for _, body := range []string{
		"",
		"null",
		"[]",
		"not json",
		`{"messages":null}`,
		`{"messages":[]}`,
		`{"messages":[null]}`,
		`{"messages":[{"role":"user"}]}`,
		`{"messages":[{"role":"user","content":null}]}`,
		`{"messages":[{"role":"user","content":42}]}`,
		`{"messages":[{"role":"user","content":[{"type":"image_url"}]}]}`,
		`{"model":123,"messages":[]}`,
		`{"input":{"nested":"object"}}`,
	} {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Errorf("panicked on body %q: %v", body, r)
				}
			}()
			_, _, _ = PromptFromRequestBody([]byte(body))
		}()
	}
}

func TestTruncate(t *testing.T) {
	if got := truncate("hello", 10); got != "hello" {
		t.Errorf("short string was altered: %q", got)
	}
	if got := truncate("hello world", 5); got != "hello" {
		t.Errorf("truncate = %q, want %q", got, "hello")
	}
	if got := truncate("abc", 0); got != "abc" {
		t.Errorf("non-positive max should be a no-op, got %q", got)
	}
}

func TestTruncateDoesNotSplitRunes(t *testing.T) {
	// "é" is two bytes; cutting at 1 must drop it rather than emit half a rune,
	// which would make the JSON payload to the verifier invalid UTF-8.
	s := "é"
	got := truncate(s, 1)
	if got != "" {
		t.Errorf("truncate(%q, 1) = %q, want an empty string", s, got)
	}

	emoji := "ab🙂cd"
	for n := 0; n <= len(emoji); n++ {
		out := truncate(emoji, n)
		if !isValidUTF8(out) {
			t.Errorf("truncate(%q, %d) produced invalid UTF-8: %q", emoji, n, out)
		}
	}
}

func isValidUTF8(s string) bool {
	for _, r := range s {
		if r == '�' && !strings.ContainsRune(s, '�') {
			return false
		}
	}
	// A cheap structural check: the strings package round-trips valid UTF-8.
	return strings.ToValidUTF8(s, "?") == s
}
