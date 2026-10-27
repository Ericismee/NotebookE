# VieNeu v3 Turbo speech for podcasts and videos

NotebookE supports a separately hosted VieNeu OpenAI-compatible speech server.
The NotebookE API and worker do not download or run model weights themselves.

## Start the speech server

Follow [VieNeu's installation instructions](https://github.com/pnnbao97/VieNeu-TTS)
in a separate environment. The preset suggestions match SDK 3.8.3 (25 voices).
From the VieNeu checkout, start its speech API:

```sh
uv run python -m apps.openai_speech
```

The documented default is port 8000, which NotebookE already uses for SurrealDB.
Run VieNeu in a separate container/host, or configure a different listening port
using the upstream server's options. Do not point the credential at SurrealDB.

## Configure NotebookE

### Local Windows setup

This workstation runs VieNeu SDK 3.8.3 from upstream commit
`bd0f88eb8de7d560753a6172ba9c868f309a101c` in `.codex-runtime/VieNeu-TTS`.
The model cache stays in `.codex-runtime/huggingface`. Start it with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-vieneu.ps1
```

The server uses ONNX/CPU at `http://127.0.0.1:8001/v1`; health is available at
`http://127.0.0.1:8001/health`. `start-all.ps1` also starts it when installed.
The default video voice is Hải Đăng. Podcast profiles use Hải Đăng/Mai Anh,
Thiện Minh, and Quang Sơn/Ngọc Trân respectively. Users can change these in
the creation dialog or speaker profiles. The previous selections are backed up
locally in `artifacts/vieneu/settings-before.json`.

### Other installations

1. Add an **OpenAI Compatible** credential, with the reachable VieNeu base URL
   ending in `/v1`. In Docker, localhost refers to the container, so use the
   service hostname or `host.docker.internal` as appropriate.
2. Add a **Text-to-Speech** model named `vieneu-v3-turbo` linked to that credential.
   This name activates the VieNeu adapter; no Hugging Face inference subscription
   is involved.
3. For podcasts, select that model in the speaker profile, or override it per
   speaker. Enter/select a VieNeu voice name, for example `Minh Quân`, `Mai Anh`,
   `Quang Sơn` or `Ngọc Trân`. Each speaker can use a different voice.
4. For videos, select the voice model and voice in the creation dialog.
5. To prefer VieNeu for new videos, set it as the default Text-to-Speech model
   under Models. Existing podcast profiles keep their explicit model choices;
   update those profiles to prefer VieNeu there too.

Custom voices enrolled on your server can be entered by name. Presets vary by
SDK version; [the model card](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo)
documents the current names. The older demo names in screenshots may differ.

The adapter requests WAV and converts it to MP3 with pydub/FFmpeg for the existing
media pipelines. FFmpeg must be available to the worker. A failed VieNeu request
fails the job, allowing an explicit retry; it does not silently switch voices.
Restart the API, worker and frontend after updating. Migration 30 adds the video
speech-model snapshot field automatically when the API starts.

## Listen before generating

Video creation and both podcast creation dialogs offer a **Listen to sample**
button beside each voice. Samples use a fixed sentence in the selected language;
changing the voice or language stops the previous sample. Playback uses the same
model and voice identifiers sent to generation.

Voices are grouped and filtered by Male/Female. Prepare all preset samples once:

```powershell
uv run --env-file .env python -m scripts.prepare_voice_previews
```

MP3 samples persist in `data/voice_previews`. The browser preloads ready samples,
so clicking Listen plays the prepared audio without synthesizing again. Changing
the sample sentence or model revision automatically selects a new cache key.
New custom voices can be generated on first preview, then reused. Podcast
language labels such as Vietnamese/English are normalized to `vi`/`en` before
calling podcast-creator, including legacy profiles and retries.

Vietnamese offers VieNeu presets; English switches to the registered Edge TTS
model's native English voices. Edge TTS 7.3.1 or newer is required for the current
service protocol. Voice lists are filtered by language at the API as well as the
UI. Each podcast request stores its chosen language and voices in the episode
snapshot, without changing the saved speaker profile. Retry preserves them.

## Attribution

VieNeu-TTS by Phạm Nguyễn Ngọc Bảo; model package
`pnnbao-ump/VieNeu-TTS-v3-Turbo`. See upstream Apache-2.0 notices when redistributing
model or voice assets. NotebookE connects to the server without bundling them.
