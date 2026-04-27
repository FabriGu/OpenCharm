# Claude Code Integration Roadmap

> **Status: Partially implemented.** See `relay/claude_executor.py` for current state.

## Overview

Connect the ESP32S3 OpenCharm device to Claude Code for executing desktop actions via voice commands and image analysis.

**Demo Capabilities:**
- A) Take picture of notebook/UI mockup → Claude Code edits live website
- B) Voice command "create folder test on desktop" → Creates folder

---

## Architecture Decision

**Chosen Approach: Hybrid (Anthropic API + Claude CLI Subprocess)**

| Component | Method | Why |
|-----------|--------|-----|
| Image Analysis | Anthropic API (direct) | CLI doesn't support images natively |
| Text Commands | Claude CLI subprocess | Full tool access, native integration |
| Action Execution | Claude CLI with `--allowedTools` | Pre-approved Bash, Write, Edit |

```
ESP32S3 → HTTP → relay_server.py → {
   Images: Anthropic API (vision) → Extract intent
   Actions: claude CLI subprocess → Execute on desktop
} → Telegram (notification)
```

---

## Files to Create/Modify

### New Files

1. **`relay/claude_executor.py`** (~200 lines)
   - `ClaudeCodeExecutor` class for subprocess management
   - `execute_action(prompt, working_dir)` method
   - JSON output parsing
   - Timeout and error handling

2. **`relay/intent_parser.py`** (~100 lines)
   - `IntentParser` class
   - Regex patterns for common intents (create folder, edit file, etc.)
   - `parse(transcription, has_images)` → `ParsedIntent`

### Modified Files

3. **`relay/relay_server.py`** (add ~150 lines)
   - New endpoint: `POST /execute/action`
   - New endpoint: `POST /session/execute` (integrates with existing session)
   - Import new modules
   - Add `AI_MODE` env var toggle

4. **`relay/.env`** (add ~5 lines)
   - `CLAUDE_WORKING_DIR=/Users/fabrizioguccione/Desktop`
   - `CLAUDE_TIMEOUT=120`
   - `AI_MODE=execute` (or `observe` for Telegram-only)

### No Changes Required

- **Firmware** - Keep ESP32S3 firmware unchanged (same HTTP endpoints)
- **config.h** - No changes needed
- **Telegram** - Keep existing notification flow

---

## Implementation Steps

### Phase 1: Core Executor (Day 1)

**Step 1.1: Create `claude_executor.py`**

```python
# Key implementation:
class ClaudeCodeExecutor:
    async def execute_action(self, prompt: str, image_paths: list = None) -> dict:
        # Build command
        cmd = ["claude", "-p", "--output-format", "json",
               "--allowedTools", "Bash,Write,Edit,Read"]

        # Execute subprocess
        process = await asyncio.create_subprocess_exec(
            *cmd, prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=self.timeout
        )

        return json.loads(stdout.decode())
```

**Step 1.2: Create `intent_parser.py`**

```python
# Pattern matching for voice commands:
PATTERNS = {
    "create_folder": r"create\s+(?:a\s+)?folder\s+(?:called\s+)?(\w+)",
    "create_file": r"create\s+(?:a\s+)?file\s+(?:called\s+)?(\w+\.\w+)",
    "open_app": r"open\s+(\w+)",
    "edit_website": r"(?:edit|update|change)\s+(?:the\s+)?website",
}
```

**Step 1.3: Add new endpoint to `relay_server.py`**

```python
@app.post("/execute/action")
async def execute_action(
    command: str = Form(...),
    image: UploadFile = File(None)
):
    """Execute desktop action via Claude Code."""
    # Parse intent
    # Execute via ClaudeCodeExecutor
    # Notify via Telegram
    # Return result
```

### Phase 2: Session Integration (Day 1-2)

**Step 2.1: Modify `/session/process` to support action mode**

Add `AI_MODE` check:
- `observe`: Current behavior (analyze + Telegram)
- `execute`: Analyze + Execute action + Telegram notification

**Step 2.2: Add `/session/execute` endpoint**

New endpoint that:
1. Uses existing session buffer (audio + images)
2. Transcribes audio → extracts intent
3. If image present: analyze with Anthropic API
4. Execute action via Claude CLI
5. Notify result to Telegram

### Phase 3: Image-to-Action Flow (Day 2)

**Step 3.1: Implement image analysis pipeline**

```python
async def analyze_image_for_action(image_data: bytes, voice_command: str):
    """Use Anthropic API to analyze image and determine action."""

    # Call Anthropic API with vision
    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-5-20250929",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", ...}},
                {"type": "text", "text": f"""
                    User voice command: {voice_command}

                    Analyze this UI mockup and generate specific instructions
                    for implementing it. Return JSON with:
                    - "action": what to do
                    - "files_to_create": list of files
                    - "html_structure": if applicable
                """}
            ]
        }]
    )

    return response.content[0].text
```

**Step 3.2: Chain image analysis → code execution**

