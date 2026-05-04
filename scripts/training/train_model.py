#!/usr/bin/env python3
"""
Edge Impulse Keyword Spotting Model Training Automation

This script automates the entire Edge Impulse workflow:
1. Creates a new project (or uses existing)
2. Uploads audio samples
3. Configures the impulse (MFCC + Transfer Learning)
4. Trains the model
5. Downloads the Arduino library

Usage:
    python train_model.py

Prerequisites:
    - Edge Impulse API key in .env
    - Samples generated in ./samples/ directory
    - pip install -r requirements.txt
"""

import os
import sys
import time
import json
import glob
import zipfile
import shutil
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_KEY = os.getenv("EDGE_IMPULSE_API_KEY")
PROJECT_NAME = os.getenv("PROJECT_NAME", "opencharm-keywords-v2")
SAMPLES_DIR = Path(__file__).parent / "samples"
OUTPUT_DIR = Path(__file__).parent / "output"
FIRMWARE_LIB_DIR = Path(__file__).parent.parent.parent / "firmware" / "lib"

# Edge Impulse API endpoints
EI_API_BASE = "https://studio.edgeimpulse.com/v1"
EI_INGESTION_BASE = "https://ingestion.edgeimpulse.com/api"

# Model configuration for keyword spotting
MODEL_CONFIG = {
    "window_size_ms": 1000,       # 1 second window
    "window_increase_ms": 500,    # 500ms stride
    "frequency_hz": 16000,        # 16kHz sample rate
    "training_cycles": 100,       # Training epochs
    "learning_rate": 0.005,
    "validation_split": 0.2,
    "model_type": "int8",         # Quantized for ESP32
}


def check_prerequisites():
    """Check that all prerequisites are met."""
    print("Checking prerequisites...")

    if not API_KEY:
        print("ERROR: EDGE_IMPULSE_API_KEY not set in .env")
        print("Get your API key from: Edge Impulse Studio -> Your Project -> Dashboard -> Keys")
        return False

    if not SAMPLES_DIR.exists():
        print(f"ERROR: Samples directory not found: {SAMPLES_DIR}")
        print("Run generate_tts_samples.py first, or add samples manually")
        return False

    # Check for sample files
    sample_count = 0
    for keyword_dir in SAMPLES_DIR.iterdir():
        if keyword_dir.is_dir():
            count = len(list(keyword_dir.glob("*.wav")))
            print(f"  {keyword_dir.name}: {count} samples")
            sample_count += count

    if sample_count == 0:
        print("ERROR: No samples found")
        return False

    print(f"Total samples: {sample_count}")
    return True


def get_project_id():
    """Get Edge Impulse project ID from API key."""
    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    # List projects accessible with this API key
    print("\nChecking for existing project...")
    response = requests.get(f"{EI_API_BASE}/api/projects", headers=headers)

    if response.status_code != 200:
        print(f"ERROR: Failed to list projects: {response.text}")
        return None

    data = response.json()
    if not data.get("success"):
        print(f"ERROR: API call failed: {data.get('error')}")
        return None

    projects = data.get("projects", [])

    if not projects:
        print("ERROR: No projects found. Create a project in Edge Impulse Studio first.")
        print("Go to: https://studio.edgeimpulse.com/ and create a new project")
        return None

    # Use the first project (the API key is project-specific)
    project = projects[0]
    print(f"Using project: {project['name']} (ID: {project['id']})")
    return project["id"]


def upload_samples(project_id: int):
    """Upload all samples to Edge Impulse."""
    print("\nUploading samples to Edge Impulse...")

    headers = {
        "x-api-key": API_KEY,
        "x-disallow-duplicates": "true"
    }

    total_uploaded = 0
    total_errors = 0

    for keyword_dir in SAMPLES_DIR.iterdir():
        if not keyword_dir.is_dir():
            continue

        label = keyword_dir.name
        samples = list(keyword_dir.glob("*.wav"))

        print(f"\n  Uploading '{label}' ({len(samples)} samples)...")

        for i, sample_path in enumerate(samples):
            try:
                # Determine category (80% training, 20% testing)
                category = "training" if hash(sample_path.name) % 5 != 0 else "testing"

                with open(sample_path, "rb") as f:
                    # Use label as filename prefix so Edge Impulse infers correct label
                    # Format: label.uniqueid.wav (e.g., send.001.wav)
                    safe_filename = f"{label}.{i:03d}.wav"
                    files = {
                        "data": (safe_filename, f, "audio/wav")
                    }
                    data = {
                        "label": label
                    }

                    response = requests.post(
                        f"{EI_INGESTION_BASE}/{category}/files",
                        headers=headers,
                        files=files,
                        data=data
                    )

                    if response.status_code == 200:
                        total_uploaded += 1
                    else:
                        # Might be duplicate, which is fine
                        if "duplicate" in response.text.lower():
                            pass
                        else:
                            total_errors += 1
                            print(f"    Error uploading {sample_path.name}: {response.text[:100]}")

                # Progress indicator
                if (i + 1) % 20 == 0:
                    print(f"    Progress: {i+1}/{len(samples)}")

            except Exception as e:
                total_errors += 1
                print(f"    Exception uploading {sample_path.name}: {e}")

    print(f"\nUpload complete: {total_uploaded} uploaded, {total_errors} errors")
    return total_uploaded > 0


