#!/usr/bin/env python3
"""
OpenCharm Relay Server

Receives images and audio from the XIAO ESP32S3 via HTTP POST,
analyzes with Claude Vision/Whisper, and sends results to Telegram.

Usage:
    uvicorn relay_server:app --host 0.0.0.0 --port 8080
"""

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from functools import lru_cache
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import httpx

# Claude Code integration modules
from claude_executor import ClaudeCodeExecutor, get_executor, ActionStatus
from intent_parser import IntentParser, get_parser, IntentCategory

# Whisper for audio transcription (lazy loaded)
whisper_model = None

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# =============================================================================
# AI BACKEND CONFIGURATION
# =============================================================================
# Options: "ollama_local", "ollama_school", "openai"
AI_BACKEND = os.getenv("AI_BACKEND", "ollama_local")  # <-- Change this to switch backends

# =============================================================================
# OLLAMA CONFIGURATION
# =============================================================================
# Legacy toggle (use AI_BACKEND instead)
OLLAMA_MODE = "local" if AI_BACKEND == "ollama_local" else "school"

# Local Ollama (runs on your laptop)
OLLAMA_LOCAL_URL = "http://localhost:11434"
OLLAMA_LOCAL_VISION_MODEL = "llava:latest"      # For image analysis
OLLAMA_LOCAL_TEXT_MODEL = "llama3.2:latest"     # For text/voice analysis

# School GPU Ollama (requires VPN)
OLLAMA_SCHOOL_URL = "http://itp-ml.itp.tsoa.nyu.edu:11434"
OLLAMA_SCHOOL_VISION_MODEL = "llava"
OLLAMA_SCHOOL_TEXT_MODEL = "llama3.2:latest"

# Set active config based on mode
if OLLAMA_MODE == "local":
    OLLAMA_BASE_URL = OLLAMA_LOCAL_URL
    OLLAMA_VISION_MODEL = OLLAMA_LOCAL_VISION_MODEL
    OLLAMA_TEXT_MODEL = OLLAMA_LOCAL_TEXT_MODEL
else:
    OLLAMA_BASE_URL = OLLAMA_SCHOOL_URL
    OLLAMA_VISION_MODEL = OLLAMA_SCHOOL_VISION_MODEL
    OLLAMA_TEXT_MODEL = OLLAMA_SCHOOL_TEXT_MODEL

# Legacy compatibility
OLLAMA_MODEL = OLLAMA_VISION_MODEL

# =============================================================================
# OTHER AI APIs (fallback options)
# =============================================================================
# OpenRouter (paid, many models)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")

# Anthropic direct (paid)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# OpenAI / Codex (for future use)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Whisper configuration
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # tiny, base, small, medium, large-v3

# =============================================================================
# CLAUDE CODE CONFIGURATION
# =============================================================================
# AI_MODE: "observe" (Telegram only) or "execute" (run desktop actions)
AI_MODE = os.getenv("AI_MODE", "execute")
CLAUDE_WORKING_DIR = os.getenv("CLAUDE_WORKING_DIR", os.path.expanduser("~/Projects/demo"))
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "120"))


def get_whisper_model():
    """Lazy load Whisper model on first use."""
    global whisper_model
    if whisper_model is None:
        logger.info(f"Loading Whisper model: {WHISPER_MODEL_SIZE}")
        from faster_whisper import WhisperModel
        # Use CPU by default, change to "cuda" for GPU
        whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded successfully")
    return whisper_model


def transcribe_audio(audio_path: str) -> str:
    """Transcribe audio file using Whisper."""
    model = get_whisper_model()
    segments, info = model.transcribe(audio_path, beam_size=5)

    # Combine all segments into one string
    transcription = " ".join([segment.text.strip() for segment in segments])
    logger.info(f"Transcribed ({info.language}, {info.duration:.1f}s): {transcription[:100]}...")
    return transcription


async def analyze_text_with_ollama(text: str, context: str = "") -> str:
    """Send text to Ollama for understanding and response. Optimized for concise output."""
    prompt = f"{SYSTEM_PROMPT}\n\n{text}"
    if context:
        prompt = f"{context}\n\n{prompt}"

    # Use configured text model (with fallbacks)
    models_to_try = [OLLAMA_TEXT_MODEL, "mistral:latest", "llama3.2:latest", "llama3.2"]

    async with httpx.AsyncClient() as client:
        for model in models_to_try:
            try:
                logger.info(f"Trying Ollama model: {model}")
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 80,  # ~60 words max
                            "temperature": 0.3,  # Focused
                            "repeat_penalty": 1.15,  # Prevent rambling
                            "stop": ["\n\n", "Additionally", "Furthermore", "However,"]
                        }
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    result = response.json()
                    ai_response = result.get("response", "No response from Ollama")
                    logger.info(f"Ollama ({model}) response: {ai_response[:100]}...")
                    return ai_response
                else:
                    logger.warning(f"Ollama model {model} failed: {response.status_code} - {response.text[:200]}")
                    continue
            except Exception as e:
                logger.warning(f"Ollama model {model} exception: {e}")
                continue

        # All models failed
        logger.error(f"All Ollama models failed. Is VPN connected? Base URL: {OLLAMA_BASE_URL}")
        return f"[Ollama unavailable - transcription was: {text}]"