```python
async def execute_ui_mockup(image_data: bytes, context: str):
    # Step 1: Analyze image with Anthropic API
    analysis = await analyze_image_for_action(image_data, context)

    # Step 2: Execute implementation with Claude CLI
    result = await claude_executor.execute_action(
        f"Based on this analysis, implement the changes:\n{analysis}"
    )

    return result
```

### Phase 4: Testing & Demo Prep (Day 2-3)

**Step 4.1: Test folder creation flow**
```bash
# Simulate voice command
curl -X POST http://localhost:8080/execute/action \
  -F "command=create a folder called test on the desktop"
```

**Step 4.2: Test image + voice flow**
```bash
# Simulate image capture with voice
curl -X POST http://localhost:8080/execute/action \
  -F "command=implement this UI mockup" \
  -F "image=@mockup.jpg"
```

**Step 4.3: End-to-end device test**
1. Power on ESP32S3
2. Say "RECORD" → "create folder demo" → "STOP"
3. Say "POST"
4. Verify folder created on Desktop
5. Verify Telegram notification received

---

## Data Flow Diagrams

### Flow A: Voice Command → Folder Creation

```
User: "create folder demo"
        ↓
ESP32S3: RECORD → STOP → POST
        ↓ HTTP POST /session/audio + /session/process
relay_server.py
        ↓ Whisper transcription
"create folder demo"
        ↓ IntentParser
Intent: CREATE_FOLDER, target: "demo"
        ↓ ClaudeCodeExecutor
claude -p "Create a folder called demo on Desktop"
        ↓ mkdir ~/Desktop/demo
Success: folder created
        ↓ Telegram
"Created folder 'demo' on Desktop"
```

### Flow B: Image + Voice → Website Edit

```
User: [takes photo of notebook mockup]
User: "implement this design"
        ↓
ESP32S3: CAPTURE → RECORD → STOP → POST
        ↓ HTTP POST /session/image + /session/audio + /session/process
relay_server.py
        ↓ Whisper: "implement this design"
        ↓ Anthropic API (vision)
Analyze mockup → Extract HTML/CSS structure
        ↓ ClaudeCodeExecutor
claude -p "Create these files with this HTML..."
        ↓ Write index.html, style.css
Success: files created
        ↓ Telegram
"Created website from mockup: index.html, style.css"
```

---

## Safety & Permissions

### Allowed Actions (Pre-approved)
- Create files/folders in `~/Desktop`, `~/Documents`, `~/Projects`
- Read files
- Edit existing files
- Git operations (status, add, commit)

### Restricted Actions (Require confirmation via Telegram)
- Delete operations
- System modifications
- Network operations

### Blocked Actions
- `rm -rf /`
- `sudo` commands
- Operations outside allowed directories

```python
ALLOWED_DIRECTORIES = [
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Projects"),
]

def is_path_allowed(path: str) -> bool:
    real_path = os.path.realpath(path)
    return any(real_path.startswith(allowed) for allowed in ALLOWED_DIRECTORIES)
```

---

## Environment Variables

```bash
# relay/.env additions

# Claude Code Integration
CLAUDE_WORKING_DIR=/Users/fabrizioguccione/Desktop
CLAUDE_TIMEOUT=120
AI_MODE=execute  # "observe" for Telegram-only, "execute" for actions

# Anthropic API (for vision - already have ANTHROPIC_API_KEY)
# Reuse existing key
```

---

## Rollback Plan

If issues arise:
1. Set `AI_MODE=observe` in `.env` → Reverts to Telegram-only mode
2. Existing endpoints (`/capture/image`, `/session/process`) unchanged
3. New endpoints can be disabled without affecting core functionality

---

## Success Criteria

### Demo A: Folder Creation
- [ ] Say "create folder test" → Folder appears on Desktop within 5 seconds
- [ ] Telegram notification confirms action
- [ ] No errors in relay server logs

### Demo B: Website from Mockup
- [ ] Take photo of notebook with UI sketch
- [ ] Say "implement this design"
- [ ] HTML/CSS files created matching the sketch
- [ ] Telegram shows created files
- [ ] Total time < 30 seconds

---

## Estimated Timeline

| Phase | Tasks | Duration |
|-------|-------|----------|
| Phase 1 | Core executor + intent parser | 2-3 hours |
| Phase 2 | Session integration | 1-2 hours |
| Phase 3 | Image analysis pipeline | 2-3 hours |
| Phase 4 | Testing + demo prep | 2-3 hours |
| **Total** | | **7-11 hours** |

---

## User Decisions

1. **Working Directory**: `~/Projects/demo` - Clean demo folder
2. **Website Demo**: Edit existing site with Playwright browser control for live reload
3. **Safety Mode**: Demo mode (auto-approve all actions)

---

## Updated Implementation: Playwright Integration

Since we have Playwright MCP available, the website editing flow becomes:

```
User: [photo of UI mockup] + "implement this design"
        ↓
Anthropic API (vision) → Analyze mockup
        ↓
Claude Code → Write/Edit HTML/CSS files
        ↓
Playwright MCP → browser_navigate to local file OR dev server
        ↓
User sees live result in browser
        ↓
Telegram notification with screenshot
```

This provides instant visual feedback during the demo!
