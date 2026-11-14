"""Create, or update, Mothusi's ElevenLabs agent (conversation.md §12).

    uv run python scripts/voice_agent.py create            # prints the new agent id, nothing else
    uv run python scripts/voice_agent.py update <agent_id> # re-applies this file's configuration

The key is read from the environment (ELEVENLABS_API_KEY), loaded from .env.local and then .env with
python-dotenv: it is never written in code and never printed. The agent's configuration lives here,
next to the design, so what the voice may do is reviewed like any other code:

- one client tool, ask_mothusi, that sends the teacher's words down the same path as typing and
  returns Mothusi's answer, composed in code, to be read aloud verbatim;
- no tool that approves, releases or sends anything;
- authentication on: only the signed links from GET /voice/session can start a conversation;
- names kept out by asking for L-numbers (the prompt and the box's notice) and by Mothusi's
  answers carrying only pseudonyms. Transcript redaction needs an ElevenLabs enterprise plan,
  so it is not set.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from dotenv import load_dotenv
from elevenlabs import ElevenLabs

NAME = "Mothusi - assessment colleague"
FIRST_MESSAGE = "Hi, I'm Mothusi. What can I do for you?"
PROMPT = (
    "You are Mothusi's voice: a colleague who helps a South African Grade 10 teacher with "
    "their assessment work. For every request, call ask_mothusi with the teacher's words and "
    "read its answer aloud exactly as given. Never add marks, quotes, curriculum facts or "
    "learner details of your own. If ask_mothusi says it is at capacity, say so and that the "
    "answer will appear in the box. If asked to approve or release something, say that is "
    "done on the screen. Ask the teacher to say learner numbers like L-014, not names. "
    "Keep your own words short."
)
ASK_MOTHUSI: dict[str, Any] = {
    "type": "client",
    "name": "ask_mothusi",
    "description": (
        "Send the teacher's request to Mothusi and get the answer to read aloud. "
        "Use it for every request."
    ),
    "expects_response": True,
    "response_timeout_secs": 60,
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The teacher's request, in their own words.",
            }
        },
        "required": ["question"],
    },
}


def conversation_config() -> dict[str, Any]:
    return {
        "agent": {
            "first_message": FIRST_MESSAGE,
            "language": "en",
            "prompt": {
                "prompt": PROMPT,
                "llm": "gemini-2.5-flash",
                "temperature": 0,
                "tools": [ASK_MOTHUSI],
            },
        },
    }


def platform_settings() -> dict[str, Any]:
    return {
        "auth": {"enable_auth": True},
    }


def main(argv: list[str]) -> int:
    load_dotenv(".env.local")
    load_dotenv(".env")
    # The SDK reads ELEVENLABS_API_KEY as a default argument when it is imported, before
    # load_dotenv has run, so hand it over from the environment explicitly.
    client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))
    if argv[:1] == ["create"]:
        agent = client.conversational_ai.agents.create(
            name=NAME,
            conversation_config=conversation_config(),  # pyright: ignore[reportArgumentType]
            platform_settings=platform_settings(),  # pyright: ignore[reportArgumentType]
        )
        print(agent.agent_id)
        return 0
    if argv[:1] == ["update"] and len(argv) == 2:
        client.conversational_ai.agents.update(
            agent_id=argv[1],
            name=NAME,
            conversation_config=conversation_config(),  # pyright: ignore[reportArgumentType]
            platform_settings=platform_settings(),  # pyright: ignore[reportArgumentType]
        )
        print(argv[1])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
