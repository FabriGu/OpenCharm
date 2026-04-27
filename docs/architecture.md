# SmartBracelet Network Architecture Diagrams

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          SmartBracelet System                            │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  ESP32S3     │  WiFi   │ Relay Server │ Process │ Claude Code  │
│  Bracelet    │────────▶│  (FastAPI)   │────────▶│  Subprocess  │
│              │  HTTP   │              │ stdin/  │              │
│  - Camera    │         │  - Whisper   │ stdout  │  - Bash      │
│  - Mic (PDM) │         │  - Vision AI │         │  - Edit      │
│  - Keywords  │         │  - Telegram  │         │  - Read      │
│  - LED       │◀────────│  - Claude    │◀────────│  - Write     │
└──────────────┘  200 OK └──────────────┘ JSON    └──────────────┘
                              │
                              │ Telegram API
                              ▼
                         ┌──────────────┐
                         │   Telegram   │
                         │     Bot      │
                         │ (User Feed)  │
                         └──────────────┘
```

---

## Data Flow: Voice Command Execution

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Example: User says "git status"                                         │
└─────────────────────────────────────────────────────────────────────────┘

1️⃣ ESP32 CAPTURE
┌──────────────┐
│   ESP32S3    │  User speaks "git status"
│              │  ↓
│ [RECORD]     │  Keyword detected → Start recording
│ [STOP]       │  Keyword detected → Stop recording
│              │  ↓
│ 16kHz WAV    │  Build WAV file (16kHz, 16-bit, mono)
│ Audio Buffer │
└──────────────┘
       │
       │ POST /capture/audio
       │ multipart/form-data
       │ Field: audio (audio/wav)
       ▼

2️⃣ RELAY PROCESSING
┌──────────────┐
│ Relay Server │  Receive HTTP POST
│              │  ↓
│ FastAPI      │  Save temp WAV file
│ Async        │  ↓
│              │  Whisper transcription
│ [Whisper]    │  "git status" (confidence: 0.95)
│  base model  │
└──────────────┘
       │
       │ HTTP 200 OK (immediate response to ESP32)
       │
       ├─────────────────────────────────────┐
       │                                     │
       │ Background Task                     │ Main Thread
       ▼                                     ▼
┌──────────────┐                    ┌──────────────┐
│   Telegram   │                    │   ESP32S3    │
│     Bot      │                    │              │
│              │                    │ LED: Success │
│ "You said:   │                    │ (Green flash)│
│  git status" │                    └──────────────┘
└──────────────┘
       │
       │ AI Processing continues...
       ▼

3️⃣ CLAUDE CODE INTEGRATION
┌──────────────┐
│ Relay Server │  Format task prompt
│              │  ↓
│              │  {
│              │    "type": "user",
│              │    "content": "Execute: git status"
│              │  }
│              │  ↓
│ [stdin pipe] │  Write JSON + newline
└──────────────┘
       │
       │ stdin stream
       │ (subprocess.PIPE)
       ▼
┌──────────────┐
│ Claude Code  │  Parse command
│  Subprocess  │  ↓
│              │  Understand: "Run git status"
│  --print     │  ↓
│  --stream    │  Check safety:
│              │  ✅ git status (read-only, safe)
│              │  ↓
│ [Bash tool]  │  Execute: git status
│              │  ↓
│              │  Capture output:
│              │  "On branch wifi
│              │   Changes not staged:
│              │     modified: relay/.env"
└──────────────┘
       │
       │ stdout stream
       │ (JSON lines)
       ▼
┌──────────────┐
│ Relay Server │  Read stdout line-by-line
│              │  ↓
│ [stdout      │  Parse JSON response
│  reader]     │  ↓
│              │  Format for Telegram:
│              │  ```
│              │  On branch wifi
│              │  M relay/.env
│              │  ```
└──────────────┘
       │
       │ Send to Telegram
       ▼
┌──────────────┐
│   Telegram   │
│     Bot      │
│              │
│ *Git Status* │
│              │
│ ```          │
│ On branch    │
│   wifi       │
│ M relay/.env │
│ ```          │
└──────────────┘

4️⃣ COMPLETION
       │
       │ Log success metrics
       ▼
   [Complete]
   Total time: 3.5s
   - Capture: 1.0s
   - Transfer: 0.2s
   - Whisper: 1.5s
   - Claude: 0.5s
   - Telegram: 0.3s
```

