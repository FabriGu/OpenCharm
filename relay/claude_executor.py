#!/usr/bin/env python3
"""
Claude Code Executor

Executes desktop actions via Claude CLI subprocess.
Supports file operations, bash commands, and code generation.

Usage:
    executor = ClaudeCodeExecutor(working_dir="~/Projects/demo")
    result = await executor.execute("create a folder called test")
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ActionStatus(Enum):
    """Status of an executed action."""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ActionResult:
    """Result of a Claude Code action execution."""
    action_id: str
    status: ActionStatus
    prompt: str
    output: Optional[str] = None
    error: Optional[str] = None
    files_modified: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "action_id": self.action_id,
            "status": self.status.value,
            "prompt": self.prompt,
            "output": self.output,
            "error": self.error,
            "files_modified": self.files_modified,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @property
    def success(self) -> bool:
        """Check if action completed successfully."""
        return self.status == ActionStatus.COMPLETED


class ClaudeCodeExecutor:
    """
    Executes desktop actions via Claude CLI subprocess.

    Features:
    - Async execution with timeout
    - JSON output parsing
    - Action history tracking
    - File modification detection
    """

    # Allowed tools for demo mode (pre-approved, no confirmation needed)
    DEFAULT_ALLOWED_TOOLS = ["Bash", "Write", "Edit", "Read", "Glob", "Grep"]

    def __init__(
        self,
        working_dir: str = "~/Projects/demo",
        timeout: int = 120,
        allowed_tools: Optional[list[str]] = None,
    ):
        """
        Initialize the Claude Code executor.

        Args:
            working_dir: Directory where actions will be executed
            timeout: Maximum execution time in seconds
            allowed_tools: List of allowed Claude tools (default: Bash, Write, Edit, Read)
        """
        self.working_dir = os.path.expanduser(working_dir)
        self.timeout = timeout
        self.allowed_tools = allowed_tools or self.DEFAULT_ALLOWED_TOOLS
        self.action_history: dict[str, ActionResult] = {}

        # Ensure working directory exists
        os.makedirs(self.working_dir, exist_ok=True)
        logger.info(f"ClaudeCodeExecutor initialized: working_dir={self.working_dir}")

    def _generate_action_id(self) -> str:
        """Generate a unique action ID."""
        import uuid
        return str(uuid.uuid4())[:8]

    def _build_command(self, prompt: str) -> tuple[list[str], str]:
        """
        Build the Claude CLI command.

        Returns:
            Tuple of (command list, prompt to pass via stdin)
        """
        cmd = [
            "claude",
            "-p",  # Print mode (non-interactive)
            "--output-format", "json",  # Structured output
        ]

        # Add allowed tools
        if self.allowed_tools:
            cmd.extend(["--allowedTools", ",".join(self.allowed_tools)])

        # Prompt will be passed via stdin
        return cmd, prompt

    def _extract_files_modified(self, output: str) -> list[str]:
        """Extract list of modified files from Claude output."""
        files = set()

        # Common patterns for file operations
        patterns = [
            r"(?:Created|Modified|Wrote to|Updated|Saved):\s*['\"]?([^\s'\"]+)['\"]?",
            r"(?:mkdir|touch|cp|mv)\s+[^\s]*\s+([^\s\n]+)",
            r"Writing to\s+['\"]?([^\s'\"]+)['\"]?",
            r"File\s+['\"]?([^\s'\"]+)['\"]?\s+(?:created|written|saved)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, output, re.IGNORECASE | re.MULTILINE)
            files.update(matches)

        # Filter to only include paths that look like files/directories
        return [f for f in files if "/" in f or "." in f or f.startswith("~")]

    async def execute(self, prompt: str, context: Optional[str] = None) -> ActionResult:
        """
        Execute an action via Claude CLI.

        Args:
            prompt: The action to execute (natural language)
            context: Optional additional context (e.g., transcription, analysis)

        Returns:
            ActionResult with status, output, and metadata
        """
        action_id = self._generate_action_id()

        result = ActionResult(
            action_id=action_id,
            status=ActionStatus.PENDING,
            prompt=prompt,
            started_at=datetime.now(),
        )
        self.action_history[action_id] = result

        # Build full prompt with context
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\nTask: {prompt}"

        # Add working directory instruction
        full_prompt = f"Working directory: {self.working_dir}\n\n{full_prompt}"

        try:
            result.status = ActionStatus.EXECUTING
            cmd, stdin_prompt = self._build_command(full_prompt)

            logger.info(f"Executing action {action_id}: {prompt[:100]}...")
            logger.debug(f"Command: {' '.join(cmd[:5])}...")

            # Create subprocess with stdin for prompt
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )

            # Wait with timeout, passing prompt via stdin
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin_prompt.encode("utf-8")),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                result.status = ActionStatus.TIMEOUT
                result.error = f"Action timed out after {self.timeout} seconds"
                result.completed_at = datetime.now()
                logger.warning(f"Action {action_id} timed out")
                return result

            # Decode output
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")

            # Parse JSON output
            if process.returncode == 0:
                try:
                    data = json.loads(stdout_text)
                    result.status = ActionStatus.COMPLETED
                    result.output = data.get("result", stdout_text)
                    result.cost_usd = data.get("total_cost_usd", 0.0)
                    result.duration_ms = data.get("duration_ms", 0)
                    result.files_modified = self._extract_files_modified(
                        str(data.get("result", ""))
                    )

                    # Check for errors in the response
                    if data.get("is_error"):
                        result.status = ActionStatus.FAILED
                        result.error = data.get("result", "Unknown error")

                except json.JSONDecodeError:
                    # Non-JSON output (text mode fallback)
                    result.status = ActionStatus.COMPLETED
                    result.output = stdout_text
                    result.files_modified = self._extract_files_modified(stdout_text)
            else:
                result.status = ActionStatus.FAILED
                result.output = stdout_text
                result.error = stderr_text or f"Exit code: {process.returncode}"

            result.completed_at = datetime.now()

            logger.info(
                f"Action {action_id} {result.status.value}: "
                f"{result.output[:100] if result.output else 'no output'}..."
            )

        except FileNotFoundError:
            result.status = ActionStatus.FAILED
            result.error = "Claude CLI not found. Is 'claude' installed and in PATH?"
            result.completed_at = datetime.now()
            logger.error(f"Action {action_id} failed: Claude CLI not found")

        except Exception as e:
            result.status = ActionStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now()
            logger.exception(f"Action {action_id} failed with exception")

        return result

    async def create_folder(self, name: str) -> ActionResult:
        """
        Create a folder in the working directory.

        Args:
            name: Name of the folder to create

        Returns:
            ActionResult
        """
        prompt = f"Create a folder called '{name}' in the current directory. Use mkdir command."
        return await self.execute(prompt)

    async def create_file(self, name: str, content: str = "") -> ActionResult:
        """
        Create a file in the working directory.

        Args:
            name: Name of the file to create
            content: Optional content for the file

        Returns:
            ActionResult
        """
        if content:
            prompt = f"Create a file called '{name}' with this content:\n\n{content}"
        else:
            prompt = f"Create an empty file called '{name}'"
        return await self.execute(prompt)

    async def edit_file(self, path: str, instructions: str) -> ActionResult:
        """
        Edit an existing file.

        Args:
            path: Path to the file (relative to working_dir)
            instructions: What changes to make

        Returns:
            ActionResult
        """
        prompt = f"Edit the file '{path}' with these changes:\n\n{instructions}"
        return await self.execute(prompt)

    async def run_command(self, command: str) -> ActionResult:
        """
        Run a shell command.

        Args:
            command: The shell command to run

        Returns:
            ActionResult
        """
        prompt = f"Run this command: {command}"
        return await self.execute(prompt)

    def get_action(self, action_id: str) -> Optional[ActionResult]:
        """Get a previous action by ID."""
        return self.action_history.get(action_id)

    def get_recent_actions(self, limit: int = 10) -> list[ActionResult]:
        """Get recent action history."""
        actions = list(self.action_history.values())
        actions.sort(key=lambda a: a.started_at or datetime.min, reverse=True)
        return actions[:limit]


# Singleton instance for easy import
_executor: Optional[ClaudeCodeExecutor] = None


def get_executor(
    working_dir: str = "~/Projects/demo",
    timeout: int = 120,
) -> ClaudeCodeExecutor:
    """Get or create the global executor instance."""
    global _executor
    if _executor is None:
        _executor = ClaudeCodeExecutor(working_dir=working_dir, timeout=timeout)
    return _executor