# System prompt for OpenCharm assistant - ULTRA CONCISE
# This prompt is optimized for wearable use: quick, actionable, no fluff
SYSTEM_PROMPT = """You are a wearable AI assistant. Respond in 1-2 sentences MAX.

Rules:
- ONE actionable point per response
- No greetings, no "I see", no "Based on the image"
- Use imperative verbs: "Try...", "Check...", "Add..."
- Skip explanations unless asked

You're a helpful coworker giving quick advice, not writing an essay."""

# Alternative prompts for different contexts (can be used via prompt parameter)
PROMPTS = {
    "quick": "One sentence. What should I do?",
    "detail": "Give me 2-3 specific observations.",
    "debug": "What's wrong here? One sentence.",
}


# =============================================================================
# OPENAI API FUNCTIONS (for future Codex/GPT-4 integration)
# =============================================================================

async def analyze_image_with_openai(image_b64: str, prompt: str = "What do you see?") -> str:
    """Analyze image using OpenAI GPT-4 Vision. Optimized for concise wearable responses."""
    if not OPENAI_API_KEY:
        return "[OpenAI not configured - add OPENAI_API_KEY to .env]"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",  # Fast and cheap for wearable
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}",
                                        "detail": "low"  # Faster, cheaper (765 vs 1105 tokens)
                                    }
                                }
                            ]
                        }
                    ],
                    "max_tokens": 100,  # Force brevity (was 500)
                    "temperature": 0.3,  # More focused, less rambling
                    "presence_penalty": 0.3,  # Discourage repetition
                },
                timeout=30.0  # Faster timeout for wearable responsiveness
            )

            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"OpenAI error: {response.status_code} - {response.text}")
                return f"OpenAI error: {response.status_code}"
        except Exception as e:
            logger.error(f"OpenAI exception: {e}")
            return f"OpenAI error: {e}"


async def analyze_text_with_openai(text: str) -> str:
    """Analyze text using OpenAI GPT-4. Optimized for concise wearable responses."""
    if not OPENAI_API_KEY:
        return "[OpenAI not configured - add OPENAI_API_KEY to .env]"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4o-mini",  # Fast and cheap
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text}  # Direct, no prefix
                    ],
                    "max_tokens": 100,  # Force brevity (was 500)
                    "temperature": 0.3,  # More focused
                    "presence_penalty": 0.3,  # Discourage repetition
                },
                timeout=15.0  # Fast timeout for wearable
            )

            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"OpenAI error: {response.status_code} - {response.text}")
                return f"OpenAI error: {response.status_code}"
        except Exception as e:
            logger.error(f"OpenAI exception: {e}")
            return f"OpenAI error: {e}"


async def analyze_text_with_anthropic(text: str) -> str:
    """Analyze text using Anthropic Claude. Optimized for concise wearable responses."""
    if not ANTHROPIC_API_KEY:
        return "[Anthropic not configured - add ANTHROPIC_API_KEY to .env]"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 100,
                    "system": SYSTEM_PROMPT,
                    "messages": [
                        {"role": "user", "content": text}
                    ]
                },
                timeout=15.0
            )

            if response.status_code == 200:
                result = response.json()
                return result["content"][0]["text"]
            else:
                logger.error(f"Anthropic error: {response.status_code} - {response.text}")
                return f"Anthropic error: {response.status_code}"
        except Exception as e:
            logger.error(f"Anthropic exception: {e}")
            return f"Anthropic error: {e}"


async def analyze_text(text: str) -> str:
    """Unified text analysis using configured AI_BACKEND."""
    if AI_BACKEND == "anthropic" and ANTHROPIC_API_KEY:
        logger.info("Using Anthropic Claude for text analysis")
        return await analyze_text_with_anthropic(text)
    elif AI_BACKEND == "openai" and OPENAI_API_KEY:
        logger.info("Using OpenAI for text analysis")
        return await analyze_text_with_openai(text)
    else:
        # Use Ollama (local or school)
        return await analyze_text_with_ollama(text)


# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# BACKGROUND TASK MANAGEMENT (for graceful shutdown)
# =============================================================================
background_tasks: set = set()


