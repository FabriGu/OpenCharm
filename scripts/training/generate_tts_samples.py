#!/usr/bin/env python3
"""
Generate synthetic voice samples for keyword spotting using Google Cloud TTS.

This script generates diverse voice samples for the keywords:
RECORD, STOP, CAPTURE, POST

Each keyword gets samples with variations in:
- Voice (male/female, different accents)
- Speed (0.75 to 1.25 for post, 0.85-1.15 for others)
- Pitch (-5 to +5 semitones)

Usage:
    python generate_tts_samples.py

Output:
    ./samples/
        record/
        stop/
        capture/
        post/
        noise/
        unknown/
"""

import os
import sys
import random
import itertools
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables FIRST
load_dotenv()

# Check for Google Cloud credentials after loading .env
if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
    print("WARNING: GOOGLE_APPLICATION_CREDENTIALS not set.")
    print("Set it in .env or export it before running this script.")
    print("Example: export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json")

try:
    from google.cloud import texttospeech
    GOOGLE_TTS_AVAILABLE = True
except ImportError:
    GOOGLE_TTS_AVAILABLE = False
    print("Google Cloud TTS not installed. Run: pip install google-cloud-texttospeech")

from pydub import AudioSegment
from pydub.generators import WhiteNoise
from pydub.effects import normalize
import requests

# Configuration
# Use phonetic spelling to force correct pronunciation
# "record" as verb (re-CORD) not noun (REH-cord)
KEYWORDS_PHONETIC = {
    "record": "rec-ord",   # Forces stress on second syllable (verb)
    "stop": "stop",
    "capture": "capture",
    "post": "post",
}
KEYWORDS = list(KEYWORDS_PHONETIC.keys())

# Per-keyword sample configuration (post needs more samples due to difficulty)
SAMPLES_CONFIG = {
    "record": 300,
    "stop": 300,
    "capture": 300,
    "post": 400,  # Extra samples for hard-to-detect keyword
}
DEFAULT_SAMPLES_PER_KEYWORD = 300

# Output directory
OUTPUT_DIR = Path(__file__).parent / "samples"
SAMPLE_RATE = 16000  # Hz (required by Edge Impulse for keyword spotting)
SAMPLE_DURATION_MS = 1000  # 1 second samples

# Per-keyword trim configuration (post needs minimal trimming to preserve T sound)
TRIM_CONFIG = {
    "record": 50,
    "stop": 50,
    "capture": 40,  # Capture needs less trimming
    "post": 10,     # Minimal trimming for post to preserve final T
}
DEFAULT_TRIM_MS = 50

# Per-keyword speed ranges (post needs wider range for training robustness)
SPEED_CONFIG = {
    "record": [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15],
    "stop": [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15],
    "capture": [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15],
    "post": [0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25],  # Wider range
}
DEFAULT_SPEEDS = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15]

# Pitch variations (semitones)
PITCHES = [-4, -2, 0, 2, 4]

# Per-keyword volume boost (post gets +3dB for clearer T consonant)
VOLUME_BOOST_DB = {
    "record": 0,
    "stop": 0,
    "capture": 0,
    "post": 3,  # Boost post to make T more audible
}

