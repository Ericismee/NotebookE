"""
Pixelle-Video bridge for NotebookE.

Builds a Pixelle-Video configuration dict from NotebookE's credential/model
store and initialises a PixelleVideoCore instance ready for video generation.

Design principles:
- Zero global state: every call returns a fresh, fully-configured instance.
- No file I/O: config is built in-memory; Pixelle-Video's config.yaml is
  bypassed entirely by patching config_manager at runtime.
- Async-first: follows the same pattern as podcast_commands.py.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import tempfile
from typing import Any

from loguru import logger

from notebooke.ai.edge_tts_provider import prepare_text_for_speech
from notebooke.config import DATA_FOLDER
from notebooke.videos.storytelling import (
    build_caption_cues,
    decode_scene_plan,
    encode_scene_plan,
    scene_from_mapping,
    split_narration_sentences,
)
from notebooke.videos.visual_fallback import build_visual_prompt, render_topic_visual

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

VIDEOS_FOLDER = f"{DATA_FOLDER}/videos"
os.makedirs(VIDEOS_FOLDER, exist_ok=True)


async def _run_process(command: list[str], timeout: float = 300) -> tuple[int, str]:
    """Run a media subprocess without blocking the async worker."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return -1, "Media process timed out"
    return process.returncode or 0, stderr.decode("utf-8", errors="replace")


async def resolve_llm_config() -> dict[str, Any]:
    """
    Return a minimal LLM config dict for Pixelle-Video's LLMService.

    Prefers the NotebookE default chat model; falls back to the first
    language model found.  Raises ValueError when nothing is configured.
    """
    from notebooke.ai.models import Model, model_manager

    # Try the configured default video model first, fallback to chat model
    model = await model_manager.get_default_model("video")
    if model is None:
        model = await model_manager.get_default_model("chat")
    if model is None:
        # Fall back to the first available language model
        lang_models = await Model.get_models_by_type("language")
        if not lang_models:
            raise ValueError(
                "No language model configured in NotebookE. "
                "Please add a model in Settings → Models."
            )
        model = await model_manager.get_model(str(lang_models[0].id))

    if model is None:
        raise ValueError("Could not provision a language model.")

    # Resolve credentials
    api_key = ""
    base_url: str | None = None
    # model_obj is an esperanto LanguageModel; inspect its internals
    raw = model  # esperanto LanguageModel
    try:
        # esperanto stores config in model.config or exposes it via attributes
        if hasattr(raw, "api_key"):
            api_key = raw.api_key or ""
        if hasattr(raw, "base_url"):
            base_url = raw.base_url or None
    except Exception:
        pass

    model_name = getattr(raw, "model_name", None) or getattr(raw, "name", "gpt-3.5-turbo")
    provider = str(
        getattr(raw, "PROVIDER", None)
        or getattr(raw, "provider", None)
        or raw.__class__.__module__.split(".")[-1]
    ).lower()

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model_name,
        "provider": provider,
    }


