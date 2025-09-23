# Codex Parity Improvement Plan

Use the TODOs below as execution checkpoints. Follow phases sequentially unless dependencies explicitly permit parallel work. Prioritize foundational plumbing before feature expansion. Keep Claude functionality stable while upgrading Codex support.

## Phase 1 · Observability & Request Lifecycle
- [ ] TODO: Wrap `ProxyService.handle_codex_request` (`ccproxy/services/proxy_service.py`) in the same `request_context` / `timed_operation` blocks used in `handle_request`, capturing method/path/model/session metadata.
  - Notes: Ensure the existing request middleware (`RequestIDMiddleware`) can pass through the reused context.
- [ ] TODO: Ensure non-streaming Codex responses update context metadata (`status_code`, token/cost fields) before returning.
- [ ] TODO: Populate Prometheus metrics (request count, response time, token/cost counters) from Codex paths using the same helpers as Claude.
  - Verification: Prometheus `service_type="codex"` samples appear when hitting `/codex/responses`.
- [ ] TODO: Replace ad-hoc streaming responses in `handle_codex_request` with `StreamingResponseWithLogging` so Codex streams land in shared logging/metrics.
  - Dependency: Requires the request context refactor above.
- [ ] TODO: Add targeted unit coverage or integration “spy” to confirm logging/metrics emit for Codex traffic (mirroring Claude tests if present).

## Phase 2 · API Feature Parity (Adapters, Models & Routes)
- [ ] TODO: Expand `ResponseAdapter.chat_to_response_request` to faithfully translate tools, `response_format`, developer/system roles, reasoning flags, and image blocks to Response API payloads.
  - Reference: parity with `OpenAIAdapter` (`ccproxy/adapters/openai/adapter.py`).
- [ ] TODO: Update streaming conversion (`stream_response_to_chat`) so Response API tool calls and reasoning deltas round-trip into OpenAI Chat Completions chunks (tool_calls, reasoning content, usage updates).
- [ ] TODO: Normalize non-streaming conversions to map tool outputs and usage data into OpenAI schema.
- [ ] TODO: Implement tool/function-calling support for `/codex/chat/completions` by bridging tool calls to the Response API (or clearly blocking with actionable error if upstream forbids it). Ensure parity with `/codex/responses` tool handling.
- [ ] TODO: Audit OpenAI request parameters (temperature, top_p, penalties, logit_bias, etc.) and determine which can be propagated to Response API; implement translation or explicit validation errors in `ResponseAdapter`.
- [ ] TODO: Wire Codex requests into dynamic model/token limit fetching (same services used by Claude) so max_tokens/context_window introspection works for Response API models.
- [ ] TODO: Expand model support by validating additional Response API models and updating model discovery/mapping so Codex mirrors the dynamic model list surfaced for Claude (via `model_info_service`). Add regression guardrails for unsupported models.
- [ ] TODO: Revisit instruction injection logic in `CodexRequestTransformer` so optional modes (see Phase 3) can be respected without breaking Response API requirements, enabling custom system prompts where allowed.
- [ ] TODO: Add or document a `{session_id}/chat/completions` Codex route; if infeasible, add explicit guard + README note. Ensure session headers remain usable for persistent conversations.

## Phase 3 · Configuration, Detection & CLI Parity
- [ ] TODO: Extend `CodexSettings` (`ccproxy/config/codex.py`) with toggles comparable to Claude (`system_prompt_injection_mode`, verbose logging switch, header overrides) so users can opt into custom system prompts when supported.
  - Dependency: Instruction mode support in Phase 2 adapter work.
- [ ] TODO: Bind Codex dynamic-model/token settings to configuration flags/environment variables, mirroring Claude’s dynamic info toggles.
- [ ] TODO: Surface new toggles via CLI (`ccproxy/cli/commands/config` & `auth`) so users can enable/disable Codex features without editing config files manually.
- [ ] TODO: Add CLI command to inspect Codex detection cache (mirroring Claude detection helpers) and ensure detection failure fallbacks log parity warnings.
- [ ] TODO: Confirm Docker credential path helpers treat `.codex/auth.json` consistently with existing Claude flows.

## Phase 4 · Testing & QA
- [ ] TODO: Augment `tests/unit/services/test_codex_proxy.py` with scenarios covering metrics emission, request-context reuse, and streaming logging.
- [ ] TODO: Add adapter-specific tests verifying tool/reasoning/response_format conversions (request + streaming + final response) for both `/codex/responses` and `/codex/chat/completions`.
- [ ] TODO: Add tests covering propagation of newly supported OpenAI parameters, including negative tests for disallowed knobs.
- [ ] TODO: Add tests ensuring dynamic model/token discovery works for Codex (mocking `model_info_service`).
- [ ] TODO: Build regression fixture for instruction mode variations (default, minimal, disabled) to ensure injection changes stay compatible with Response API while allowing custom system prompts when configured.
- [ ] TODO: If available, add contract/integration tests against mocked Response API for tool-call and reasoning payloads.

## Phase 5 · Documentation & Rollout
- [ ] TODO: Update README Codex sections to reflect new capabilities, limitations, and configuration options. Remove or narrow limitations around tools, parameters, models, system prompts, reasoning output, and session management once mitigated; retain the ChatGPT Plus requirement warning.
- [ ] TODO: Refresh docs under `docs/` (examples, user guide) so CLI instructions, env vars, and session guidance match new toggles, including dynamic model/token info.
- [ ] TODO: Add CHANGELOG entry summarizing Codex parity improvements and migration guidance.
- [ ] TODO: Outline rollout checklist (enable flags, monitor metrics, fallback plan) before shipping.

## Final Verification
- [ ] TODO: Run end-to-end smoke tests (API + CLI) comparing Claude vs Codex paths for identical prompts, confirming logs/metrics/tool-calls, parameter handling, session behavior, system prompt handling, and dynamic model lookups match expectations.
- [ ] TODO: Validate that Codex parity work does not regress Claude behavior (targeted regression tests + manual spot checks).
