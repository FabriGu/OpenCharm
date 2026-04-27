#!/usr/bin/env python3
"""
Intent Parser

Parses voice transcriptions into structured intents for action execution.
Uses regex pattern matching to identify common commands.

Usage:
    parser = IntentParser()
    intent = parser.parse("create a folder called test")
    # Intent(category=CREATE_FOLDER, target="test", confidence=0.9)
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IntentCategory(Enum):
    """Categories of user intents."""
    CREATE_FOLDER = "create_folder"
    CREATE_FILE = "create_file"
    DELETE_ITEM = "delete_item"
    RENAME_ITEM = "rename_item"
    OPEN_ITEM = "open_item"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    EDIT_WEBSITE = "edit_website"
    IMPLEMENT_UI = "implement_ui"
    GIT_OPERATION = "git_operation"
    UNKNOWN = "unknown"


@dataclass
class ParsedIntent:
    """Structured representation of a parsed intent."""
    category: IntentCategory
    target: Optional[str] = None
    destination: Optional[str] = None
    content: Optional[str] = None
    confidence: float = 0.0
    raw_text: str = ""
    requires_image: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "category": self.category.value,
            "target": self.target,
            "destination": self.destination,
            "content": self.content,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
            "requires_image": self.requires_image,
        }


class IntentParser:
    """
    Parse voice transcriptions into structured intents.

    Uses regex patterns to identify common commands and extract parameters.
    Falls back to UNKNOWN for unrecognized inputs.
    """

    # Pattern definitions: (category, patterns, requires_image)
    # Each pattern captures the target in group 1 when applicable
    PATTERNS: list[tuple[IntentCategory, list[str], bool]] = [
        # Folder operations
        (IntentCategory.CREATE_FOLDER, [
            r"create\s+(?:a\s+)?(?:new\s+)?folder\s+(?:called\s+|named\s+)?['\"]?(\w+)['\"]?",
            r"make\s+(?:a\s+)?(?:new\s+)?folder\s+(?:called\s+|named\s+)?['\"]?(\w+)['\"]?",
            r"new\s+folder\s+(?:called\s+|named\s+)?['\"]?(\w+)['\"]?",
            r"(?:add|create)\s+(?:a\s+)?directory\s+(?:called\s+|named\s+)?['\"]?(\w+)['\"]?",
        ], False),

        # File operations
        (IntentCategory.CREATE_FILE, [
            r"create\s+(?:a\s+)?(?:new\s+)?file\s+(?:called\s+|named\s+)?['\"]?([\w.]+)['\"]?",
            r"make\s+(?:a\s+)?(?:new\s+)?file\s+(?:called\s+|named\s+)?['\"]?([\w.]+)['\"]?",
            r"new\s+file\s+(?:called\s+|named\s+)?['\"]?([\w.]+)['\"]?",
            r"touch\s+['\"]?([\w.]+)['\"]?",
        ], False),

        # Delete operations
        (IntentCategory.DELETE_ITEM, [
            r"delete\s+(?:the\s+)?(?:file\s+|folder\s+)?['\"]?([\w./]+)['\"]?",
            r"remove\s+(?:the\s+)?(?:file\s+|folder\s+)?['\"]?([\w./]+)['\"]?",
            r"rm\s+['\"]?([\w./]+)['\"]?",
        ], False),

        # Rename operations
        (IntentCategory.RENAME_ITEM, [
            r"rename\s+['\"]?([\w./]+)['\"]?\s+(?:to\s+)?['\"]?([\w./]+)['\"]?",
            r"mv\s+['\"]?([\w./]+)['\"]?\s+['\"]?([\w./]+)['\"]?",
        ], False),

        # Open operations
        (IntentCategory.OPEN_ITEM, [
            r"open\s+(?:the\s+)?(?:file\s+|folder\s+|app\s+)?['\"]?([\w./]+)['\"]?",
            r"launch\s+['\"]?([\w./]+)['\"]?",
            r"start\s+['\"]?([\w./]+)['\"]?",
        ], False),

        # Edit file
        (IntentCategory.EDIT_FILE, [
            r"edit\s+(?:the\s+)?(?:file\s+)?['\"]?([\w./]+)['\"]?",
            r"modify\s+(?:the\s+)?(?:file\s+)?['\"]?([\w./]+)['\"]?",
            r"change\s+(?:the\s+)?(?:file\s+)?['\"]?([\w./]+)['\"]?",
            r"update\s+(?:the\s+)?(?:file\s+)?['\"]?([\w./]+)['\"]?",
        ], False),

        # Run command
        (IntentCategory.RUN_COMMAND, [
            r"run\s+(?:the\s+)?(?:command\s+)?['\"]?(.+)['\"]?",
            r"execute\s+['\"]?(.+)['\"]?",
        ], False),

        # Website/UI operations (usually require image)
        (IntentCategory.EDIT_WEBSITE, [
            r"(?:edit|update|change|modify)\s+(?:the\s+)?(?:web\s*)?(?:site|page)",
            r"change\s+(?:the\s+)?(?:web\s*)?(?:site|page)\s+(?:to\s+)?(?:look\s+)?(?:like\s+)?(?:this)?",
        ], True),

        (IntentCategory.IMPLEMENT_UI, [
            r"implement\s+(?:this\s+)?(?:ui\s+)?(?:design|mockup|layout|sketch)",
            r"build\s+(?:this\s+)?(?:ui\s+)?(?:design|mockup|layout|sketch)",
            r"create\s+(?:this\s+)?(?:ui\s+)?(?:from\s+)?(?:the\s+)?(?:design|mockup|image|sketch)",
            r"make\s+(?:this\s+)?(?:ui\s+)?(?:design|mockup|layout)",
            r"implement\s+this",
            r"build\s+this",
            r"create\s+this",
        ], True),

        # Git operations
        (IntentCategory.GIT_OPERATION, [
            r"git\s+(status|add|commit|push|pull|diff|log)",
            r"(?:commit|push|pull)\s+(?:the\s+)?(?:changes)?",
            r"show\s+(?:the\s+)?(?:git\s+)?(?:status|diff|log)",
        ], False),
    ]

    def __init__(self):
        """Initialize the intent parser."""
        # Pre-compile all patterns for efficiency
        self._compiled_patterns: list[tuple[IntentCategory, list[re.Pattern], bool]] = []
        for category, patterns, requires_image in self.PATTERNS:
            compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
            self._compiled_patterns.append((category, compiled, requires_image))

    def parse(self, text: str, has_images: bool = False) -> ParsedIntent:
        """
        Parse a text input into a structured intent.

        Args:
            text: The transcription or command text
            has_images: Whether images are available (affects interpretation)

        Returns:
            ParsedIntent with category, target, and confidence
        """
        text = text.strip()
        text_lower = text.lower()

        # Try each category's patterns
        for category, patterns, requires_image in self._compiled_patterns:
            for pattern in patterns:
                match = pattern.search(text_lower)
                if match:
                    # Extract target from first capture group
                    target = match.group(1) if match.groups() else None

                    # Extract destination from second group (for rename, etc.)
                    destination = match.group(2) if len(match.groups()) > 1 else None

                    return ParsedIntent(
                        category=category,
                        target=target,
                        destination=destination,
                        confidence=0.85,
                        raw_text=text,
                        requires_image=requires_image,
                    )

        # If we have images but no clear command, assume UI implementation
        if has_images:
            return ParsedIntent(
                category=IntentCategory.IMPLEMENT_UI,
                confidence=0.6,
                raw_text=text,
                requires_image=True,
            )

        # Unknown intent
        return ParsedIntent(
            category=IntentCategory.UNKNOWN,
            confidence=0.0,
            raw_text=text,
            requires_image=False,
        )

    def build_prompt(self, intent: ParsedIntent) -> str:
        """
        Build an action prompt from a parsed intent.

        Args:
            intent: The parsed intent

        Returns:
            A natural language prompt for Claude Code execution
        """
        prompts = {
            IntentCategory.CREATE_FOLDER: (
                f"Create a new folder named '{intent.target}'. "
                f"Use mkdir command and confirm it was created successfully."
            ),
            IntentCategory.CREATE_FILE: (
                f"Create a new file named '{intent.target}'. "
                f"If it has an extension, add appropriate boilerplate content."
            ),
            IntentCategory.DELETE_ITEM: (
                f"Delete the item named '{intent.target}'. "
                f"First check if it exists, then remove it."
            ),
            IntentCategory.RENAME_ITEM: (
                f"Rename '{intent.target}' to '{intent.destination}'."
            ),
            IntentCategory.OPEN_ITEM: (
                f"Open '{intent.target}' using the default application."
            ),
            IntentCategory.EDIT_FILE: (
                f"Edit the file '{intent.target}'. {intent.raw_text}"
            ),
            IntentCategory.RUN_COMMAND: (
                f"Run the command: {intent.target}"
            ),
            IntentCategory.EDIT_WEBSITE: (
                f"Edit the website based on these instructions: {intent.raw_text}"
            ),
            IntentCategory.IMPLEMENT_UI: (
                f"Implement this UI design. Create the necessary HTML, CSS, and JavaScript files. "
                f"User instruction: {intent.raw_text}"
            ),
            IntentCategory.GIT_OPERATION: (
                f"Execute git operation: {intent.raw_text}"
            ),
            IntentCategory.UNKNOWN: (
                f"Execute the following user request: {intent.raw_text}"
            ),
        }

        return prompts.get(intent.category, intent.raw_text)


# Singleton instance
_parser: Optional[IntentParser] = None


def get_parser() -> IntentParser:
    """Get or create the global parser instance."""
    global _parser
    if _parser is None:
        _parser = IntentParser()
    return _parser