---

## Data Flow: Image Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Example: User says "CAPTURE" then "POST"                                │
└─────────────────────────────────────────────────────────────────────────┘

1️⃣ ESP32 CAPTURE
┌──────────────┐
│   ESP32S3    │  User says "CAPTURE"
│              │  ↓
│ [CAPTURE]    │  Keyword detected
│ countdown    │  ↓
│              │  3... 2... 1... *flash*
│ [OV2640]     │  ↓
│ Camera       │  Capture JPEG (640x480)
│              │  ↓
│ Session      │  Buffer in session (not processed yet)
│ buffer       │
└──────────────┘
       │
       │ POST /session/image
       │ (buffered, not analyzed)
       ▼
┌──────────────┐
│ Relay Server │  Store in session_buffer
│              │
│ session:     │  {
│  images: [1] │    "image_files": [
│  audio: [0]  │      {"data": bytes, "timestamp": "..."}
│              │    ]
│              │  }
└──────────────┘
       │
       │ User says "POST"
       ▼

2️⃣ SESSION PROCESSING
┌──────────────┐
│   ESP32S3    │  POST /session/process
│              │  ↓
│ [POST]       │  Trigger processing of ALL buffered content
└──────────────┘
       │
       ▼
┌──────────────┐
│ Relay Server │  Combine session content:
│              │  - 1 image (JPEG)
│              │  - 0 audio files
│              │  ↓
│              │  Prepare multi-modal prompt
└──────────────┘
       │
       │ Format for Claude Code
       ▼

3️⃣ MULTI-MODAL ANALYSIS
┌──────────────┐
│ Relay Server │  {
│              │    "type": "user",
│              │    "content": "Analyze workspace image",
│              │    "attachments": [
│              │      {
│              │        "type": "image",
│              │        "mime_type": "image/jpeg",
│              │        "data": "base64..."
│              │      }
│              │    ]
│              │  }
└──────────────┘
       │
       │ stdin
       ▼
┌──────────────┐
│ Claude Code  │  Receive image + prompt
│  Subprocess  │  ↓
│              │  Vision analysis:
│ [Vision]     │  - Sees desk with laptop
│              │  - Code on screen (C++)
│              │  - Notes: indentation inconsistent
│              │  ↓
│              │  Generate suggestion:
│              │  "Fix indentation in main.cpp lines 45-60"
│              │  ↓
│ [Edit tool]  │  Execute fix:
│              │  Edit /path/to/main.cpp
│              │  - Fix indentation
│              │  ↓
│              │  Return result:
│              │  "Fixed indentation in main.cpp"
└──────────────┘
       │
       │ stdout
       ▼
┌──────────────┐
│ Relay Server │  Parse response
│              │  ↓
│              │  Clear session buffer
│              │  ↓
│              │  Send to Telegram + ESP32
└──────────────┘
       │
       ├───────────────────┐
       │                   │
       ▼                   ▼
