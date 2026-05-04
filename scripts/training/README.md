# Edge Impulse Keyword Spotting Training

Automated training scripts for the OpenCharm keyword spotting model.

## Keywords

- **RECORD** - Start audio recording
- **STOP** - Stop recording (don't process)
- **SNAP** - Take photo with countdown
- **SEND** - Process all accumulated content with AI

## Quick Start

### 1. Install Dependencies

```bash
cd scripts/training
pip install -r requirements.txt
```

### 2. Configure Credentials

```bash
cp .env.example .env
# Edit .env with your API keys
```

**Required:**
- `EDGE_IMPULSE_API_KEY` - Get from Edge Impulse Studio > Your Project > Dashboard > Keys

**Optional (for synthetic voice generation):**
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to Google Cloud service account JSON

### 3. Generate Voice Samples

**Option A: Use Google TTS (automated)**
```bash
python generate_tts_samples.py
```

**Option B: Record your own voice**
Place WAV files (16kHz, mono, 1 second) in:
```
samples/
  record/
    record_001.wav
    record_002.wav
    ...
  stop/
    stop_001.wav
    ...
  snap/
  send/
  noise/
  unknown/
```

**Option C: Both (recommended)**
Generate TTS samples, then add your own recordings for better accuracy.

### 4. Train Model

```bash
python train_model.py
```

This will:
1. Upload samples to Edge Impulse
2. Configure the impulse (MFE + Transfer Learning)
3. Train the model
4. Download the Arduino library

### 5. Install in Firmware

The script will install the library to `firmware/lib/ei-keyword-spotting-new/`.

Manually update:
1. Back up the existing `ei-keyword-spotting` library if needed
2. Rename `ei-keyword-spotting-new` to `ei-keyword-spotting`
3. Update `main.cpp` include if needed

## Manual Training (Alternative)

If the automation doesn't work, you can train manually in Edge Impulse Studio:

1. Go to https://studio.edgeimpulse.com
2. Create new project
3. Upload samples from `./samples/` directory
4. Create impulse: Time Series → MFE → Classification
5. Train model
6. Download Arduino library
7. Extract to `firmware/lib/ei-keyword-spotting/`

## Tips for Better Accuracy

1. **Add your own voice samples** - TTS is good but your voice will be more accurate
2. **Record in your environment** - Background noise matters
3. **Vary your pronunciation** - Fast, slow, loud, quiet
4. **50+ samples per keyword minimum** - More is better
5. **Good noise/unknown class** - Prevents false positives

## Troubleshooting

### "API key not found"
- Make sure `.env` file exists with your `EDGE_IMPULSE_API_KEY`
- Get key from Edge Impulse Studio > Dashboard > Keys

### "Google TTS not available"
- Install: `pip install google-cloud-texttospeech`
- Set up credentials: https://cloud.google.com/text-to-speech/docs/quickstart

### "Training failed"
- Check Edge Impulse Studio for detailed error messages
- Make sure you have enough samples (50+ per keyword)
- Try reducing training cycles if memory issues

### "Model accuracy too low"
- Add more samples, especially your own voice
- Improve unknown/noise class diversity
- Try different DSP settings (MFE vs MFCC)
