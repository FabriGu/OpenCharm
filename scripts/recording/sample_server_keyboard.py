#!/usr/bin/env python3
"""
Keyword Sample Recording Server - Keyboard Control Mode

Keyboard-driven sample collection for Edge Impulse training.
Works with ESP32 that auto-records when triggered via HTTP.

Controls:
    R = Select "record" keyword
    S = Select "stop" keyword
    C = Select "capture" keyword
    P = Select "post" keyword
    G = GO! Start recording (for currently selected keyword)
    Q = Quit

LED feedback from ESP32:
    BLUE pulse = Get ready
    RED = Recording
    GREEN flash = Saved successfully

Usage:
    python sample_server_keyboard.py
"""

import os
import sys
import wave
import struct
import threading
import time
import termios
import tty
import select
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

# Keywords mapping: key -> (name, display)
KEYWORDS = {
    'r': ('record', 'RECORD'),
    's': ('stop', 'STOP'),
    'c': ('capture', 'CAPTURE'),
    'p': ('post', 'POST'),
}

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
    DIM = '\033[2m'
    END = '\033[0m'

# =============================================================================
# Global State
# =============================================================================
class RecorderState:
    def __init__(self):
        self.current_keyword = 'record'  # Default keyword
        self.samples_collected = {kw: 0 for kw in [k[0] for k in KEYWORDS.values()]}
        self.lock = threading.Lock()
        self.last_error = None
        self.recording_requested = False
        self.recording_complete = threading.Event()
        self.last_result = None

        # Count existing samples
        for key, (kw, _) in KEYWORDS.items():
            kw_dir = OUTPUT_DIR / kw
            if kw_dir.exists():
                existing = len(list(kw_dir.glob(f"{kw}_real_*.wav")))
                self.samples_collected[kw] = existing

    def select_keyword(self, key):
        if key in KEYWORDS:
            with self.lock:
                self.current_keyword = KEYWORDS[key][0]
            return True
        return False

    def get_current_count(self):
        return self.samples_collected[self.current_keyword]

    def get_next_filename(self):
        count = self.get_current_count()
        return f"{self.current_keyword}_real_{count + 1:03d}.wav"

    def increment(self):
        with self.lock:
            self.samples_collected[self.current_keyword] += 1

    def total_collected(self):
        return sum(self.samples_collected.values())

    def total_needed(self):
        return len(KEYWORDS) * SAMPLES_PER_KEYWORD

    def request_recording(self):
        with self.lock:
            self.recording_requested = True
            self.recording_complete.clear()
            self.last_result = None

    def is_recording_requested(self):
        with self.lock:
            return self.recording_requested

    def clear_recording_request(self):
        with self.lock:
            self.recording_requested = False

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
    print("║       KEYBOARD-CONTROLLED SAMPLE RECORDER                ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(Colors.END)

    # Current keyword selection
    kw = state.current_keyword.upper()
    count = state.get_current_count()
    total = SAMPLES_PER_KEYWORD

    print(f"\n  {Colors.YELLOW}Current keyword:{Colors.END}")
    print(f"\n  {Colors.BOLD}{Colors.WHITE}  ╔{'═' * (len(kw) + 6)}╗")
    print(f"  ║   {Colors.CYAN}{kw}{Colors.WHITE}   ║")
    print(f"  ╚{'═' * (len(kw) + 6)}╝{Colors.END}")

    # Progress bar for current keyword
    bar_width = 30
    filled = int(bar_width * count / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    percent = int(100 * count / total) if total > 0 else 0

    print(f"\n  Progress: [{Colors.GREEN}{bar}{Colors.END}] {count}/{total} ({percent}%)")

    # All keywords status
    print(f"\n  {Colors.BOLD}All Keywords:{Colors.END}")
    for key, (kw_name, kw_display) in KEYWORDS.items():
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

    # Overall progress
    total_done = state.total_collected()
    total_all = state.total_needed()
    overall_pct = int(100 * total_done / total_all) if total_all > 0 else 0
    print(f"\n  {Colors.BOLD}Overall: {total_done}/{total_all} samples ({overall_pct}%){Colors.END}")

    # Instructions
    print(f"\n  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")
    print(f"  {Colors.BOLD}Keyboard Controls:{Colors.END}")
    print(f"    {Colors.CYAN}R{Colors.END} = Record  {Colors.CYAN}S{Colors.END} = Stop  {Colors.CYAN}C{Colors.END} = Capture  {Colors.CYAN}P{Colors.END} = Post")
    print(f"    {Colors.GREEN}{Colors.BOLD}G{Colors.END} = GO! Record sample for selected keyword")
    print(f"    {Colors.RED}Q{Colors.END} = Quit")
    print(f"  {Colors.MAGENTA}─────────────────────────────────────────────{Colors.END}")

    # ESP32 LED guide
    print(f"\n  {Colors.BOLD}ESP32 LED:{Colors.END}")
    print(f"    {Colors.BLUE}● BLUE{Colors.END} = Ready")
    print(f"    {Colors.RED}● RED{Colors.END} = Recording - SPEAK NOW!")
    print(f"    {Colors.GREEN}● GREEN{Colors.END} = Saved successfully")

    if state.last_error:
        print(f"\n  {Colors.RED}Last error: {state.last_error}{Colors.END}")

    if state.last_result:
        print(f"\n  {Colors.GREEN}Last: {state.last_result}{Colors.END}")

    print(f"\n  {Colors.DIM}Waiting for keypress...{Colors.END}")

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

            # Tell ESP32 whether to record
            if state.is_recording_requested():
                state.clear_recording_request()
                self.wfile.write(state.current_keyword.upper().encode())
            else:
                self.wfile.write(b"WAIT")
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
                state.last_result = None
                self.send_response(400)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"ERROR: {result}".encode())
                state.recording_complete.set()
                return

            state.last_error = None
            filename = state.get_next_filename()

            save_sample(samples, state.current_keyword, filename)
            state.increment()
            state.last_result = f"Saved: {filename} ({result}ms speech)"

            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(f"OK: {filename}".encode())

            state.recording_complete.set()
        else:
            self.send_response(404)
            self.end_headers()

# =============================================================================
# Keyboard Input Handler
# =============================================================================
def get_key_nonblocking():
    """Get a keypress without blocking (returns None if no key)"""
    if select.select([sys.stdin], [], [], 0.1)[0]:
        return sys.stdin.read(1).lower()
    return None

def setup_terminal():
    """Set terminal to raw mode for single keypress detection"""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setraw(fd)
    return old_settings

def restore_terminal(old_settings):
    """Restore terminal to normal mode"""
    fd = sys.stdin.fileno()
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

# =============================================================================
# Main
# =============================================================================
def main():
    print_ui()

    print(f"\n  {Colors.CYAN}Starting server on port {SERVER_PORT}...{Colors.END}")

    # Start HTTP server in background thread
    server = HTTPServer(('0.0.0.0', SERVER_PORT), RecorderHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"  {Colors.GREEN}Server ready!{Colors.END}")
    print(f"  {Colors.YELLOW}Connect ESP32 and use keyboard to control.{Colors.END}")
    time.sleep(1)
    print_ui()

    # Set up terminal for raw input
    old_settings = setup_terminal()

    try:
        while True:
            key = get_key_nonblocking()

            if key is None:
                continue

            if key == 'q':
                break

            if key in KEYWORDS:
                state.select_keyword(key)
                print_ui()

            elif key == 'g':
                # Request recording
                state.request_recording()
                restore_terminal(old_settings)
                print(f"\n  {Colors.RED}{Colors.BOLD}>>> RECORDING! Say '{state.current_keyword.upper()}' <<<{Colors.END}")
                print(f"  {Colors.DIM}Waiting for ESP32 to capture...{Colors.END}")

                # Wait for recording to complete (with timeout)
                if state.recording_complete.wait(timeout=10):
                    pass  # Recording completed
                else:
                    state.last_error = "Recording timeout - is ESP32 connected?"

                old_settings = setup_terminal()
                print_ui()

            elif key == '\x03':  # Ctrl+C
                break

    except KeyboardInterrupt:
        pass

    finally:
        restore_terminal(old_settings)
        server.shutdown()
        print(f"\n\n{Colors.YELLOW}Session ended.{Colors.END}")
        print(f"Collected {state.total_collected()} samples total.")
        print(f"Samples saved to: {OUTPUT_DIR}\n")

if __name__ == "__main__":
    main()