┌──────────────┐    ┌──────────────┐
│   Telegram   │    │   ESP32S3    │
│              │    │              │
│ "I see your  │    │ LED: Success │
│  workspace.  │    │ (Green)      │
│  Fixed       │    └──────────────┘
│  indentation │
│  in main.cpp"│
└──────────────┘
```

---

## Safety & Confirmation Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Example: User says "delete old files"                                   │
└─────────────────────────────────────────────────────────────────────────┘

1️⃣ COMMAND RECEIVED
┌──────────────┐
│   ESP32S3    │  Voice: "delete old files"
│              │  ↓
│ [Audio]      │  Transcription: "delete old files"
└──────────────┘
       │
       ▼
┌──────────────┐
│ Relay Server │  Whisper → "delete old files"
│              │  ↓
│              │  Send to Claude Code
└──────────────┘
       │
       ▼

2️⃣ CLAUDE ANALYSIS
┌──────────────┐
│ Claude Code  │  Understand command
│              │  ↓
│              │  Generate plan:
│              │  - find . -name "*.old" -type f
│              │  - rm *.old
│              │  ↓
│ [Safety      │  Check safety rules:
│  check]      │  ⚠️ DANGEROUS: 'rm' command detected
│              │  ↓
│              │  Request confirmation
└──────────────┘
       │
       │ Return: {
       │   "type": "confirmation_required",
       │   "action": "Delete 5 .old files",
       │   "command": "rm file1.old file2.old ..."
       │ }
       ▼

3️⃣ CONFIRMATION REQUEST
┌──────────────┐
│ Relay Server │  Parse response
│              │  ↓
│ [Action      │  Detect confirmation_required
│ Confirm]     │  ↓
│              │  Send to Telegram:
└──────────────┘
       │
       ▼
┌──────────────┐
│   Telegram   │  ⚠️ *Action Confirmation Required*
│     Bot      │
│              │  Command: `rm file1.old file2.old ...`
│ [Inline      │  Context: Delete 5 .old files
│  keyboard]   │
│              │  [✅ Approve]  [❌ Deny]
└──────────────┘
       │
       │ User clicks ✅ Approve
       │ (or timeout after 60s → auto-deny)
       ▼

4️⃣ EXECUTION OR CANCELLATION
┌──────────────┐
│ Relay Server │  Receive approval
│              │  ↓
│              │  Send to Claude Code:
│              │  {
│              │    "type": "user",
│              │    "content": "Approved: execute deletion"
│              │  }
└──────────────┘
       │
       ▼
┌──────────────┐
│ Claude Code  │  Execute command
│              │  ↓
│ [Bash tool]  │  rm file1.old file2.old ...
│              │  ↓
│              │  Return: "Deleted 5 files"
└──────────────┘
       │
       ▼
┌──────────────┐
│   Telegram   │  ✅ *Deletion Complete*
│              │
│              │  Deleted 5 files:
│              │  - file1.old
│              │  - file2.old
│              │  - ...
└──────────────┘

Alternative: User clicks ❌ Deny
       │
       ▼
┌──────────────┐
│ Relay Server │  Send cancellation
│              │  ↓
│ Claude Code  │  Abort operation
│              │  ↓
│ Telegram     │  ❌ *Action Cancelled*
│              │  Deletion request denied by user
└──────────────┘
```

---

## Subprocess Communication Pattern

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Relay Server ↔ Claude Code Subprocess (stdin/stdout)                    │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐         ┌──────────────────────────┐
│      Relay Server        │         │    Claude Code Process   │
│      (Python/FastAPI)    │         │    (Node.js subprocess)  │
└──────────────────────────┘         └──────────────────────────┘

INITIALIZATION
─────────────────────────────────────────────────────────────────

1. Start subprocess:
   ┌────────────────────┐
   │ asyncio.create_    │
   │ subprocess_exec(   │
   │   'claude',        │
   │   '--print',       │
   │   '--stream-json'  │
   │ )                  │
   └────────────────────┘
          │
          ├──── stdin  = PIPE ───────▶
          ├──── stdout = PIPE ◀───────
          └──── stderr = PIPE ◀───────

2. Create async tasks:
   - Writer task (stdin)
   - Reader task (stdout)
   - Error reader (stderr)

COMMUNICATION
─────────────────────────────────────────────────────────────────

Input Queue                              Output Queue
┌────────────┐                          ┌────────────┐
│  Message 1 │                          │ Response 1 │
│  Message 2 │                          │ Response 2 │
│  Message 3 │                          │ Response 3 │
└────────────┘                          └────────────┘
     │                                        ▲
     │                                        │
     ▼                                        │
┌──────────────┐                      ┌──────────────┐
│ Writer Task  │  stdin (JSON line)   │ Reader Task  │
│              │─────────────────────▶│              │
│ while True:  │                      │ while True:  │
│   msg = await│                      │   line =     │
│   queue.get()│                      │   await      │
│   write_json │                      │   readline() │
│   await drain│                      │   parse_json │
│              │                      │   queue.put  │
└──────────────┘                      └──────────────┘
                                             │
                                             ▼
                                      ┌──────────────┐
                                      │ FastAPI      │
                                      │ endpoint     │
                                      │ awaits       │
                                      │ response     │
                                      └──────────────┘

