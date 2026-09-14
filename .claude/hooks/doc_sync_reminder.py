#!/usr/bin/env python3
"""PostToolUse (Edit|Write) hook: nudge toward doc updates for architecturally
significant files. Non-blocking -- prints hookSpecificOutput.additionalContext
so Claude sees the reminder, decides for itself if THIS change needs it.

CLAUDE.md's "Keep this document updated" rule already asks for this manually;
this hook exists because that rule got missed once (see git history around
2026-08 for the Commander-only importer doc drift across four files).

Order matters: more specific rules must come before broader directory rules.
"""
import json
import sys

RULES = [
    # (path needle, "docs that should still match", optional trailing note)
    ("backend/database/models.py", "docs/data-model.md", None),
    (
        "backend/knowledge_pipeline/scryfall_importer/formats.py",
        "docs/knowledge-pipeline.md, CLAUDE.md, docs/self-hosting.md, and docs/architecture.md",
        "all four went stale together the last time formats.py changed",
    ),
    ("backend/knowledge_pipeline/scryfall_importer/", "docs/knowledge-pipeline.md", None),
    ("backend/api/routes/", "docs/architecture.md (project/module structure)", None),
    ("backend/api/main.py", "docs/architecture.md (project/module structure)", None),
    ("backend/api/schemas.py", "docs/architecture.md (project/module structure)", None),
    (
        "backend/ai/claude_client.py",
        "CLAUDE.md (Stack table) and docs/architecture.md "
        "(Claude vs. backend division of responsibility)",
        None,
    ),
    (
        "backend/config.py",
        "docs/self-hosting.md (env var table) and backend/.env.example",
        None,
    ),
    ("Dockercompose.dev.yaml", "CLAUDE.md (Docker section) and docs/self-hosting.md", None),
    ("Dockercompose.yaml", "CLAUDE.md (Docker section) and docs/self-hosting.md", None),
    ("frontend/src/pages/Decks.tsx", "docs/frontend.md", None),
    ("frontend/src/lib/", "docs/frontend.md", None),
    ("frontend/vite.config.ts", "docs/frontend.md", None),
]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path:
        return

    normalized = file_path.replace("\\", "/")

    for needle, docs, note in RULES:
        if needle in normalized:
            name = normalized.rsplit("/", 1)[-1]
            message = f"Doc sync check: {name} changed -- verify {docs} still match."
            if note:
                message += f" ({note}.)"
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": message,
                }
            }))
            return


if __name__ == "__main__":
    main()
