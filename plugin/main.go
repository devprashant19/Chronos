// Chronos is a Bifrost plugin that stops answers carrying outdated facts from
// reaching users — including answers replayed from Bifrost's semantic cache.
//
// It does four things, in the gateway, on the request path:
//
//  1. Classifies a prompt's volatility before the provider is called, and sets
//     the semantic cache's per-request TTL from it, so a stock price is not
//     cached as long as a chemistry definition.
//  2. Hands each answer to the Chronos verifier, which extracts time-sensitive
//     claims, checks them against the live web with Tavily, and scores them.
//  3. Reports the outcome on the response as x-chronos-* headers.
//  4. Evicts the cache entry behind any cached answer that turns out to be
//     outdated, so the next caller gets a fresh one.
//
// Design constraints, in priority order:
//
//   - Chronos must never break the gateway. Every failure path — verifier down,
//     slow, malformed, budget spent — degrades to passing the response through
//     with verdict "unverifiable". No hook returns an error that fails a request.
//   - Chronos must not slow down the expensive path. Fresh answers are verified
//     asynchronously, so the added latency is a goroutine spawn. Only cache
//     hits, which cost no provider time and are the only answers that can be
//     stale, are verified inline.
//   - The plugin stays thin. All model calls, Tavily calls, scoring and storage
//     live in the verifier service; this file is transport glue and policy.
//
// Build:
//
//	go build -buildmode=plugin -o chronos.so .
//
// The .so must be built with the same Go toolchain, build flags and dependency
// versions as the gateway binary. See deploy/Dockerfile.bifrost, which builds
// the plugin as a package of Bifrost's own transports module to guarantee it.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/maximhq/bifrost/core/schemas"
)

// Version is stamped at build time with -ldflags "-X main.Version=...".
var Version = "dev"

var (
	// corrCounter and processEpoch together make correlation ids unique within
	// a process and distinct across restarts.
	corrCounter  uint64
	processEpoch = time.Now().UnixNano() / int64(time.Millisecond)
)

// Chronos's own context keys. Values set in one hook are read in a later hook
// of the same request.
const (
	ctxKeyClass   schemas.BifrostContextKey = "chronos-class"
	ctxKeyTTL     schemas.BifrostContextKey = "chronos-ttl"
	ctxKeyPrompt  schemas.BifrostContextKey = "chronos-prompt"
	ctxKeyModel   schemas.BifrostContextKey = "chronos-model"
	ctxKeyCacheKy schemas.BifrostContextKey = "chronos-cache-key"
	ctxKeyResult  schemas.BifrostContextKey = "chronos-result"
	ctxKeyCorrID  schemas.BifrostContextKey = "chronos-correlation-id"
)

// Semantic-cache context keys, mirrored by value rather than imported.
//
// Importing plugins/semanticcache would version-lock Chronos to that plugin's
// module for two string constants. The values are part of the cache plugin's
// documented request interface (plugins/semanticcache/main.go), the same
// interface its x-bf-cache-* headers map onto.
const (
	cacheKeyCtx schemas.BifrostContextKey = "semantic_cache-key"
	cacheTTLCtx schemas.BifrostContextKey = "semantic_cache-ttl"
)

// Response headers Chronos writes. These are set in
// HTTPTransportResponseHeadersHook so that streaming clients, which never see
// a response body Chronos could annotate, still get the verdict.
const (
	hdrScore   = "x-chronos-score"
	hdrVerdict = "x-chronos-verdict"
	hdrClaims  = "x-chronos-claims"
	hdrStale   = "x-chronos-stale"
	hdrTTL     = "x-chronos-ttl"
	hdrReport  = "x-chronos-report"
	hdrClass   = "x-chronos-class"
	hdrMode    = "x-chronos-mode"
	hdrLatency = "x-chronos-latency-ms"
)

// Verdicts.
const (
	verdictCurrent      = "current"
	verdictCheck        = "check"
	verdictOutdated     = "outdated"
	verdictUnverifiable = "unverifiable"
	verdictPending      = "pending"
)

