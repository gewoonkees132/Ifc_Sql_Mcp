"""Summarise headless Claude Code stream-json transcripts: tool calls, final answer, cost.

Usage: python -I parse_transcript.py out/q1.jsonl [out/q2.jsonl ...]
"""

# ruff: noqa: T201

import json
import sys
from pathlib import Path

PREFIX = "mcp__ifc-sql__"


def summarise(path: Path) -> None:
    """Print one transcript's tool calls and final answer."""
    calls: list[str] = []
    final = cost = turns = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("type") == "assistant":
            for block in message["message"].get("content", []):
                if block.get("type") == "tool_use":
                    arguments = json.dumps(block["input"], ensure_ascii=False)[:260]
                    calls.append(f"{block['name'].removeprefix(PREFIX)} {arguments}")
        if message.get("type") == "result":
            final = message.get("result")
            cost = message.get("total_cost_usd")
            turns = message.get("num_turns")
    print(f"##### {path.name}  turns={turns} cost=${cost} tool_calls={len(calls)}")
    for call in calls:
        print("  -", call)
    print("----- final answer:\n" + (final or "(none)") + "\n")


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        summarise(Path(argument))
