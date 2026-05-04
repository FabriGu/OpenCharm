# OpenCharm + Claude Code - Quick Start Guide

**Goal:** Get voice commands executing code in under 30 minutes

---

## Prerequisites

✅ **Already Working:**
- ESP32S3 firmware running
- Relay server (`relay_server.py`) functional
- Whisper transcription working
- Telegram bot configured

✅ **Need to Add:**
- Claude Code CLI installed
- Python asyncio knowledge (basic)

---

## 5-Minute Test: Can This Work?

**Test Claude Code streaming:**

```bash
# Test 1: Interactive mode (confirm it works)
claude

# Test 2: Streaming JSON mode (what we'll use)
echo '{"type": "user", "content": "Execute git status in this project"}' | \
  claude --print \
    --input-format stream-json \
    --output-format stream-json \
    --include-partial-messages

# Expected output: JSON lines with git status result
```

**If this works, you're good to go!** If not:
- Check: `which claude` → `/opt/homebrew/bin/claude`
- Update: `npm install -g @anthropic-ai/claude-code` (or equivalent)
- Verify: `claude --version`

---

## 10-Minute Prototype: Basic Integration

**Step 1: Create minimal interface (3 min)**

```python
# relay/claude_test.py
import asyncio
import json

async def test_claude():
    # Start subprocess
    process = await asyncio.create_subprocess_exec(
        'claude', '--print',
        '--input-format', 'stream-json',
        '--output-format', 'stream-json',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd='/Users/fabrizioguccione/Projects/OpenCharm'
    )

    # Send command
    message = json.dumps({
        "type": "user",
        "content": "Execute: git status"
    }) + '\n'
    process.stdin.write(message.encode())
    await process.stdin.drain()

    # Read response
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        response = json.loads(line.decode())
        print(f"Type: {response.get('type')}")
        if response.get('type') == 'message':
            print(f"Response: {response.get('content')}")
            break

    # Cleanup
    process.terminate()
    await process.wait()

if __name__ == "__main__":
    asyncio.run(test_claude())
```

**Step 2: Run it (2 min)**

```bash
cd /Users/fabrizioguccione/Projects/OpenCharm/relay
python3 claude_test.py
```

**Expected output:**
```
Type: partial
Type: partial
Type: tool_use
Type: tool_result
Type: message
Response: On branch wifi
Changes not staged for commit:
  modified: relay/.env
```

**Step 3: If it works, continue. If not, debug:** (5 min)
- Check: Claude Code version compatible?
- Check: Working directory correct?
- Check: stdin/stdout not buffered?

---

## 30-Minute Integration: Full Setup

### A. Create `claude_interface.py` (10 min)

Copy the complete implementation from `docs/implementation-guide.md` → Section 1.1

Or minimal version:

```python
# relay/claude_interface.py
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

class ClaudeCodeInterface:
    def __init__(self):
        self.process = None
        self._running = False

    async def start(self):
        logger.info("Starting Claude Code...")
        self.process = await asyncio.create_subprocess_exec(
            'claude', '--print',
            '--input-format', 'stream-json',
            '--output-format', 'stream-json',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd='/Users/fabrizioguccione/Projects/OpenCharm'
        )
        self._running = True
        logger.info("Claude Code started")

    async def stop(self):
        if self.process:
            self.process.terminate()
            await self.process.wait()
        self._running = False

    async def execute_command(self, command: str) -> str:
        # Send
        message = json.dumps({"type": "user", "content": f"Execute: {command}"}) + '\n'
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()

        # Receive
        full_response = ""
        while True:
            line = await self.process.stdout.readline()
            response = json.loads(line.decode())
            if response.get('type') == 'message':
                full_response = response.get('content', '')
                break

        return full_response
```

### B. Integrate into `relay_server.py` (10 min)

**Add to imports:**
```python
from claude_interface import ClaudeCodeInterface
```

**Add to lifespan:**
```python
claude_interface = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global claude_interface

    # Startup
    logger.info("Starting Claude Code subprocess...")
    claude_interface = ClaudeCodeInterface()
    await claude_interface.start()

    yield

    # Shutdown
    if claude_interface:
        await claude_interface.stop()
```

