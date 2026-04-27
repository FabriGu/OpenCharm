# SmartBracelet Network Infrastructure Design - Executive Summary

**Date:** 2026-03-25
**Agent:** Network Infrastructure Design Agent
**Status:** ✅ Design Complete - Ready for Implementation

---

## TL;DR

**Recommendation:** Use **subprocess stdin/stdout** for Relay Server → Claude Code communication.

**Why:**
- ✅ Native Claude Code interface (designed for this)
- ✅ Lowest latency (~200-500ms local execution)
- ✅ Best demo reliability (no cloud dependencies)
- ✅ Streaming responses (progress updates)
- ✅ Full tool ecosystem access (Bash, Edit, Read, Write)

**Architecture:**
```
ESP32S3 → (HTTP) → Relay Server → (subprocess) → Claude Code
         WiFi      FastAPI/Python   stdin/stdout   CLI tools
```

---

## Current System (Already Working)

### ESP32S3 → Relay Server
- **Protocol:** HTTP POST (multipart/form-data)
- **Transport:** WiFi local network (192.168.1.181:8080)
- **Endpoints:**
  - `/capture/image` - Direct image processing
  - `/capture/audio` - Direct audio processing
  - `/session/image`, `/session/audio`, `/session/process` - Buffered workflow
- **Processing:** FastAPI async handlers, background tasks
- **AI:** Whisper (local), OpenAI GPT-4o, Ollama (fallback)
- **Output:** Telegram bot messages

**Status:** ✅ **DO NOT CHANGE** - Working reliably in production

---

## Proposed Addition: Claude Code Integration

### Relay Server → Claude Code

**Method:** Subprocess with stdin/stdout streaming

```python
# Start subprocess
process = await asyncio.create_subprocess_exec(
    'claude', '--print',
    '--input-format', 'stream-json',
    '--output-format', 'stream-json',
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE
)

# Send task
await stdin.write(json.dumps({
    "type": "user",
    "content": "Execute: git status"
}).encode() + b'\n')

# Read response (streaming)
while True:
    line = await stdout.readline()
    response = json.loads(line)
    if response["type"] == "message":
        break  # Complete
```

**Characteristics:**
- ✅ Native interface (Claude Code designed for this)
- ✅ Streaming (partial messages for progress)
- ✅ Low latency (~200-500ms)
- ✅ Reliable (no network, local execution)
- ⚠️ Process lifecycle management needed
- ⚠️ Memory usage (long-running process)

---

## Message Flows

### Flow 1: Voice Command → Code Execution

```
User speaks "git status"
        ↓
ESP32 detects keyword, records audio (1.5s)
        ↓
HTTP POST /capture/audio → Relay Server (0.2s)
        ↓
Whisper transcription (1.2s)
        ↓
Claude Code: Execute git status (0.3s)
        ↓
Telegram: "On branch wifi..." (0.3s)
        ↓
Total: ~3.5 seconds
```

### Flow 2: Image Analysis → Code Changes

```
User says "CAPTURE"
        ↓
ESP32 countdown 3...2...1... capture (3s)
        ↓
HTTP POST /session/image → Buffered (0.2s)
        ↓
User says "POST"
        ↓
HTTP POST /session/process → Relay Server (0.1s)
        ↓
Claude Code: Vision analysis + Edit tool (2s)
        ↓
Telegram: "Fixed indentation in main.cpp" (0.3s)
        ↓
Total: ~5.6 seconds
```

### Flow 3: Dangerous Operation → Confirmation

```
User says "delete old files"
        ↓
Transcription: "delete old files" (1.5s)
        ↓
Claude Code: Detects 'rm' command → Request confirmation (0.3s)
        ↓
Telegram: "⚠️ Action Confirmation Required" (0.3s)
        ↓
User clicks ✅ Approve (manual, variable)
        ↓
Claude Code: Execute deletion (0.5s)
        ↓
Telegram: "✅ Deleted 5 files" (0.3s)
```

---

## Safety Layer

### Action Classification

**Auto-Approve (Safe):**
- Read-only git commands: `git status`, `git log`, `git diff`
- File viewing: `ls`, `cat`, `grep`, `find`
- Info commands: `pwd`, `which`, `npm list`

**Require Confirmation (Dangerous):**
- Deletions: `rm -rf`
- Privilege escalation: `sudo`
- Remote operations: `git push`, `curl | bash`
- Package installs: `npm install`, `pip install`
- File writes outside project directory

**Always Deny (Blacklisted):**
- System-level deletions: `rm -rf /`
- Fork bombs: `:(){ :|:& };:`
- Filesystem formatting: `mkfs`