// plugin holds process-wide state. Bifrost loads a plugin once per process, so
// this is effectively a singleton; everything in it is read-only after Init
// except the counters, which are atomic.
type pluginState struct {
	cfg      Config
	verifier *VerifierClient
	evictor  *Evictor

	// inflight bounds the number of concurrent background verifications, so a
	// burst of traffic cannot spawn unbounded goroutines against the verifier.
	inflight chan struct{}

	// results carries a verdict from PostLLMHook to the response-headers hook.
	// See state.go for why the request context cannot do this.
	results *resultStore

	wg     sync.WaitGroup
	closed chan struct{}
	once   sync.Once
}

var state *pluginState

// record parks a verdict where the response-headers hook will find it.
//
// It writes to both the context and the request-id store: the context is the
// natural place and costs nothing, and the store is the one that actually
// works across the transport boundary.
func (st *pluginState) record(ctx *schemas.BifrostContext, res result) {
	ctx.SetValue(ctxKeyResult, res)
	st.results.put(correlationID(ctx), res)
}

// correlationID returns the key under which a verdict is parked for this
// request. Chronos's own id is preferred; Bifrost's request id is a fallback
// for code paths that never passed through HTTPTransportPreHook.
func correlationID(ctx *schemas.BifrostContext) string {
	if id, ok := ctx.Value(ctxKeyCorrID).(string); ok && id != "" {
		return id
	}
	if id, ok := ctx.Value(schemas.BifrostContextKeyRequestID).(string); ok {
		return id
	}
	return ""
}

// nextCorrelationID produces a short, process-unique id without pulling in a
// UUID dependency: a plugin shares its dependency graph with the gateway, so
// every import is a version constraint worth avoiding.
func nextCorrelationID() string {
	n := atomic.AddUint64(&corrCounter, 1)
	return strconv.FormatInt(processEpoch, 36) + "-" + strconv.FormatUint(n, 36)
}

// result is what a verification produced, as the headers hook needs it.
type result struct {
	Score     float64
	Verdict   string
	Claims    int
	Stale     int
	TTL       int
	ReportID  string
	Class     string
	LatencyMS int64
}

// ---------------------------------------------------------------------------
// Exported plugin symbols
// ---------------------------------------------------------------------------

// Init is called once when Bifrost loads the plugin.
//
// It never returns an error for a recoverable problem. A gateway that refuses
// to boot because a freshness checker was misconfigured is strictly worse than
// one that boots and logs that Chronos is degraded.
func Init(config any) error {
	cfg, warnings := LoadConfig(config)

	st := &pluginState{
		cfg:      cfg,
		verifier: NewVerifierClient(cfg.VerifierURL, cfg.verifyTimeout()),
		evictor:  NewEvictor(cfg.BifrostAdminURL, cfg.AdminAuthHeader, cfg.AdminAuthValue),
		inflight: make(chan struct{}, 64),
		results:  newResultStore(),
		closed:   make(chan struct{}),
	}
	state = st

	for _, w := range warnings {
		logf("warn", "config: %s", w)
	}
	logf("info", "chronos %s loaded: mode=%s verifier=%s guard_threshold=%d sync_on_cache_hit=%t evict_stale=%t",
		Version, cfg.Mode, cfg.VerifierURL, cfg.GuardThreshold, cfg.syncOnCacheHit(), cfg.evictStale())

	// Probe the verifier in the background purely so the logs say early on
	// whether it is reachable. Boot does not depend on it.
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := st.verifier.Health(ctx); err != nil {
			logf("warn", "verifier not reachable yet (%v); answers will pass through as %q until it is", err, verdictUnverifiable)
			return
		}
		logf("info", "verifier reachable at %s", cfg.VerifierURL)
	}()

	return nil
}

// GetName returns the plugin's system identifier, which must match the `name`
// in the config.json plugins[] entry.
func GetName() string {
	return "chronos"
}

