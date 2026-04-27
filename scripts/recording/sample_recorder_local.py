#!/usr/bin/env python3
"""
Local Microphone Sample Recorder for Edge Impulse

Records samples directly from your computer's microphone.
No ESP32 needed - better audio quality for training data.

Controls:
    R = Select "record" keyword
    S = Select "stop" keyword
    C = Select "capture" keyword
    P = Select "post" keyword
    N = Record NOISE sample (ambient/silence)
    U = Record UNKNOWN word sample
    G = GO! Record sample
    Q = Quit

Requirements:
    pip install sounddevice numpy

Usage:
    python sample_recorder_local.py
"""

import os
import sys
import wave
import struct
import time
import threading
from pathlib import Path

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install sounddevice numpy")
    sys.exit(1)

# =============================================================================
# Configuration
# =============================================================================
SAMPLE_RATE = 16000
TARGET_DURATION_MS = 1000
TARGET_SAMPLES = int(SAMPLE_RATE * TARGET_DURATION_MS / 1000)
RECORD_DURATION_S = 2.0  # Record 2 seconds, extract 1 second

# Keywords mapping: key -> (name, display, mode)
# mode: 'keyword' = speech detection, 'noise' = full recording, 'unknown' = speech with prompt
KEYWORDS = {
    'r': ('record', 'RECORD', 'keyword'),
    's': ('stop', 'STOP', 'keyword'),
    'c': ('capture', 'CAPTURE', 'keyword'),
    'p': ('post', 'POST', 'keyword'),
    'n': ('noise', 'NOISE', 'noise'),
    'u': ('unknown', 'UNKNOWN', 'unknown'),
}

SAMPLES_PER_KEYWORD = 20

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "training" / "samples"

# =============================================================================
# PER-KEYWORD VAD CONFIGURATION
# =============================================================================
# Default VAD settings
VAD_FRAME_MS = 20
VAD_THRESHOLD_RATIO = 2.5    # Multiplier above noise floor
VAD_MIN_SPEECH_MS = 80       # Minimum speech duration
VAD_SILENCE_MS = 100         # Silence detection window
VAD_PRE_SPEECH_MS = 50       # Pre-speech padding
VAD_POST_SPEECH_MS = 50      # Post-speech padding (default)

# Per-keyword VAD overrides (to handle different phonetics)
VAD_KEYWORD_CONFIG = {
    "record": {
        "threshold_ratio": 2.5,
        "min_speech_ms": 80,
        "silence_ms": 100,
        "pre_speech_ms": 50,
        "post_speech_ms": 80,  # Slight extra for trailing 'd'
    },
    "stop": {
        "threshold_ratio": 2.5,
        "min_speech_ms": 60,   # Shorter word
        "silence_ms": 80,
        "pre_speech_ms": 50,
        "post_speech_ms": 80,  # Extra for final 'p'
    },
    "capture": {
        "threshold_ratio": 2.2,  # Lower threshold - word has quiet parts
        "min_speech_ms": 120,    # Two syllables
        "silence_ms": 120,       # Wait longer for full word
        "pre_speech_ms": 50,
        "post_speech_ms": 120,   # Extra padding to catch full 'ture' ending
    },
    "post": {
        "threshold_ratio": 2.0,  # Lower - word has quiet ending
        "min_speech_ms": 60,     # Short word
        "silence_ms": 150,       # Long wait - T sound is quiet
        "pre_speech_ms": 50,
        "post_speech_ms": 200,   # CRITICAL: Extra padding to capture final T!
    },
    "unknown": {
        "threshold_ratio": 2.5,
        "min_speech_ms": 80,
        "silence_ms": 150,
        "pre_speech_ms": 50,
        "post_speech_ms": 100,
    },
}

# Unknown words to prompt user (subset for variety)
UNKNOWN_WORD_PROMPTS = [
    "hello", "report", "reward", "shop", "stock", "captain", "chapter",
    "most", "coast", "toast", "start", "play", "pause", "next", "back",
    "yes", "okay", "go", "wait", "look", "one", "two", "three",
]

# Calibrated noise floor (set during startup)
CALIBRATED_NOISE_FLOOR = None

# =============================================================================
# Terminal Colors
# =============================================================================
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'