def _build_pixelle_config(llm_config: dict[str, Any]) -> dict[str, Any]:
    """
    Assemble a complete Pixelle-Video config dict from resolved LLM config.
    Uses Edge-TTS for TTS (free, no extra key required).
    Uses the static HTML template so no image-generation API is needed.
    """
    return {
        "project_name": "NotebookE-Video",
        "llm": {
            "api_key": llm_config["api_key"],
            "base_url": llm_config.get("base_url"),
            "model": llm_config["model"],
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        # TTS: local Edge-TTS, free, works offline
        "tts": {
            "inference_mode": "local",
            "local": {
                "voice": "vi-VN-NamMinhNeural",
                "speed": 1.1,
            },
        },
        # Allow Pixelle-Video to fall back to its internal API configs if ComfyUI is not set
        "output_dir": VIDEOS_FOLDER,
        "api_providers": {
            "openai": {
                "api_key": llm_config["api_key"]
                if llm_config.get("provider") in {"openai", "openai-compatible"}
                else "",
                "base_url": llm_config.get("base_url") or "https://api.openai.com/v1",
            }
        },
    }

def _patch_config_manager(config_dict: dict[str, Any]) -> None:
    """
    Inject our in-memory config into Pixelle-Video's config_manager singleton
    so LLMService reads our credentials instead of a config.yaml file.
    """
    try:
        from pixelle_video.config import config_manager

        # config_manager.config is a Pydantic model.  We patch the sub-models
        # directly so we don't need to know the exact Pydantic schema.
        llm_cfg = config_dict["llm"]
        cm_llm = config_manager.config.llm

        if hasattr(cm_llm, "api_key"):
            object.__setattr__(cm_llm, "api_key", llm_cfg["api_key"])
        if hasattr(cm_llm, "base_url"):
            object.__setattr__(cm_llm, "base_url", llm_cfg.get("base_url"))
        if hasattr(cm_llm, "model"):
            object.__setattr__(cm_llm, "model", llm_cfg["model"])

        # Patch API providers for image generation fallback
        cm_openai = config_manager.config.api_providers.openai
        cm_openai.api_key = config_dict["api_providers"]["openai"]["api_key"]
        cm_openai.base_url = config_dict["api_providers"]["openai"]["base_url"]

        logger.info(
            f"[pixelle-bridge] Config patched — model={llm_cfg['model']}, "
            f"base_url={llm_cfg.get('base_url')}"
        )
    except Exception as e:
        # Non-fatal: LLMService also accepts overrides at call time
        logger.warning(f"[pixelle-bridge] Could not patch config_manager: {e}")


def _legacy_normalize_vietnamese_tts(text: str) -> str:
    """
    Universal linguistic normalizer for Microsoft Edge TTS (vi-VN-NamMinhNeural).
    Dynamically optimizes ANY English technical terms, acronyms, and compound words
    so they are pronounced in standard, accurate English by the neural engine
    WITHOUT requiring hardcoded dictionaries or static word lists in code.
    """
    # 1. Universal CamelCase & PascalCase boundary splitting:
    # Automatically splits compound terms like 'PromptInjection' -> 'Prompt Injection',
    # 'FastAPI' -> 'Fast API', 'SurrealDB' -> 'Surreal DB', 'DeepSeek' -> 'Deep Seek',
    # 'ZeroKnowledge' -> 'Zero Knowledge', 'LangChain' -> 'Lang Chain', 'ChatGPT' -> 'Chat GPT'.
    # Works dynamically for 100% of technical terms without any hardcoded dictionary.
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', text)

    # 2. Universal technology extensions and domain suffixes:
    # 'Next.js' -> 'Next JS', 'script.py' -> 'script py', 'open.ai' -> 'open ai'
    text = re.sub(r'\b([A-Za-z0-9]+)\.(js|ts|py|ai|io|sh|cpp|rs|org|com|net)\b', r'\1 \2', text, flags=re.IGNORECASE)

    # 3. Clean slashes and underscores:
    # 'CI/CD' -> 'CI và CD', 'UI/UX' -> 'UI và UX', 'user_id' -> 'user id'
    text = re.sub(r'(?<=[a-zA-Z0-9])\s*/\s*(?=[a-zA-Z0-9])', ' và ', text)
    text = re.sub(r'(?<=[a-zA-Z0-9])_(?=[a-zA-Z0-9])', ' ', text)

    # 4. Universal English Acronyms & Initialisms:
    # Standalone 2 to 5 uppercase ASCII letters (e.g. AI, API, LLM, SQL, SPA, GPU, CPU, XSS, CSRF, HTTP, REST, UI, UX)
    # When surrounded by Vietnamese text, Azure Neural TTS often confuses them with Vietnamese words (e.g. 'AI' -> 'ai đó').
    # Spacing or hyphenating the letters ('A-I', 'A-P-I', 'L-L-M', 'S-Q-L') guides Edge-TTS to articulate each
    # English letter name clearly and natively ('Ây Ai', 'Ây Pi Ai', 'En En Em').
    VIET_COMMON_UPPER = {
        'CÁ', 'CÓ', 'VÀ', 'LÀ', 'ĐÓ', 'NÀY', 'CHO', 'VỚI', 'MỘT', 'KHI',
        'TỪ', 'TẠI', 'ĐƯỢC', 'NHƯ', 'TRONG', 'TRÊN', 'BỞI', 'VÌ', 'HAY'
    }
    SPECIAL_ACRONYMS = {
        'JSON': 'Dây-sơn',
        'RAG': 'R-A-G',
    }
    def _format_acronym(match):
        word = match.group(1)
        if word in VIET_COMMON_UPPER:
            return word
        if word in SPECIAL_ACRONYMS:
            return SPECIAL_ACRONYMS[word]
        # Hyphenate each letter: e.g. API -> A-P-I, LLM -> L-L-M, AI -> A-I
        return '-'.join(list(word))

    text = re.sub(r'\b([A-Z]{2,5})\b', _format_acronym, text)

    # 5. Natural phonetic nuance for common English IT terms in Vietnamese speech:
    # Guides vi-VN-NamMinhNeural to pronounce English loanwords
    # clearly, naturally and natively instead of swallowing syllables or misreading.
    TECH_PHONETICS = [
        (r'\bPrompt\s+Injection\b', 'P-rom In-dếch-sừn'),
        (r'\bZero\s+Day\b', 'Di-rô Đây'),
        (r'\bZero\s+Knowledge\b', 'Di-rô Nô-lích'),
        (r'\bZero\s+shot\b', 'Di-rô shot'),
        (r'\bFine\s+tuning\b', 'Phai-tu-ninh'),
        (r'\bLoad\s+Balancer\b', 'Lốt Ba-lân-sơ'),
        (r'\bAgents?\b', 'Ây-dừn'),
        (r'\bPrompts?\b', 'P-rom'),
        (r'\bServers?\b', 'Sơ-vơ'),
        (r'\bDatabases?\b', 'Đa-ta-bây'),
        (r'\bDatasets?\b', 'Đa-ta-sét'),
        (r'\bData\b', 'Đa-ta'),
        (r'\bCaches?\b', 'Két'),
        (r'\bPipelines?\b', 'Pai-lai'),
        (r'\bFrameworks?\b', 'Phờ-rem-uốc'),
        (r'\bMiddlewares?\b', 'Mít-đồ-oe'),
        (r'\bTransformers?\b', 'Tran-x-pho-mơ'),
        (r'\bEmbeddings?\b', 'Em-bét-đinh'),
        (r'\bBenchmarks?\b', 'Ben-mác'),
        (r'\bTokens?\b', 'Tô-kừn'),
        (r'\bBackends?\b', 'Bách-en'),
        (r'\bFrontends?\b', 'Phờ-ron-ten'),
        (r'\bFullstack\b', 'Phun-x-tắc'),
        (r'\bClients?\b', 'Clai-ừn'),
        (r'\bClouds?\b', 'Clao'),
        (r'\bDockers?\b', 'Đốc-cơ'),
        (r'\bKubernetes\b', 'Cu-bơ-ne-tít'),
        (r'\bMicroservices?\b', 'Mai-cro-sơ-vít'),
        (r'\bCookies?\b', 'Cúc-ki'),
        (r'\bSessions?\b', 'Sét-sừn'),
        (r'\bBugs?\b', 'Bắc'),
        (r'\bDebug\b', 'Đi-bắc'),
        (r'\bHackers?\b', 'Hắc-cơ'),
        (r'\bFirewalls?\b', 'Phai-ơ-woan'),
        (r'\bMalwares?\b', 'Man-oe'),
        (r'\bSpywares?\b', 'Spai-oe'),
        (r'\bPhishings?\b', 'Phi-sinh'),
        (r'\bRansomwares?\b', 'Ran-sơm-oe'),
        (r'\bBypass\b', 'Bai-pát'),
        (r'\bPayloads?\b', 'Pây-lốt'),
        (r'\bBotnets?\b', 'Bót-nét'),
        (r'\bBackdoors?\b', 'Bách-đo'),
        (r'\bQuery\b', 'Quơ-ri'),
        (r'\bWorkflows?\b', 'Uốc-phờ-lâu'),
        (r'\bDashboards?\b', 'Đát-bo'),
        (r'\bPlugins?\b', 'Bờ-lắc-in'),
        (r'\bStreamings?\b', 'Sờ-tri-minh'),
        (r'\bOnline\b', 'On-lai'),
        (r'\bOffline\b', 'Ọp-lai'),
        (r'\bInternet\b', 'In-tơ-nét'),
        (r'\bEmails?\b', 'I-mêu'),
        (r'\bFiles?\b', 'Phai'),
        (r'\bProxys?|Proxies\b', 'Pờ-rốc-xi'),
        (r'\bGateways?\b', 'Gét-uây'),
        (r'\bRouters?\b', 'Rao-tơ'),
        (r'\bDevelopers?\b', 'Đê-vê-lốp-pơ'),
        (r'\bReleases?\b', 'Ri-lít'),
        (r'\bUpdates?\b', 'Ắp-đét'),
        (r'\bDownloads?\b', 'Đao-lốt'),
        (r'\bUploads?\b', 'Ắp-lốt'),
        (r'\bSlides?\b', 'Sơ-lai'),
        (r'\bDemos?\b', 'Đê-mô'),
        (r'\bLogs?\b', 'Lốc'),
        (r'\bLinks?\b', 'Linh'),
    ]
    for pat, rep in TECH_PHONETICS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)

    # 6. Collapse duplicate hyphens or spaces
    text = re.sub(r'-{2,}', '-', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def normalize_vietnamese_tts(text: str) -> str:
    """Use the same Edge TTS pronunciation pipeline as podcast generation."""
    return prepare_text_for_speech(text, "vi-VN")


def extract_smart_title(text: str, index: int = 0) -> str:
    """
    Extracts or generates a meaningful, professional topic title for a scene
    (e.g., 'CƠ CHẾ HOẠT ĐỘNG BẢO MẬT', 'LỖ HỔNG BẢO MẬT', 'KIẾN TRÚC HỆ THỐNG').
    Never slices words into sentences like 'HÃY TƯỞNG TƯỢNG MỘT TRỢ'.
    """
    clean_text = text.lower()
    
    PATTERNS = [
        (r'\b(bảo vệ|phòng thủ|ngăn chặn|cô lập|kiểm duyệt)\b', "CƠ CHẾ BẢO VỆ & PHÒNG THỦ"),
        (r'\b(kiến trúc|cấu trúc|spa|framework|mô hình)\b', "KIẾN TRÚC HỆ THỐNG"),
        (r'\b(bảo mật|an ninh|tấn công|lỗ hổng|nguy cơ|rủi ro|xâm nhập|khai thác|thao túng)\b', "LỖ HỔNG BẢO MẬT"),
        (r'\b(cơ chế|hoạt động|vận hành|nguyên lý|bản chất)\b', "CƠ CHẾ HOẠT ĐỘNG"),
        (r'\b(dữ liệu|ngữ cảnh|thông tin|đầu vào|input|prompt)\b', "XỬ LÝ DỮ LIỆU & NGỮ CẢNH"),
        (r'\b(mệnh lệnh|thực thi|câu lệnh|chỉ thị|hành động|api)\b', "ĐIỀU KHIỂN & THỰC THI"),
        (r'\b(đánh đổi|so sánh|ưu thế|nhược điểm|hiệu năng)\b', "PHÂN TÍCH & ĐÁNH ĐỔI"),
        (r'\b(ứng dụng|thực tiễn|triển khai|doanh nghiệp)\b', "ỨNG DỤNG THỰC TIỄN"),
        (r'\b(tổng kết|kết luận|tương lai|tầm nhìn|bài học)\b', "TỔNG KẾT & TẦM NHÌN"),
    ]
    for pat, cand in PATTERNS:
        if re.search(pat, clean_text):
            return cand
            
    DEFAULT_STAGES = [
        "ĐẶT VẤN ĐỀ & BỐI CẢNH",
        "CƠ CHẾ HOẠT ĐỘNG CỐT LÕI",
        "MỔ XẺ BẢN CHẤT KỸ THUẬT",
        "RỦI RO & THÁCH THỨC",
        "KIẾN TRÚC BẢO VỆ CHUYÊN SÂU",
        "SO SÁNH & ĐÁNH ĐỔI",
        "GIẢI PHÁP PHÒNG THỦ TOÀN DIỆN",
        "HIỆU NĂNG & THỰC THI",
        "TRIỂN KHAI THỰC TẾ",
        "TỔNG KẾT & TẦM NHÌN",
    ]
    return DEFAULT_STAGES[index] if index < len(DEFAULT_STAGES) else f"PHÂN TÍCH CHUYÊN SÂU {index + 1}"


def split_into_display_sentences(text: str) -> list[str]:
    """
    Splits a narration paragraph into clean, comfortable display slides (each 10-24 words)
    so Sentence A shows -> disappears when read -> Sentence B appears in the same place.
    """
    t = text.replace('Next.js', 'Next__DOT__js').replace('Node.js', 'Node__DOT__js').replace('Vue.js', 'Vue__DOT__js')
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.?!;\n])\s+', t) if s.strip()]
    
    final_slides: list[str] = []
    for s in raw_sentences:
        s = s.replace('__DOT__', '.').strip()
        words = s.split()
        if len(words) <= 24:
            final_slides.append(s)
        else:
            clauses = [c.strip() for c in re.split(r'(?<=,)\s+', s) if c.strip()]
            if len(clauses) > 1:
                chunk: list[str] = []
                for cl in clauses:
                    if chunk and (sum(len(x.split()) for x in chunk) >= 10):
                        final_slides.append(" ".join(chunk))
                        chunk = [cl]
                    else:
                        chunk.append(cl)
                if chunk:
                    if final_slides and sum(len(x.split()) for x in chunk) < 6:
                        final_slides[-1] += " " + " ".join(chunk)
                    else:
                        final_slides.append(" ".join(chunk))
            else:
                final_slides.append(s)
    return final_slides if final_slides else [text]


