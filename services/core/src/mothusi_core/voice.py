"""Server-side ElevenLabs session credentials; failures never expose the API key."""

import os

import httpx


class VoiceOff(Exception):  # noqa: N818 — public contract name
    """Voice is unavailable, with a reason safe to show the teacher."""


async def signed_url(client: httpx.AsyncClient) -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceOff("no ElevenLabs key configured")
    agent = os.environ.get("ELEVENLABS_AGENT_ID")
    if not agent:
        raise VoiceOff("no ElevenLabs agent configured")
    try:
        response = await client.get(
            "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
            params={"agent_id": agent},
            headers={"xi-api-key": key},
            timeout=10,
        )
    except httpx.HTTPStatusError as error:
        raise VoiceOff(f"ElevenLabs refused the session: {error.response.status_code}") from None
    except httpx.RequestError:
        raise VoiceOff("ElevenLabs is unreachable") from None
    if response.status_code != 200:
        raise VoiceOff(f"ElevenLabs refused the session: {response.status_code}")
    try:
        url = response.json()["signed_url"]
        if not isinstance(url, str) or not url or key in url:
            raise ValueError("invalid session")
    except ValueError, KeyError, TypeError:
        raise VoiceOff("ElevenLabs refused the session: 200") from None
    return url