def track_background_task(task: asyncio.Task):
    """Track a background task for cleanup on shutdown."""
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern lifespan handler for startup and shutdown."""
    # STARTUP
    logger.info("=" * 60)
    logger.info("OPENCHARM RELAY SERVER")
    logger.info("=" * 60)
    logger.info(f"AI Backend: {AI_BACKEND.upper()}")
    logger.info("-" * 60)

    if AI_BACKEND == "ollama_local":
        logger.info(f"Ollama URL: {OLLAMA_BASE_URL}")
        logger.info(f"Vision Model: {OLLAMA_VISION_MODEL}")
        logger.info(f"Text Model: {OLLAMA_TEXT_MODEL}")
        logger.info("Make sure 'ollama serve' is running!")
    elif AI_BACKEND == "ollama_school":
        logger.info(f"Ollama URL: {OLLAMA_BASE_URL}")
        logger.info(f"Vision Model: {OLLAMA_VISION_MODEL}")
        logger.info(f"Text Model: {OLLAMA_TEXT_MODEL}")
        logger.info("Make sure VPN is connected!")
    elif AI_BACKEND == "openai":
        logger.info("Using OpenAI API (GPT-4o)")
        if OPENAI_API_KEY:
            logger.info("OpenAI API key: configured")
        else:
            logger.warning("OpenAI API key: MISSING! Add OPENAI_API_KEY to .env")
    elif AI_BACKEND == "anthropic":
        logger.info("Using Anthropic Claude API")
        if ANTHROPIC_API_KEY:
            logger.info("Anthropic API key: configured")
        else:
            logger.warning("Anthropic API key: MISSING! Add ANTHROPIC_API_KEY to .env")

    logger.info("=" * 60)

    yield  # Server runs here

    # SHUTDOWN
    logger.info("Shutting down - cancelling background tasks...")
    for task in list(background_tasks):
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    background_tasks.clear()
    logger.info("Shutdown complete")


# FastAPI app with lifespan handler
app = FastAPI(
    title="OpenCharm Relay",
    description="Forwards bracelet captures to Telegram",
    lifespan=lifespan
)

# Track last capture for debugging
last_capture = {
    "type": None,
    "timestamp": None,
    "size": 0,
    "status": None
}

# =============================================================================
# SESSION BUFFER (for RECORD/SNAP/SEND workflow)
# =============================================================================
# Stores audio and images until SEND is triggered
# Content is buffered on server, processed together when SEND is called
session_buffer = {
    "audio_files": [],      # List of (audio_data, transcription) tuples
    "image_files": [],      # List of (image_data, timestamp) tuples
    "created_at": None,     # When session started
    "last_activity": None,  # Last content added
}

def clear_session():
    """Clear the session buffer."""
    session_buffer["audio_files"] = []
    session_buffer["image_files"] = []
    session_buffer["created_at"] = None
    session_buffer["last_activity"] = None
    logger.info("Session buffer cleared")

def get_session_summary() -> dict:
    """Get summary of current session."""
    return {
        "audio_count": len(session_buffer["audio_files"]),
        "image_count": len(session_buffer["image_files"]),
        "created_at": session_buffer["created_at"],
        "last_activity": session_buffer["last_activity"],
    }


async def analyze_image_with_claude(image_data: bytes, prompt: str = "What do you see? Provide brief, actionable feedback.") -> str:
    """Send image to vision AI and return analysis. Uses configured AI_BACKEND."""

    # Encode image to base64
    image_b64 = base64.b64encode(image_data).decode("utf-8")

    # Use configured backend
    if AI_BACKEND == "anthropic" and ANTHROPIC_API_KEY:
        logger.info("Using Anthropic Claude for image analysis")
        return await _call_anthropic(image_b64, prompt)

    if AI_BACKEND == "openai" and OPENAI_API_KEY:
        logger.info("Using OpenAI for image analysis")
        return await analyze_image_with_openai(image_b64, prompt)

    # Try Ollama (local or school)
    if AI_BACKEND.startswith("ollama"):
        try:
            result = await _call_ollama(image_b64, prompt)
            if result and not result.startswith("Ollama error"):
                return result
            logger.warning(f"Ollama failed, trying fallback: {result}")
        except Exception as e:
            logger.warning(f"Ollama unavailable: {e}")

    # Fallbacks (paid APIs)
    if OPENAI_API_KEY:
        logger.info("Falling back to OpenAI")
        return await analyze_image_with_openai(image_b64, prompt)

    if OPENROUTER_API_KEY:
        return await _call_openrouter(image_b64, prompt)

    if ANTHROPIC_API_KEY:
        return await _call_anthropic(image_b64, prompt)

    logger.warning("No AI API available")
    return "Image received but no AI backend available. Set AI_BACKEND or add API keys to .env"


async def _call_ollama(image_b64: str, prompt: str) -> str:
    """Call Ollama API with LLaVA for vision. Optimized for concise output."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
                "images": [image_b64],
                "stream": False,
                "options": {
                    "num_predict": 100,  # ~75 words max
                    "temperature": 0.3,
                    "repeat_penalty": 1.15,
                    "stop": ["\n\n", "Additionally", "Furthermore"]
                }
            },
            timeout=60.0  # Reduced for better UX
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "No response from Ollama")
        else:
            logger.error(f"Ollama error: {response.status_code} - {response.text}")
            return f"Ollama error: {response.status_code}"


