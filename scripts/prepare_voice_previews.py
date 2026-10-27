"""Generate versioned Vietnamese/English samples once for instant playback."""

import asyncio

from dotenv import load_dotenv

load_dotenv(override=True)

from notebooke.ai.models import DefaultModels  # noqa: E402
from notebooke.ai.speech import list_speech_voices, preview_speech  # noqa: E402


async def main():
    defaults = await DefaultModels.get_instance()
    model_id = defaults.default_text_to_speech_model
    if not model_id:
        raise RuntimeError("Configure a default speech model first")
    count = 0
    for language in ("vi", "en"):
        voices = await list_speech_voices(model_id, language)
        for index, voice in enumerate(voices, 1):
            audio = await preview_speech(voice["model_id"], voice["id"], language)
            count += 1
            print(
                f"{language} {index}/{len(voices)}: {voice['name']} ({voice['gender']}) — {len(audio.audio_data)} bytes ready",
                flush=True,
            )
    print(f"READY: {count} voice samples", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