// HTTPTransportPreHook runs once per HTTP request, before the LLM pipeline.
//
// This is the cheapest possible place to decide how long the answer may be
// cached, because the decision depends only on the prompt. It costs a few
// regex scans and no allocation beyond the parsed prompt.
//
// Returning (nil, nil) continues processing; Chronos never short-circuits here.
func HTTPTransportPreHook(ctx *schemas.BifrostContext, req *schemas.HTTPRequest) (*schemas.HTTPResponse, error) {
	st := state
	if st == nil || ctx == nil || req == nil {
		return nil, nil
	}
	if !st.cfg.watchesPath(req.Path) {
		return nil, nil
	}

	model, prompt, _ := PromptFromRequestBody(req.Body)
	class := ClassifyPrompt(prompt)
	ttl := st.cfg.ttlForClass(string(class.Class))

	// Set the per-request cache TTL as a header, which is the documented
	// interface, and again as a context value in PreLLMHook. Both, because the
	// transport reads headers at a point we do not control the ordering of.
	if req.Headers != nil {
		req.Headers["x-bf-cache-ttl"] = formatDuration(ttl)

		// Optionally make the cache engage for clients that do not send a key.
		// Without a cache key the semantic cache ignores the request entirely.
		if st.cfg.AutoCacheKey != "" && req.CaseInsensitiveHeaderLookup("x-bf-cache-key") == "" {
			req.Headers["x-bf-cache-key"] = st.cfg.AutoCacheKey
			ctx.SetValue(ctxKeyCacheKy, st.cfg.AutoCacheKey)
		} else if existing := req.CaseInsensitiveHeaderLookup("x-bf-cache-key"); existing != "" {
			ctx.SetValue(ctxKeyCacheKy, existing)
		}
	}

	// Chronos mints its own correlation id here rather than relying on
	// Bifrost's request id.
	//
	// The reason is empirical: BifrostContextKeyRequestID is present in the
	// LLM-pipeline context that PostLLMHook sees, but NOT in the transport
	// context that HTTPTransportResponseHeadersHook sees. A value set here, in
	// a transport hook, is visible both to PostLLMHook (context values
	// propagate downward) and to the sibling transport hook that commits the
	// headers — so it is the one key that reaches both ends.
	ctx.SetValue(ctxKeyCorrID, nextCorrelationID())

	ctx.SetValue(ctxKeyClass, string(class.Class))
	ctx.SetValue(ctxKeyTTL, ttl)
	ctx.SetValue(ctxKeyPrompt, truncate(prompt, 2000))
	ctx.SetValue(ctxKeyModel, model)

	if st.cfg.Debug {
		ctx.Log(schemas.LogLevelDebug, fmt.Sprintf(
			"chronos: class=%s ttl=%s matched=%v", class.Class, formatDuration(ttl), class.Matched))
	}
	return nil, nil
}

// PreLLMHook sets the semantic cache's per-request TTL as a typed context
// value, which is what the cache plugin actually reads (a time.Duration).
//
// Chronos is configured with placement "pre_builtin", so this runs before the
// built-in cache plugin's PreLLMHook and the value is in place when the cache
// computes an expiry.
func PreLLMHook(ctx *schemas.BifrostContext, req *schemas.BifrostRequest) (*schemas.BifrostRequest, *schemas.LLMPluginShortCircuit, error) {
	st := state
	if st == nil || ctx == nil {
		return req, nil, nil
	}

	if ttl, ok := ctx.Value(ctxKeyTTL).(time.Duration); ok && ttl > 0 {
		ctx.SetValue(cacheTTLCtx, ttl)
	}
	if key, ok := ctx.Value(ctxKeyCacheKy).(string); ok && key != "" {
		if existing, ok := ctx.Value(cacheKeyCtx).(string); !ok || existing == "" {
			ctx.SetValue(cacheKeyCtx, key)
		}
	}
	return req, nil, nil
}