# TTS voice configurations for diversity (expanded)
VOICE_CONFIGS = [
    # US English voices
    {"language_code": "en-US", "name": "en-US-Wavenet-A", "gender": "MALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-B", "gender": "MALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-C", "gender": "FEMALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-D", "gender": "MALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-E", "gender": "FEMALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-F", "gender": "FEMALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-G", "gender": "FEMALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-H", "gender": "FEMALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-I", "gender": "MALE"},
    {"language_code": "en-US", "name": "en-US-Wavenet-J", "gender": "MALE"},
    # British English voices
    {"language_code": "en-GB", "name": "en-GB-Wavenet-A", "gender": "FEMALE"},
    {"language_code": "en-GB", "name": "en-GB-Wavenet-B", "gender": "MALE"},
    {"language_code": "en-GB", "name": "en-GB-Wavenet-C", "gender": "FEMALE"},
    {"language_code": "en-GB", "name": "en-GB-Wavenet-D", "gender": "MALE"},
    {"language_code": "en-GB", "name": "en-GB-Wavenet-F", "gender": "FEMALE"},
    # Australian English voices
    {"language_code": "en-AU", "name": "en-AU-Wavenet-A", "gender": "FEMALE"},
    {"language_code": "en-AU", "name": "en-AU-Wavenet-B", "gender": "MALE"},
    {"language_code": "en-AU", "name": "en-AU-Wavenet-C", "gender": "FEMALE"},
    {"language_code": "en-AU", "name": "en-AU-Wavenet-D", "gender": "MALE"},
    # Indian English voices
    {"language_code": "en-IN", "name": "en-IN-Wavenet-A", "gender": "FEMALE"},
    {"language_code": "en-IN", "name": "en-IN-Wavenet-B", "gender": "MALE"},
    {"language_code": "en-IN", "name": "en-IN-Wavenet-C", "gender": "MALE"},
    {"language_code": "en-IN", "name": "en-IN-Wavenet-D", "gender": "FEMALE"},
]

# =============================================================================
# COMPREHENSIVE UNKNOWN WORDS LIST (165+ words organized by phonetic similarity)
# =============================================================================

# Words phonetically similar to "record" (re-CORD pattern: rɪˈkɔːd)
UNKNOWN_RECORD_SIMILAR = [
    # re- prefix words
    "reword", "reboard", "reward", "regard", "resort", "report", "retort",
    "recourse", "recall", "regret", "recruit", "reform", "research", "reject",
    "reflect", "refuse", "request", "require", "resolve", "respond", "restore",
    "result", "return", "reveal", "reverse", "review", "revolt", "reborn",
    # -cord/-ord ending
    "accord", "discord", "cord", "lord", "ford", "sword", "board", "hoard",
    "aboard", "toward", "afford", "scored", "stored", "ignored", "explored",
    # Similar vowel patterns
    "decor", "encore", "restore", "before", "deplore", "implore", "adore",
]

# Words phonetically similar to "stop" (stɒp pattern)
UNKNOWN_STOP_SIMILAR = [
    # st- onset
    "stock", "stomp", "stone", "store", "storm", "story", "stove", "stump",
    "stuck", "stuff", "stunt", "start", "state", "steam", "steel", "steep",
    "steer", "stick", "still", "sting", "stink", "stint", "stitch",
    # -op ending
    "shop", "chop", "crop", "drop", "flop", "hop", "mop", "pop", "prop",
    "top", "cop", "swap", "slop", "plop",
    # Similar patterns
    "step", "stamp", "staph", "staff", "stab", "slab", "slap", "snap", "scrap",
]

# Words phonetically similar to "capture" (ˈkæptʃər pattern)
UNKNOWN_CAPTURE_SIMILAR = [
    # capt- onset
    "captain", "caption", "captive", "capsule", "capable", "capital",
    # -ture ending
    "culture", "creature", "feature", "fixture", "fracture", "gesture",
    "lecture", "mixture", "nature", "nurture", "pasture", "picture",
    "posture", "puncture", "rapture", "rupture", "scripture", "sculpture",
    "seizure", "stature", "structure", "texture", "torture", "venture",
    "vulture", "moisture", "departure", "adventure", "furniture", "signature",
    # Similar patterns
    "chapter", "catcher", "atcher", "matcher", "hatcher", "ratchet", "catch",
]

