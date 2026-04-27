#!/usr/bin/env python3
"""
Clean Upload to Edge Impulse

1. Deletes ALL existing samples from the Edge Impulse project
2. Uploads only the current samples (real keywords + noise + unknown)

This ensures no old TTS samples contaminate the training data.
"""

import os
import sys
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("EDGE_IMPULSE_API_KEY")
EI_API_BASE = "https://studio.edgeimpulse.com/v1"
EI_INGESTION_BASE = "https://ingestion.edgeimpulse.com/api"
SAMPLES_DIR = Path(__file__).parent / "samples"

def get_project_id():
    """Get project ID from API key."""
    headers = {"x-api-key": API_KEY}
    response = requests.get(f"{EI_API_BASE}/api/projects", headers=headers)

    if response.status_code != 200:
        print(f"ERROR: Failed to get projects: {response.text}")
        return None

    data = response.json()
    if data.get("projects"):
        project = data["projects"][0]
        print(f"Project: {project['name']} (ID: {project['id']})")
        return project["id"]
    return None

def delete_all_samples(project_id):
    """Delete ALL samples from the project."""
    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}

    print("\nDeleting all existing samples...")

    # Get all samples
    for category in ["training", "testing"]:
        print(f"  Fetching {category} samples...")

        response = requests.get(
            f"{EI_API_BASE}/api/{project_id}/raw-data",
            headers=headers,
            params={"category": category, "limit": 10000}
        )

        if response.status_code != 200:
            print(f"  Warning: Could not fetch {category} samples")
            continue

        data = response.json()
        samples = data.get("samples", [])

        if not samples:
            print(f"  No {category} samples found")
            continue

        print(f"  Found {len(samples)} {category} samples, deleting...")

        # Delete in batches
        sample_ids = [s["id"] for s in samples]
        batch_size = 100

        for i in range(0, len(sample_ids), batch_size):
            batch = sample_ids[i:i+batch_size]

            response = requests.post(
                f"{EI_API_BASE}/api/{project_id}/raw-data/delete",
                headers=headers,
                json={"samples": batch}
            )

            if response.status_code == 200:
                print(f"    Deleted {min(i+batch_size, len(sample_ids))}/{len(sample_ids)}")
            else:
                print(f"    Error deleting batch: {response.status_code}")

        # Wait a bit for Edge Impulse to process
        time.sleep(1)

    print("  All samples deleted!")

def upload_samples(project_id):
    """Upload all current samples."""
    headers = {
        "x-api-key": API_KEY,
        "x-disallow-duplicates": "false"  # Allow re-upload
    }

    print("\nUploading new samples...")

    total_uploaded = 0
    total_errors = 0

    for keyword_dir in sorted(SAMPLES_DIR.iterdir()):
        if not keyword_dir.is_dir():
            continue

        label = keyword_dir.name
        samples = sorted(keyword_dir.glob("*.wav"))

        if not samples:
            continue

        print(f"\n  {label}: {len(samples)} samples")

        for i, sample_path in enumerate(samples):
            try:
                # 80% training, 20% testing
                category = "training" if i % 5 != 0 else "testing"

                with open(sample_path, "rb") as f:
                    files = {"data": (f"{label}.{i:03d}.wav", f, "audio/wav")}
                    data = {"label": label}

                    response = requests.post(
                        f"{EI_INGESTION_BASE}/{category}/files",
                        headers=headers,
                        files=files,
                        data=data
                    )

                    if response.status_code == 200:
                        total_uploaded += 1
                    else:
                        total_errors += 1
                        if "duplicate" not in response.text.lower():
                            print(f"    Error: {sample_path.name}: {response.text[:80]}")

                # Progress every 50 samples
                if (i + 1) % 50 == 0:
                    print(f"    {i+1}/{len(samples)}")

            except Exception as e:
                total_errors += 1
                print(f"    Exception: {e}")

    print(f"\n  Upload complete: {total_uploaded} uploaded, {total_errors} errors")
    return total_uploaded > 0

def main():
    print("=" * 50)
    print("Edge Impulse - Clean Upload")
    print("=" * 50)

    if not API_KEY:
        print("ERROR: No API key found in .env")
        sys.exit(1)

    # Count samples
    print("\nLocal samples:")
    total = 0
    for d in sorted(SAMPLES_DIR.iterdir()):
        if d.is_dir():
            count = len(list(d.glob("*.wav")))
            print(f"  {d.name}: {count}")
            total += count
    print(f"  Total: {total}")

    project_id = get_project_id()
    if not project_id:
        sys.exit(1)

    # Delete existing
    delete_all_samples(project_id)

    # Upload new
    upload_samples(project_id)

    print("\n" + "=" * 50)
    print("Done! Now go to Edge Impulse Studio:")
    print(f"  https://studio.edgeimpulse.com/studio/{project_id}")
    print()
    print("Steps:")
    print("  1. Data acquisition -> verify samples are correct")
    print("  2. Impulse design -> Create impulse if needed")
    print("  3. MFE -> Generate features")
    print("  4. Classifier -> Train model")
    print("  5. Deployment -> Download Arduino library")
    print("=" * 50)

if __name__ == "__main__":
    main()