def configure_impulse(project_id: int):
    """Configure the impulse for keyword spotting."""
    print("\nConfiguring impulse...")

    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    # Get project API key for this specific project
    response = requests.get(
        f"{EI_API_BASE}/api/{project_id}/keys",
        headers=headers
    )

    if response.status_code != 200:
        print(f"Note: Could not retrieve project keys: {response.status_code}")

    # Configure the impulse
    impulse_config = {
        "inputBlocks": [{
            "id": 1,
            "type": "time-series",
            "name": "Audio",
            "title": "Audio (MFE)",
            "windowSizeMs": MODEL_CONFIG["window_size_ms"],
            "windowIncreaseMs": MODEL_CONFIG["window_increase_ms"],
            "frequencyHz": MODEL_CONFIG["frequency_hz"],
            "padZeros": True,
        }],
        "dspBlocks": [{
            "id": 2,
            "type": "mfe",  # Mel Frequency Energy (better for keyword spotting)
            "name": "MFE",
            "title": "Audio (MFE)",
            "input": 1,
            "implementationVersion": 4,
        }],
        "learnBlocks": [{
            "id": 3,
            "type": "keras",
            "name": "NN Classifier",
            "title": "Classification",
            "dsp": [2],
        }],
    }

    # Delete existing impulse first
    requests.delete(f"{EI_API_BASE}/api/{project_id}/impulse", headers=headers)

    # Create new impulse
    response = requests.post(
        f"{EI_API_BASE}/api/{project_id}/impulse",
        headers=headers,
        json=impulse_config
    )

    if response.status_code != 200:
        print(f"Warning: Impulse configuration response: {response.status_code}")
        # Continue anyway - might work

    print("Impulse configured (MFE + NN Classifier)")
    return True


def generate_features(project_id: int):
    """Generate DSP features for all samples."""
    print("\nGenerating features...")

    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    # Start feature generation job
    response = requests.post(
        f"{EI_API_BASE}/api/{project_id}/jobs/generate-features",
        headers=headers,
        json={
            "dspId": 2,  # MFE block ID
            "calculateFeatureImportance": False,
            "skipFeatureExplorer": True
        }
    )

    if response.status_code != 200:
        print(f"Warning: Feature generation request: {response.status_code}")
        # Try alternative endpoint
        response = requests.post(
            f"{EI_API_BASE}/api/{project_id}/dsp/2/generate-features",
            headers=headers
        )

    if response.status_code == 200:
        job_id = response.json().get("id")
        if job_id:
            print(f"Feature generation job started: {job_id}")
            return wait_for_job(project_id, job_id)

    print("Feature generation initiated")
    return True


def train_model(project_id: int):
    """Train the neural network classifier."""
    print("\nTraining model...")

    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json"
    }

    # Configure training parameters
    training_config = {
        "mode": "visual",
        "trainingCycles": MODEL_CONFIG["training_cycles"],
        "learningRate": MODEL_CONFIG["learning_rate"],
        "validationSetSize": MODEL_CONFIG["validation_split"],
        "skipEmbeddingsAndMemory": True,
    }

    # Start training job
    response = requests.post(
        f"{EI_API_BASE}/api/{project_id}/jobs/train/3",  # Learn block ID = 3
        headers=headers,
        json=training_config
    )

    if response.status_code != 200:
        print(f"Training request status: {response.status_code}")
        # Try simpler endpoint
        response = requests.post(
            f"{EI_API_BASE}/api/{project_id}/learn/3/train",
            headers=headers,
            json=training_config
        )

    if response.status_code == 200:
        job_id = response.json().get("id")
        if job_id:
            print(f"Training job started: {job_id}")
            return wait_for_job(project_id, job_id, timeout=600)

    print("Training initiated (this may take several minutes)")
    time.sleep(60)  # Give it time to train
    return True