**Add new endpoint:**
```python
@app.post("/execute/command")
async def execute_command(command: str = Form(...)):
    """Execute command via Claude Code"""
    if not claude_interface:
        raise HTTPException(503, "Claude Code unavailable")

    try:
        response = await claude_interface.execute_command(command)
        await send_telegram_text(f"*Command:* {command}\n\n{response}")
        return {"status": "ok", "response": response}
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(500, str(e))
```

### C. Test integration (5 min)

**Terminal 1: Start server**
```bash
cd /Users/fabrizioguccione/Projects/OpenCharm/relay
uvicorn relay_server:app --host 0.0.0.0 --port 8080 --reload
```

**Terminal 2: Test endpoint**
```bash
curl -X POST http://localhost:8080/execute/command \
  -F "command=git status"
```

**Expected:** JSON response with git status output

### D. Connect to ESP32 workflow (5 min)

**Modify `/capture/audio` endpoint:**

```python
@app.post("/capture/audio")
async def capture_audio(audio: UploadFile = File(...)):
    # ... existing transcription code ...

    # NEW: Execute with Claude Code instead of Ollama
    if claude_interface and claude_interface._running:
        try:
            response = await claude_interface.execute_command(transcription)
            await send_telegram_text(response)
        except Exception as e:
            logger.warning(f"Claude Code failed, using fallback: {e}")
            # Fallback to existing Ollama/OpenAI
            response = await analyze_text(transcription)
            await send_telegram_text(response)

    # ... rest of existing code ...
```

---

## Testing Your Integration

### Test 1: Direct API Call

```bash
# Should execute git status via Claude Code
curl -X POST http://localhost:8080/execute/command \
  -F "command=git status"
```

### Test 2: Simulate ESP32 Voice Command

```bash
# Record yourself saying "git status" or use test WAV file
curl -X POST http://localhost:8080/capture/audio \
  -F "audio=@test_audio.wav"

# Check Telegram for response
```

### Test 3: End-to-End (Real ESP32)

```arduino
// ESP32: Say "RECORD" → "git status" → "STOP"
// Relay: Transcribe → Claude Code → Telegram
// You: Check Telegram for git output
```

---

## Troubleshooting

### "Claude Code unavailable"
- Check: `claude_interface` initialized in lifespan?
- Check: Subprocess started successfully?
- Check: `logger.info` messages in console?

### "Invalid JSON from Claude"
- Check: Claude Code version supports stream-json?
- Check: stdout not contaminated with debug prints?
- Add: More detailed logging of raw output

### "Subprocess died"
- Check: stderr output for crash details
- Check: Memory/CPU usage
- Add: Auto-restart logic

### Timeout waiting for response
- Check: Command is blocking? (interactive prompt)
- Increase: Timeout value (default 120s)
- Test: With simple command first (`pwd`)

---

## Success Criteria

✅ You're ready for demo when:
- [ ] `curl` test returns git status successfully
- [ ] Voice command "git status" → Telegram response
- [ ] Latency < 5 seconds end-to-end
- [ ] Subprocess survives multiple requests
- [ ] Fallback works if subprocess fails

---

## What's Next?

After basic integration works:

1. **Add safety layer** (Phase 2)
   - Implement `ActionConfirmation` class
   - Test confirmation prompts
   - Validate command classification

2. **Enhanced features** (Phase 3)
   - WebSocket progress updates
   - Streaming partial responses
   - Multi-modal (image + voice)

3. **Demo polish** (Phase 4)
   - Pre-demo checklist
   - Fallback responses
   - Performance tuning

---

## Quick Command Reference

```bash
# Start relay server
uvicorn relay_server:app --host 0.0.0.0 --port 8080

# Test Claude Code standalone
echo '{"type": "user", "content": "pwd"}' | claude --print --stream-json

# Test via API
curl -X POST http://localhost:8080/execute/command -F "command=pwd"

# Monitor logs
tail -f relay_server.log

# Check health
curl http://localhost:8080/health
```

---

## File Locations

- **Architecture Design:** `docs/network-architecture.md`
- **Current Relay Server:** `relay/relay_server.py`
- **Test This File:** `claude_test.py` (create in relay/ directory)

---

**Ready? Start with the 5-minute test above, then proceed to 30-minute integration!**

Good luck! 🚀
