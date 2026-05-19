# 0006 — OpenAI-compatible API as the first-class interface

- **Status:** accepted
- **Date:** 2026-05-02
- **Deciders:** single-author
- **Tags:** api, ecosystem, integration

## Context

LocallyAI is the inference layer. Around it, firms have:

- Existing tools they want to plug in (curl scripts, Postman collections, Raycast extensions, internal Python scripts a paralegal wrote two years ago).
- DMS integrations on the roadmap (iManage, NetDocuments, Worldox) that already speak OpenAI's API shape.
- Lawyers experimenting with third-party agents (LangChain, Continue.dev, Cline, JetBrains AI Assistant) all of which expect an OpenAI-shaped endpoint.
- Domain-specific features that don't exist in OpenAI's catalogue (conflict checks, document comparison, citation verification).

The default move would be to invent a bespoke API. Cleaner schema, fewer legacy quirks, room to express the platform's actual primitives (retrieval + ACLs + audit). The cost: every integration that "just worked" with OpenAI needs a custom adapter, and the firm's existing scripts have to be rewritten.

The question: bespoke API or compatibility-layer first?

## Decision

**OpenAI-compatible at the surface, bespoke endpoints layered on top.**

The platform exposes the OpenAI v1 routes the ecosystem expects:

- `POST /v1/chat/completions` — chat with streaming, identical request/response shape to OpenAI
- `GET /v1/models` — model list in OpenAI format
- `POST /v1/embeddings` — embeddings endpoint (when `EMBED_BACKEND=local`)

Domain-specific endpoints live alongside:

- `POST /v1/conflicts/check` — conflict-of-interest engine
- `POST /v1/documents/compare` — section-aligned diff with LLM commentary
- `POST /v1/citations/verify` — case/statute citation verification
- `GET /admin/installers`, `POST /admin/installers/{refresh,rebuild}` — installer mirror
- `GET /admin/compliance/snapshot` — HMAC-signed monthly compliance bundle

Auth is HTTP Bearer (matches OpenAI). Streaming uses SSE (matches OpenAI). Token usage is reported as `usage.sources_retrieved` — a superset of OpenAI's `usage` that any non-checking caller ignores cleanly.

See `api.py`.

## Alternatives considered

- **Bespoke REST API** modelled on the platform's actual primitives (retrieval, ACL, audit). Rejected because every consumer becomes custom work. The conflict-check / comparison / citation features are LocallyAI-specific anyway and can be bespoke without breaking the OpenAI compatibility on the chat path.
- **GraphQL.** Rejected because (a) no ecosystem tool the firm uses speaks GraphQL out of the box and (b) the underlying operations (retrieve top-K, then generate) are naturally request/response — there's no graph traversal that GraphQL would simplify.
- **gRPC** (e.g. for the inference backend). Rejected because (a) browser clients can't natively speak gRPC without a proxy and the Manager + Worker UIs run in WKWebView, and (b) the firm's IT person debugging with `curl` is a hard requirement; gRPC is hostile to that workflow.
- **MCP-only interface** (Model Context Protocol). Considered. Rejected as the *primary* interface — MCP is great for agent-side tool-calling but not for the chat-as-a-service shape lawyers expect from a chat UI. Worth adding as an additional surface in v2.
- **AI SDK Vercel adapter** as the protocol. Rejected because it's narrower than OpenAI's surface — many consumers expect `messages: [{role, content}]` request shape, which AI SDK doesn't directly serve. OpenAI compatibility subsumes it.

## Consequences

### Positive

- **Zero-friction onboarding for the firm's existing tooling.** Any script pointing at `https://api.openai.com/v1` can be redirected to `https://office-mac.local:8000/v1` with one env var change. The DMS integration design doc lists this as the single biggest reason firms are willing to switch.
- **Ecosystem leverage.** LangChain, LlamaIndex, Continue.dev, Cline, Cursor, Aider, Raycast AI — all of which accept a custom OpenAI base URL — work out of the box.
- **Bespoke domain endpoints don't bleed into the compatibility surface.** `/v1/conflicts/check` etc. sit in the same `/v1/` prefix without polluting `/chat/completions` — consumers who want only the OpenAI shape stay clean.
- **Streaming via SSE matches OpenAI's behaviour** including the `data: [DONE]` sentinel, so streaming clients work without modification.

### Negative

- **Some OpenAI request fields don't map to local inference** (logit_bias, logprobs, n>1). They're accepted and silently ignored when the backend can't honour them; documented in `docs/sop/incidents-service.md`.
- **OpenAI's API shape carries dead weight** — `function_call` vs `tools` (deprecated vs current), `system_fingerprint`, response_format with JSON-mode quirks. The platform implements the modern surface and warns on deprecated fields rather than supporting both forms.
- **`usage` is a superset** (`usage.sources_retrieved` is non-OpenAI). Strict OpenAI clients that validate response schemas can complain. Mitigation: extra fields are additive; the standard `usage.prompt_tokens` / `completion_tokens` are still present and correct.
- **OpenAI compatibility is a moving target.** When OpenAI ships a new API version with breaking changes, LocallyAI has to decide whether to chase it. The plan is to track the stable `v1` surface and ignore preview / beta endpoints.

### Neutral

- **The `model` field** in `/v1/chat/completions` requests is informational only — the actual model is selected by `LOCALLYAI_BACKEND` + `MLX_MODEL` / `OLLAMA_MODEL`. Clients that send `model: "gpt-4"` get a response from whatever the firm has configured locally. Documented in the API readme; some operators find this surprising the first time.

## References

- `api.py` — `/v1/chat/completions` + auxiliary endpoints
- `mlx_inference.py:generate` — MLX backend; matches OpenAI streaming chunk shape
- `llm_models.py` — `/v1/models` response builder
- `docs/sop/dms-integration.md` — why DMS connectors specifically target the OpenAI surface
- ADR-0008 (multi-backend inference — the OpenAI surface abstracts over the backend choice)