// PostLLMHook is where verification happens.
//
// Chronos is placed "pre_builtin", and post-hooks run in reverse order, so this
// runs *after* the semantic cache has had its say — which means it sees cached
// answers as well as fresh ones. That is the whole point: a cached answer is
// the one that can be out of date.
func PostLLMHook(ctx *schemas.BifrostContext, resp *schemas.BifrostResponse, bifrostErr *schemas.BifrostError) (*schemas.BifrostResponse, *schemas.BifrostError, error) {
	st := state
	if st == nil || ctx == nil {
		return resp, bifrostErr, nil
	}

	// Never interfere with an already-failed request.
	if bifrostErr != nil || resp == nil {
		return resp, bifrostErr, nil
	}

	answer, model := AnswerFromResponse(resp)
	if strings.TrimSpace(answer) == "" {
		// Streaming, or a non-chat response: nothing complete to verify here.
		return resp, bifrostErr, nil
	}

	prompt, _ := ctx.Value(ctxKeyPrompt).(string)
	class, _ := ctx.Value(ctxKeyClass).(string)
	cacheKey, _ := ctx.Value(ctxKeyCacheKy).(string)
	requestID, _ := ctx.Value(schemas.BifrostContextKeyRequestID).(string)
	virtualKey, _ := ctx.Value(schemas.BifrostContextKeyVirtualKey).(string)

	if model == "" {
		model, _ = ctx.Value(ctxKeyModel).(string)
	}

	vreq := VerifyRequest{
		RequestID:   requestID,
		Answer:      truncate(answer, st.cfg.MaxAnswerChars),
		Prompt:      prompt,
		Model:       model,
		VirtualKey:  virtualKey,
		CacheKey:    cacheKey,
		PromptClass: class,
		Mode:        string(st.cfg.Mode),
		Threshold:   st.cfg.GuardThreshold,
	}

	// Read the cache lookup outcome straight off the context. core exposes this
	// so a plugin does not have to dig through extra_fields, and it is populated
	// for streams too.
	if meta, ok := schemas.CacheLookupFromContext(ctx); ok && meta != nil {
		vreq.CacheHit = meta.CacheHit
		if meta.CacheID != nil {
			vreq.CacheID = *meta.CacheID
		}
		if meta.HitType != nil {
			vreq.HitType = *meta.HitType
		}
		if meta.Similarity != nil {
			vreq.Similarity = *meta.Similarity
		}
	}

	inline := st.cfg.Mode == ModeGuard || (vreq.CacheHit && st.cfg.syncOnCacheHit())

	if !inline {
		st.verifyInBackground(vreq)
		st.record(ctx, result{
			Verdict: verdictPending,
			Class:   class,
			TTL:     ttlSeconds(ctx),
		})
		return resp, bifrostErr, nil
	}

	// --- inline verification, strictly bounded ---
	vreq.Regenerate = st.cfg.Mode == ModeGuard
	vctx, cancel := context.WithTimeout(context.Background(), st.cfg.verifyTimeout())
	defer cancel()

	started := time.Now()
	vres, err := st.verifier.Verify(vctx, vreq)
	latency := time.Since(started).Milliseconds()

	if err != nil || vres == nil {
		// Degrade, loudly in the log and quietly on the wire.
		ctx.Log(schemas.LogLevelWarn, fmt.Sprintf("chronos: verification unavailable, passing through: %v", err))
		st.record(ctx, result{
			Verdict:   verdictUnverifiable,
			Class:     class,
			LatencyMS: latency,
			TTL:       ttlSeconds(ctx),
		})
		return resp, bifrostErr, nil
	}

	res := result{
		Score:     vres.Score,
		Verdict:   normaliseVerdict(vres.Verdict),
		Claims:    vres.ClaimCount,
		Stale:     vres.StaleCount,
		TTL:       vres.TTLSeconds,
		ReportID:  vres.ReportID,
		Class:     class,
		LatencyMS: latency,
	}
	st.record(ctx, res)

	// A cached answer with an outdated claim is exactly the failure Chronos
	// exists to prevent: evict it so the next caller does not get it too.
	if vreq.CacheHit && vreq.CacheID != "" && res.Verdict == verdictOutdated && st.cfg.evictStale() {
		st.evict(ctx, vreq.CacheID, vreq.CacheKey, res)
	}

	// Guard mode may substitute a corrected answer, or block.
	if st.cfg.Mode == ModeGuard && res.Score < float64(st.cfg.GuardThreshold) && res.Verdict != verdictUnverifiable {
		if vres.CorrectedAnswer != "" && ReplaceAnswerInResponse(resp, vres.CorrectedAnswer) {
			ctx.Log(schemas.LogLevelInfo, fmt.Sprintf(
				"chronos: guard replaced an answer scoring %.0f with a regenerated one (report %s)", res.Score, res.ReportID))
			return resp, nil, nil
		}
		if vres.Blocked {
			ctx.Log(schemas.LogLevelWarn, fmt.Sprintf(
				"chronos: guard blocked an answer scoring %.0f (report %s)", res.Score, res.ReportID))
			return nil, blockedError(res), nil
		}
	}

	return resp, bifrostErr, nil
}