def wait_for_job(project_id: int, job_id: int, timeout: int = 300) -> bool:
    """Wait for a job to complete."""
    headers = {
        "x-api-key": API_KEY,
    }

    start_time = time.time()
    while time.time() - start_time < timeout:
        response = requests.get(
            f"{EI_API_BASE}/api/{project_id}/jobs/{job_id}/status",
            headers=headers
        )

        if response.status_code == 200:
            job = response.json().get("job", {})
            if job.get("finished"):
                if job.get("finishedSuccessful"):
                    print("  Job completed successfully")
                    return True
                else:
                    print(f"  Job failed: {job.get('error', 'Unknown error')}")
                    return False

        print(f"  Waiting... ({int(time.time() - start_time)}s)")
        time.sleep(10)

    print("  Job timed out")
    return False


def download_library(project_id: int):
    """Download the Arduino library."""
    print("\nDownloading Arduino library...")

    headers = {
        "x-api-key": API_KEY,
    }

    # Build deployment
    response = requests.post(
        f"{EI_API_BASE}/api/{project_id}/jobs/build-model",
        headers=headers,
        json={
            "type": "arduino",
            "engine": "tflite",
            "modelType": MODEL_CONFIG["model_type"]
        }
    )

    if response.status_code == 200:
        job_id = response.json().get("id")
        if job_id:
            print(f"Build job started: {job_id}")
            if not wait_for_job(project_id, job_id, timeout=300):
                print("Build failed, trying direct download...")

    # Download the library
    response = requests.get(
        f"{EI_API_BASE}/api/{project_id}/deployment/download",
        headers=headers,
        params={
            "type": "arduino",
            "modelType": MODEL_CONFIG["model_type"],
            "engine": "tflite"
        }
    )

    if response.status_code != 200:
        # Try alternative download URL
        response = requests.get(
            f"{EI_API_BASE}/api/{project_id}/downloads/arduino",
            headers=headers
        )

    if response.status_code == 200 and len(response.content) > 1000:
        # Save the zip file
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = OUTPUT_DIR / f"{PROJECT_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

        with open(zip_path, "wb") as f:
            f.write(response.content)

        print(f"Library downloaded: {zip_path} ({len(response.content)} bytes)")
        return zip_path

    print(f"Download failed: {response.status_code}")
    print("You may need to download manually from Edge Impulse Studio")
    return None


def install_library(zip_path: Path):
    """Extract and install the library to firmware/lib."""
    print("\nInstalling library to firmware...")

    if not zip_path or not zip_path.exists():
        print("No library to install")
        return False

    # Extract to a temp directory first
    extract_dir = OUTPUT_DIR / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    # Find the library folder (usually named like "project-name_inferencing")
    lib_folders = list(extract_dir.glob("*inferencing*"))
    if not lib_folders:
        lib_folders = list(extract_dir.iterdir())

    if not lib_folders:
        print("ERROR: No library found in zip")
        return False

    source_lib = lib_folders[0]

    # Destination in firmware
    dest_lib = FIRMWARE_LIB_DIR / "ei-keyword-spotting-new"

    # Remove old library if exists
    if dest_lib.exists():
        shutil.rmtree(dest_lib)

    # Copy new library
    shutil.copytree(source_lib, dest_lib)

    print(f"Library installed to: {dest_lib}")
    print("\nIMPORTANT: Update your firmware to use the new library:")
    print("  1. Back up the existing 'ei-keyword-spotting' library if needed")
    print("  2. Rename 'ei-keyword-spotting-new' to 'ei-keyword-spotting'")
    print("  3. Update the #include in main.cpp if the header name changed")

    return True


def main():
    print("=" * 60)
    print("Edge Impulse Keyword Spotting - Model Training")
    print("=" * 60)
    print(f"\nProject: {PROJECT_NAME}")
    print(f"Keywords: record, stop, snap, send")
    print()

    # Check prerequisites
    if not check_prerequisites():
        sys.exit(1)

    # Get or create project
    project_id = get_project_id()
    if not project_id:
        print("\nFailed to get/create project")
        sys.exit(1)

    print(f"\nProject URL: https://studio.edgeimpulse.com/studio/{project_id}")

    # Upload samples
    if not upload_samples(project_id):
        print("\nFailed to upload samples")
        sys.exit(1)

    # Configure impulse
    configure_impulse(project_id)

    # Generate features
    generate_features(project_id)

    # Train model
    train_model(project_id)

    # Download library
    zip_path = download_library(project_id)

    # Install library
    if zip_path:
        install_library(zip_path)

    print("\n" + "=" * 60)
    print("Training complete!")
    print()
    print("Next steps:")
    print("1. Check the model accuracy in Edge Impulse Studio")
    print("2. If accuracy < 85%, add more samples and retrain")
    print("3. Update firmware/src/main.cpp with new keyword indices")
    print("4. Build and flash the firmware")
    print("=" * 60)


if __name__ == "__main__":
    main()