SAMPLE EXCHANGE
─────────────────────────────────────────────────────────────────

Relay Server sends:
{
  "type": "user",
  "content": "Execute: git status"
}

Claude Code responds (streaming):
{
  "type": "partial",
  "content": "Running git"
}
{
  "type": "partial",
  "content": " status..."
}
{
  "type": "tool_use",
  "tool": "Bash",
  "command": "git status"
}
{
  "type": "tool_result",
  "output": "On branch wifi\nChanges not staged..."
}
{
  "type": "message",
  "content": "On branch wifi\nChanges not staged for commit:\n  modified: relay/.env"
}

HEALTH MONITORING
─────────────────────────────────────────────────────────────────

┌──────────────┐
│ Health Check │  Every 30 seconds
│   Task       │
│              │  Check:
│ while True:  │  - process.returncode is None ✓
│   sleep(30)  │  - stdin writable ✓
│   if dead:   │  - stdout readable ✓
│     restart  │
└──────────────┘
     │
     │ If process dies
     ▼
┌──────────────┐
│  Restart     │  1. Terminate gracefully
│  Procedure   │  2. Wait 5s
│              │  3. Kill if needed
│  1. term()   │  4. Restart subprocess
│  2. wait(5s) │  5. Clear buffers
│  3. kill()   │  6. Resume operation
│  4. start()  │
└──────────────┘

ERROR HANDLING
─────────────────────────────────────────────────────────────────

Scenario 1: JSON Parse Error
  stdin: {invalid json}
  stdout: {"type": "error", "message": "Invalid JSON"}
  → Log error, send fallback response

Scenario 2: Process Crash
  process.returncode = -11 (SIGSEGV)
  → Restart subprocess, retry last command

Scenario 3: Timeout (no response after 60s)
  → Send SIGTERM, restart, return timeout error

Scenario 4: Permission Denied
  stdout: {"type": "error", "code": "EACCES"}
  → Request user confirmation via Telegram
```

---

## Network Topology

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Local Network (192.168.1.0/24)                       │
└─────────────────────────────────────────────────────────────────────────┘

                      ┌──────────────┐
                      │  WiFi Router │
                      │  or Hotspot  │
                      │              │
                      │  2.4GHz only │
                      │  (ESP32 req) │
                      └──────────────┘
                             │
                 ┌───────────┼───────────┐
                 │                       │
                 ▼                       ▼
        ┌──────────────┐        ┌──────────────┐
        │   ESP32S3    │        │    Laptop    │
        │  192.168.1.x │        │ 192.168.1.181│
        │              │        │              │
        │  Port: N/A   │        │ Port: 8080   │
        │  (client)    │        │ (server)     │
        └──────────────┘        └──────────────┘
                                       │
                                       │ localhost
                                       │
                                ┌──────┼──────┐
                                │             │
                                ▼             ▼
                        ┌──────────┐  ┌──────────┐
                        │  Relay   │  │  Claude  │
                        │  Server  │  │   Code   │
                        │  :8080   │  │  (sub-   │
                        │          │  │ process) │
                        └──────────┘  └──────────┘
                                │
                                │ HTTPS
                                │ (internet)
                                ▼
                        ┌──────────────┐
                        │   External   │
                        │   Services   │
                        │              │
                        │ - Telegram   │
                        │ - OpenAI API │
                        └──────────────┘

FIREWALL RULES (Laptop)
────────────────────────
Inbound:
  ✅ TCP 8080 (from 192.168.1.0/24)  → Relay Server
  ❌ All other ports                  → Blocked

Outbound:
  ✅ HTTPS (443)                      → Telegram, OpenAI
  ✅ HTTP (11434)                     → Ollama (localhost)
  ❌ All other                        → Blocked

NETWORK REQUIREMENTS
────────────────────────
ESP32 Requirements:
  - 2.4GHz WiFi (NOT 5GHz)
  - WPA2 Personal
  - Signal strength: > -70 dBm
  - Latency: < 50ms

Relay Server Requirements:
  - Same subnet as ESP32
  - Fixed IP or DHCP reservation
  - Port 8080 accessible

Demo Setup:
  - Use WiFi hotspot (laptop or phone)
  - Disable firewall or allow port 8080
  - Test connectivity: curl http://192.168.1.181:8080/health
```