# Words phonetically similar to "post" (pəʊst pattern)
UNKNOWN_POST_SIMILAR = [
    # -ost ending (rhyming)
    "most", "host", "ghost", "coast", "roast", "toast", "boast", "cost",
    "lost", "frost", "crossed", "tossed", "bossed", "glossed",
    # p- onset with similar vowel
    "pose", "poke", "pole", "poll", "poet", "point", "poise", "poach",
    "pope", "port", "porch", "pork", "pose", "posse", "postal", "poster",
    # Similar patterns
    "boost", "roost", "moose", "loose", "goose", "noose", "choose",
    "past", "pest", "pist", "fist", "mist", "list", "twist", "wrist",
]

# Common conversational words (general unknown class)
UNKNOWN_COMMON = [
    # Greetings & responses
    "hello", "hi", "hey", "bye", "goodbye", "yes", "no", "yeah", "nope",
    "okay", "sure", "maybe", "please", "thanks", "sorry", "pardon",
    # Questions
    "what", "where", "when", "why", "how", "who", "which",
    # Directions & positions
    "up", "down", "left", "right", "here", "there", "near", "far",
    "forward", "backward", "above", "below", "inside", "outside",
    # Actions
    "go", "come", "move", "turn", "run", "walk", "jump", "sit", "stand",
    "wait", "hold", "push", "pull", "lift", "drop", "grab", "touch",
    "look", "see", "watch", "hear", "listen", "speak", "talk", "say",
    # Technology
    "phone", "call", "text", "email", "send", "receive", "download",
    "upload", "connect", "disconnect", "login", "logout", "search",
    "find", "save", "delete", "copy", "paste", "undo", "redo",
    # Media
    "play", "pause", "rewind", "skip", "next", "back", "volume",
    "mute", "unmute", "music", "video", "photo", "image", "sound",
    # Numbers (can be confused with keywords)
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "zero", "hundred", "thousand",
    # Adjectives
    "good", "bad", "great", "nice", "fine", "cool", "hot", "cold",
    "fast", "slow", "big", "small", "new", "old", "high", "low",
    # Time
    "now", "then", "later", "soon", "today", "tomorrow", "yesterday",
    "morning", "evening", "night", "time", "hour", "minute", "second",
]

# All unknown words combined
UNKNOWN_WORDS = (
    UNKNOWN_RECORD_SIMILAR +
    UNKNOWN_STOP_SIMILAR +
    UNKNOWN_CAPTURE_SIMILAR +
    UNKNOWN_POST_SIMILAR +
    UNKNOWN_COMMON
)

# Remove duplicates while preserving order
UNKNOWN_WORDS = list(dict.fromkeys(UNKNOWN_WORDS))

# Number of samples per unknown word (generates 600+ total unknown samples)
UNKNOWN_SAMPLES_PER_WORD = 4


def create_directories():
    """Create output directories for all classes."""
    for keyword in KEYWORDS + ["noise", "unknown"]:
        (OUTPUT_DIR / keyword).mkdir(parents=True, exist_ok=True)
    print(f"Created output directories in {OUTPUT_DIR}")


def generate_tts_sample(client, text: str, voice_config: dict, speed: float, pitch: float) -> bytes:
    """Generate a single TTS sample."""
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice = texttospeech.VoiceSelectionParams(
        language_code=voice_config["language_code"],
        name=voice_config["name"],
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE,
        speaking_rate=speed,
        pitch=pitch,
    )

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    return response.audio_content