async def build_pixelle_core(
    language: str = "Vietnamese", template_preset: str = "editorial-light",
    voice_model: str | None = None,
):
    """
    Build and initialise a PixelleVideoCore instance using NotebookE credentials.

    Args:
        language: The target language to enforce for the generation models.

    Returns:
        Initialised PixelleVideoCore ready for generate_video().
    """
    try:
        from pixelle_video import PixelleVideoCore
    except ImportError as exc:
        raise ImportError(
            "pixelle-video is not installed. "
            "Run: uv sync  (it is declared in pyproject.toml)"
        ) from exc

    from notebooke.videos.templates import get_video_template

    visual_template = get_video_template(template_preset)
    llm_config = await resolve_llm_config()
    # Image generation has its own credential. The storyboard model may be a
    # compatible Gemma endpoint, so never assume its key can call OpenAI Images.
    from notebooke.ai.key_provider import get_api_key
    image_api_key = await get_api_key("openai") or ""
    config_dict = _build_pixelle_config(llm_config)
    config_dict["api_providers"]["openai"]["api_key"] = image_api_key

    # Patch the singleton so LLMService picks up our credentials
    _patch_config_manager(config_dict)
    
    core = PixelleVideoCore()
    await core.initialize()

    # Keep video generation useful when no image provider is configured: render a
    # deterministic visual motif from the AI director's scene prompt.
    original_media = core.media

    def local_visual_result(kwargs, reason: str):
        from pixelle_video.models.media import MediaResult

        logger.info(f"[pixelle-bridge] {reason} Rendering a topic-aware local visual.")
        index = kwargs.get("index", 0)
        task_id = str(kwargs.get("task_id", "video")).replace(":", "_")
        fallback_path = os.path.join(
            VIDEOS_FOLDER, f"fallback_{task_id}_{index}.png"
        )
        visual_prompt = build_visual_prompt(
            str(kwargs.get("prompt") or "knowledge concept"),
            str(kwargs.get("layout") or "cinematic"),
        )
        render_topic_visual(
            visual_prompt,
            fallback_path,
            int(kwargs.get("width") or 1920),
            int(kwargs.get("height") or 1080),
        )
        return MediaResult(media_type="image", url=os.path.abspath(fallback_path))

    async def fallback_media(*args, **kwargs):
        workflow = str(kwargs.get("workflow") or "")
        openai_key = config_dict["api_providers"]["openai"]["api_key"]
        if workflow.startswith("api/openai/") and not openai_key:
            return local_visual_result(
                kwargs,
                "No compatible image-provider credential is configured.",
            )
        try:
            return await original_media(*args, **kwargs)
        except Exception as e:
            logger.warning(
                f"[pixelle-bridge] Media generation failed: {e}. "
                "Rendering a topic-aware local visual."
            )
            return local_visual_result(kwargs, "Image-provider request failed.")

    core.media = fallback_media


    # Intercept audio generation to speak the FULL analytical text with phonetic normalization
    if hasattr(core, 'frame_processor'):
        speech_model = None
        if voice_model:
            from notebooke.ai.models import Model, model_manager
            from notebooke.exceptions import ConfigurationError

            selected = await Model.get(voice_model)
            if selected.type != "text_to_speech":
                raise ConfigurationError("Video voice model must be text-to-speech")
            speech_model = await model_manager.get_model(voice_model)

        async def generate_audio(**params):
            if speech_model is None:
                return await core.tts(**params)
            return await speech_model.agenerate_speech(
                text=params["text"], voice=params.get("voice") or "Minh Quân",
                output_file=params["output_path"],
            )

        original_audio = core.frame_processor._step_generate_audio
        original_step_compose = core.frame_processor._step_compose_frame
        original_step_segment = core.frame_processor._step_create_video_segment
        original_compose = core.frame_processor._compose_frame_html

        # 1. AUDIO GENERATION: speaks each sentence separately to measure exact audio duration
        # and builds short progressive captions that exactly fill each sentence duration.
        async def split_audio_gen(frame, config):
            original_narration = frame.narration
            scene = decode_scene_plan(original_narration, frame.index)
            speech_text = scene.narration
            frame._scene_plan = scene

            sentences = split_narration_sentences(speech_text)
            from pixelle_video.utils.os_util import get_task_frame_path
            main_audio_path = get_task_frame_path(config.task_id, frame.index, "audio")
            os.makedirs(os.path.dirname(main_audio_path), exist_ok=True)

            if len(sentences) <= 1 and speech_model is None:
                tts_text = sentences[0] if sentences else speech_text
                if language.lower() in ["tiếng việt", "vietnamese", "vi"]:
                    tts_text = normalize_vietnamese_tts(tts_text)
                frame.narration = tts_text
                try:
                    res = await original_audio(frame, config)
                    frame._caption_cues = build_caption_cues(
                        sentences[0] if sentences else speech_text,
                        frame.duration,
                    )
                    return res
                finally:
                    frame.narration = original_narration

            # Generate audio for each sentence individually
            sub_audios = []
            for idx, sentence in enumerate(sentences):
                sub_audio_path = main_audio_path.replace(".mp3", f"_sentence_{idx}.mp3")
                tts_sentence = sentence
                if (speech_model is None or speech_model.PROVIDER == "edgetts") and language.lower() in ["tiếng việt", "vietnamese", "vi"]:
                    tts_sentence = normalize_vietnamese_tts(sentence)

                tts_params = {
                    "text": tts_sentence,
                    "inference_mode": config.tts_inference_mode,
                    "output_path": sub_audio_path,
                    "index": frame.index + 1,
                }
                if config.voice_id:
                    tts_params["voice"] = config.voice_id
                if config.tts_speed is not None:
                    tts_params["speed"] = config.tts_speed

                await generate_audio(**tts_params)
                dur = await core.frame_processor._get_audio_duration(sub_audio_path)
                sub_audios.append((sentence, sub_audio_path, dur))

            # Concatenate all sentence audios using ffmpeg concat filter into main_audio_path
            inputs = []
            for _, a_path, _ in sub_audios:
                inputs.extend(["-i", a_path])
            n = len(sub_audios)
            filter_str = "".join([f"[{i}:a]" for i in range(n)]) + f"concat=n={n}:v=0:a=1[outa]"
            cmd = [
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[outa]",
                main_audio_path
            ]
            return_code, stderr = await _run_process(cmd)
            if return_code != 0 or not os.path.exists(main_audio_path):
                logger.warning(f"[pixelle-bridge] Audio concat failed, fallback to first audio. Error: {stderr[:200]}")
                import shutil
                shutil.copy(sub_audios[0][1], main_audio_path)

            frame.audio_path = main_audio_path
            frame.duration = await core.frame_processor._get_audio_duration(main_audio_path)
            frame._caption_cues = [
                cue
                for sentence, _, duration in sub_audios
                for cue in build_caption_cues(sentence, duration)
            ]
            frame.narration = original_narration
            logger.info(
                f"[pixelle-bridge] Frame {frame.index}: Generated "
                f"{len(sub_audios)} natural speech units and "
                f"{len(frame._caption_cues)} progressive captions "
                f"(total {frame.duration:.2f}s)"
            )
            return main_audio_path
        core.frame_processor._step_generate_audio = split_audio_gen

        # 2. ADAPTIVE COMPOSITION: one coherent visual system with a scene-specific
        # layout selected by the AI director (cinematic, split, diagram, timeline...).
        async def adaptive_compose_frame(frame, storyboard, config, output_path):
            base_template = config.frame_template or "1080x1920/image_default.html"
            res_prefix = base_template.split('/')[0] if '/' in base_template else "1080x1920"
            scene = getattr(frame, "_scene_plan", None) or decode_scene_plan(
                frame.narration, frame.index
            )
            adaptive_template = f"{res_prefix}/image_notebooke_adaptive.html"
            original_template = config.frame_template
            config.frame_template = adaptive_template

            if not hasattr(config, 'template_params') or not config.template_params:
                config.template_params = {}
            config.template_params.update({
                "author": "@NotebookE",
                "brand": "NotebookE",
                **visual_template.template_params,
                "layout": scene.layout,
                "purpose": html.escape(scene.purpose),
                "scene_number": f"{frame.index + 1:02d}",
                "total_scenes": f"{len(storyboard.frames):02d}" if storyboard else "01",
                "keywords": html.escape("  •  ".join(scene.visual_keywords[:3])),
            })

            try:
                return await original_compose(frame, storyboard, config, output_path)
            finally:
                config.frame_template = original_template

        core.frame_processor._compose_frame_html = adaptive_compose_frame

        # 3. INTERCEPT STEP_COMPOSE_FRAME: Render dynamic sentence-by-sentence slides
        async def multi_slide_step_compose(frame, storyboard, config):
            raw_narration = frame.narration
            scene = getattr(frame, "_scene_plan", None) or decode_scene_plan(
                raw_narration, frame.index
            )
            title = scene.title

            # Set the clean topic title on storyboard
            if storyboard:
                storyboard.title = html.escape(title)

            caption_cues = getattr(frame, "_caption_cues", None)
            if not caption_cues:
                caption_cues = build_caption_cues(scene.narration, frame.duration or 5.0)

            if len(caption_cues) <= 1:
                frame.narration = html.escape(caption_cues[0][0])
                try:
                    return await original_step_compose(frame, storyboard, config)
                finally:
                    frame.narration = raw_narration

            # Multiple slides: use the EXACT duration measured directly from the sentence's audio!
            from pixelle_video.utils.os_util import get_task_frame_path
            base_composed = get_task_frame_path(config.task_id, frame.index, "composed")
            sub_slides = []
            for idx, (slide_text, slide_dur) in enumerate(caption_cues):
                sub_output = base_composed.replace(".png", f"_s{idx}.png")
                frame.narration = html.escape(slide_text)
                composed_path = await core.frame_processor._compose_frame_html(frame, storyboard, config, sub_output)
                sub_slides.append((composed_path, slide_dur))

            frame._sub_slides = sub_slides
            frame.composed_image_path = sub_slides[0][0]
            frame.narration = raw_narration
            logger.info(
                f"[pixelle-bridge] Frame {frame.index}: Rendered {len(sub_slides)} "
                f"short synchronized captions with adaptive '{scene.layout}' layout"
            )

        core.frame_processor._step_compose_frame = multi_slide_step_compose

        # 4. INTERCEPT STEP_CREATE_VIDEO_SEGMENT: Concat slides with audio so Sentence A appears, then Sentence B replaces it
        async def multi_slide_step_segment(frame, config):
            if hasattr(frame, '_sub_slides') and len(frame._sub_slides) >= 2:
                from pixelle_video.utils.os_util import get_task_frame_path
                output_path = get_task_frame_path(config.task_id, frame.index, "segment")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                concat_lines = []
                for img_path, dur in frame._sub_slides:
                    norm_path = os.path.abspath(img_path).replace("\\", "/")
                    concat_lines.append(f"file '{norm_path}'")
                    concat_lines.append(f"duration {dur:.2f}")
                norm_last = os.path.abspath(frame._sub_slides[-1][0]).replace("\\", "/")
                concat_lines.append(f"file '{norm_last}'")

                concat_content = "\n".join(concat_lines) + "\n"
                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
                    tf.write(concat_content)
                    concat_file = tf.name

                try:
                    fps = getattr(config, 'video_fps', 30) or 30
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0", "-i", concat_file,
                        "-i", frame.audio_path,
                        "-vf", f"fps={fps}",
                        "-r", str(fps),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac",
                        "-shortest",
                        output_path
                    ]
                    return_code, stderr = await _run_process(cmd)
                    if return_code == 0 and os.path.exists(output_path):
                        logger.info(f"[pixelle-bridge] Frame {frame.index}: Created dynamic sentence-by-sentence segment with {fps}fps CFR: {output_path}")
                        frame.video_segment_path = output_path
                        return output_path
                    else:
                        logger.warning(f"[pixelle-bridge] Concat ffmpeg failed, falling back to static segment. Error: {stderr[:200]}")
                finally:
                    if os.path.exists(concat_file):
                        os.unlink(concat_file)

            return await original_step_segment(frame, config)

        core.frame_processor._step_create_video_segment = multi_slide_step_segment

    # Enforce deep, analytical, and insightful video scripting
    import pixelle_video.utils.content_generators as cg

    def _build_deep_analytical_prompt(
        source_text: str,
        n_scenes: int,
        lang: str,
        min_words: int = 55,
        max_words: int = 90,
    ) -> str:
        phonetic_note = ""
        if lang.lower() in ["tiếng việt", "vietnamese", "vi"]:
            phonetic_note = """
- QUY TẮC PHÁT ÂM TIẾNG ANH CHUẨN XÁC & NGỮ PHÁP TIẾNG VIỆT TỰ NHIÊN:
  1. GIỮ NGUYÊN 100% THUẬT NGỮ TIẾNG ANH CHUẨN XÁC (TUYỆT ĐỐI KHÔNG PHIÊN ÂM TIẾNG VIỆT):
     - Giọng đọc AI (Hoài Mỹ / Nam Minh) phát âm tiếng Anh rất chuẩn nếu giữ nguyên từ gốc tiếng Anh.
     - TUYỆT ĐỐI KHÔNG phiên âm sang tiếng Việt (CẤM viết 'Đốc-kơ', 'Pai-thần', 'Nốt-búc-i', 'ây-ai', 'Phát-A-P-I', 'Gít-háp', 'Sơ-rin-D-B'...).
     - BẮT BUỘC giữ nguyên dạng tiếng Anh chuẩn quốc tế cho các tên công nghệ, công cụ, kiến trúc và thuật ngữ:
       * NotebookE, Docker, Python, FastAPI, Next.js, SurrealDB, AI, API, RAG, LLM, OpenAI, GitHub, PostgreSQL, Redis, Vector Database, Prompt Engineering, Caching, Backend, Frontend...
  2. GIỮ ĐÚNG NGỮ PHÁP TIẾNG VIỆT KHI DÙNG THUẬT NGỮ TIẾNG ANH:
     - Để câu văn không bị gãy hoặc sai ngữ pháp tiếng Việt khi chứa thuật ngữ tiếng Anh, hãy dùng danh từ chỉ loại hoặc từ nối tự nhiên phía trước tên thuật ngữ:
       * 'Nền tảng NotebookE...', 'Hệ thống NotebookE...' (thay vì nói cộc lốc 'NotebookE...')
       * 'Công nghệ AI...', 'Trí tuệ nhân tạo AI...', 'Mô hình AI...'
       * 'Công cụ Docker...', 'Môi trường Docker...'
       * 'Ngôn ngữ Python...'
       * 'Khung ứng dụng FastAPI...', 'Framework FastAPI...'
       * 'Cơ sở dữ liệu SurrealDB...'
       * 'Kiến trúc RAG...', 'Quy trình RAG...'
       * 'Giao diện lập trình API...'
  3. DẤU CÂU & NGẮT NGHỈ RÕ RÀNG:
     - Sử dụng dấu phẩy (,) và dấu chấm (.) hợp lý để tạo nhịp thở ngắt nghỉ tự nhiên giữa cụm từ tiếng Việt và từ tiếng Anh, giúp giọng đọc Hoài Mỹ chuyển đổi ngôn ngữ mượt mà, phát âm tiếng Anh chuẩn xác, vang và rõ chữ.
"""

        return f"""# VAI TRÒ & SỨ MỆNH:
Bạn là một Chuyên gia Phân tích Học thuật và Đạo diễn Nội dung Video Giáo dục Cao cấp.
Nhiệm vụ của bạn là chuyển hóa tài liệu/chủ đề được cung cấp thành kịch bản gồm đúng {n_scenes} phân cảnh (storyboard scenes).
Kịch bản phải ĐẠT ĐỘ SÂU VƯỢT TRỘI, MỔ XẺ CHI TIẾT, LẬP LUẬN SẮC BÉN VÀ CÓ HÀM LƯỢNG TRI THỨC CAO. Tuyệt đối không tóm tắt hời hợt, không nói sáo rỗng hay chỉ liệt kê lại tiêu đề.

# BỘ NGUYÊN TẮC PHÂN TÍCH CHUYÊN SÂU & MỞ ĐẦU ĐA DẠNG:
0. BÁM SÁT NGUỒN (FACTUAL GROUNDING):
   - Chỉ dùng dữ kiện có trong nội dung nguồn. Không tự tạo số liệu, trích dẫn, nghiên cứu, tên tổ chức, sự kiện hoặc khẳng định thực tế.
   - Nếu nguồn không đủ dữ kiện cho một nhận định, hãy diễn đạt có điều kiện hoặc bỏ nhận định đó.
   - Các ví dụ trong hướng dẫn này chỉ minh họa cấu trúc và giọng văn; tuyệt đối không sao chép chúng thành dữ kiện của video.

1. ĐA DẠNG HÓA CÁCH MỞ ĐẦU VIDEO (HOOK DIVERSITY) - BẮT BUỘC CHO PHÂN CẢNH 1:
   - TUYỆT ĐỐI CẤM các câu mở đầu sáo rỗng, rập khuôn nhàm chán như:
     * "Bạn đã bao giờ tự hỏi..."
     * "Chào mừng các bạn đến với..."
     * "Hôm nay chúng ta sẽ tìm hiểu/khám phá..."
     * "Trong video hôm nay/ngày hôm nay..."
     * "Bạn có biết rằng..."
   - PHÂN CẢNH 1 BẮT BUỘC SỬ DỤNG MỘT TRONG CÁC PHONG CÁCH MỞ ĐẦU (HOOK) ẤN TƯỢNG VÀ SẮC BÉN DƯỚI ĐÂY:
     * Hook 1 (Thực trạng / Thống kê giật mình): Nêu số liệu, cảnh báo hoặc nghịch lý nghiêm trọng ngay câu đầu.
       (Ví dụ: "Hơn 90% mô hình AI Agent hiện nay có thể bị chiếm đoạt quyền điều khiển chỉ bằng một email văn bản bình thường.")
     * Hook 2 (Tình huống thực tế / Nguy cơ cận kề): Đưa người xem vào kịch bản tấn công hoặc sự cố thực tế.
       (Ví dụ: "Hãy tưởng tượng trợ lý ảo công ty tự động trích xuất toàn bộ dữ liệu tài chính gửi cho tin tặc mà không hề báo động.")
     * Hook 3 (Nút thắt kỹ thuật cốt lõi): Đi thẳng vào điểm mù kiến trúc mà ít ai nhận ra.
       (Ví dụ: "Ranh giới mong manh giữa dữ liệu và mã lệnh thực thi chính là lỗ hổng chí mạng của trí tuệ nhân tạo hiện đại.")
     * Hook 4 (Tuyên bố phản trực giác / So sánh tương phản): Đưa ra nhận định trái ngược với suy nghĩ thông thường.
       (Ví dụ: "Càng thông minh và tự chủ, các AI Agent lại càng trở thành con mồi dễ bị thao túng nhất nếu thiếu đi lớp kiểm duyệt độc lập.")

2. MỔ XẺ BẢN CHẤT & NGUYÊN LÝ HOẠT ĐỘNG (Why & How):
   - Đừng chỉ dừng lại ở việc khái niệm đó là gì (What). Hãy làm rõ: Tại sao vấn đề/giải pháp này xuất hiện? Bản chất kỹ thuật/tri thức cốt lõi hoạt động ra sao? Cơ chế bên trong vận hành như thế nào?
   - Chỉ ra mối quan hệ nhân quả logic chặt chẽ giữa các thành phần và hệ quả của chúng.

3. PHÂN TÍCH SO SÁNH & SỰ ĐÁNH ĐỔI (Trade-offs & Analytical Insights):
   - Phân tích rõ các ưu thế vượt trội, các điểm đánh đổi kỹ thuật (ví dụ: bảo mật vs tiện lợi, độ trễ vs độ chính xác), hoặc so sánh với giải pháp truyền thống.
   - Nêu lên các góc nhìn đa chiều, phản biện và bài học rút ra có giá trị thực tiễn cao.

4. TIẾN TRÌNH LUẬN ĐIỂM LIÊN KẾT (Narrative Continuity):
   - {n_scenes} phân cảnh phải tạo thành một mạch phân tích hoàn chỉnh, chặt chẽ:
     * Phân cảnh mở đầu: Mở bài độc đáo (Hook), đặt ra bài toán cốt lõi và sự cấp bách.
     * Các phân cảnh giữa: Đi sâu mổ xẻ từng tầng cấu trúc, cơ chế vận hành, kiến trúc và phân tích chi tiết.
     * Các phân cảnh cuối: Đánh giá tác động thực tiễn, tầm nhìn phát triển và đúc kết giá trị chuyên môn sâu sắc.

# NGÔN NGỮ HÌNH ẢNH VÀ NHỊP KỂ:
- Mỗi cảnh phải đóng một vai trò riêng trong lập luận; không lặp lại cùng một bố cục hoặc cùng một hình ảnh chung chung.
- "visual_layout" phải chọn đúng một giá trị:
  * cinematic: mở bài, kết luận hoặc cảnh giàu cảm xúc với hình toàn khung.
  * split: giải thích một khái niệm bằng hình và lời song song.
  * diagram: cơ chế, kiến trúc, quan hệ giữa các thành phần.
  * timeline: tiến trình, lịch sử, chuỗi thời gian hoặc các bước tuần tự.
  * comparison: đối chiếu hai phương pháp, hai trạng thái hoặc trade-off.
  * focus: một số liệu, phát hiện hoặc luận điểm trung tâm cần nhấn mạnh.
  * process: chuỗi bước, đầu vào/đầu ra hoặc quy trình vận hành.
  * metrics: xu hướng, tín hiệu, số liệu hoặc thay đổi theo thời gian.
- "visual_prompt" phải viết bằng tiếng Anh, mô tả cụ thể chủ thể, môi trường, quan hệ và góc máy đúng nội dung cảnh. Yêu cầu hình sạch, giàu thông tin thị giác, không chứa chữ, logo hoặc watermark.
- "visual_keywords" gồm 2-4 cụm từ rất ngắn lấy từ nội dung, dùng như nhãn định hướng thị giác.
- Với "metrics", hãy mô tả một biểu đồ thật sự (đường, cột, phân vùng hoặc before/after) dựa trên số liệu có trong nguồn; chỉ dùng nhãn ngắn cần thiết.
- Với "diagram", "process" và "timeline", hãy mô tả rõ các nút, bước, mũi tên, đầu vào/đầu ra hoặc mốc thời gian để model ảnh tạo infographic có cấu trúc; không biến thành ảnh trừu tượng.
- "purpose" là nhãn ngắn 2-5 từ dành cho người xem, ví dụ "Cơ chế vận hành" hoặc "Phân tích đánh đổi". Không ghi hướng dẫn sản xuất như "dùng Hook 4", "phân cảnh mở đầu" hay "sử dụng bố cục".
- Xen kẽ bố cục giữa các cảnh. Không dùng cùng một visual_layout quá hai cảnh liên tiếp.

# YÊU CẦU TIÊU ĐỀ & LỜI KỂ MỖI PHÂN CẢNH:
1. "title": Tiêu đề 3-6 từ, là một khái niệm chuyên môn rõ ràng; không cắt một câu thoại làm tiêu đề.
2. "narration": {min_words}-{max_words} từ, gồm 2-4 câu hoàn chỉnh. Lời kể phải nhiều thông tin hơn phần chữ trên màn hình, có quan hệ nguyên nhân-kết quả, giải thích Why và How, kèm ví dụ hoặc đánh đổi khi nguồn cho phép.
3. Không viết lời dẫn chuyển cảnh vô nghĩa. Mỗi câu phải bổ sung một tầng hiểu biết mới.
{phonetic_note}
# NGÔN NGỮ: Bắt buộc viết hoàn toàn bằng {lang.upper()}.

# NỘI DUNG NGUỒN CẦN PHÂN TÍCH:
{source_text}

# YÊU CẦU ĐỊNH DẠNG JSON:
Chỉ trả về định dạng JSON hợp lệ duy nhất:
{{
  "scenes": [
    {{
      "title": "CƠ CHẾ HOẠT ĐỘNG BẢO MẬT",
      "purpose": "Đặt vấn đề và chỉ ra quan hệ nhân quả",
      "visual_layout": "diagram",
      "visual_prompt": "Editorial systems diagram of an AI assistant receiving an untrusted document, branching toward protected data and risky tool actions, precise technical visual, cinematic lighting, no text, no logo",
      "visual_keywords": ["Dữ liệu", "Chỉ thị", "Quyền thực thi"],
      "narration": "Một trợ lý AI có thể đọc email, quản lý file và gọi API, nhưng chính chuỗi năng lực này tạo ra một đường truyền rủi ro từ dữ liệu đến hành động. Khi hệ thống không tách biệt nội dung tham khảo với chỉ thị thực thi, một câu lệnh ẩn trong tài liệu có thể điều khiển công cụ dưới danh nghĩa người dùng. Vấn đề cốt lõi vì thế nằm ở ranh giới tin cậy, không chỉ ở độ thông minh của mô hình."
    }},
    {{
      "title": "LỖ HỔNG DỮ LIỆU VÀ LỆNH",
      "purpose": "So sánh trạng thái an toàn và không an toàn",
      "visual_layout": "comparison",
      "visual_prompt": "Split editorial comparison, left side trusted instruction channel with clear isolation, right side mixed data and command channel causing unauthorized action, sophisticated cybersecurity illustration, no text, no logo",
      "visual_keywords": ["Phân tách", "Kiểm duyệt", "An toàn"],
      "narration": "Ranh giới giữa dữ liệu và lệnh thực thi quyết định hệ thống có chống được Prompt Injection hay không. Ở kiến trúc an toàn, nguồn ngoài chỉ cung cấp bằng chứng và mọi hành động đều qua lớp kiểm duyệt độc lập. Ở kiến trúc yếu, dữ liệu và quyền điều khiển đi chung một kênh, khiến mô hình có thể biến nội dung độc hại thành thao tác thật. Sự khác biệt nằm ở thiết kế quyền hạn, không nằm ở một câu nhắc nhở dài hơn."
    }}
  ]
}}
Hãy tạo đúng {n_scenes} phân cảnh, bảo đảm mạch kể liên tục và hình ảnh của từng cảnh bám sát chính luận điểm đang được đọc."""

    def _robust_parse_json(text: str) -> dict:
        text_clean = text.strip()
        try:
            return json.loads(text_clean)
        except Exception:
            pass
        m = re.search(r'```(?:json)?\s*(.*?)\s*```', text_clean, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        s = text_clean.find('{')
        e = text_clean.rfind('}')
        if s != -1 and e != -1 and e > s:
            try:
                return json.loads(text_clean[s:e+1])
            except Exception:
                pass
        return cg._parse_json(text)

    def _encode_storyboard_result(result: dict, n_scenes: int) -> list[str]:
        values = result.get("scenes") or result.get("narrations")
        if not isinstance(values, list):
            raise ValueError("Missing 'scenes' or 'narrations' key in LLM response")

        encoded: list[str] = []
        for index, value in enumerate(values[:n_scenes]):
            if isinstance(value, str) and "|||" in value:
                scene = decode_scene_plan(value, index)
            else:
                scene = scene_from_mapping(value, index)
            if not scene.narration:
                continue
            if scene.title.startswith("Ý CHÍNH "):
                scene.title = extract_smart_title(scene.narration, index)
            encoded.append(encode_scene_plan(scene))

        if len(encoded) < n_scenes:
            logger.warning(
                f"[pixelle-bridge] Expected {n_scenes} scenes, got "
                f"{len(encoded)} valid scenes. Proceeding anyway."
            )
        return encoded

    async def relaxed_gen_topic(llm_service, topic: str, n_scenes: int = 5, min_words: int = 55, max_words: int = 90):
        logger.info(f"[pixelle-bridge] Generating {n_scenes} deep analytical narrations from topic...")
        prompt = _build_deep_analytical_prompt(source_text=topic, n_scenes=n_scenes, lang=language, min_words=min_words, max_words=max_words)
        response = await llm_service(prompt=prompt, temperature=0.7, max_tokens=4096, response_format={"type": "json_object"})
        result = _robust_parse_json(response)
        
        return _encode_storyboard_result(result, n_scenes)

    async def relaxed_gen_content(llm_service, content: str, n_scenes: int = 5, min_words: int = 55, max_words: int = 90):
        logger.info(f"[pixelle-bridge] Generating {n_scenes} deep analytical narrations from content...")
        prompt = _build_deep_analytical_prompt(source_text=content, n_scenes=n_scenes, lang=language, min_words=min_words, max_words=max_words)
        response = await llm_service(prompt=prompt, temperature=0.7, max_tokens=4096, response_format={"type": "json_object"})
        result = _robust_parse_json(response)

        return _encode_storyboard_result(result, n_scenes)

    cg.generate_narrations_from_topic = relaxed_gen_topic
    cg.generate_narrations_from_content = relaxed_gen_content
    try:
        import pixelle_video.pipelines.standard as standard_mod
        standard_mod.generate_narrations_from_topic = relaxed_gen_topic

        async def use_storyboard_visual_prompts(
            llm_service,
            narrations,
            min_words=30,
            max_words=60,
            progress_callback=None,
        ):
            prompts = [
                build_visual_prompt(
                    decode_scene_plan(value, index).visual_prompt,
                    decode_scene_plan(value, index).layout,
                )
                for index, value in enumerate(narrations)
            ]
            if progress_callback:
                progress_callback(len(prompts), len(prompts), "Visual plan ready")
            return prompts

        standard_mod.generate_image_prompts = use_storyboard_visual_prompts
    except Exception as e:
        logger.warning(f"[pixelle-bridge] Could not patch standard module: {e}")

    logger.info(

        f"[pixelle-bridge] PixelleVideoCore initialised — "
        f"model={llm_config['model']}"
    )
    return core, llm_config
