#!/usr/bin/env python3
"""
Quick test for Google Cloud TTS setup.
Run this to verify your credentials are working.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment
load_dotenv()

def test_tts():
    print("=" * 50)
    print("Google Cloud TTS Test")
    print("=" * 50)

    # Check credentials
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path:
        print("\nERROR: GOOGLE_APPLICATION_CREDENTIALS not set")
        print("Add it to .env file or export it:")
        print("  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json")
        return False

    if not Path(creds_path).exists():
        print(f"\nERROR: Credentials file not found: {creds_path}")
        return False

    print(f"\nCredentials: {creds_path}")
    print("  File exists: Yes")

    # Try to import and use TTS
    try:
        from google.cloud import texttospeech
        print("  Import: Success")
    except ImportError:
        print("\nERROR: google-cloud-texttospeech not installed")
        print("Run: pip install google-cloud-texttospeech")
        return False

    # Try to create client
    try:
        client = texttospeech.TextToSpeechClient()
        print("  Client: Connected")
    except Exception as e:
        print(f"\nERROR: Failed to create client: {e}")
        return False

    # Generate a test sample
    try:
        # Use "rec-ord" to force verb pronunciation (re-CORD not REH-cord)
        synthesis_input = texttospeech.SynthesisInput(text="rec-ord")
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Wavenet-D",
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        )

        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        # Save test file
        output_path = Path(__file__).parent / "test_record.wav"
        with open(output_path, "wb") as f:
            f.write(response.audio_content)

        print(f"  Test file: {output_path}")
        print(f"  Size: {len(response.audio_content)} bytes")

    except Exception as e:
        print(f"\nERROR: TTS generation failed: {e}")
        return False

    print("\n" + "=" * 50)
    print("SUCCESS! Google TTS is working correctly.")
    print("You can now run: python generate_tts_samples.py")
    print("=" * 50)
    return True


if __name__ == "__main__":
    test_tts()