def normalize_and_pad(
    audio_bytes: bytes,
    target_duration_ms: int = SAMPLE_DURATION_MS,
    keyword: str = None,
) -> AudioSegment:
    """
    Normalize audio to target duration with padding, trimming initial spike.

    Args:
        audio_bytes: Raw audio bytes from TTS
        target_duration_ms: Target duration in milliseconds
        keyword: Keyword being processed (for per-keyword settings)
    """
    # Load audio
    audio = AudioSegment(
        data=audio_bytes,
        sample_width=2,  # 16-bit
        frame_rate=SAMPLE_RATE,
        channels=1
    )

    # Get per-keyword trim amount (post needs minimal trimming)
    trim_ms = TRIM_CONFIG.get(keyword, DEFAULT_TRIM_MS) if keyword else DEFAULT_TRIM_MS

    # Trim initial spike/click from TTS output (common artifact)
    if len(audio) > trim_ms:
        audio = audio[trim_ms:]

    # Apply fade in to smooth any remaining transients
    fade_in_ms = 10 if keyword == "post" else 20  # Shorter fade for post
    audio = audio.fade_in(duration=fade_in_ms)

    # Apply fade out for keywords with final consonants (preserve T in post)
    if keyword == "post":
        audio = audio.fade_out(duration=5)  # Very short fade to preserve T
    else:
        audio = audio.fade_out(duration=10)

    # Normalize volume
    audio = audio.normalize()

    # Apply per-keyword volume boost
    boost_db = VOLUME_BOOST_DB.get(keyword, 0) if keyword else 0
    if boost_db != 0:
        audio = audio + boost_db

    # Pad or trim to target duration
    if len(audio) < target_duration_ms:
        # Center the audio with silence padding
        silence_needed = target_duration_ms - len(audio)
        padding_start = silence_needed // 2
        padding_end = silence_needed - padding_start
        audio = AudioSegment.silent(duration=padding_start) + audio + AudioSegment.silent(duration=padding_end)
    elif len(audio) > target_duration_ms:
        # Trim from center
        start = (len(audio) - target_duration_ms) // 2
        audio = audio[start:start + target_duration_ms]

    return audio


def add_background_noise(audio: AudioSegment, noise_level_db: float = -20) -> AudioSegment:
    """Add subtle background noise to audio."""
    noise = WhiteNoise().to_audio_segment(duration=len(audio))
    noise = noise - abs(noise_level_db)  # Reduce noise volume
    return audio.overlay(noise)