---

## Latency Analysis

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Latency Breakdown                               │
└─────────────────────────────────────────────────────────────────────────┘

Voice Command: "git status"

│
│ User speaks
▼ ──────────────────────────────────────────────────────────────
│ 1️⃣ KEYWORD DETECTION (on ESP32)
│    - Continuous inference: 100-200ms per slice
│    - Word detection: 300-500ms (3-5 slices)
│    - Latency: ~400ms average
▼ ──────────────────────────────────────────────────────────────
│ 2️⃣ AUDIO CAPTURE (on ESP32)
│    - Recording duration: variable (user speech)
│    - Example: "git status" = 1.0s
│    - Latency: 1000ms (user-controlled)
▼ ──────────────────────────────────────────────────────────────
│ 3️⃣ KEYWORD STOP (on ESP32)
│    - "STOP" detection: 300-500ms
│    - Latency: ~400ms
▼ ──────────────────────────────────────────────────────────────
│ 4️⃣ NETWORK TRANSFER (ESP32 → Relay)
│    - WAV file size: ~32KB (1s @ 16kHz)
│    - WiFi speed: 1-5 Mbps (2.4GHz)
│    - Transfer time: 50-150ms
│    - Latency: ~100ms
▼ ──────────────────────────────────────────────────────────────
│ 5️⃣ WHISPER TRANSCRIPTION (on Laptop)
│    - Model: base (74M params)
│    - Audio: 1.0s
│    - CPU: M-series Mac
│    - Latency: 800-1500ms (varies with CPU)
│    - Average: ~1200ms
▼ ──────────────────────────────────────────────────────────────
│ 6️⃣ CLAUDE CODE EXECUTION (on Laptop)
│    - Parse command: 50ms
│    - Execute tool (git status): 100-300ms
│    - Format response: 50ms
│    - Latency: ~200ms (simple commands)
│    - Latency: ~2000ms (complex operations)
▼ ──────────────────────────────────────────────────────────────
│ 7️⃣ TELEGRAM SEND (Laptop → Internet → Telegram)
│    - API call latency: 200-500ms
│    - Latency: ~300ms
▼ ──────────────────────────────────────────────────────────────
│ User sees result in Telegram
│

TOTAL LATENCY BUDGET (excluding user speech time)
──────────────────────────────────────────────────
Optimistic:  400 + 400 + 100 + 800  + 200  + 200  = 2100ms (~2s)
Realistic:   400 + 400 + 100 + 1200 + 500  + 300  = 2900ms (~3s)
Pessimistic: 500 + 500 + 150 + 1500 + 2000 + 500  = 5150ms (~5s)