// HTTPTransportResponseHeadersHook writes the x-chronos-* headers once, just
// before the transport commits them. Doing it here rather than in
// HTTPTransportPostHook is what makes the verdict visible to streaming
// clients, whose headers are sent before the first chunk.
func HTTPTransportResponseHeadersHook(ctx *schemas.BifrostContext, req *schemas.HTTPRequest, resp *schemas.HTTPResponseMetadata) error {
	st := state
	if st == nil || ctx == nil || resp == nil {
		return nil
	}
	// Observe mode is deliberately silent on the wire.
	if st.cfg.Mode == ModeObserve {
		return nil
	}
	if req != nil && !st.cfg.watchesPath(req.Path) {
		return nil
	}

	// The store is the reliable path across the transport boundary; the
	// context is checked too, in case a future Bifrost propagates upward.
	var res result
	var ok bool
	corrID := correlationID(ctx)
	if corrID != "" {
		res, ok = st.results.take(corrID)
	}
	if !ok {
		res, ok = ctx.Value(ctxKeyResult).(result)
	}
	if !ok {
		// Nothing ran: not an inference route, or an error response. Say
		// nothing rather than guess.
		return nil
	}

	resp.SetHeader(hdrMode, string(st.cfg.Mode))
	resp.SetHeader(hdrVerdict, res.Verdict)

	if res.Class != "" {
		resp.SetHeader(hdrClass, res.Class)
	}
	if res.Verdict != verdictPending && res.Verdict != verdictUnverifiable {
		resp.SetHeader(hdrScore, strconv.Itoa(int(res.Score+0.5)))
		resp.SetHeader(hdrClaims, strconv.Itoa(res.Claims))
		resp.SetHeader(hdrStale, strconv.Itoa(res.Stale))
	}
	if res.TTL > 0 {
		resp.SetHeader(hdrTTL, strconv.Itoa(res.TTL))
	} else if ttl := ttlSeconds(ctx); ttl > 0 {
		resp.SetHeader(hdrTTL, strconv.Itoa(ttl))
	}
	if res.ReportID != "" {
		resp.SetHeader(hdrReport, st.cfg.VerifierURL+"/v1/reports/"+res.ReportID)
	}
	if res.LatencyMS > 0 {
		resp.SetHeader(hdrLatency, strconv.FormatInt(res.LatencyMS, 10))
	}
	return nil
}

// Cleanup waits briefly for background verifications to drain so that a
// shutdown does not lose reports that are already paid for.
func Cleanup() error {
	st := state
	if st == nil {
		return nil
	}
	st.once.Do(func() { close(st.closed) })

	done := make(chan struct{})
	go func() {
		st.wg.Wait()
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		logf("warn", "cleanup: gave up waiting for in-flight verifications")
	}
	logf("info", "chronos unloaded")
	return nil
}

// ---------------------------------------------------------------------------
// internals
// ---------------------------------------------------------------------------

// verifyInBackground hands an answer to the verifier without blocking the
// response.
//
// The goroutine deliberately does NOT capture the BifrostContext: that object
// belongs to the request and may be pooled and reused the moment the response
// is written. Everything needed is already copied into vreq.
func (st *pluginState) verifyInBackground(vreq VerifyRequest) {
	select {
	case st.inflight <- struct{}{}:
	default:
		// At capacity. Dropping a background report is the correct trade: the
		// alternative is unbounded goroutines, or slowing the gateway down.
		logf("warn", "verification queue full, skipping report for request %s", vreq.RequestID)
		return
	}

	st.wg.Add(1)
	go func() {
		defer st.wg.Done()
		defer func() { <-st.inflight }()
		defer func() {
			if r := recover(); r != nil {
				logf("error", "recovered from panic in background verification: %v", r)
			}
		}()

		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()

		vres, err := st.verifier.VerifyAsync(ctx, vreq)
		if err != nil {
			logf("warn", "background verification failed for request %s: %v", vreq.RequestID, err)
			return
		}
		if vres == nil {
			return
		}

		// The verifier reports asynchronously-discovered staleness back to us
		// only insofar as eviction is concerned; everything else it owns.
		if vreq.CacheHit && vreq.CacheID != "" && normaliseVerdict(vres.Verdict) == verdictOutdated && st.cfg.evictStale() {
			ectx, ecancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer ecancel()
			if err := st.evictor.ClearCacheID(ectx, vreq.CacheID); err != nil {
				logf("warn", "stale cache entry %s could not be evicted: %v", vreq.CacheID, err)
				return
			}
			logf("info", "stale_hit_prevented cache_id=%s cache_key=%s score=%.0f report=%s",
				vreq.CacheID, vreq.CacheKey, vres.Score, vres.ReportID)
		}
	}()
}

