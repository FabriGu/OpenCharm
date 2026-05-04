# OpenCharm Network Architecture Design

**Author:** Network Infrastructure Design Agent
**Date:** 2026-03-25
**Purpose:** Design communication layer for ESP32S3 → Relay Server → Claude Code integration

---

## Executive Summary

This document proposes a **hybrid HTTP + subprocess stdin/stdout** architecture for connecting the OpenCharm ESP32S3 device to Claude Code through a relay server. The design prioritizes **demo reliability**, **action safety**, and **minimal firmware changes**.

### Key Recommendations
1. **Keep existing ESP32 HTTP interface** (don't touch firmware)
2. **Use subprocess stdin/stdout** for Claude Code communication (native, reliable)
3. **Implement action confirmation layer** before execution
4. **Add WebSocket for real-time progress updates** (optional, demo enhancement)

---

## Current Architecture Analysis

### ESP32S3 → Relay Server (EXISTING - DO NOT CHANGE)
```
Protocol:     HTTP POST (multipart/form-data)
Transport:    WiFi → Local network
Endpoints:
  - POST /capture/image          → Direct processing
  - POST /capture/audio          → Direct processing
  - POST /session/image          → Session buffer
  - POST /session/audio          → Session buffer
  - POST /session/process        → Trigger AI analysis
Server:       FastAPI (uvicorn) on 192.168.1.181:8080
Format:       Multipart form data (image/jpeg, audio/wav)
Processing:   Async handlers with background tasks
AI Backend:   OpenAI GPT-4o, Ollama (local/school), Anthropic
Output:       Telegram bot messages
```

**Characteristics:**
- ✅ **Reliable:** HTTP is well-tested, timeout handling works
- ✅ **Async:** FastAPI async handlers don't block ESP32
- ✅ **Proven:** Currently working in production
- ⚠️ **One-way:** ESP32 sends, gets HTTP 200, no real-time feedback
- ⚠️ **Fixed workflow:** Hard to add new actions without firmware update

---

## Proposed Architecture: Relay Server → Claude Code

### Option 1: Subprocess stdin/stdout (RECOMMENDED)

```python
# In relay_server.py
import asyncio
import json

class ClaudeCodeInterface:
    def __init__(self):
        self.process = None
        self.input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue()

    async def start(self):
        """Start Claude Code subprocess with streaming I/O"""
        self.process = await asyncio.create_subprocess_exec(
            'claude', '--print',
            '--input-format', 'stream-json',
            '--output-format', 'stream-json',
            '--include-partial-messages',
            '--replay-user-messages',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd='/Users/fabrizioguccione/Projects/OpenCharm'
        )

        # Start reader/writer tasks
        asyncio.create_task(self._read_output())
        asyncio.create_task(self._write_input())

    async def _write_input(self):
        """Stream JSON messages to Claude Code stdin"""
        while True:
            message = await self.input_queue.get()
            json_str = json.dumps(message) + '\n'
            self.process.stdin.write(json_str.encode())
            await self.process.stdin.drain()

    async def _read_output(self):
        """Stream JSON responses from Claude Code stdout"""
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                response = json.loads(line.decode())
                await self.output_queue.put(response)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from Claude: {line}")

    async def send_task(self, task: dict, context: dict) -> dict:
        """Send a task to Claude Code and get response"""
        # Format prompt with context
        prompt = self._format_prompt(task, context)

        # Send message
        message = {
            "type": "user",
            "content": prompt,
            "attachments": context.get("attachments", [])
        }
        await self.input_queue.put(message)

        # Collect response chunks
        full_response = ""
        while True:
            chunk = await self.output_queue.get()

            # Handle partial messages (streaming)
            if chunk.get("type") == "partial":
                full_response += chunk.get("content", "")
                # Optional: Send progress via WebSocket

            # Handle complete messages
            elif chunk.get("type") == "message":
                full_response += chunk.get("content", "")
                break

        return {"response": full_response}

    def _format_prompt(self, task: dict, context: dict) -> str:
        """Format task into Claude Code prompt"""
        task_type = task.get("type")

        if task_type == "analyze_image":
            return f"""Analyze this image from the OpenCharm wearable device.

Context:
- Captured: {context.get('timestamp')}
- User request: {context.get('voice_command', 'None')}

Task: Provide brief, actionable feedback about the image.
Respond in 1-2 sentences, imperative voice."""

        elif task_type == "voice_command":
            return f"""Execute this voice command from the OpenCharm device.

Voice input: "{context.get('transcription')}"
Location: /Users/fabrizioguccione/Projects/OpenCharm

SAFETY RULES:
1. NEVER run destructive operations without explicit confirmation
2. File operations limited to project directory
3. No network operations (except localhost)
4. git operations allowed (read-only preferred)

Execute the command if safe, or explain what you would do if confirmation needed."""

        return "Unknown task type"
```

**Advantages:**
- ✅ **Native interface:** Claude Code designed for stdin/stdout
- ✅ **Streaming:** Partial messages for progress updates
- ✅ **Reliable:** No network layer to fail
- ✅ **Session management:** Built into Claude Code
- ✅ **Tool execution:** Full access to Bash, Edit, Read, etc.
- ✅ **Context aware:** Claude can read codebase files

**Disadvantages:**
- ⚠️ Process lifecycle management (restart on crash)
- ⚠️ Memory usage (long-running process)
- ⚠️ No direct Telegram integration (relay must forward)

---

### Option 2: HTTP API (BACKUP - if subprocess fails)

```python
# If Claude Code adds HTTP API in the future
import httpx

async def send_to_claude_http(prompt: str, files: dict = None):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:5555/v1/chat",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "files": files,
                "cwd": "/Users/fabrizioguccione/Projects/OpenCharm"
            },
            timeout=120.0
        )
        return response.json()
```

**Note:** Claude Code doesn't currently expose HTTP API, but this would be simpler if available.

---

### Option 3: WebSocket for Real-Time Updates (ENHANCEMENT)

```python
# Add WebSocket endpoint to relay server for demo feedback
from fastapi import WebSocket

@app.websocket("/ws/progress")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Register websocket for progress updates
    active_connections.add(websocket)

    try:
        while True:
            # Keep connection alive, send progress updates
            data = await websocket.receive_text()
    finally:
        active_connections.remove(websocket)

async def broadcast_progress(message: str):
    """Send progress update to all connected clients"""
    for ws in active_connections:
        await ws.send_json({"type": "progress", "message": message})
```

**Use case:** Demo dashboard showing real-time processing status
- "Transcribing audio..."
- "Analyzing image with Claude Vision..."
- "Executing code change..."
- "✓ Complete"

---

## Security & Safety Layer

### Action Confirmation Strategy

```python
class ActionConfirmation:
    """Safety layer for Claude Code actions"""

    # Whitelist: Auto-approve safe operations
    SAFE_COMMANDS = {
        'git status', 'git log', 'git diff',
        'ls', 'pwd', 'cat',
        'grep', 'find', 'head', 'tail'
    }

    # Require confirmation
    DANGEROUS_PATTERNS = [
        r'rm -rf',
        r'sudo',
        r'chmod',
        r'git push',
        r'npm install',
        r'pip install',
    ]

    async def should_confirm(self, command: str) -> bool:
        """Check if command needs user confirmation"""
        # Allow whitelisted commands
        if command.strip() in self.SAFE_COMMANDS:
            return False

        # Check dangerous patterns
        for pattern in self.DANGEROUS_PATTERNS:
            if re.search(pattern, command):
                return True

        # Check file write operations outside project
        if 'write' in command.lower() and '/Users/fabrizioguccione/Projects/OpenCharm' not in command:
            return True

        return False

    async def request_confirmation(self, command: str, context: str) -> bool:
        """Request confirmation via Telegram"""
        message = f"""⚠️ *Action Confirmation Required*

Command: `{command}`
Context: {context}

Reply with:
✅ /approve - Execute
❌ /deny - Cancel
"""
        await send_telegram_text(message)

        # Wait for user response (with timeout)
        response = await wait_for_telegram_response(timeout=60)
        return response == "/approve"
```

### File System Sandboxing

```python
ALLOWED_PATHS = [
    '/Users/fabrizioguccione/Projects/OpenCharm',
    '/tmp/opencharm-*'  # Temp files
]

def validate_path(path: str) -> bool:
    """Ensure file operations stay within project"""
    abs_path = os.path.abspath(path)
    return any(abs_path.startswith(allowed) for allowed in ALLOWED_PATHS)
```

---

## Message Flow Diagrams

### Flow 1: Voice Command → Code Execution

```
1. ESP32 → Relay Server
   POST /capture/audio → WAV file

2. Relay Server → Whisper
   Transcribe → "git status"

3. Relay Server → Claude Code (subprocess)
   stdin: {"type": "user", "content": "Execute: git status"}

4. Claude Code
   - Understands command
   - Uses Bash tool: git status
   - Returns output

5. Relay Server → Telegram
   Send formatted result

6. Relay Server → ESP32
   HTTP 200 OK (ESP32 LED feedback)
```

### Flow 2: Image Analysis → Actionable Feedback

```
1. ESP32 → Relay Server
   POST /capture/image → JPEG file

2. Relay Server → Claude Code
   stdin: {
     "type": "user",
     "content": "Analyze this workspace image",
     "attachments": [{"type": "image", "data": "base64..."}]
   }

3. Claude Code
   - Vision analysis
   - Suggests code improvement
   - Uses Edit tool to apply fix

4. Relay Server → Telegram
   "I see your desk setup. Fixed indentation in main.cpp"

5. Relay Server → ESP32
   HTTP 200 OK
```

### Flow 3: Session Workflow (RECORD → SNAP → POST)

```
1. Voice: "RECORD"
   ESP32 starts recording

2. Voice: "STOP"
   POST /session/audio → Buffered

3. Voice: "CAPTURE"
   POST /session/image → Buffered

4. Voice: "POST"
   POST /session/process → Process ALL

5. Relay Server → Claude Code
   stdin: {
     "type": "user",
     "content": "Context: User said '{transcription}' while showing image",
     "attachments": [{"type": "image", ...}, {"type": "audio", ...}]
   }

6. Claude Code
   - Multi-modal analysis
   - Executes appropriate tools
   - Returns comprehensive response
```

---

## Error Handling & Resilience

### Subprocess Management

```python
class ClaudeCodeManager:
    """Manage Claude Code subprocess lifecycle"""

    async def start_with_retry(self, max_retries: int = 3):
        """Start subprocess with exponential backoff"""
        for attempt in range(max_retries):
            try:
                await self.start()
                logger.info("Claude Code subprocess started")
                return
            except Exception as e:
                wait_time = 2 ** attempt
                logger.error(f"Subprocess start failed (attempt {attempt+1}): {e}")
                await asyncio.sleep(wait_time)

        raise RuntimeError("Failed to start Claude Code subprocess")

    async def health_check(self):
        """Periodic health check (heartbeat)"""
        while True:
            await asyncio.sleep(30)

            if self.process.returncode is not None:
                logger.error("Claude Code subprocess died! Restarting...")
                await self.start_with_retry()

    async def graceful_shutdown(self):
        """Clean shutdown on server stop"""
        if self.process:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
```

### Network Resilience

```python
# WiFi disconnect handling (already in ESP32 firmware)
# - Auto-reconnect on connection loss
# - Health check endpoint: GET /health
# - WiFi power save disabled for max performance

# Relay server timeouts
HTTP_TIMEOUT_MS = 30000          # 30s for normal operations
AI_TIMEOUT_MS = 120000           # 2min for AI processing
CLAUDE_CODE_TIMEOUT_MS = 180000  # 3min for complex operations

# Retry logic
async def send_with_retry(func, *args, max_retries=3, **kwargs):
    """Retry failed operations with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt
            logger.warning(f"Retry {attempt+1}/{max_retries} after {wait_time}s: {e}")
            await asyncio.sleep(wait_time)
```

---

## Demo Reliability Considerations

### Making Demos Bulletproof

1. **Pre-Demo Checklist**
   ```bash
   # Verify all services running
   - [ ] WiFi hotspot active (2.4GHz)
   - [ ] Relay server: uvicorn relay_server:app --host 0.0.0.0 --port 8080
   - [ ] Ollama/OpenAI API accessible
   - [ ] Telegram bot responding
   - [ ] ESP32 connected (green LED)
   - [ ] Claude Code subprocess healthy
   ```

2. **Visual Feedback Improvements**
   ```python
   # Add LED feedback for each stage
   LED_COLOR_PROCESSING = led.Color(255, 255, 0)    # Yellow: AI processing
   LED_COLOR_EXECUTING = led.Color(0, 255, 255)     # Cyan: Code execution
   LED_COLOR_WAITING = led.Color(128, 0, 128)       # Purple: Waiting for confirmation

   # Add audio feedback (optional)
   # - Short beep on command received
   # - Chime on success
   # - Buzz on error
   ```

3. **Fallback Responses**
   ```python
   # If Claude Code subprocess fails
   FALLBACK_RESPONSES = {
       "analyze_image": "Image received. [Claude Code unavailable - using vision API only]",
       "voice_command": "Voice command: '{cmd}'. [Manual execution required - Claude Code offline]"
   }
   ```

4. **Demo Mode**
   ```python
   # config.py
   DEMO_MODE = True  # Skip confirmations, use canned responses for speed

   if DEMO_MODE:
       # Pre-load responses for common commands
       DEMO_RESPONSES = {
           "git status": "On branch wifi\nChanges not staged...",
           "list files": "main.cpp  config.h  README.md"
       }
   ```

---

## Performance Optimization

### Latency Budget (Target: < 5s total)

```
User speaks → Result on Telegram
├─ ESP32 capture audio: 0.5-2s (variable, user speech length)
├─ WiFi transfer: 0.2s (local network)
├─ Whisper transcription: 1-3s (depends on model size)
├─ Claude Code execution: 1-5s (depends on task complexity)
├─ Telegram send: 0.3s
└─ ESP32 LED feedback: 0.1s

Optimizations:
1. Use Whisper 'base' model (1-2s vs 3-5s for 'medium')
2. Keep Claude Code subprocess warm (no cold start)
3. Use streaming responses (show partial results)
4. Parallel processing (transcribe while uploading)
```

### Resource Management

```python
# Memory limits
MAX_AUDIO_BUFFER = 16_000 * 10 * 2  # 10 sec @ 16kHz, 16-bit
MAX_IMAGE_SIZE = 2 * 1024 * 1024     # 2MB JPEG

# Concurrent operations
MAX_CONCURRENT_TASKS = 3  # Prevent relay overload

# Process limits
asyncio.Semaphore(MAX_CONCURRENT_TASKS)  # Limit parallel Claude Code calls
```

---

## Implementation Phases

### Phase 1: Basic Integration (Week 1)
- [ ] Create `ClaudeCodeInterface` class
- [ ] Test subprocess stdin/stdout communication
- [ ] Implement simple voice command execution
- [ ] Add error handling and logging

### Phase 2: Safety Layer (Week 2)
- [ ] Implement `ActionConfirmation` class
- [ ] Add Telegram confirmation prompts
- [ ] File system sandboxing
- [ ] Command whitelist/blacklist

### Phase 3: Enhanced Features (Week 3)
- [ ] WebSocket progress updates
- [ ] Streaming responses
- [ ] Session context management
- [ ] Multi-modal analysis (image + voice)

### Phase 4: Demo Polish (Week 4)
- [ ] LED feedback improvements
- [ ] Fallback responses
- [ ] Pre-demo health checks
- [ ] Performance tuning

---

## Alternative Architectures Considered

### ❌ Direct ESP32 → Claude Code
**Why not:**
- ESP32 limited HTTPS support (certificate validation issues)
- No way to run Claude Code on ESP32 (resource constraints)
- Relay server needed for Whisper/Vision anyway

### ❌ Cloud Relay (AWS Lambda, etc.)
**Why not:**
- Higher latency (round-trip to cloud)
- More points of failure
- Demo reliability depends on internet
- Cost considerations

### ❌ MQTT Pub/Sub
**Why not:**
- Overkill for single device
- Adds complexity (broker setup)
- No advantage over HTTP for this use case

### ✅ **Chosen: Hybrid HTTP + Subprocess**
**Why:**
- Minimal changes to working ESP32 firmware
- Leverages Claude Code's native interface
- Local execution (fast, reliable)
- Easy to test and debug

---

## Testing Strategy

### Unit Tests
```python
# test_claude_interface.py
async def test_subprocess_start():
    interface = ClaudeCodeInterface()
    await interface.start()
    assert interface.process is not None
    assert interface.process.returncode is None

async def test_send_simple_task():
    interface = ClaudeCodeInterface()
    response = await interface.send_task(
        {"type": "voice_command"},
        {"transcription": "git status"}
    )
    assert "On branch" in response["response"]

async def test_action_confirmation():
    confirm = ActionConfirmation()
    assert await confirm.should_confirm("rm -rf /")
    assert not await confirm.should_confirm("git status")
```

### Integration Tests
```python
# test_e2e.py
async def test_voice_to_execution():
    # 1. Simulate ESP32 audio upload
    audio_data = load_test_audio("git_status.wav")
    response = await client.post("/capture/audio", files={"audio": audio_data})

    # 2. Check transcription
    assert response.status_code == 200

    # 3. Verify Claude Code execution (check Telegram messages)
    messages = await get_telegram_messages(timeout=10)
    assert any("branch wifi" in msg for msg in messages)

async def test_image_analysis():
    image_data = load_test_image("workspace.jpg")
    response = await client.post("/capture/image", files={"image": image_data})
    assert response.status_code == 200
```

### Load Testing
```bash
# Simulate multiple concurrent requests
locust -f tests/load_test.py --host http://localhost:8080
```

---

## Monitoring & Debugging

### Logging Strategy
```python
import logging

# Structured logging
logger = logging.getLogger(__name__)

# Log levels by component
logging.getLogger('relay_server').setLevel(logging.INFO)
logging.getLogger('claude_interface').setLevel(logging.DEBUG)
logging.getLogger('telegram').setLevel(logging.WARNING)

# Log critical events
logger.info("Audio received", extra={
    "source": "ESP32",
    "size_bytes": len(audio_data),
    "duration_sec": duration
})

logger.debug("Claude Code response", extra={
    "prompt": prompt[:100],
    "response": response[:200],
    "latency_ms": latency
})
```

### Metrics Collection
```python
# Track performance
import time
from collections import defaultdict

class Metrics:
    def __init__(self):
        self.counters = defaultdict(int)
        self.histograms = defaultdict(list)

    def increment(self, metric: str):
        self.counters[metric] += 1

    def observe(self, metric: str, value: float):
        self.histograms[metric].append(value)

    def report(self):
        return {
            "counters": dict(self.counters),
            "histograms": {
                k: {
                    "mean": sum(v) / len(v),
                    "p95": sorted(v)[int(len(v) * 0.95)],
                    "max": max(v)
                }
                for k, v in self.histograms.items()
            }
        }

metrics = Metrics()

# Usage
metrics.increment("audio_received")
metrics.observe("transcription_latency_ms", 1234)
```

---

## Conclusion

The **recommended architecture** is:

1. **Keep existing ESP32 HTTP interface** - Don't touch working firmware
2. **Add Claude Code subprocess integration** - Use stdin/stdout streaming
3. **Implement safety layer** - Confirmation for dangerous operations
4. **Optional WebSocket** - Real-time progress for demo polish

This design prioritizes:
- ✅ **Demo reliability** - Local execution, no cloud dependencies
- ✅ **Safety** - Confirmation layer prevents accidents
- ✅ **Performance** - Subprocess faster than HTTP API
- ✅ **Flexibility** - Easy to add new capabilities

### Next Steps
1. Implement `ClaudeCodeInterface` class
2. Test basic voice command execution
3. Add action confirmation layer
4. Perform load testing
5. Create pre-demo checklist
6. Document failure recovery procedures

---

## Appendices

### A. Claude Code CLI Reference
```bash
# Interactive mode (default)
claude

# Non-interactive mode (for subprocess)
claude --print \
  --input-format stream-json \
  --output-format stream-json \
  --include-partial-messages \
  --replay-user-messages

# With system prompt
claude --system-prompt "You are a code assistant for OpenCharm project"

# With tools restriction
claude --allowed-tools "Bash,Edit,Read,Write"
```

### B. Message Format Spec
```json
{
  "input": {
    "type": "user",
    "content": "Execute git status",
    "attachments": [
      {
        "type": "image",
        "mime_type": "image/jpeg",
        "data": "base64-encoded-data"
      }
    ]
  },
  "output": {
    "type": "message",
    "content": "On branch wifi\nChanges not staged for commit...",
    "tool_calls": [
      {
        "tool": "Bash",
        "command": "git status",
        "output": "..."
      }
    ]
  }
}
```

### C. Error Codes
```
ESP32 Errors:
- 500: Camera capture failed
- 502: Telegram API error
- 504: Timeout (network/AI)

Relay Server Errors:
- 600: Claude Code subprocess crashed
- 601: Action confirmation denied
- 602: Unsafe file path
- 603: Command blacklisted

Claude Code Errors:
- 700: Tool execution failed
- 701: Permission denied
- 702: Invalid JSON input
```

### D. Environment Variables
```bash
# .env configuration
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
AI_BACKEND=openai
OPENAI_API_KEY=xxx
WHISPER_MODEL_SIZE=base
CLAUDE_CODE_PATH=/opt/homebrew/bin/claude
PROJECT_ROOT=/Users/fabrizioguccione/Projects/OpenCharm
DEMO_MODE=false
```

---

**End of Network Architecture Design Document**
