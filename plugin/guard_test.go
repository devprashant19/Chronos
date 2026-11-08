package main

import (
	"strings"
	"testing"

	"github.com/maximhq/bifrost/core/schemas"
)

// Guard mode has two halves in the plugin: substitute a corrected answer, or
// refuse with a 409. Neither had a test until 8 Nov — only the config parse
// did — so a README line saying guard mode was "unit-tested in plugin/" was
// wrong. These are the tests that line described.

func chatResponse(answer string) *schemas.BifrostResponse {
	text := answer
	return &schemas.BifrostResponse{
		ChatResponse: &schemas.BifrostChatResponse{
			Choices: []schemas.BifrostResponseChoice{{
				ChatNonStreamResponseChoice: &schemas.ChatNonStreamResponseChoice{
					Message: &schemas.ChatMessage{
						Content: &schemas.ChatMessageContent{ContentStr: &text},
					},
				},
			}},
		},
	}
}

func TestReplaceAnswerInResponseSwapsTheText(t *testing.T) {
	resp := chatResponse("The latest stable release of Python is 3.11.")

	if !ReplaceAnswerInResponse(resp, "The latest stable release of Python is 3.14.") {
		t.Fatal("expected the replacement to succeed")
	}
	got, _ := AnswerFromResponse(resp)
	if got != "The latest stable release of Python is 3.14." {
		t.Fatalf("answer after replacement = %q", got)
	}
}

func TestReplaceAnswerInResponseDropsContentBlocks(t *testing.T) {
	// A response carried as blocks must not keep its old blocks beside the
	// new string, or a client reading blocks still gets the stale answer.
	resp := chatResponse("")
	old := "stale"
	msg := resp.ChatResponse.Choices[0].ChatNonStreamResponseChoice.Message
	msg.Content.ContentStr = nil
	msg.Content.ContentBlocks = []schemas.ChatContentBlock{{Text: &old}}

	if !ReplaceAnswerInResponse(resp, "fresh") {
		t.Fatal("expected the replacement to succeed")
	}
	if msg.Content.ContentBlocks != nil {
		t.Fatalf("old content blocks survived: %+v", msg.Content.ContentBlocks)
	}
	if msg.Content.ContentStr == nil || *msg.Content.ContentStr != "fresh" {
		t.Fatalf("content = %+v", msg.Content.ContentStr)
	}
}

func TestReplaceAnswerInResponseReportsWhenItCannot(t *testing.T) {
	// The caller falls back to blocking when this returns false, so a false
	// "true" would serve the unchanged stale answer with no warning.
	cases := map[string]*schemas.BifrostResponse{
		"nil response":     nil,
		"no chat response": {},
		"no choices":       {ChatResponse: &schemas.BifrostChatResponse{}},
		"streaming choice": {ChatResponse: &schemas.BifrostChatResponse{Choices: []schemas.BifrostResponseChoice{{}}}},
	}
	for name, resp := range cases {
		if ReplaceAnswerInResponse(resp, "fresh") {
			t.Errorf("%s: reported success with nothing to replace", name)
		}
	}
	if ReplaceAnswerInResponse(chatResponse("stale"), "") {
		t.Error("an empty correction must not count as a replacement")
	}
}

func TestBlockedErrorIsA409ThatDoesNotFallBack(t *testing.T) {
	err := blockedError(result{Score: 34, Verdict: verdictOutdated, Stale: 1, ReportID: "r-1"})

	if err.StatusCode == nil || *err.StatusCode != 409 {
		t.Fatalf("status = %v, want 409", err.StatusCode)
	}
	// Falling back to another provider would just produce another unverified
	// answer, which is not the problem being solved.
	if err.AllowFallbacks == nil || *err.AllowFallbacks {
		t.Fatal("a blocked answer must not trigger provider fallbacks")
	}
	if err.Error == nil || err.Error.Code == nil || *err.Error.Code != "stale_answer_blocked" {
		t.Fatalf("error code = %+v", err.Error)
	}
	for _, want := range []string{"34/100", "1 claim(s)", `"report_id":"r-1"`, `"verdict":"outdated"`} {
		if !strings.Contains(err.Error.Message, want) {
			t.Errorf("message %q lacks %q", err.Error.Message, want)
		}
	}
}

func TestGuardActsOnAnyOutdatedAnswerWhateverItScored(t *testing.T) {
	guard := Config{Mode: ModeGuard, GuardThreshold: 50}

	cases := []struct {
		name string
		cfg  Config
		res  result
		want bool
	}{
		// The bug: a contradicted claim is outdated even when the arithmetic
		// lands at 50 or above, and keying on the score alone let it through.
		{"outdated at or above the threshold", guard, result{Verdict: verdictOutdated, Score: 50.4}, true},
		{"outdated below the threshold", guard, result{Verdict: verdictOutdated, Score: 34}, true},
		{"check below the threshold", guard, result{Verdict: verdictCheck, Score: 45}, true},
		{"check above the threshold", guard, result{Verdict: verdictCheck, Score: 70}, false},
		{"current", guard, result{Verdict: verdictCurrent, Score: 95}, false},
		// Not knowing is not a reason to block.
		{"unverifiable scores 0 and is left alone", guard, result{Verdict: verdictUnverifiable, Score: 0}, false},
		{"annotate never acts", Config{Mode: ModeAnnotate, GuardThreshold: 50}, result{Verdict: verdictOutdated, Score: 10}, false},
	}
	for _, c := range cases {
		if got := guardShouldAct(c.cfg, c.res); got != c.want {
			t.Errorf("%s: guardShouldAct = %v, want %v", c.name, got, c.want)
		}
	}
}
