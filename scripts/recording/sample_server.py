#!/usr/bin/env python3
"""
Keyword Sample Recording Server - Auto Mode

Works with the ESP32 auto-recording firmware. The ESP32 automatically
records in a loop with countdowns. This server:
- Tells ESP32 which keyword to record
- Receives audio and auto-detects the spoken word
- Centers it in a 1-second window and saves it
- Shows a nice terminal UI with progress

Usage:
    python sample_server.py

Then just watch your ESP32's LED:
    BLUE pulse = Get ready (see keyword on screen)
    RED = SPEAK NOW!
    GREEN flash = Saved successfully
"""

import os
import sys
import wave
import struct
import threading
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

# =============================================================================
# Configuration
# =============================================================================
SERVER_PORT = 5555
SAMPLE_RATE = 16000
TARGET_DURATION_MS = 1000
TARGET_SAMPLES = int(SAMPLE_RATE * TARGET_DURATION_MS / 1000)

# Keywords to record (in order)
KEYWORDS = ["record", "stop", "capture", "post"]
SAMPLES_PER_KEYWORD = 20

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "training" / "samples"

# VAD Configuration
VAD_FRAME_MS = 20
VAD_THRESHOLD_RATIO = 2.0
VAD_MIN_SPEECH_MS = 100
VAD_SILENCE_MS = 150
VAD_PRE_SPEECH_MS = 80

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
    END = '\033[0m'

# =============================================================================
# Global State
# =============================================================================
class RecorderState:
    def __init__(self):
        self.current_keyword_idx = 0
        self.samples_collected = {kw: 0 for kw in KEYWORDS}
        self.lock = threading.Lock()
        self.last_error = None

        # Count existing real samples
        for kw in KEYWORDS:
            kw_dir = OUTPUT_DIR / kw
            if kw_dir.exists():
                existing = len(list(kw_dir.glob(f"{kw}_real_*.wav")))
                self.samples_collected[kw] = existing

    @property
    def current_keyword(self):
        if self.current_keyword_idx >= len(KEYWORDS):
            return None
        return KEYWORDS[self.current_keyword_idx]

    @property
    def current_count(self):
        if self.current_keyword is None:
            return 0
        return self.samples_collected[self.current_keyword]

    def increment(self):
        with self.lock:
            if self.current_keyword:
                self.samples_collected[self.current_keyword] += 1

                # Auto-advance to next keyword if done
                if self.current_count >= SAMPLES_PER_KEYWORD:
                    self.current_keyword_idx += 1
                    if self.current_keyword_idx < len(KEYWORDS):
                        return "NEXT"
                    else:
                        return "ALL_DONE"
        return "OK"

    def is_complete(self):
        return self.current_keyword_idx >= len(KEYWORDS)

    def get_next_filename(self):
        if self.current_keyword is None:
            return None
        return f"{self.current_keyword}_real_{self.current_count + 1:03d}.wav"

    def total_collected(self):
        return sum(self.samples_collected.values())

    def total_needed(self):
        return len(KEYWORDS) * SAMPLES_PER_KEYWORD

state = RecorderState()

# =============================================================================
# Voice Activity Detection
# =============================================================================
def calculate_rms(samples):
    if len(samples) == 0:
        return 0
    sum_sq = sum(s * s for s in samples)
    return (sum_sq / len(samples)) ** 0.5