// evict clears one cache entry and logs a stale_hit_prevented event.
func (st *pluginState) evict(ctx *schemas.BifrostContext, cacheID, cacheKey string, res result) {
	ectx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	if err := st.evictor.ClearCacheID(ectx, cacheID); err != nil {
		ctx.Log(schemas.LogLevelWarn, fmt.Sprintf("chronos: could not evict stale cache entry %s: %v", cacheID, err))
		logf("warn", "could not evict stale cache entry %s: %v", cacheID, err)
		return
	}

	// Logged twice on purpose. ctx.Log is the idiomatic path for a Bifrost
	// plugin, but its output did not reach the gateway's log stream in
	// testing, and stale_hit_prevented is the one event an operator must be
	// able to see. The stderr line is the one that is actually observable.
	msg := fmt.Sprintf(
		"stale_hit_prevented cache_id=%s cache_key=%s score=%.0f stale_claims=%d report=%s",
		cacheID, cacheKey, res.Score, res.Stale, res.ReportID)
	ctx.Log(schemas.LogLevelInfo, "chronos: "+msg)
	logf("info", "%s", msg)
}

// blockedError builds the error guard mode returns instead of a stale answer.
//
// AllowFallbacks is false: falling back to another provider would just produce
// another unverified answer, which is not the problem we are solving.
func blockedError(res result) *schemas.BifrostError {
	allow := false
	payload, _ := json.Marshal(map[string]any{
		"chronos": map[string]any{
			"verdict":      res.Verdict,
			"score":        res.Score,
			"stale_claims": res.Stale,
			"report_id":    res.ReportID,
		},
	})
	msg := fmt.Sprintf(
		"Chronos blocked this answer: it scored %.0f/100 for freshness (%d claim(s) look outdated). Details: %s",
		res.Score, res.Stale, string(payload))

	return &schemas.BifrostError{
		IsBifrostError: true,
		Type:           strPtr("chronos_stale_answer"),
		StatusCode:     intPtr(409),
		AllowFallbacks: &allow,
		Error: &schemas.ErrorField{
			Type:    strPtr("chronos_stale_answer"),
			Code:    strPtr("stale_answer_blocked"),
			Message: msg,
		},
	}
}

// normaliseVerdict keeps unknown verdicts from leaking into headers.
func normaliseVerdict(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case verdictCurrent:
		return verdictCurrent
	case verdictCheck:
		return verdictCheck
	case verdictOutdated:
		return verdictOutdated
	case verdictPending:
		return verdictPending
	default:
		return verdictUnverifiable
	}
}

// ttlSeconds reads the TTL the pre-hook chose, in seconds.
func ttlSeconds(ctx *schemas.BifrostContext) int {
	if ttl, ok := ctx.Value(ctxKeyTTL).(time.Duration); ok && ttl > 0 {
		return int(ttl.Seconds())
	}
	return 0
}

// formatDuration renders a duration the way x-bf-cache-ttl wants it.
func formatDuration(d time.Duration) string {
	if d <= 0 {
		return "0s"
	}
	if d%time.Hour == 0 {
		return strconv.Itoa(int(d/time.Hour)) + "h"
	}
	if d%time.Minute == 0 {
		return strconv.Itoa(int(d/time.Minute)) + "m"
	}
	return strconv.Itoa(int(d.Seconds())) + "s"
}

func intPtr(i int) *int       { return &i }
func strPtr(s string) *string { return &s }

// logf writes a structured line to stderr.
//
// Init has no BifrostContext to log through, and background goroutines outlive
// the request whose context they came from, so Chronos keeps its own logger for
// those paths and uses ctx.Log everywhere a request is in scope.
func logf(level, format string, args ...any) {
	fmt.Fprintf(os.Stderr, "{\"level\":%q,\"plugin\":\"chronos\",\"msg\":%q}\n",
		level, fmt.Sprintf(format, args...))
}
