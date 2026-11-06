package main

import (
	"encoding/json"
	"strings"

	"github.com/maximhq/bifrost/core/schemas"
)

// chatRequestPeek is the smallest view of an inference request body that
// Chronos needs: the model, and the last user turn.
//
// Chronos deliberately does not unmarshal the full request. The transport
// pre-hook runs on every request, so it parses as little as possible and never
// re-serialises the body.
type chatRequestPeek struct {
	Model    string            `json:"model"`
	Stream   bool              `json:"stream"`
	Messages []json.RawMessage `json:"messages"`

	// Responses-API shape.
	Input json.RawMessage `json:"input"`
}

type messagePeek struct {
	Role    string          `json:"role"`
	Content json.RawMessage `json:"content"`
}

type contentBlockPeek struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// decodeContent handles the two legal shapes of a message content field: a
// bare string, or an array of typed blocks.
func decodeContent(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}

	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}

	var blocks []contentBlockPeek
	if err := json.Unmarshal(raw, &blocks); err == nil {
		var sb strings.Builder
		for _, b := range blocks {
			if b.Text == "" {
				continue
			}
			if sb.Len() > 0 {
				sb.WriteByte('\n')
			}
			sb.WriteString(b.Text)
		}
		return sb.String()
	}
	return ""
}

// PromptFromRequestBody returns the model and the user prompt Chronos should
// classify: the last user message, which is what a cache key is built from and
// what the answer actually responds to.
func PromptFromRequestBody(body []byte) (model string, prompt string, stream bool) {
	if len(body) == 0 {
		return "", "", false
	}

	var peek chatRequestPeek
	if err := json.Unmarshal(body, &peek); err != nil {
		return "", "", false
	}

	// Responses API: `input` may be a plain string.
	if len(peek.Messages) == 0 && len(peek.Input) > 0 {
		return peek.Model, decodeContent(peek.Input), peek.Stream
	}

	for i := len(peek.Messages) - 1; i >= 0; i-- {
		var m messagePeek
		if err := json.Unmarshal(peek.Messages[i], &m); err != nil {
			continue
		}
		if strings.EqualFold(m.Role, "user") {
			return peek.Model, decodeContent(m.Content), peek.Stream
		}
	}

	// No user turn: fall back to the last message of any role.
	if n := len(peek.Messages); n > 0 {
		var m messagePeek
		if err := json.Unmarshal(peek.Messages[n-1], &m); err == nil {
			return peek.Model, decodeContent(m.Content), peek.Stream
		}
	}
	return peek.Model, "", peek.Stream
}

// AnswerFromResponse pulls the assistant text out of a Bifrost response.
//
// It handles the non-streaming chat shape, which is the only shape Chronos
// verifies: a streamed answer is assembled by the client, so there is no
// complete text to check inside the gateway. Streams still get headers (with
// verdict "pending") through HTTPTransportResponseHeadersHook.
func AnswerFromResponse(resp *schemas.BifrostResponse) (answer string, model string) {
	if resp == nil || resp.ChatResponse == nil {
		return "", ""
	}
	chat := resp.ChatResponse
	model = chat.Model

	var sb strings.Builder
	for i := range chat.Choices {
		choice := chat.Choices[i]
		if choice.ChatNonStreamResponseChoice == nil {
			continue
		}
		msg := choice.ChatNonStreamResponseChoice.Message
		if msg == nil || msg.Content == nil {
			continue
		}
		if msg.Content.ContentStr != nil {
			if sb.Len() > 0 {
				sb.WriteByte('\n')
			}
			sb.WriteString(*msg.Content.ContentStr)
			continue
		}
		for _, block := range msg.Content.ContentBlocks {
			if block.Text == nil || *block.Text == "" {
				continue
			}
			if sb.Len() > 0 {
				sb.WriteByte('\n')
			}
			sb.WriteString(*block.Text)
		}
	}
	return sb.String(), model
}

// ReplaceAnswerInResponse swaps the assistant text of the first choice, used by
// guard mode when the verifier returns a corrected answer.
//
// It only ever rewrites text that is already a plain string, and reports
// whether it succeeded so the caller can fall back to annotating instead of
// silently serving an unchanged answer.
func ReplaceAnswerInResponse(resp *schemas.BifrostResponse, corrected string) bool {
	if resp == nil || resp.ChatResponse == nil || corrected == "" {
		return false
	}
	for i := range resp.ChatResponse.Choices {
		choice := resp.ChatResponse.Choices[i]
		if choice.ChatNonStreamResponseChoice == nil {
			continue
		}
		msg := choice.ChatNonStreamResponseChoice.Message
		if msg == nil {
			continue
		}
		if msg.Content == nil {
			msg.Content = &schemas.ChatMessageContent{}
		}
		text := corrected
		msg.Content.ContentStr = &text
		msg.Content.ContentBlocks = nil
		return true
	}
	return false
}

// truncate keeps a payload bounded without splitting a UTF-8 rune.
func truncate(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	cut := s[:max]
	for len(cut) > 0 && !isUTF8Boundary(cut) {
		cut = cut[:len(cut)-1]
	}
	return cut
}

// isUTF8Boundary reports whether s ends on a complete rune.
func isUTF8Boundary(s string) bool {
	if s == "" {
		return true
	}
	// Walk back over continuation bytes (10xxxxxx) to find the lead byte.
	i := len(s) - 1
	cont := 0
	for i >= 0 && s[i]&0xC0 == 0x80 {
		cont++
		i--
	}
	if i < 0 {
		return false
	}
	lead := s[i]
	var want int
	switch {
	case lead&0x80 == 0x00:
		want = 0
	case lead&0xE0 == 0xC0:
		want = 1
	case lead&0xF0 == 0xE0:
		want = 2
	case lead&0xF8 == 0xF0:
		want = 3
	default:
		return false
	}
	return cont == want
}