### Confirmation Flow

```python
# Before executing command
safety, reason = classify_command("rm -rf /tmp/test")

if safety == DENY:
    return "❌ Action denied"

if safety == CONFIRM:
    approved = await request_telegram_confirmation(
        command="rm -rf /tmp/test",
        context="Delete temporary files",
        timeout=60  # seconds
    )

    if not approved:
        return "❌ Action cancelled"

# Execute if approved or auto-safe
execute_command()
```

---

## Performance Analysis

### Latency Budget (Target: < 5s total)

| Stage | Optimistic | Realistic | Pessimistic |
|-------|-----------|-----------|-------------|
| Keyword detection | 400ms | 400ms | 500ms |
| Audio capture | Variable (user speech) | 1000ms | 2000ms |
| WiFi transfer | 100ms | 100ms | 150ms |
| Whisper (base) | 800ms | 1200ms | 1500ms |
| Claude Code exec | 200ms | 500ms | 2000ms |
| Telegram send | 200ms | 300ms | 500ms |
| **Total** | **~2.1s** | **~3.5s** | **~5.2s** |

### Optimizations

1. **Whisper Model:** Use 'base' (not 'tiny' or 'medium')
   - 'tiny': Fast (0.5s) but poor accuracy
   - 'base': Good balance (1.2s, 95%+ accuracy) ← **Recommended**
   - 'medium': Best accuracy (3s) but too slow

2. **Claude Code:**
   - Keep subprocess warm (no cold start)
   - Pre-load common commands (cache)
   - Use streaming for progress updates

3. **Network:**
   - Disable WiFi power save on ESP32
   - Use local WiFi hotspot (not internet router)
   - Fixed IP or DHCP reservation

---

## Implementation Phases

### Phase 1: Basic Integration (Day 1) ← **START HERE**
- [ ] Create `claude_interface.py` class
- [ ] Test subprocess stdin/stdout communication
- [ ] Implement simple voice command execution
- [ ] Add error handling and logging

**Deliverable:** Voice command "git status" → Telegram response

### Phase 2: Safety Layer (Day 2)
- [ ] Implement `ActionConfirmation` class
- [ ] Add Telegram confirmation prompts
- [ ] File system path validation
- [ ] Command whitelist/blacklist

**Deliverable:** Dangerous commands require confirmation

### Phase 3: Enhanced Features (Day 3)
- [ ] WebSocket progress updates (optional)
- [ ] Streaming responses (partial messages)
- [ ] Session context management
- [ ] Multi-modal analysis (image + voice)

**Deliverable:** Real-time progress updates in demo

### Phase 4: Demo Polish (Day 4)
- [ ] LED feedback improvements
- [ ] Fallback responses (if Claude Code fails)
- [ ] Pre-demo health checks
- [ ] Performance tuning

**Deliverable:** Bulletproof demo experience

---

## Files Created

1. **`docs/network-architecture.md`** (12,000 words)
   - Detailed architecture design
   - Protocol comparisons
   - Implementation examples
   - Error handling strategies
   - Testing approaches

2. **`docs/architecture-diagram.md`** (4,000 words)
   - ASCII art system diagrams
   - Message flow visualizations
   - Subprocess communication patterns
   - Network topology
   - Latency analysis

3. **`docs/implementation-guide.md`** (3,500 words)
   - Step-by-step implementation
   - Complete code examples
   - Testing procedures
   - Demo preparation
   - Troubleshooting guide

4. **`docs/NETWORK_DESIGN_SUMMARY.md`** (This file)
   - Executive summary
   - Key recommendations
   - Quick reference

---

## Key Decision Points

### ✅ Decisions Made

1. **Use subprocess stdin/stdout** (not HTTP API, not cloud)
   - Rationale: Native interface, lowest latency, best reliability

2. **Keep existing ESP32 firmware unchanged**
   - Rationale: Already working, high risk to modify

3. **Implement safety/confirmation layer**
   - Rationale: Prevent accidental destructive operations

4. **Use Whisper 'base' model**
   - Rationale: Best accuracy/speed tradeoff

5. **Local execution (laptop, not cloud)**
   - Rationale: Demo reliability, lower latency, no internet dependency

### ❓ Open Questions (for user)

1. **Should demo mode skip confirmations?**
   - Pro: Faster, more impressive
   - Con: Less realistic safety demonstration
   - Recommendation: Make it configurable (`DEMO_MODE=true`)