OPTIMIZATION STRATEGIES
───────────────────────
1. Use Whisper 'tiny' model:   -500ms (but worse accuracy)
2. Use Whisper 'base' model:    Current (good balance)
3. Parallel Telegram send:     -200ms (don't wait for send)
4. Cache common commands:      -500ms (skip Claude Code)
5. Pre-warm subprocess:        -100ms (avoid cold start)
6. Local WiFi optimization:    -50ms  (disable power save)

TARGET FOR DEMO
───────────────
Goal: < 3 seconds total (excluding user speech)
      ✅ Achievable with current architecture
      ✅ Use 'base' Whisper model
      ✅ Pre-warm Claude Code subprocess
      ✅ Optimize network (disable WiFi power save)
```

---

## Comparison: Architecture Options

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Architecture Comparison                             │
└─────────────────────────────────────────────────────────────────────────┘

OPTION A: Subprocess stdin/stdout (RECOMMENDED)
────────────────────────────────────────────────
┌──────────┐    ┌──────────┐    ┌──────────┐
│ ESP32S3  │───▶│  Relay   │───▶│  Claude  │
│          │◀───│  Server  │◀───│   Code   │
│  HTTP    │    │  Python  │    │  (stdin/ │
│          │    │          │    │  stdout) │
└──────────┘    └──────────┘    └──────────┘

Pros:
  ✅ Native Claude Code interface
  ✅ Streaming responses (progress updates)
  ✅ Low latency (no network overhead)
  ✅ Session management built-in
  ✅ Full tool access (Bash, Edit, Read, etc.)
  ✅ Local execution (no cloud deps)

Cons:
  ⚠️ Process lifecycle management needed
  ⚠️ Memory usage (long-running process)
  ⚠️ stdout parsing required

Latency: ~200-500ms (local execution)
Reliability: High (local, no network)
Complexity: Medium (subprocess management)

────────────────────────────────────────────────

OPTION B: HTTP API (future possibility)
────────────────────────────────────────────────
┌──────────┐    ┌──────────┐    ┌──────────┐
│ ESP32S3  │───▶│  Relay   │───▶│  Claude  │
│          │◀───│  Server  │◀───│   Code   │
│  HTTP    │    │  Python  │    │  HTTP    │
│          │    │  httpx   │    │  Server  │
└──────────┘    └──────────┘    └──────────┘

Pros:
  ✅ Simpler communication (REST API)
  ✅ Easier to test (curl, Postman)
  ✅ Stateless (each request independent)
  ✅ Load balancing possible

Cons:
  ❌ Claude Code doesn't expose HTTP API yet
  ⚠️ Higher latency (HTTP overhead)
  ⚠️ No streaming (unless WebSocket)
  ⚠️ Session management manual

Latency: ~300-800ms (HTTP round-trip)
Reliability: High (HTTP well-tested)
Complexity: Low (standard REST)

────────────────────────────────────────────────

OPTION C: Direct ESP32 → Claude Code (not viable)
────────────────────────────────────────────────
┌──────────┐                    ┌──────────┐
│ ESP32S3  │───────────────────▶│  Claude  │
│          │◀───────────────────│   Code   │
│  HTTPS?  │                    │  ???     │
│          │                    │          │
└──────────┘                    └──────────┘

Pros:
  ✅ Fewer hops (lower latency)
  ✅ Simpler architecture

Cons:
  ❌ ESP32 limited HTTPS support
  ❌ No Whisper on ESP32 (need cloud API)
  ❌ No way to run Claude Code on ESP32
  ❌ Still need relay for AI processing
  ❌ Certificate validation issues

Verdict: NOT VIABLE (need relay anyway)

────────────────────────────────────────────────

OPTION D: Cloud Relay (AWS Lambda, etc.)
────────────────────────────────────────────────
┌──────────┐    ┌──────────┐    ┌──────────┐
│ ESP32S3  │───▶│   AWS    │───▶│  Claude  │
│          │◀───│  Lambda  │◀───│   Code   │
│  HTTPS   │    │  Relay   │    │  API?    │
│          │    │          │    │          │
└──────────┘    └──────────┘    └──────────┘

Pros:
  ✅ Scalable (multiple devices)
  ✅ Always available (no laptop needed)
  ✅ Distributed (geographic redundancy)

Cons:
  ❌ Higher latency (internet round-trip)
  ❌ More failure points (ESP32 → AWS → Claude)
  ❌ Demo reliability depends on internet
  ❌ Cost (API calls, Lambda invocations)
  ❌ Claude Code not designed for cloud

Latency: ~1000-2000ms (internet overhead)
Reliability: Medium (multiple network hops)
Complexity: High (cloud infrastructure)

Verdict: Overkill for single-device demo

────────────────────────────────────────────────

RECOMMENDATION: Option A (Subprocess stdin/stdout)
Reasons:
  1. ✅ Native Claude Code interface
  2. ✅ Lowest latency (local execution)
  3. ✅ Best demo reliability (no cloud deps)
  4. ✅ Streaming for progress updates
  5. ✅ Full tool ecosystem access
```

---

## End of Architecture Diagrams

**Next:** See `network-architecture.md` for detailed implementation guide