def generate_keyword_samples():
    """Generate TTS samples for all keywords with per-keyword configuration."""
    if not GOOGLE_TTS_AVAILABLE:
        print("ERROR: Google Cloud TTS not available. Cannot generate samples.")
        print("Install with: pip install google-cloud-texttospeech")
        return False

    client = texttospeech.TextToSpeechClient()

    for keyword in KEYWORDS:
        # Get per-keyword configuration
        samples_needed = SAMPLES_CONFIG.get(keyword, DEFAULT_SAMPLES_PER_KEYWORD)
        speeds = SPEED_CONFIG.get(keyword, DEFAULT_SPEEDS)

        print(f"\nGenerating samples for '{keyword}' (target: {samples_needed})...")
        keyword_dir = OUTPUT_DIR / keyword

        # Count existing samples to avoid overwriting
        existing_count = len(list(keyword_dir.glob(f"{keyword}_*.wav")))
        if existing_count > 0:
            print(f"  Found {existing_count} existing samples")

        # Generate all combinations with keyword-specific speeds
        all_combinations = list(itertools.product(VOICE_CONFIGS, speeds, PITCHES))
        random.shuffle(all_combinations)

        # If we need more samples than combinations, allow repetition with variations
        if samples_needed > len(all_combinations):
            # Repeat combinations with slight variations
            extended = all_combinations * (samples_needed // len(all_combinations) + 1)
            random.shuffle(extended)
            selected = extended[:samples_needed]
        else:
            selected = all_combinations[:samples_needed]

        for i, (voice, speed, pitch) in enumerate(selected):
            try:
                # Generate TTS audio (use phonetic version for correct pronunciation)
                phonetic_text = KEYWORDS_PHONETIC.get(keyword, keyword)
                audio_bytes = generate_tts_sample(client, phonetic_text, voice, speed, pitch)

                # Normalize and pad with keyword-specific settings
                audio = normalize_and_pad(audio_bytes, keyword=keyword)

                # Add noise variation (40% clean, 30% light noise, 30% heavier noise)
                noise_roll = random.random()
                if noise_roll > 0.7:
                    # Heavier noise
                    audio = add_background_noise(audio, noise_level_db=random.uniform(-20, -12))
                elif noise_roll > 0.4:
                    # Light noise
                    audio = add_background_noise(audio, noise_level_db=random.uniform(-30, -20))
                # else: clean audio

                # Save with index offset if existing samples present
                sample_idx = existing_count + i + 1
                filename = f"{keyword}_{sample_idx:03d}.wav"
                audio.export(keyword_dir / filename, format="wav")

                if (i + 1) % 50 == 0:
                    print(f"  Generated {i+1}/{samples_needed} samples")

            except Exception as e:
                print(f"  Error generating sample {i+1}: {e}")
                continue

        final_count = len(list(keyword_dir.glob(f"{keyword}_*.wav")))
        print(f"  Completed: {final_count} total samples for '{keyword}'")

    return True


def generate_unknown_samples():
    """
    Generate samples for 'unknown' class (non-keyword words).

    Uses comprehensive word list including:
    - Words phonetically similar to each keyword (confusables)
    - Common conversational words
    - Numbers and commands

    Target: 600+ unknown samples for balanced training.
    """
    if not GOOGLE_TTS_AVAILABLE:
        return False

    client = texttospeech.TextToSpeechClient()
    unknown_dir = OUTPUT_DIR / "unknown"

    # Count existing samples
    existing_count = len(list(unknown_dir.glob("unknown_*.wav")))
    if existing_count > 0:
        print(f"\nFound {existing_count} existing unknown samples")

    print(f"\nGenerating 'unknown' class samples ({len(UNKNOWN_WORDS)} words)...")
    print(f"  Record-similar: {len(UNKNOWN_RECORD_SIMILAR)} words")
    print(f"  Stop-similar: {len(UNKNOWN_STOP_SIMILAR)} words")
    print(f"  Capture-similar: {len(UNKNOWN_CAPTURE_SIMILAR)} words")
    print(f"  Post-similar: {len(UNKNOWN_POST_SIMILAR)} words")
    print(f"  Common words: {len(UNKNOWN_COMMON)} words")

    sample_count = 0
    total_target = len(UNKNOWN_WORDS) * UNKNOWN_SAMPLES_PER_WORD

    for word in UNKNOWN_WORDS:
        # Generate samples for each unknown word
        num_samples = UNKNOWN_SAMPLES_PER_WORD

        for i in range(num_samples):
            try:
                voice = random.choice(VOICE_CONFIGS)
                speed = random.choice(DEFAULT_SPEEDS)
                pitch = random.choice(PITCHES)

                audio_bytes = generate_tts_sample(client, word, voice, speed, pitch)
                audio = normalize_and_pad(audio_bytes)

                # Add noise variation (similar distribution to keywords)
                noise_roll = random.random()
                if noise_roll > 0.7:
                    audio = add_background_noise(audio, noise_level_db=random.uniform(-20, -12))
                elif noise_roll > 0.4:
                    audio = add_background_noise(audio, noise_level_db=random.uniform(-30, -20))

                # Use unique filename with word included
                filename = f"unknown_{word}_{i+1}.wav"
                audio.export(unknown_dir / filename, format="wav")
                sample_count += 1

                if sample_count % 100 == 0:
                    print(f"  Generated {sample_count}/{total_target} unknown samples...")

            except Exception as e:
                continue

    final_count = len(list(unknown_dir.glob("unknown_*.wav")))
    print(f"  Completed: {final_count} total unknown samples")
    return True


def generate_noise_samples():
    """
    Generate comprehensive noise and silence samples.

    Categories:
    1. White/pink/brown noise at various levels (200 samples)
    2. Near-silence (-50dB to -60dB noise) (100 samples)
    3. Pure digital silence (50 samples)
    4. Filtered noise (simulating room tone, HVAC, etc.) (100 samples)

    Total target: 450+ noise samples
    """
    noise_dir = OUTPUT_DIR / "noise"

    # Count existing samples
    existing_count = len(list(noise_dir.glob("noise_*.wav")))
    if existing_count > 0:
        print(f"\nFound {existing_count} existing noise samples")

    print(f"\nGenerating comprehensive noise samples...")
    sample_idx = existing_count

    # ==========================================================================
    # 1. WHITE NOISE AT VARIOUS LEVELS (200 samples)
    # ==========================================================================
    print("  Generating white noise variations...")
    for i in range(200):
        duration = random.randint(900, 1100)
        noise = WhiteNoise().to_audio_segment(duration=duration)

        # Apply random filtering to create variety
        filter_type = random.choice(["none", "low", "high", "band", "none", "none"])
        if filter_type == "low":
            noise = noise.low_pass_filter(random.randint(1000, 4000))
        elif filter_type == "high":
            noise = noise.high_pass_filter(random.randint(500, 2000))
        elif filter_type == "band":
            noise = noise.low_pass_filter(random.randint(3000, 6000))
            noise = noise.high_pass_filter(random.randint(200, 800))

        # Volume variation: -5dB to -25dB (various noise levels)
        noise = noise - random.uniform(5, 25)

        # Normalize to 1 second
        if len(noise) < SAMPLE_DURATION_MS:
            noise = noise + AudioSegment.silent(duration=SAMPLE_DURATION_MS - len(noise))
        else:
            noise = noise[:SAMPLE_DURATION_MS]

        noise = noise.set_frame_rate(SAMPLE_RATE)
        noise = noise.set_channels(1)

        sample_idx += 1
        filename = f"noise_white_{sample_idx:03d}.wav"
        noise.export(noise_dir / filename, format="wav")

        if (sample_idx) % 50 == 0:
            print(f"    Generated {sample_idx} samples...")

    # ==========================================================================
    # 2. NEAR-SILENCE (very quiet noise, -50dB to -60dB) (100 samples)
    # ==========================================================================
    print("  Generating near-silence samples...")
    for i in range(100):
        silence = AudioSegment.silent(duration=SAMPLE_DURATION_MS)
        # Very quiet noise (-50dB to -60dB) to simulate real quiet environments
        quiet_noise = WhiteNoise().to_audio_segment(duration=SAMPLE_DURATION_MS)
        quiet_noise = quiet_noise - random.uniform(50, 60)

        # Optional filtering for variety
        if random.random() > 0.5:
            quiet_noise = quiet_noise.low_pass_filter(random.randint(2000, 4000))

        silence = silence.overlay(quiet_noise)
        silence = silence.set_frame_rate(SAMPLE_RATE)
        silence = silence.set_channels(1)

        sample_idx += 1
        filename = f"noise_nearsilence_{sample_idx:03d}.wav"
        silence.export(noise_dir / filename, format="wav")

    print(f"    Near-silence: 100 samples")

    # ==========================================================================
    # 3. PURE DIGITAL SILENCE (50 samples)
    # ==========================================================================
    print("  Generating pure digital silence...")
    for i in range(50):
        # True digital silence (all zeros)
        silence = AudioSegment.silent(duration=SAMPLE_DURATION_MS)
        silence = silence.set_frame_rate(SAMPLE_RATE)
        silence = silence.set_channels(1)

        sample_idx += 1
        filename = f"noise_silence_{sample_idx:03d}.wav"
        silence.export(noise_dir / filename, format="wav")

    print(f"    Pure silence: 50 samples")

    # ==========================================================================
    # 4. ROOM TONE / HVAC NOISE (filtered low-frequency noise) (100 samples)
    # ==========================================================================
    print("  Generating room tone/HVAC noise...")
    for i in range(100):
        duration = SAMPLE_DURATION_MS
        noise = WhiteNoise().to_audio_segment(duration=duration)

        # Low-pass filter to simulate room tone/HVAC
        noise = noise.low_pass_filter(random.randint(300, 1000))

        # Volume: -15dB to -35dB
        noise = noise - random.uniform(15, 35)

        noise = noise.set_frame_rate(SAMPLE_RATE)
        noise = noise.set_channels(1)

        sample_idx += 1
        filename = f"noise_roomtone_{sample_idx:03d}.wav"
        noise.export(noise_dir / filename, format="wav")

    print(f"    Room tone: 100 samples")

    final_count = len(list(noise_dir.glob("noise_*.wav")))
    print(f"  Completed: {final_count} total noise samples")
    return True


def print_sample_stats():
    """Print statistics about generated samples."""
    print("\n" + "=" * 60)
    print("SAMPLE STATISTICS")
    print("=" * 60)

    total = 0
    for keyword in KEYWORDS:
        kw_dir = OUTPUT_DIR / keyword
        count = len(list(kw_dir.glob("*.wav"))) if kw_dir.exists() else 0
        target = SAMPLES_CONFIG.get(keyword, DEFAULT_SAMPLES_PER_KEYWORD)
        status = "OK" if count >= target else f"NEED {target - count} more"
        print(f"  {keyword:12}: {count:4} samples ({status})")
        total += count

    unknown_dir = OUTPUT_DIR / "unknown"
    unknown_count = len(list(unknown_dir.glob("*.wav"))) if unknown_dir.exists() else 0
    unknown_target = len(UNKNOWN_WORDS) * UNKNOWN_SAMPLES_PER_WORD
    print(f"  {'unknown':12}: {unknown_count:4} samples (target: {unknown_target}+)")
    total += unknown_count

    noise_dir = OUTPUT_DIR / "noise"
    noise_count = len(list(noise_dir.glob("*.wav"))) if noise_dir.exists() else 0
    print(f"  {'noise':12}: {noise_count:4} samples (target: 450+)")
    total += noise_count

    print("-" * 40)
    print(f"  {'TOTAL':12}: {total:4} samples")
    print("=" * 60)


def main():
    print("=" * 60)
    print("Edge Impulse Keyword Spotting - Sample Generator")
    print("=" * 60)
    print(f"\nKeywords: {', '.join(KEYWORDS)}")
    print(f"Per-keyword sample targets:")
    for kw in KEYWORDS:
        count = SAMPLES_CONFIG.get(kw, DEFAULT_SAMPLES_PER_KEYWORD)
        speeds = len(SPEED_CONFIG.get(kw, DEFAULT_SPEEDS))
        boost = VOLUME_BOOST_DB.get(kw, 0)
        trim = TRIM_CONFIG.get(kw, DEFAULT_TRIM_MS)
        extra = f" (wider speed range, +{boost}dB boost, {trim}ms trim)" if kw == "post" else ""
        print(f"  {kw}: {count} samples{extra}")
    print(f"\nUnknown words: {len(UNKNOWN_WORDS)} (target: {len(UNKNOWN_WORDS) * UNKNOWN_SAMPLES_PER_WORD}+ samples)")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    # Create directories
    create_directories()

    # Generate samples
    success = True

    if GOOGLE_TTS_AVAILABLE and os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        success &= generate_keyword_samples()
        success &= generate_unknown_samples()
    else:
        print("\nWARNING: Skipping TTS generation (credentials not configured)")
        print("You can manually record samples and place them in the samples/ directory")
        print("Or configure GOOGLE_APPLICATION_CREDENTIALS and re-run")

    success &= generate_noise_samples()

    # Print statistics
    print_sample_stats()

    print("\n" + "=" * 60)
    if success:
        print("Sample generation complete!")
        print(f"\nNext steps:")
        print(f"1. Review samples in {OUTPUT_DIR}")
        print(f"2. Add your own voice recordings for better accuracy")
        print(f"3. Upload to Edge Impulse for training")
    else:
        print("Some errors occurred during generation.")
        print("Check the output above for details.")
    print("=" * 60)


if __name__ == "__main__":
    main()
