#!/usr/bin/env python3
"""
Serial Sample Recorder - Auto Mode

ESP32 records in a loop automatically. This script just listens and saves.
"""

import serial
import serial.tools.list_ports
import struct
import wave
import os
import sys
import time
from pathlib import Path

# Config
SAMPLE_RATE = 16000
RECORD_SAMPLES = 32000  # 2 seconds
KEYWORDS = ["record", "stop", "capture", "post"]
SAMPLES_PER_KEYWORD = 20
OUTPUT_DIR = Path(__file__).parent.parent / "training" / "samples"

# Colors
class C:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def find_esp32_port():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if 'usbmodem' in p.device or 'USB' in p.device or 'ACM' in p.device:
            return p.device
    return None

def count_existing_samples():
    counts = {}
    for kw in KEYWORDS:
        kw_dir = OUTPUT_DIR / kw
        if kw_dir.exists():
            counts[kw] = len(list(kw_dir.glob(f"{kw}_real_*.wav")))
        else:
            counts[kw] = 0
    return counts

def save_wav(samples, keyword, index):
    output_dir = OUTPUT_DIR / keyword
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{keyword}_real_{index:03d}.wav"
    filepath = output_dir / filename

    with wave.open(str(filepath), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    return filepath

def find_speech_and_center(samples, target_samples=16000):
    frame_size = 320
    energies = []
    for i in range(0, len(samples) - frame_size, frame_size):
        frame = samples[i:i + frame_size]
        energy = sum(s * s for s in frame) / len(frame)
        energies.append(energy ** 0.5)

    if not energies:
        return samples[:target_samples]

    sorted_e = sorted(energies)
    noise_floor = sum(sorted_e[:5]) / 5 if len(sorted_e) >= 5 else sorted_e[0]
    threshold = max(noise_floor * 2, 100)

    start_frame = None
    end_frame = None
    for i, e in enumerate(energies):
        if e > threshold:
            if start_frame is None:
                start_frame = i
            end_frame = i

    if start_frame is None:
        mid = len(samples) // 2
        start = max(0, mid - target_samples // 2)
        return samples[start:start + target_samples]

    start_sample = max(0, start_frame * frame_size - 800)
    end_sample = min(len(samples), (end_frame + 1) * frame_size + 800)
    speech = samples[start_sample:end_sample]
    speech_len = len(speech)

    if speech_len >= target_samples:
        excess = speech_len - target_samples
        trim_start = excess // 2
        return speech[trim_start:trim_start + target_samples]
    else:
        padding = target_samples - speech_len
        pad_start = padding // 2
        pad_end = padding - pad_start
        return [0] * pad_start + list(speech) + [0] * pad_end

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_status(keyword, kw_idx, counts, message=""):
    clear_screen()
    print(f"{C.BOLD}{C.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║       ESP32 KEYWORD SAMPLE RECORDER (Auto Mode)         ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(C.END)

    kw_upper = keyword.upper()
    print(f"\n  {C.YELLOW}Say this word when LED turns RED:{C.END}")
    print(f"\n  {C.BOLD}{C.CYAN}    >>> {kw_upper} <<<{C.END}")

    done = counts[keyword]
    total = SAMPLES_PER_KEYWORD
    bar_width = 30
    filled = int(bar_width * done / total)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"\n  Progress: [{C.GREEN}{bar}{C.END}] {done}/{total}")

    print(f"\n  {C.BOLD}All Keywords:{C.END}")
    for i, kw in enumerate(KEYWORDS):
        c = counts[kw]
        if c >= SAMPLES_PER_KEYWORD:
            status = f"{C.GREEN}✓ DONE{C.END}"
        elif i == kw_idx:
            status = f"{C.YELLOW}● Recording...{C.END}"
        else:
            status = f"○ Pending"
        print(f"    {kw.upper():10} {status} ({c}/{SAMPLES_PER_KEYWORD})")

    total_done = sum(counts.values())
    total_all = len(KEYWORDS) * SAMPLES_PER_KEYWORD
    print(f"\n  {C.BOLD}Overall: {total_done}/{total_all} samples{C.END}")

    print(f"\n  {C.BLUE}─────────────────────────────────────────────{C.END}")
    print(f"  LED Guide:")
    print(f"    {C.BLUE}● BLUE pulse{C.END} = Get ready (3 sec countdown)")
    print(f"    {C.RED}● RED{C.END} = SPEAK NOW!")
    print(f"    {C.GREEN}● GREEN{C.END} = Saved!")
    print(f"  {C.BLUE}─────────────────────────────────────────────{C.END}")

    if message:
        print(f"\n  {message}")

def main():
    port = find_esp32_port()
    if not port:
        print(f"{C.RED}ERROR: No ESP32 found!{C.END}")
        sys.exit(1)

    print(f"Found ESP32 on {port}")
    counts = count_existing_samples()

    # Find starting keyword
    kw_idx = 0
    for i, kw in enumerate(KEYWORDS):
        if counts[kw] < SAMPLES_PER_KEYWORD:
            kw_idx = i
            break
    else:
        print(f"{C.GREEN}All samples already collected!{C.END}")
        return

    try:
        ser = serial.Serial(port, 115200, timeout=10)
        time.sleep(2)
    except Exception as e:
        print(f"{C.RED}ERROR: {e}{C.END}")
        sys.exit(1)

    keyword = KEYWORDS[kw_idx]
    print_status(keyword, kw_idx, counts, f"{C.YELLOW}Waiting for ESP32...{C.END}")

    try:
        while kw_idx < len(KEYWORDS):
            keyword = KEYWORDS[kw_idx]

            # Read lines until we see AUDIO_START
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()

                if line == "AUDIO_START":
                    # Read the raw audio bytes
                    audio_data = ser.read(RECORD_SAMPLES * 2)

                    # Wait for AUDIO_END
                    while True:
                        end_line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if "AUDIO_END" in end_line:
                            break

                    # Process audio
                    if len(audio_data) >= RECORD_SAMPLES * 2:
                        samples = list(struct.unpack(f'<{RECORD_SAMPLES}h', audio_data[:RECORD_SAMPLES * 2]))
                        centered = find_speech_and_center(samples)

                        counts[keyword] += 1
                        filepath = save_wav(centered, keyword, counts[keyword])

                        # Check if keyword is done
                        if counts[keyword] >= SAMPLES_PER_KEYWORD:
                            kw_idx += 1
                            if kw_idx < len(KEYWORDS):
                                keyword = KEYWORDS[kw_idx]

                        if kw_idx >= len(KEYWORDS):
                            break

                        print_status(keyword, kw_idx, counts, f"{C.GREEN}Saved: {filepath.name}{C.END}")
                    else:
                        print_status(keyword, kw_idx, counts, f"{C.RED}Incomplete data, retrying...{C.END}")

                    break  # Go back to waiting for next AUDIO_START

                elif line:
                    # Show other output but keep waiting
                    print_status(keyword, kw_idx, counts, f"ESP32: {line}")

            if kw_idx >= len(KEYWORDS):
                break

        # Done!
        clear_screen()
        print(f"\n{C.GREEN}{C.BOLD}  ✓ ALL SAMPLES COLLECTED!{C.END}\n")
        print(f"  Samples saved to: {OUTPUT_DIR}")
        print(f"\n  Next steps:")
        print(f"  1. Go to Edge Impulse Studio")
        print(f"  2. Upload these samples")
        print(f"  3. Retrain the model")
        print(f"  4. Download new Arduino library\n")

    except KeyboardInterrupt:
        print(f"\n\n{C.YELLOW}Stopped.{C.END}")
        print(f"Collected {sum(counts.values())} samples.\n")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