2. **Should we add WebSocket for progress updates?**
   - Pro: Real-time feedback, better demo polish
   - Con: Additional complexity
   - Recommendation: Optional (Phase 3), not critical

3. **Timeout values for confirmation?**
   - Current: 60 seconds
   - Demo mode: 30 seconds?
   - Auto-deny or manual intervention?

---

## Risk Assessment

### Low Risk ✅
- Subprocess communication (well-tested pattern)
- Whisper integration (already working)
- Telegram integration (already working)
- ESP32 firmware (no changes needed)

### Medium Risk ⚠️
- Process lifecycle management (restart on crash)
- Confirmation timeout handling (user experience)
- Memory usage with long-running process
- Edge cases in safety classification

### High Risk ❌
- None identified (conservative architecture)

### Mitigation Strategies

1. **Process crash:** Auto-restart with exponential backoff
2. **Memory leak:** Periodic health checks, restart threshold
3. **Confirmation timeout:** Clear user feedback, fallback to deny
4. **Safety bypass:** Multiple layers (classification + confirmation)

---

## Demo Checklist

**Before Demo:**
- [ ] WiFi hotspot active (2.4GHz)
- [ ] Relay server running (`uvicorn relay_server:app --host 0.0.0.0 --port 8080`)
- [ ] Claude Code subprocess healthy (check logs)
- [ ] Telegram bot responding (send test message)
- [ ] ESP32 connected (green LED, check serial)
- [ ] Whisper model loaded (first transcription may be slow)
- [ ] OpenAI API key valid (check .env)

**Demo Script:**
1. Voice command: "git status" → Show branch info
2. Voice command: "list files" → Show directory contents
3. Image capture: "CAPTURE" (countdown) → "POST" → Analysis
4. Dangerous command: "delete old files" → Confirmation prompt → Execute/Cancel

**Fallback Plan:**
- If Claude Code fails: Use existing Ollama/OpenAI analysis
- If WiFi drops: ESP32 auto-reconnects, show LED feedback
- If Telegram fails: Check logs, manual send test

---

## Success Metrics

### Performance Targets
- ✅ Total latency < 5 seconds (excluding user speech)
- ✅ Whisper transcription < 2 seconds
- ✅ Claude Code execution < 1 second (simple commands)
- ✅ ESP32 response time < 500ms (HTTP 200 OK)

### Reliability Targets
- ✅ 99% success rate for voice commands
- ✅ Zero false positives for safety classification
- ✅ Graceful degradation (fallback to existing AI if Claude Code fails)
- ✅ Auto-recovery from subprocess crashes

### Demo Goals
- ✅ Wow factor: Real-time code execution from voice
- ✅ Safety demonstration: Confirmation prompts
- ✅ Multi-modal: Image + voice analysis
- ✅ Smooth workflow: < 5s end-to-end

---

## Next Steps

### Immediate (Today)
1. Review architecture documents with user
2. Get approval on subprocess approach
3. Discuss open questions (demo mode, WebSocket, timeouts)

### Short-term (Week 1)
1. Implement Phase 1: Basic subprocess integration
2. Test voice command execution
3. Validate latency targets

### Medium-term (Week 2-3)
1. Implement Phase 2: Safety layer
2. Add confirmation prompts
3. Test dangerous operations

### Long-term (Week 4+)
1. Phase 3: Enhanced features (WebSocket, streaming)
2. Phase 4: Demo polish
3. Load testing and optimization

---

## Questions for User

1. **Architecture approval:** Is subprocess stdin/stdout acceptable?
2. **Demo mode:** Should we skip confirmations for faster demo flow?
3. **WebSocket priority:** Nice-to-have or essential for demo?
4. **Safety strictness:** Conservative (deny by default) or liberal (allow most operations)?
5. **Timeout values:** 30s or 60s for confirmation prompts?
6. **Error handling:** Auto-restart subprocess or manual intervention?

---

## References

- **Main Documentation:** `docs/network-architecture.md`
- **Diagrams:** `docs/architecture-diagram.md`
- **Implementation:** `docs/implementation-guide.md`
- **Current Code:** `relay/relay_server.py`, `firmware/src/main.cpp`

---

## Contact

For questions or clarification on this design:
- Review detailed documentation in `docs/` directory
- Check implementation examples in `docs/implementation-guide.md`
- Test with minimal example: `echo '{"type": "user", "content": "git status"}' | claude --print --stream-json`

---

**Status:** ✅ Design complete, ready for implementation approval

**Recommendation:** Start with Phase 1 (basic integration) and validate approach before proceeding to advanced features.

---

_Generated by Network Infrastructure Design Agent on 2026-03-25_
