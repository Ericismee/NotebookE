"""Language-aware voice discovery and short, authenticated speech previews.

English presets use Edge TTS 7.3.1+ for the current speech service protocol.
"""

import asyncio

from esperanto import TextToSpeechModel

from notebooke.ai.models import Model, model_manager
from notebooke.ai.vieneu_tts_provider import VieNeuTTSProvider
from notebooke.ai.voice_samples import (
    cached_sample,
    prepare_sample,
    sample_key,
    sample_metadata,
)
from notebooke.exceptions import (
    ConfigurationError,
    ExternalServiceError,
    InvalidInputError,
)


def speech_language(language: str | None) -> str | None:
    if not language:
        return None
    value = language.strip().lower()
    if value.startswith("en"):
        return "en"
    if value.startswith("vi") or value in {"tiếng việt", "tieng viet"}:
        return "vi"
    raise InvalidInputError("Speech language must be English or Vietnamese")


async def resolve_speech_model(model_id: str, language: str | None = None):
    record = await Model.get(model_id)
    if record.type != "text_to_speech":
        raise InvalidInputError("Voice selection requires a text-to-speech model")
    if speech_language(language) == "en" and record.name.lower().startswith("vieneu"):
        # VieNeu presets are Vietnamese voices. Use native English voices for
        # an English episode instead of relabelling a Vietnamese speaker.
        models = await Model.get_models_by_type("text_to_speech")
        record = next(
            (
                m
                for m in models
                if m.provider.replace("-", "").replace("_", "") == "edgetts"
            ),
            None,
        )
        if record is None:
            raise ConfigurationError(
                "Add an Edge TTS model to enable native English voices"
            )
    return record


async def list_speech_voices(model_id: str, language: str | None = None):
    record = await resolve_speech_model(model_id, language)
    model = await model_manager.get_model(str(record.id))
    if not isinstance(model, TextToSpeechModel):
        raise InvalidInputError("Voice selection requires a text-to-speech model")
    try:
        voices = (
            await model.alist_voices()
            if isinstance(model, VieNeuTTSProvider)
            else await asyncio.to_thread(lambda: list(model.available_voices.values()))
        )
    except Exception as exc:
        raise ExternalServiceError(
            "Unable to load the speech provider's voice list"
        ) from exc
    selected_language = speech_language(language)
    return [
        {
            "id": v.id,
            "name": v.name,
            "description": v.description,
            "gender": (v.gender or "unknown").lower(),
            "language_code": v.language_code,
            "model_id": str(record.id),
            **sample_metadata(
                str(record.id),
                v.id,
                selected_language or v.language_code or "",
                str(getattr(record, "updated", "")),
            ),
        }
        for v in voices
        if selected_language is None
        or (v.language_code or "").lower().startswith(selected_language)
    ]


async def preview_speech(
    model_id: str, voice: str, language: str, cached_only: bool = False
):
    lang = speech_language(language)
    voices = await list_speech_voices(model_id, lang)
    selected = next((v for v in voices if v["id"] == voice), None)
    if selected is None:
        raise InvalidInputError("Choose a voice that matches the selected language")
    key = selected.get("preview_key") or sample_key(selected["model_id"], voice, lang)
    cached = await cached_sample(key)
    if cached:
        return cached
    if cached_only:
        from notebooke.exceptions import NotFoundError

        raise NotFoundError("Voice sample has not been prepared yet")
    model = await model_manager.get_model(selected["model_id"])
    try:
        return await prepare_sample(key, model, voice, lang)
    except Exception as exc:
        raise ExternalServiceError("Unable to generate the voice preview") from exc