# =============================================================================
# Global State
# =============================================================================
class RecorderState:
    def __init__(self):
        self.current_keyword = 'record'
        self.current_mode = 'keyword'  # 'keyword', 'noise', or 'unknown'
        self.samples_collected = {kw: 0 for kw in [k[0] for k in KEYWORDS.values()]}
        self.last_error = None
        self.last_result = None
        self.unknown_word_idx = 0  # Track which unknown word to prompt

        # Count existing samples
        for key, (kw, _, mode) in KEYWORDS.items():
            kw_dir = OUTPUT_DIR / kw
            if kw_dir.exists():
                # Count real_ samples for keywords, all samples for noise/unknown
                if mode == 'keyword':
                    existing = len(list(kw_dir.glob(f"{kw}_real_*.wav")))
                else:
                    existing = len(list(kw_dir.glob(f"{kw}_*.wav")))
                self.samples_collected[kw] = existing

    def select_keyword(self, key):
        if key in KEYWORDS:
            kw, display, mode = KEYWORDS[key]
            self.current_keyword = kw
            self.current_mode = mode
            return True
        return False

    def get_current_mode(self):
        return self.current_mode

    def get_current_count(self):
        return self.samples_collected[self.current_keyword]

    def get_next_filename(self):
        count = self.get_current_count()
        if self.current_mode == 'noise':
            return f"noise_real_{count + 1:03d}.wav"
        elif self.current_mode == 'unknown':
            word = self.get_current_unknown_word()
            return f"unknown_{word}_real_{count + 1:03d}.wav"
        else:
            return f"{self.current_keyword}_real_{count + 1:03d}.wav"

    def get_current_unknown_word(self):
        """Get the current unknown word to prompt user."""
        return UNKNOWN_WORD_PROMPTS[self.unknown_word_idx % len(UNKNOWN_WORD_PROMPTS)]

    def next_unknown_word(self):
        """Advance to next unknown word."""
        self.unknown_word_idx = (self.unknown_word_idx + 1) % len(UNKNOWN_WORD_PROMPTS)

    def increment(self):
        self.samples_collected[self.current_keyword] += 1
        if self.current_mode == 'unknown':
            self.next_unknown_word()

    def total_collected(self):
        return sum(self.samples_collected.values())

    def total_needed(self):
        # Only count keyword samples for "needed" (noise/unknown are extra)
        keyword_count = sum(1 for k, (_, _, m) in KEYWORDS.items() if m == 'keyword')
        return keyword_count * SAMPLES_PER_KEYWORD

state = RecorderState()

# =============================================================================
# Audio Processing
# =============================================================================
def calculate_rms(samples):
    if len(samples) == 0:
        return 0
    return np.sqrt(np.mean(samples.astype(np.float64) ** 2))

