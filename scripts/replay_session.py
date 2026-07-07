"""
Replay a Claude Agent SDK session to see the full prompt/response exchange.

Usage:
  ./venv/bin/python scripts/replay_session.py <session-id>
  ./venv/bin/python scripts/replay_session.py <session-id> --json

Session IDs are logged by the queue worker:
  Triage  session=<id> url=<url> verdict=YES|NO
  Extract session=<id> url=<url> category=... district=...
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_agent_sdk import get_session_messages, AssistantMessage, UserMessage, SystemMessage, TextBlock


def _text(blocks) -> str:
    parts = []
    for b in (blocks or []):
        if isinstance(b, TextBlock):
            parts.append(b.text)
        elif hasattr(b, "text"):
            parts.append(b.text)
        elif isinstance(b, dict) and "text" in b:
            parts.append(b["text"])
    return "\n".join(parts).strip()


async def replay(session_id: str, as_json: bool) -> None:
    messages = await get_session_messages(session_id=session_id)

    if as_json:
        print(json.dumps([m.__dict__ if hasattr(m, "__dict__") else str(m) for m in messages], indent=2, default=str))
        return

    print(f"\n{'═' * 70}")
    print(f"  Session: {session_id}")
    print(f"{'═' * 70}\n")

    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue  # skip internal system init noise
        elif isinstance(msg, UserMessage):
            content = _text(msg.content) if hasattr(msg, "content") else str(msg)
            print(f"{'─' * 70}")
            print("USER PROMPT:")
            print(f"{'─' * 70}")
            print(content[:2000])
            print()
        elif isinstance(msg, AssistantMessage):
            content = _text(msg.content) if hasattr(msg, "content") else str(msg)
            print(f"{'─' * 70}")
            print("CLAUDE RESPONSE:")
            print(f"{'─' * 70}")
            print(content[:3000])
            print()
        else:
            # ResultMessage — show cost + stop reason
            cost = getattr(msg, "total_cost_usd", None)
            stop = getattr(msg, "stop_reason", None)
            turns = getattr(msg, "num_turns", None)
            structured = getattr(msg, "structured_output", None)
            print(f"{'─' * 70}")
            print(f"RESULT  stop={stop}  turns={turns}  cost=${cost:.6f}" if cost else f"RESULT  stop={stop}  turns={turns}")
            if structured:
                print("Structured output:")
                print(json.dumps(structured, indent=2))
            print()

    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay a Claude Code session")
    parser.add_argument("session_id", help="Session ID from worker logs")
    parser.add_argument("--json", action="store_true", help="Dump raw JSON instead of formatted output")
    args = parser.parse_args()

    asyncio.run(replay(args.session_id, args.json))
