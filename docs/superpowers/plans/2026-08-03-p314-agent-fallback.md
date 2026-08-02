# P3.14 Real Agent Compute Fallback Implementation Plan

**Goal:** Make every agentd question and daily-report inference use one verified Radeon-first router with an honest Local Metal fallback, fail-closed citation validation, and observable backend metadata.

**Architecture:** Add a small synchronous compute router owned by agentd. It classifies transport/status/shape failures, keeps a per-logical-role Radeon circuit breaker, validates the actual OpenAI-compatible product response, and returns a structured route result. The ordinary tool loop and the daily-report workflow call this router instead of posting directly to a configured gateway. Citation ids are accumulated from successful tool results and validated before the response crosses the API boundary.

**Constraints:** Keep the five logical model roles unchanged. A Local Metal `brain` request physically uses `perceive`, and fast-track calls set `chat_template_kwargs.enable_thinking=false`. Do not retry authentication, caller-validation, or privacy-policy failures. Never expose endpoint credentials or raw upstream bodies in errors or health/status output.

---

## Task 1: Specify the router contract with failing tests

**Files:**
- Create: `services/agentd/tests/test_router.py`
- Modify: `services/agentd/tests/test_health.py`

1. Add tests for remote success and exact route metadata.
2. Add tests for remote timeout then local success, including local physical model `perceive`.
3. Add tests for dual failure with sanitized reasons.
4. Add tests for retryable 429/502-504, missing model, invalid JSON, and invalid response shape.
5. Add tests proving 400/401/403 and policy rejection do not cross backends.
6. Add deterministic circuit-open, cooldown probe, and recovery tests with an injected clock.
7. Run the new tests and record the expected RED result before implementation.

## Task 2: Implement shared configuration and compute router

**Files:**
- Modify: `services/agentd/src/agentd/config.py`
- Create: `services/agentd/src/agentd/router.py`

1. Add `RADEON_GATEWAY_URL` with backward-compatible `GATEWAY_URL`, plus `LOCAL_GATEWAY_URL`.
2. Define stable route result and sanitized failure types.
3. Implement OpenAI-compatible chat routing and response-shape validation.
4. Record backend, physical/logical model, degraded flag, stable reason, and latency.
5. Implement per-role remote circuit breaker with bounded failures and cooldown recovery.
6. Force `enable_thinking=false` for fast-track calls.
7. Run router and health tests to GREEN.

## Task 3: Integrate questions and citation allowlisting

**Files:**
- Modify: `services/agentd/src/agentd/server.py`
- Create: `services/agentd/tests/test_chat_routing.py`

1. Replace direct gateway calls with the router for every tool-loop round.
2. Accumulate only event ids and citation labels returned by this request's tools.
3. Parse every final `[event#id HH:MM app]` marker and reject ids/labels outside that allowlist.
4. On first invalid product output, request one correction through the same router.
5. On the second invalid output, return a safe evidence-insufficient answer with no invented citation.
6. Add top-level `dejaview` route and citation metadata to successful responses.
7. Return 503 with two sanitized stable failure reasons when both compute paths fail.
8. Run question, tool, and API-contract tests to GREEN.

## Task 4: Move daily reports onto the same router

**Files:**
- Modify: `services/agentd/scripts/demo_daily_report.py`
- Modify: `services/agentd/scripts/demo_stage.py`
- Modify: `services/agentd/scripts/test_demo_p34.py`

1. Replace the report script's direct gateway helper with the shared router.
2. Ensure planner, writer, correction, and reviewer calls carry truthful route metadata.
3. Remove demo-stage backend preselection as the authority; consume the completed report's actual route result.
4. Preserve report artifact validation and citation gates.
5. Test remote success, fallback success, invalid report fallback/correction, dual failure, and displayed backend consistency.

## Task 5: Verify, document, accept, and publish

**Files:**
- Modify: `README.md`
- Modify: `STATUS.md`
- Modify: `TASKBOARD.json`
- Modify: `docs/verification-log.md`

1. Run focused agentd and P3.4 regression tests.
2. Run the complete first-party test suite.
3. Run synthetic live fault injection: Radeon success, broken remote with Local Metal success, dual failure, and circuit recovery; save only sanitized evidence.
4. Confirm health/status and API metadata match the backend that actually returned the product result.
5. Document environment variables and operational behavior without secrets.
6. Append a `[VERIFY] P3.14` entry, move `doing` to `accept`, update counts/next priority, commit with the required author, inspect the commit message for forbidden trailers, and push.

