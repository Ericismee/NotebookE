# ADR-008: VieNeu via a separate speech server

Date: 2026-10-17

Status: Accepted

## Context

Vietnamese podcasts and videos need more speaker choices than the current
Edge presets. VieNeu v3 Turbo exposes an OpenAI-compatible speech endpoint,
but its runtime and model weights would enlarge the NotebookE worker image.
Its WAV output also differs from the MP3 files consumed by current media jobs.

## Decision

Reuse OpenAI-compatible credentials and model records. Route compatible TTS
models whose names start with `vieneu` through a dedicated Esperanto adapter.
Host VieNeu separately; request WAV and convert to MP3 in a background thread.
Validate and pin the configured endpoint using the existing URL protection.

Videos accept a model record ID, provisioned by the existing model manager.
The creation UI initially prefers VieNeu if it is the configured TTS default.
Podcasts continue using their explicit profile/per-speaker models. Language
presets must preserve voices belonging to other providers or speaker overrides.

## Consequences

Users must run and configure a speech server; this change does not install
weights automatically. API and workers need network access to that server.
Preset suggestions match SDK 3.8.3, while free-text voice IDs permit custom voices.
Jobs fail visibly on speech errors rather than changing providers automatically.
Resolved credentials remain outside persisted episode snapshots.

## Language selection and previews

The authenticated API filters voices by language and synthesizes a fixed, short
preview sentence. Vietnamese uses VieNeu presets; English uses the registered
Edge TTS model's native English presets. Native English identities are not
inferred from the bilingual capability of the Vietnamese model. The response
includes the effective model ID, which is also passed to media generation.
Episode-specific podcast language and voice overrides are copied into transient
profiles and persisted only in the episode snapshot. Retry preserves those
choices. Preview playback is canceled and its object URL released when the
voice or language changes.

Prepared samples are stored as MP3 files under the data folder. The key includes
the effective model, voice, language, model revision and sample sentence. Atomic
replacement prevents partial files; per-sample locks avoid repeated synthesis
inside one API process. Authenticated GET requests serve existing audio, and
the browser preloads ready samples into a bounded cache of audio bytes. No model
call is needed when the user plays a prepared sample.