async def _call_openrouter(image_b64: str, prompt: str) -> str:
    """Call OpenRouter API with Claude Vision."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/FabriGu/OpenCharm",
                "X-Title": "OpenCharm"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 500
            },
            timeout=60.0
        )

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            logger.error(f"OpenRouter error: {response.status_code} - {response.text}")
            return f"AI analysis failed: {response.status_code}"


async def _call_anthropic(image_b64: str, prompt: str) -> str:
    """Call Anthropic API directly with Claude Vision."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64
                                }
                            }
                        ]
                    }
                ]
            },
            timeout=60.0
        )

        if response.status_code == 200:
            result = response.json()
            return result["content"][0]["text"]
        else:
            logger.error(f"Anthropic error: {response.status_code} - {response.text}")
            return f"AI analysis failed: {response.status_code}"


async def send_telegram_text(text: str) -> bool:
    """Send a text message to Telegram."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown"
            },
            timeout=30.0
        )
        return response.status_code == 200


@app.get("/")
async def root():
    """Root endpoint with status."""
    return {
        "service": "OpenCharm Relay",
        "status": "running",
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "last_capture": last_capture
    }


@app.get("/health")
async def health():
    """Health check endpoint for bracelet connectivity test."""
    return {"status": "ok"}


async def _analyze_image_background(image_data: bytes, prompt: str):
    """Background task: Analyze image with AI and send result to Telegram."""
    try:
        logger.info(f"[Background] Starting image analysis...")
        analysis = await analyze_image_with_claude(image_data, prompt)
        logger.info(f"[Background] Analysis complete: {analysis[:100]}...")
        await send_telegram_text(analysis)
        logger.info("[Background] Analysis sent to Telegram")
        last_capture["status"] = "analyzed"
    except asyncio.CancelledError:
        logger.info("[Background] Image analysis task cancelled during shutdown")
        raise  # Re-raise to propagate cancellation
    except Exception as e:
        logger.error(f"[Background] Analysis failed: {e}")
        last_capture["status"] = f"error: {str(e)}"


@app.post("/capture/image")
async def capture_image(image: UploadFile = File(...), prompt: str = Form(None)):
    """
    Receive JPEG image from bracelet, send to Telegram, respond IMMEDIATELY.
    AI analysis happens in background (doesn't block ESP32).

    The bracelet sends: POST /capture/image with multipart/form-data
    Field name: 'image', content-type: image/jpeg
    Optional: 'prompt' for custom analysis request
    """
    import time

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured!")
        raise HTTPException(status_code=500, detail="Telegram not configured")

    try:
        # Read image data
        image_data = await image.read()
        logger.info(f"Received image: {len(image_data)} bytes")

        # Update tracking
        last_capture["type"] = "image"
        last_capture["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        last_capture["size"] = len(image_data)

        # Send image to Telegram first (so user sees what was captured)
        async with httpx.AsyncClient() as client:
            files = {"photo": ("capture.jpg", image_data, "image/jpeg")}
            data = {"chat_id": TELEGRAM_CHAT_ID}

            response = await client.post(
                f"{TELEGRAM_API_URL}/sendPhoto",
                files=files,
                data=data,
                timeout=30.0
            )

            if response.status_code != 200:
                logger.error(f"Telegram error: {response.text}")
                raise HTTPException(status_code=502, detail=f"Telegram error: {response.text}")

            message_id = response.json()['result']['message_id']
            logger.info(f"Image sent to Telegram: message_id={message_id}")

        # Start AI analysis in BACKGROUND (doesn't block response to ESP32)
        analysis_prompt = prompt or "What do you see? Provide brief, actionable feedback."
        task = asyncio.create_task(_analyze_image_background(image_data, analysis_prompt))
        track_background_task(task)  # Track for graceful shutdown
        logger.info("Analysis started in background - responding to ESP32 now")

        # RESPOND IMMEDIATELY (ESP32 gets response in ~1-2 seconds, not 10-20)
        last_capture["status"] = "processing"
        return {
            "status": "ok",
            "message_id": message_id,
            "message": "Image received, analysis in progress"
        }

    except httpx.TimeoutException:
        logger.error("Telegram timeout")
        last_capture["status"] = "timeout"
        raise HTTPException(status_code=504, detail="Telegram timeout")
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        last_capture["status"] = f"error: {str(e)}"
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/capture/audio")
async def capture_audio(audio: UploadFile = File(...)):
    """
    Receive WAV audio from bracelet, transcribe with Whisper, analyze with Ollama,
    send voice + AI response to Telegram.

    The bracelet sends: POST /capture/audio with multipart/form-data
    Field name: 'audio', content-type: audio/wav
    Audio format: 16kHz, 16-bit, mono PCM with WAV header
    """
    import time

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured!")
        raise HTTPException(status_code=500, detail="Telegram not configured")

    temp_wav = None
    temp_ogg = None

    try:
        # Read audio data
        audio_data = await audio.read()
        logger.info(f"Received audio: {len(audio_data)} bytes")

        # Update tracking
        last_capture["type"] = "audio"
        last_capture["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        last_capture["size"] = len(audio_data)

        # Save to temp WAV file
        temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav.write(audio_data)
        temp_wav.close()

        # Step 1: Transcribe audio with Whisper
        logger.info("Transcribing audio with Whisper...")
        transcription = transcribe_audio(temp_wav.name)

        if not transcription.strip():
            transcription = "(no speech detected)"

        # Convert WAV to OGG/Opus using ffmpeg for Telegram voice
        temp_ogg = temp_wav.name.replace(".wav", ".ogg")

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", temp_wav.name,
            "-c:a", "libopus",
            "-b:a", "128k",
            "-vbr", "on",
            "-application", "voip",
            temp_ogg
        ]

        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr.decode()}")
            raise HTTPException(status_code=500, detail="Audio conversion failed")

        logger.info(f"Converted to OGG: {os.path.getsize(temp_ogg)} bytes")

        # Step 2: Send voice message to Telegram (so user sees what was recorded)
        async with httpx.AsyncClient() as client:
            with open(temp_ogg, "rb") as ogg_file:
                files = {"voice": ("voice.ogg", ogg_file.read(), "audio/ogg")}
                data = {"chat_id": TELEGRAM_CHAT_ID}

                response = await client.post(
                    f"{TELEGRAM_API_URL}/sendVoice",
                    files=files,
                    data=data,
                    timeout=30.0
                )

                if response.status_code != 200:
                    logger.error(f"Telegram error: {response.text}")
                    raise HTTPException(status_code=502, detail=f"Telegram error: {response.text}")

                voice_message_id = response.json()['result']['message_id']
                logger.info(f"Voice sent to Telegram: message_id={voice_message_id}")

        # Step 3: Send transcription to Telegram
        await send_telegram_text(f"*You said:* {transcription}")

        # Step 4: Analyze with Ollama in BACKGROUND (doesn't block ESP32)
        async def _analyze_audio_background(text: str):
            try:
                logger.info("[Background] Analyzing transcription with Ollama...")
                ai_response = await analyze_text(text)
                await send_telegram_text(ai_response)
                logger.info("[Background] AI response sent to Telegram")
                last_capture["status"] = "transcribed_and_analyzed"
            except asyncio.CancelledError:
                logger.info("[Background] Audio analysis task cancelled during shutdown")
                raise  # Re-raise to propagate cancellation
            except Exception as e:
                logger.error(f"[Background] Audio analysis failed: {e}")

        task = asyncio.create_task(_analyze_audio_background(transcription))
        track_background_task(task)  # Track for graceful shutdown
        logger.info("Analysis started in background - responding to ESP32 now")

        # RESPOND IMMEDIATELY (ESP32 gets response in ~3-5 seconds, not 10+)
        last_capture["status"] = "transcribed"
        return {
            "status": "ok",
            "message_id": voice_message_id,
            "transcription": transcription,
            "message": "Audio received, AI response in progress"
        }

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timeout")
        last_capture["status"] = "ffmpeg_timeout"
        raise HTTPException(status_code=500, detail="Audio conversion timeout")
    except httpx.TimeoutException:
        logger.error("Telegram timeout")
        last_capture["status"] = "telegram_timeout"
        raise HTTPException(status_code=504, detail="Telegram timeout")
    except Exception as e:
        logger.error(f"Error processing audio: {e}")
        last_capture["status"] = f"error: {str(e)}"
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup temp files
        if temp_wav and os.path.exists(temp_wav.name):
            os.remove(temp_wav.name)
        if temp_ogg and os.path.exists(temp_ogg):
            os.remove(temp_ogg)


@app.post("/capture/image_with_text")
async def capture_image_with_text(
    image: UploadFile = File(...),
    text: str = Form(None)
):
    """
    Receive image with optional transcribed text (for when relay does transcription).
    Sends image with caption to Telegram.
    """
    import time

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise HTTPException(status_code=500, detail="Telegram not configured")

    try:
        image_data = await image.read()
        logger.info(f"Received image with text: {len(image_data)} bytes, text='{text}'")

        last_capture["type"] = "image_with_text"
        last_capture["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        last_capture["size"] = len(image_data)

        async with httpx.AsyncClient() as client:
            files = {"photo": ("capture.jpg", image_data, "image/jpeg")}
            data = {"chat_id": TELEGRAM_CHAT_ID}

            if text:
                # Add text as caption
                data["caption"] = f"Voice command: {text}"

            response = await client.post(
                f"{TELEGRAM_API_URL}/sendPhoto",
                files=files,
                data=data,
                timeout=30.0
            )

            if response.status_code == 200:
                result = response.json()
                last_capture["status"] = "sent"
                return {"status": "ok", "message_id": result["result"]["message_id"]}
            else:
                raise HTTPException(status_code=502, detail=f"Telegram error: {response.text}")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# SESSION ENDPOINTS (RECORD/STOP/SNAP/SEND workflow)
# =============================================================================
# These endpoints buffer content without immediate AI processing.
# Content is processed together when /session/process is called.

@app.get("/session/status")
async def session_status():
    """Get current session buffer status."""
    return {
        "status": "ok",
        "session": get_session_summary()
    }


@app.post("/session/audio")
async def session_add_audio(audio: UploadFile = File(...)):
    """
    Add audio to session buffer (STOP keyword).
    Audio is transcribed and stored, but NOT sent to AI yet.
    """
    import time

    temp_wav = None

    try:
        audio_data = await audio.read()
        logger.info(f"[Session] Received audio: {len(audio_data)} bytes")

        # Initialize session if needed
        if session_buffer["created_at"] is None:
            session_buffer["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Save to temp file for transcription
        temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        temp_wav.write(audio_data)
        temp_wav.close()

        # Transcribe with Whisper
        logger.info("[Session] Transcribing audio...")
        transcription = transcribe_audio(temp_wav.name)
        if not transcription.strip():
            transcription = "(no speech detected)"

        # Store in session buffer
        session_buffer["audio_files"].append({
            "data": audio_data,
            "transcription": transcription,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "size": len(audio_data)
        })
        session_buffer["last_activity"] = time.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"[Session] Audio added: '{transcription[:50]}...'")
        logger.info(f"[Session] Buffer: {len(session_buffer['audio_files'])} audio, {len(session_buffer['image_files'])} images")

        # Send confirmation to Telegram (but NOT AI analysis)
        await send_telegram_text(f"*[Buffered]* {transcription}")

        return {
            "status": "ok",
            "transcription": transcription,
            "session": get_session_summary(),
            "message": "Audio buffered - say SEND to process"
        }

    except Exception as e:
        logger.error(f"[Session] Audio error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_wav and os.path.exists(temp_wav.name):
            os.remove(temp_wav.name)


@app.post("/session/image")
async def session_add_image(image: UploadFile = File(...)):
    """
    Add image to session buffer (SNAP keyword).
    Image is stored but NOT analyzed by AI yet.
    """
    import time

    try:
        image_data = await image.read()
        logger.info(f"[Session] Received image: {len(image_data)} bytes")

        # Initialize session if needed
        if session_buffer["created_at"] is None:
            session_buffer["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # Store in session buffer
        session_buffer["image_files"].append({
            "data": image_data,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "size": len(image_data)
        })
        session_buffer["last_activity"] = time.strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"[Session] Image added")
        logger.info(f"[Session] Buffer: {len(session_buffer['audio_files'])} audio, {len(session_buffer['image_files'])} images")

        # Send image to Telegram (but NOT AI analysis)
        async with httpx.AsyncClient() as client:
            files = {"photo": ("capture.jpg", image_data, "image/jpeg")}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": f"[Buffered] Image {len(session_buffer['image_files'])} - say SEND to process"
            }
            await client.post(f"{TELEGRAM_API_URL}/sendPhoto", files=files, data=data, timeout=30.0)

        return {
            "status": "ok",
            "session": get_session_summary(),
            "message": "Image buffered - say SEND to process"
        }

    except Exception as e:
        logger.error(f"[Session] Image error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session/process")
async def session_process():
    """
    Process all buffered content with AI (SEND keyword).
    Combines all audio transcriptions and images, sends to AI for analysis.
    """
    import time

    audio_count = len(session_buffer["audio_files"])
    image_count = len(session_buffer["image_files"])

    if audio_count == 0 and image_count == 0:
        return {
            "status": "empty",
            "message": "No content in session buffer"
        }

    logger.info(f"[Session] Processing: {audio_count} audio, {image_count} images")

    try:
        # Combine all transcriptions
        all_transcriptions = []
        for i, audio in enumerate(session_buffer["audio_files"]):
            all_transcriptions.append(f"[Audio {i+1}]: {audio['transcription']}")

        combined_text = "\n".join(all_transcriptions) if all_transcriptions else ""

        # Prepare analysis prompt
        if image_count > 0 and audio_count > 0:
            # Multi-modal: images + voice
            prompt = f"""The user has provided {image_count} image(s) and {audio_count} voice message(s).

Voice messages:
{combined_text}

Please analyze the images in the context of what the user said. Provide helpful, actionable feedback."""

        elif image_count > 0:
            # Images only
            prompt = "Analyze this image and provide brief, actionable feedback."

        elif audio_count > 0:
            # Voice only
            prompt = combined_text

        else:
            prompt = "What do you see?"

        # Send processing notification
        await send_telegram_text(f"*Processing {audio_count} audio + {image_count} images...*")

        # Process with AI
        if image_count > 0:
            # Use the most recent image for analysis (or first if we want different behavior)
            # For multi-image, we'd need to combine them or analyze separately
            latest_image = session_buffer["image_files"][-1]["data"]
            analysis = await analyze_image_with_claude(latest_image, prompt)

            # If multiple images, analyze each
            if image_count > 1:
                analysis = f"*[Analyzing {image_count} images]*\n\n{analysis}"
                # Could add logic to analyze each image separately if needed

        else:
            # Text only analysis
            analysis = await analyze_text(combined_text)

        # Send final analysis to Telegram
        await send_telegram_text(analysis)

        # Clear the session buffer
        processed_summary = get_session_summary()
        clear_session()

        logger.info(f"[Session] Processing complete, buffer cleared")

        return {
            "status": "ok",
            "processed": processed_summary,
            "message": "Session processed and cleared"
        }

    except Exception as e:
        logger.error(f"[Session] Processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session/clear")
async def session_clear():
    """
    Clear the session buffer without processing.
    Use this to cancel/reset the current session.
    """
    summary = get_session_summary()
    clear_session()

    await send_telegram_text("*[Session cleared]*")

    return {
        "status": "ok",
        "cleared": summary,
        "message": "Session buffer cleared"
    }


# =============================================================================
# CLAUDE CODE EXECUTION ENDPOINTS
# =============================================================================
# These endpoints execute desktop actions via Claude Code CLI

@app.post("/execute/action")
async def execute_action(
    command: str = Form(...),
    image: UploadFile = File(None),
):
    """
    Execute a desktop action via Claude Code.

    This is a direct execution endpoint (bypasses session buffering).
    Use for quick commands like "create folder test".

    Args:
        command: Natural language command (e.g., "create folder test")
        image: Optional image for UI mockup analysis

    Returns:
        Action result with status and output
    """
    import time

    logger.info(f"[Execute] Received command: {command[:100]}...")

    try:
        # Initialize executor
        executor = get_executor(working_dir=CLAUDE_WORKING_DIR, timeout=CLAUDE_TIMEOUT)
        parser = get_parser()

        # Handle image if provided
        image_data = None
        image_analysis = None
        if image:
            image_data = await image.read()
            logger.info(f"[Execute] Image provided: {len(image_data)} bytes")

            # Analyze image with vision API for context
            if ANTHROPIC_API_KEY:
                image_analysis = await _analyze_image_for_action(image_data, command)
                logger.info(f"[Execute] Image analysis: {image_analysis[:200]}...")

        # Parse intent
        has_images = image_data is not None
        intent = parser.parse(command, has_images=has_images)
        logger.info(f"[Execute] Parsed intent: {intent.category.value} (confidence: {intent.confidence})")

        # Build execution prompt
        prompt = parser.build_prompt(intent)
        if image_analysis:
            prompt = f"UI Analysis:\n{image_analysis}\n\nTask: {prompt}"

        # Execute via Claude Code
        logger.info(f"[Execute] Executing: {prompt[:100]}...")
        result = await executor.execute(prompt)

        # Send notification to Telegram
        if result.success:
            telegram_msg = f"*[Action Completed]*\n\n{result.output[:500] if result.output else 'Success'}"
            if result.files_modified:
                telegram_msg += f"\n\n*Files:* {', '.join(result.files_modified)}"
        else:
            telegram_msg = f"*[Action Failed]*\n\n{result.error or 'Unknown error'}"

        await send_telegram_text(telegram_msg)

        return {
            "status": "success" if result.success else "failed",
            "action_id": result.action_id,
            "intent": intent.to_dict(),
            "result": result.to_dict(),
        }

    except Exception as e:
        logger.exception(f"[Execute] Error: {e}")
        await send_telegram_text(f"*[Action Error]*\n\n{str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/session/execute")
async def session_execute():
    """
    Execute actions from session buffer via Claude Code.

    This is the action-mode version of /session/process.
    Uses buffered audio transcriptions and images to execute desktop actions.

    Workflow:
    1. RECORD → speak command → STOP (buffers audio)
    2. CAPTURE (optional, buffers image)
    3. POST → triggers this endpoint → executes action
    """
    import time

    audio_count = len(session_buffer["audio_files"])
    image_count = len(session_buffer["image_files"])

    if audio_count == 0 and image_count == 0:
        return {
            "status": "empty",
            "message": "No content in session buffer"
        }

    logger.info(f"[Session Execute] Processing: {audio_count} audio, {image_count} images")

    try:
        # Initialize modules
        executor = get_executor(working_dir=CLAUDE_WORKING_DIR, timeout=CLAUDE_TIMEOUT)
        parser = get_parser()

        # Combine all transcriptions
        all_transcriptions = []
        for audio in session_buffer["audio_files"]:
            all_transcriptions.append(audio["transcription"])
        combined_text = " ".join(all_transcriptions).strip()

        logger.info(f"[Session Execute] Combined transcription: {combined_text}")

        # Analyze images if present
        image_analysis = None
        if image_count > 0 and ANTHROPIC_API_KEY:
            latest_image = session_buffer["image_files"][-1]["data"]
            image_analysis = await _analyze_image_for_action(latest_image, combined_text)
            logger.info(f"[Session Execute] Image analysis: {image_analysis[:200]}...")

        # Parse intent
        intent = parser.parse(combined_text, has_images=image_count > 0)
        logger.info(f"[Session Execute] Parsed intent: {intent.category.value}")

        # Build execution prompt
        prompt = parser.build_prompt(intent)
        if image_analysis:
            prompt = f"UI Analysis from mockup:\n{image_analysis}\n\nTask: {prompt}"

        # Send processing notification
        await send_telegram_text(f"*[Executing]*\n{combined_text[:100]}...")

        # Execute via Claude Code
        result = await executor.execute(prompt)

        # Send result to Telegram
        if result.success:
            telegram_msg = f"*[Action Completed]*\n\n{result.output[:500] if result.output else 'Success'}"
            if result.files_modified:
                telegram_msg += f"\n\n*Files:* {', '.join(result.files_modified)}"
        else:
            telegram_msg = f"*[Action Failed]*\n\n{result.error or 'Unknown error'}"

        await send_telegram_text(telegram_msg)

        # Clear the session buffer
        processed_summary = get_session_summary()
        clear_session()

        return {
            "status": "success" if result.success else "failed",
            "action_id": result.action_id,
            "transcription": combined_text,
            "intent": intent.to_dict(),
            "result": result.to_dict(),
            "processed": processed_summary,
        }

    except Exception as e:
        logger.exception(f"[Session Execute] Error: {e}")
        await send_telegram_text(f"*[Execution Error]*\n\n{str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


async def _analyze_image_for_action(image_data: bytes, context: str) -> str:
    """
    Analyze an image for action execution using Anthropic Vision API.

    Args:
        image_data: JPEG image bytes
        context: Voice command or context for analysis

    Returns:
        Structured analysis of the image for code generation
    """
    if not ANTHROPIC_API_KEY:
        return "[Image analysis unavailable - no API key]"

    image_b64 = base64.b64encode(image_data).decode("utf-8")

    prompt = f"""Analyze this UI mockup/design image for implementation.

User's voice command: "{context}"

Provide a structured analysis including:
1. Overall layout description (sections, columns, etc.)
2. UI elements identified (buttons, inputs, cards, etc.)
3. Color scheme observed
4. Typography notes
5. Specific HTML/CSS structure recommendations

Keep the analysis concise and actionable for code generation."""

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-5-20250929",
                    "max_tokens": 1000,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_b64
                                }
                            },
                            {"type": "text", "text": prompt}
                        ]
                    }]
                },
                timeout=30.0
            )

            if response.status_code == 200:
                result = response.json()
                return result["content"][0]["text"]
            else:
                logger.error(f"Anthropic error: {response.status_code} - {response.text}")
                return f"[Image analysis failed: {response.status_code}]"

        except Exception as e:
            logger.error(f"Image analysis exception: {e}")
            return f"[Image analysis error: {e}]"


@app.get("/execute/status/{action_id}")
async def get_action_status(action_id: str):
    """Get the status of a previous action."""
    executor = get_executor(working_dir=CLAUDE_WORKING_DIR, timeout=CLAUDE_TIMEOUT)
    result = executor.get_action(action_id)

    if not result:
        raise HTTPException(status_code=404, detail="Action not found")

    return {
        "status": "success",
        "action": result.to_dict(),
    }


@app.get("/execute/history")
async def get_action_history(limit: int = 10):
    """Get recent action history."""
    executor = get_executor(working_dir=CLAUDE_WORKING_DIR, timeout=CLAUDE_TIMEOUT)
    actions = executor.get_recent_actions(limit)

    return {
        "status": "success",
        "count": len(actions),
        "actions": [a.to_dict() for a in actions],
    }


if __name__ == "__main__":
    import uvicorn

    # Check configuration
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set! Set it in .env file")
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set! Set it in .env file")

    # Get local IP for bracelet configuration
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        local_ip = "unknown"

    logger.info(f"Starting relay server...")
    logger.info(f"Local IP: {local_ip}")
    logger.info(f"Bracelet should POST to: http://{local_ip}:8080/capture/image")

    uvicorn.run(app, host="0.0.0.0", port=8080, timeout_graceful_shutdown=10)