def find_speech_boundaries(audio_data, sample_rate=SAMPLE_RATE):
    frame_samples = int(sample_rate * VAD_FRAME_MS / 1000)

    energies = []
    for i in range(0, len(audio_data) - frame_samples, frame_samples):
        frame = audio_data[i:i + frame_samples]
        energies.append(calculate_rms(frame))

    if not energies:
        return None

    # Estimate noise floor
    noise_frames = min(5, len(energies) // 4)
    noise_floor = sum(sorted(energies)[:max(1, noise_frames)]) / max(1, noise_frames)
    noise_floor = max(noise_floor, 50)

    threshold = noise_floor * VAD_THRESHOLD_RATIO

    # Find speech start
    speech_start_frame = None
    for i, e in enumerate(energies):
        if e > threshold:
            speech_start_frame = i
            break

    if speech_start_frame is None:
        return None

    # Find speech end
    silence_frames_needed = int(VAD_SILENCE_MS / VAD_FRAME_MS)
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
    min_speech_frames = int(VAD_MIN_SPEECH_MS / VAD_FRAME_MS)

    if speech_duration_frames < min_speech_frames:
        return None

    # Convert to samples
    pre_speech_samples = int(sample_rate * VAD_PRE_SPEECH_MS / 1000)
    start_sample = max(0, speech_start_frame * frame_samples - pre_speech_samples)
    end_sample = min(len(audio_data), (speech_end_frame + 1) * frame_samples + pre_speech_samples)

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
        result = [0] * pad_start + list(speech) + [0] * pad_end
        return result[:target_samples]

def process_audio(raw_audio_bytes):
    try:
        wav_io = BytesIO(raw_audio_bytes)
        with wave.open(wav_io, 'rb') as wav:
            n_channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            n_frames = wav.getnframes()
            raw_data = wav.readframes(n_frames)

            if sample_width == 2:
                samples = list(struct.unpack(f'<{n_frames * n_channels}h', raw_data))
            else:
                return None, "Unsupported format"

            if n_channels == 2:
                samples = samples[::2]
    except Exception as e:
        return None, f"WAV error: {e}"

    boundaries = find_speech_boundaries(samples)

    if boundaries is None:
        return None, "No speech detected"

    start, end = boundaries
    speech_duration_ms = (end - start) * 1000 // SAMPLE_RATE

    centered = center_audio_in_window(samples, start, end)
    return centered, speech_duration_ms

def save_sample(samples, keyword, filename):
    output_dir = OUTPUT_DIR / keyword
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename

    with wave.open(str(filepath), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(struct.pack(f'<{len(samples)}h', *samples))

    return filepath

# =============================================================================
# Display Functions
# =============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_ui():
    clear_screen()

    print(f"{Colors.BOLD}{Colors.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║          ESP32 KEYWORD SAMPLE RECORDER                   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(Colors.END)

    if state.is_complete():
        print(f"\n{Colors.GREEN}{Colors.BOLD}  ✓ ALL SAMPLES COLLECTED!{Colors.END}\n")
        print("  Next steps:")
        print("  1. Go to Edge Impulse Studio")
        print("  2. Upload these samples")
        print("  3. Retrain the model")
        print("  4. Download new Arduino library")
        print(f"\n  Samples saved to: {OUTPUT_DIR}")
        return

    kw = state.current_keyword.upper()
    count = state.current_count
    total = SAMPLES_PER_KEYWORD

    # Big keyword display
    print(f"\n  {Colors.YELLOW}Now recording:{Colors.END}")
    print(f"\n  {Colors.BOLD}{Colors.WHITE}  ╔{'═' * (len(kw) + 6)}╗")
    print(f"  ║   {Colors.CYAN}{kw}{Colors.WHITE}   ║")
    print(f"  ╚{'═' * (len(kw) + 6)}╝{Colors.END}")

    # Progress bar
    bar_width = 30
    filled = int(bar_width * count / total)
    bar = "█" * filled + "░" * (bar_width - filled)
    percent = int(100 * count / total)

    print(f"\n  Progress: [{Colors.GREEN}{bar}{Colors.END}] {count}/{total} ({percent}%)")

    # All keywords status
    print(f"\n  {Colors.BOLD}All Keywords:{Colors.END}")
    for i, k in enumerate(KEYWORDS):
        c = state.samples_collected[k]
        if i < state.current_keyword_idx:
            status = f"{Colors.GREEN}✓ DONE{Colors.END}"
        elif i == state.current_keyword_idx:
            status = f"{Colors.YELLOW}● Recording...{Colors.END}"
        else:
            status = f"{Colors.WHITE}○ Pending{Colors.END}"
        print(f"    {k.upper():10} {status} ({c}/{SAMPLES_PER_KEYWORD})")

    # Overall progress
    total_done = state.total_collected()
    total_all = state.total_needed()
    overall_pct = int(100 * total_done / total_all)
    print(f"\n  {Colors.BOLD}Overall: {total_done}/{total_all} samples ({overall_pct}%){Colors.END}")

    # Instructions
    print(f"\n  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")
    print(f"  {Colors.BOLD}Watch the ESP32 LED:{Colors.END}")
    print(f"    {Colors.BLUE}● BLUE pulse{Colors.END} = Get ready")
    print(f"    {Colors.RED}● RED{Colors.END} = SPEAK NOW! Say \"{kw}\"")
    print(f"    {Colors.GREEN}● GREEN flash{Colors.END} = Saved!")
    print(f"  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")

    if state.last_error:
        print(f"\n  {Colors.RED}Last error: {state.last_error}{Colors.END}")

# =============================================================================
# HTTP Server
# =============================================================================
class RecorderHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/status':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()

            if state.is_complete():
                self.wfile.write(b"DONE")
            else:
                self.wfile.write(state.current_keyword.upper().encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/upload':
            content_length = int(self.headers['Content-Length'])
            audio_data = self.rfile.read(content_length)

            samples, result = process_audio(audio_data)

            if samples is None:
                state.last_error = result
                self.send_response(400)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"ERROR: {result}".encode())
                print_ui()
                return

            state.last_error = None
            filename = state.get_next_filename()

            if filename is None:
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"ALL_DONE")
                print_ui()
                return

            save_sample(samples, state.current_keyword, filename)
            status = state.increment()

            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()

            if status == "ALL_DONE":
                self.wfile.write(b"ALL_DONE")
            else:
                self.wfile.write(f"OK: {filename}".encode())

            print_ui()
        else:
            self.send_response(404)
            self.end_headers()

# =============================================================================
# Main
# =============================================================================
def main():
    print_ui()

    print(f"\n  {Colors.CYAN}Server starting on port {SERVER_PORT}...{Colors.END}")
    print(f"  {Colors.YELLOW}Flash the ESP32 and it will start automatically!{Colors.END}\n")

    server = HTTPServer(('0.0.0.0', SERVER_PORT), RecorderHandler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Stopped by user.{Colors.END}")
        print(f"Collected {state.total_collected()} samples total.\n")
        server.shutdown()

if __name__ == "__main__":
    main()