def find_speech_boundaries(audio_data, keyword=None):
    """
    Find speech boundaries in audio using VAD.

    Args:
        audio_data: Audio samples
        keyword: Optional keyword name for per-keyword VAD settings
    """
    global CALIBRATED_NOISE_FLOOR

    # Get per-keyword VAD config or use defaults
    vad_config = VAD_KEYWORD_CONFIG.get(keyword, {})
    threshold_ratio = vad_config.get("threshold_ratio", VAD_THRESHOLD_RATIO)
    min_speech_ms = vad_config.get("min_speech_ms", VAD_MIN_SPEECH_MS)
    silence_ms = vad_config.get("silence_ms", VAD_SILENCE_MS)
    pre_speech_ms = vad_config.get("pre_speech_ms", VAD_PRE_SPEECH_MS)
    post_speech_ms = vad_config.get("post_speech_ms", VAD_POST_SPEECH_MS)

    frame_samples = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)

    energies = []
    for i in range(0, len(audio_data) - frame_samples, frame_samples):
        frame = audio_data[i:i + frame_samples]
        energies.append(calculate_rms(frame))

    if not energies:
        return None

    # Use calibrated noise floor if available, otherwise estimate from recording
    if CALIBRATED_NOISE_FLOOR is not None:
        noise_floor = CALIBRATED_NOISE_FLOOR
    else:
        noise_frames = min(5, len(energies) // 4)
        sorted_energies = sorted(energies)
        noise_floor = sum(sorted_energies[:max(1, noise_frames)]) / max(1, noise_frames)
        noise_floor = max(noise_floor, 50)

    threshold = noise_floor * threshold_ratio

    # Find speech start
    speech_start_frame = None
    for i, e in enumerate(energies):
        if e > threshold:
            speech_start_frame = i
            break

    if speech_start_frame is None:
        return None

    # Find speech end - look for sustained silence
    silence_frames_needed = int(silence_ms / VAD_FRAME_MS)
    speech_end_frame = len(energies) - 1
    silence_count = 0

    for i in range(speech_start_frame, len(energies)):
        if energies[i] < threshold:
            silence_count += 1
            if silence_count >= silence_frames_needed:
                speech_end_frame = i - silence_frames_needed
                break
        else:
            silence_count = 0

    # Check minimum duration
    speech_duration_frames = speech_end_frame - speech_start_frame
    min_speech_frames = int(min_speech_ms / VAD_FRAME_MS)

    if speech_duration_frames < min_speech_frames:
        return None

    # Convert to samples with per-keyword padding
    pre_speech_samples = int(SAMPLE_RATE * pre_speech_ms / 1000)
    post_speech_samples = int(SAMPLE_RATE * post_speech_ms / 1000)

    start_sample = max(0, speech_start_frame * frame_samples - pre_speech_samples)
    end_sample = min(len(audio_data), (speech_end_frame + 1) * frame_samples + post_speech_samples)

    return (start_sample, end_sample)

def center_audio_in_window(audio_data, start, end, target_samples=TARGET_SAMPLES):
    speech = audio_data[start:end]
    speech_len = len(speech)

    if speech_len >= target_samples:
        excess = speech_len - target_samples
        trim_start = excess // 2
        return speech[trim_start:trim_start + target_samples]
    else:
        padding_needed = target_samples - speech_len
        pad_start = padding_needed // 2
        pad_end = padding_needed - pad_start
        result = np.concatenate([
            np.zeros(pad_start, dtype=np.int16),
            speech,
            np.zeros(pad_end, dtype=np.int16)
        ])
        return result[:target_samples]

def process_audio(audio_data, keyword=None, mode='keyword'):
    """
    Process recorded audio based on mode.

    Args:
        audio_data: Raw audio samples
        keyword: Keyword name for per-keyword VAD settings
        mode: 'keyword' (speech detection), 'noise' (full recording), 'unknown' (speech)

    Returns:
        (processed_audio, info_string) or (None, error_string)
    """
    if mode == 'noise':
        # For noise samples, just center the full recording (no VAD)
        # Take 1 second from the middle of the recording
        total_samples = len(audio_data)
        if total_samples >= TARGET_SAMPLES:
            start = (total_samples - TARGET_SAMPLES) // 2
            centered = audio_data[start:start + TARGET_SAMPLES]
        else:
            # Pad if somehow short
            padding = TARGET_SAMPLES - total_samples
            centered = np.concatenate([
                np.zeros(padding // 2, dtype=np.int16),
                audio_data,
                np.zeros(padding - padding // 2, dtype=np.int16)
            ])
        return centered, "noise sample"

    # For keyword and unknown modes, use VAD
    boundaries = find_speech_boundaries(audio_data, keyword=keyword)

    if boundaries is None:
        return None, "No speech detected"

    start, end = boundaries
    speech_duration_ms = (end - start) * 1000 // SAMPLE_RATE

    centered = center_audio_in_window(audio_data, start, end)
    return centered, f"{speech_duration_ms}ms speech"

def save_sample(samples, keyword, filename):
    output_dir = OUTPUT_DIR / keyword
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename

    with wave.open(str(filepath), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.tobytes())

    return filepath

# =============================================================================
# Recording Function
# =============================================================================
def record_sample():
    """Record audio from microphone with mode-aware processing."""
    kw = state.current_keyword.upper()
    mode = state.get_current_mode()

    # Display appropriate prompt based on mode
    if mode == 'noise':
        print(f"\r  {Colors.CYAN}{Colors.BOLD}>>> Recording NOISE (stay quiet) <<<{Colors.END}      ")
        record_duration = 1.5  # Shorter for noise
    elif mode == 'unknown':
        word = state.get_current_unknown_word().upper()
        print(f"\r  {Colors.YELLOW}{Colors.BOLD}>>> SPEAK: \"{word}\" <<<{Colors.END}      ")
        record_duration = RECORD_DURATION_S
    else:
        print(f"\r  {Colors.RED}{Colors.BOLD}>>> SPEAK: {kw} <<<{Colors.END}      ")
        record_duration = RECORD_DURATION_S

    # Record audio
    try:
        audio = sd.rec(
            int(record_duration * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.int16
        )
        sd.wait()
    except Exception as e:
        return False, f"Recording error: {e}"

    # Flatten to 1D array
    audio = audio.flatten()

    # Process based on mode
    keyword_for_vad = state.current_keyword if mode == 'keyword' else 'unknown'
    processed, result = process_audio(audio, keyword=keyword_for_vad, mode=mode)

    if processed is None:
        return False, result

    # Save
    filename = state.get_next_filename()
    filepath = save_sample(processed, state.current_keyword, filename)
    state.increment()

    return True, f"Saved: {filename} ({result})"

# =============================================================================
# Display
# =============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_ui():
    clear_screen()

    print(f"{Colors.BOLD}{Colors.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║        LOCAL MICROPHONE SAMPLE RECORDER                  ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(Colors.END)

    # Current selection
    kw = state.current_keyword.upper()
    mode = state.get_current_mode()
    count = state.get_current_count()
    total = SAMPLES_PER_KEYWORD

    # Mode-specific display
    if mode == 'noise':
        display_text = "NOISE (ambient/silence)"
        color = Colors.CYAN
    elif mode == 'unknown':
        word = state.get_current_unknown_word().upper()
        display_text = f"UNKNOWN: \"{word}\""
        color = Colors.YELLOW
    else:
        display_text = kw
        color = Colors.CYAN

    print(f"\n  {Colors.YELLOW}Current mode:{Colors.END}")
    print(f"\n  {Colors.BOLD}{Colors.WHITE}  ╔{'═' * (len(display_text) + 6)}╗")
    print(f"  ║   {color}{display_text}{Colors.WHITE}   ║")
    print(f"  ╚{'═' * (len(display_text) + 6)}╝{Colors.END}")

    # Progress bar
    bar_width = 30
    filled = int(bar_width * count / total) if total > 0 else 0
    filled = min(filled, bar_width)  # Cap at max
    bar = "█" * filled + "░" * (bar_width - filled)
    percent = int(100 * count / total) if total > 0 else 0

    print(f"\n  Progress: [{Colors.GREEN}{bar}{Colors.END}] {count}/{total} ({percent}%)")

    # Keyword section
    print(f"\n  {Colors.BOLD}Keywords:{Colors.END}")
    for key, (kw_name, kw_display, kw_mode) in KEYWORDS.items():
        if kw_mode != 'keyword':
            continue  # Skip noise/unknown here
        c = state.samples_collected[kw_name]
        is_current = kw_name == state.current_keyword

        if c >= SAMPLES_PER_KEYWORD:
            status = f"{Colors.GREEN}✓ DONE{Colors.END}"
        elif is_current:
            status = f"{Colors.YELLOW}● Selected{Colors.END}"
        else:
            status = f"{Colors.DIM}○ {c}/{SAMPLES_PER_KEYWORD}{Colors.END}"

        key_hint = f"[{key.upper()}]"
        marker = "→" if is_current else " "
        print(f"   {marker} {key_hint:4} {kw_display:10} {status}")

    # Extra samples section
    print(f"\n  {Colors.BOLD}Extra (no minimum):{Colors.END}")
    for key, (kw_name, kw_display, kw_mode) in KEYWORDS.items():
        if kw_mode == 'keyword':
            continue  # Skip keywords
        c = state.samples_collected[kw_name]
        is_current = kw_name == state.current_keyword

        if is_current:
            status = f"{Colors.YELLOW}● Selected{Colors.END}"
        else:
            status = f"{Colors.DIM}○ {c} samples{Colors.END}"

        key_hint = f"[{key.upper()}]"
        marker = "→" if is_current else " "
        desc = "ambient noise" if kw_mode == 'noise' else "other words"
        print(f"   {marker} {key_hint:4} {kw_display:10} {status} ({desc})")

    # Overall
    total_done = state.total_collected()
    total_needed = state.total_needed()
    overall_pct = int(100 * min(total_done, total_needed) / total_needed) if total_needed > 0 else 0
    print(f"\n  {Colors.BOLD}Keywords: {min(total_done, total_needed)}/{total_needed} ({overall_pct}%){Colors.END}")
    extra = state.samples_collected.get('noise', 0) + state.samples_collected.get('unknown', 0)
    print(f"  {Colors.DIM}Extra samples (noise+unknown): {extra}{Colors.END}")

    # Per-keyword VAD info (when relevant)
    if mode == 'keyword':
        vad_config = VAD_KEYWORD_CONFIG.get(state.current_keyword, {})
        post_ms = vad_config.get("post_speech_ms", VAD_POST_SPEECH_MS)
        silence_ms = vad_config.get("silence_ms", VAD_SILENCE_MS)
        print(f"\n  {Colors.DIM}VAD: {post_ms}ms post-padding, {silence_ms}ms silence wait{Colors.END}")

    # Instructions
    print(f"\n  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")
    print(f"  {Colors.BOLD}Controls:{Colors.END}")
    print(f"    {Colors.CYAN}R{Colors.END} = Record  {Colors.CYAN}S{Colors.END} = Stop  {Colors.CYAN}C{Colors.END} = Capture  {Colors.CYAN}P{Colors.END} = Post")
    print(f"    {Colors.CYAN}N{Colors.END} = Noise   {Colors.CYAN}U{Colors.END} = Unknown word")
    print(f"    {Colors.GREEN}{Colors.BOLD}G{Colors.END} = GO! Record sample")
    print(f"    {Colors.RED}Q{Colors.END} = Quit")
    print(f"  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")

    if state.last_error:
        print(f"\n  {Colors.RED}Error: {state.last_error}{Colors.END}")

    if state.last_result:
        print(f"\n  {Colors.GREEN}Last: {state.last_result}{Colors.END}")

    print(f"\n  {Colors.DIM}Press a key...{Colors.END}")

# =============================================================================
# Calibration
# =============================================================================
def calibrate_noise_floor():
    """Record 1 second of silence to calibrate noise floor"""
    global CALIBRATED_NOISE_FLOOR

    print(f"\n  {Colors.CYAN}Calibrating noise floor...{Colors.END}")
    print(f"  {Colors.DIM}Stay quiet for 1 second...{Colors.END}")

    try:
        # Record 1 second of ambient noise
        audio = sd.rec(
            int(1.0 * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype=np.int16
        )
        sd.wait()

        audio = audio.flatten()

        # Calculate RMS of the recording
        frame_samples = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)
        energies = []
        for i in range(0, len(audio) - frame_samples, frame_samples):
            frame = audio[i:i + frame_samples]
            energies.append(calculate_rms(frame))

        if energies:
            # Use 75th percentile to account for occasional noise spikes
            sorted_energies = sorted(energies)
            idx = int(len(sorted_energies) * 0.75)
            CALIBRATED_NOISE_FLOOR = sorted_energies[idx]
            CALIBRATED_NOISE_FLOOR = max(CALIBRATED_NOISE_FLOOR, 30)  # Minimum floor

            print(f"  {Colors.GREEN}Calibrated! Noise floor: {CALIBRATED_NOISE_FLOOR:.0f}{Colors.END}")
            print(f"  {Colors.DIM}Speech threshold: {CALIBRATED_NOISE_FLOOR * VAD_THRESHOLD_RATIO:.0f}{Colors.END}")
            return True
        else:
            print(f"  {Colors.RED}Calibration failed - no audio data{Colors.END}")
            return False

    except Exception as e:
        print(f"  {Colors.RED}Calibration error: {e}{Colors.END}")
        return False

# =============================================================================
# Main
# =============================================================================
def main():
    # Check for audio devices
    try:
        devices = sd.query_devices()
        default_input = sd.query_devices(kind='input')
        print(f"Using microphone: {default_input['name']}")
    except Exception as e:
        print(f"Error finding audio device: {e}")
        print("Make sure a microphone is connected.")
        sys.exit(1)

    # Calibrate noise floor
    calibrate_noise_floor()
    time.sleep(1)
    print_ui()

    # Simple input loop (no raw terminal mode needed)
    try:
        while True:
            key = input().strip().lower()

            if key == 'q':
                break

            if key in KEYWORDS:
                state.select_keyword(key)
                state.last_error = None
                state.last_result = None
                print_ui()

            elif key == 'g':
                success, result = record_sample()
                if success:
                    state.last_result = result
                    state.last_error = None
                else:
                    state.last_error = result
                    state.last_result = None
                print_ui()

    except KeyboardInterrupt:
        pass

    print(f"\n{Colors.YELLOW}Session ended.{Colors.END}")
    print(f"Collected {state.total_collected()} samples total.")
    print(f"Samples saved to: {OUTPUT_DIR}\n")

if __name__ == "__main__":
    main()
