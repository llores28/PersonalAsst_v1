# ADR-2026-04-22: Multimodal Capabilities & TTS Voice Replies

## Status
Accepted

## Context

PersonalAsst is a Telegram-first assistant. Up to this point it handled only text. Three capabilities were added in one session:

1. **Image generation** — users want to create images from text prompts.
2. **Photo analysis** — users want to upload a photo and ask Atlas about it.
3. **TTS voice replies** — users want to receive spoken audio responses, either by sending a voice message or explicitly requesting audio.

## Decisions

### 1. OpenRouter as image provider

**Decision:** Use OpenRouter (`google/gemini-2.5-flash-image`) rather than OpenAI DALL-E for image generation.

**Rationale:**
- OpenAI's image generation API requires separate endpoint and billing; already had OpenRouter integration in flight.
- Gemini Flash Image supports `modalities: ["image", "text"]` which returns base64 image data in a single chat completion response — compatible with existing JSON handling.
- Cost-capped independently via `OPENROUTER_DAILY_COST_CAP_USD`.

**Tradeoffs:**
- Adds a second LLM provider dependency (`OPENROUTER_API_KEY`).
- Feature-flagged via `OPENROUTER_IMAGE_ENABLED` — can be disabled with no code change.

### 2. Deterministic direct routing for image requests

**Decision:** Image generation and photo analysis both use synchronous fast-path functions (`_maybe_handle_direct_image_generation`, `_maybe_handle_direct_image_analysis`) that short-circuit before the LLM agent runs.

**Rationale:**
- The LLM would frequently fall back to describing prompts rather than calling the tool, especially for photo analysis where the default caption `"Please describe this image"` doesn't keyword-match the skill router.
- Direct paths are predictable, testable, and avoid per-turn LLM cost for deterministic requests.

**Tradeoffs:**
- Less flexible than letting the LLM decide — edge cases (e.g., "analyze this image AND save the description to a file") won't be handled in the fast path.
- Mitigation: fast path returns `None` if conditions aren't met, so LLM still handles edge cases.

### 3. Session-bound image state (Redis)

**Decision:** Uploaded photos are stored as base64 JSON in Redis session (`latest_uploaded_image`), consumed once, then deleted.

**Rationale:**
- No new DB table needed — Redis session already used for ephemeral turn state.
- Single-use semantics (deleted after analysis) prevent stale images bleeding into future turns.
- `get_session_field` / `delete_session_field` already existed from persona interview work.

**Tradeoffs:**
- Only one uploaded image held at a time per user. Multi-image analysis in one turn is not supported.
- Large images are stored in Redis; base64 inflates size ~33%. Telegram's max photo size is ~20MB, but the highest-res version downloaded is the compressed Telegram version (typically <2MB).

### 4. OpenAI TTS (`tts-1`) for voice replies

**Decision:** Use OpenAI's `tts-1` model for speech synthesis rather than a self-hosted alternative.

**Rationale:**
- `tts-1` is already accessible via the existing `openai_api_key` — no new provider or API key.
- Self-hosted TTS (e.g., Coqui) requires GPU or degrades quality significantly; doesn't meet HC-8 (non-technical user clarity).
- `tts-1` latency (~1-2s for typical response length) is acceptable for Telegram voice messages.

**Tradeoffs:**
- Adds OpenAI API cost per audio reply (billed per character).
- TTS is on-demand only — not triggered on every response — so typical sessions incur no TTS cost.

### 5. Per-user voice preference in `user_settings`

**Decision:** Persist TTS voice selection in the existing `user_settings` table (new `tts_voice` column, migration `009`) rather than Redis.

**Rationale:**
- Voice preference is long-lived and survives container restarts — Redis session is ephemeral.
- `user_settings` already has the correct FK to `users.id` and is the established location for per-user config (budget caps).

**Tradeoffs:**
- Requires an Alembic migration.
- `user_settings` FK resolves via `users.id` (internal PK), not Telegram ID — same pattern fixed in cost tracker bug.

### 6. Cost tracker int32 overflow fix

**Decision:** `track_cost()` now resolves Telegram ID → internal `users.id` before writing to `daily_costs`, falling back to Redis-only tracking if no DB user row exists.

**Rationale:**
- `daily_costs.user_id` is a FK to `users.id` (INTEGER, max ~2.1B). Telegram assigns IDs up to 10 digits; IDs > 2.1B overflow int32.
- Redis-based cost tracking (per-provider buckets) already uses string keys so was unaffected.
- Redis tracking preserved as primary cost signal; PostgreSQL `daily_costs` updated only when a matching `users.id` exists.

## Files Changed

| File | Change |
|------|--------|
| `src/integrations/openrouter.py` | `generate_image()`, `analyze_image()`, `_infer_image_config()`, `_track_openrouter_usage()` |
| `src/agents/orchestrator.py` | `_maybe_handle_direct_image_generation()`, `_maybe_handle_direct_image_analysis()` fast paths |
| `src/bot/handlers.py` | Photo upload handler, voice auto-flag, audio cue detection, `/voice` command |
| `src/bot/voice.py` | `synthesize_speech()`, `get_user_tts_voice()`, `set_user_tts_voice()`, temp file fix |
| `src/bot/handler_utils.py` | `_maybe_send_tts_reply()`, `_strip_markdown()`, `_clean_image_caption()`, typing actions |
| `src/models/cost_tracker.py` | `_resolve_db_user_id()`, int32 overflow fix |
| `src/db/models.py` | `UserSettings.tts_voice` column |
| `src/db/migrations/versions/009_add_tts_voice.py` | Migration for `tts_voice` column |
| `src/config/openrouter_capabilities.yaml` | Model preferences and timeouts |
| `src/main.py` | `/voice` registered in Telegram command menu |
| `docker-compose.yml` | OpenRouter env vars passed to `assistant` container |
