"""Agent provisioning is checked offline against the installed SDK's method signatures."""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, create_autospec

import pytest
from elevenlabs.pronunciation_dictionaries.client import PronunciationDictionariesClient
from elevenlabs.types import (
    AgentConfigOverrideConfig,
    AuthSettings,
    ConversationConfigClientOverrideConfigInput,
    ConversationInitiationClientDataConfigInput,
)

SPEC = importlib.util.spec_from_file_location(
    "voice_agent", Path(__file__).resolve().parents[3] / "scripts" / "voice_agent.py"
)
assert SPEC and SPEC.loader
voice_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice_agent)


def client_with_pages(*pages: SimpleNamespace) -> SimpleNamespace:
    dictionaries = create_autospec(PronunciationDictionariesClient, instance=True)
    dictionaries.list.side_effect = pages
    dictionaries.create_from_rules.return_value = SimpleNamespace(id="dictionary", version_id="v1")
    return SimpleNamespace(pronunciation_dictionaries=dictionaries, conversational_ai=Mock())


def page(items: Sequence[SimpleNamespace] = (), cursor: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        pronunciation_dictionaries=items, has_more=cursor is not None, next_cursor=cursor
    )


def test_create_pronunciation_dictionary() -> None:
    client = client_with_pages(page())
    assert voice_agent.pronunciation_dictionary(client) == {
        "pronunciation_dictionary_id": "dictionary",
        "version_id": "v1",
    }
    client.pronunciation_dictionaries.create_from_rules.assert_called_once()
    body = client.pronunciation_dictionaries.create_from_rules.call_args.kwargs
    assert body["name"] == voice_agent.DICTIONARY_NAME
    assert body["rules"] == [
        {"type": "alias", "string_to_replace": "Mothusi", "alias": voice_agent.PRONOUNCE}
    ]


def test_reuse_dictionary_on_later_page() -> None:
    existing = SimpleNamespace(
        name=voice_agent.DICTIONARY_NAME, id="existing", latest_version_id="v2"
    )
    client = client_with_pages(page(cursor="next"), page([existing]))
    assert voice_agent.pronunciation_dictionary(client) == {
        "pronunciation_dictionary_id": "existing",
        "version_id": "v2",
    }
    assert client.pronunciation_dictionaries.list.call_args.kwargs["cursor"] == "next"
    client.pronunciation_dictionaries.create_from_rules.assert_not_called()


@pytest.mark.parametrize("command", [["create"], ["update", "agent-test"]])
def test_agent_attaches_dictionary(monkeypatch: pytest.MonkeyPatch, command: list[str]) -> None:
    client = client_with_pages(page())

    def make_client(**_: object) -> SimpleNamespace:
        return client

    def load_dotenv(_: str) -> None:
        return None

    monkeypatch.setattr(voice_agent, "ElevenLabs", make_client)
    monkeypatch.setattr(voice_agent, "load_dotenv", load_dotenv)
    assert voice_agent.main(command) == 0
    method = (
        client.conversational_ai.agents.create
        if command == ["create"]
        else client.conversational_ai.agents.update
    )
    assert method.call_args.kwargs["conversation_config"]["tts"][
        "pronunciation_dictionary_locators"
    ] == [{"pronunciation_dictionary_id": "dictionary", "version_id": "v1"}]


def test_session_overrides_and_context_use_sdk_fields() -> None:
    from elevenlabs.types import AgentPlatformSettingsRequestModel

    settings = AgentPlatformSettingsRequestModel.model_validate(voice_agent.platform_settings())
    assert cast(AuthSettings, settings.auth).enable_auth is True
    overrides = cast(
        ConversationConfigClientOverrideConfigInput,
        cast(
            ConversationInitiationClientDataConfigInput, settings.overrides
        ).conversation_config_override,
    )
    assert cast(AgentConfigOverrideConfig, overrides.agent).first_message is True
    config = voice_agent.conversation_config(
        {"pronunciation_dictionary_id": "dictionary", "version_id": "v1"}
    )
    assert config["agent"]["first_message"] == ""
    prompt = config["agent"]["prompt"]["prompt"]
    for variable in [
        "page",
        "run_id",
        "run_title",
        "run_state",
        "waiting_for_teacher",
        "teacher_task",
        "run_stopped",
        "disclaimer_given",
        "previous_reply",
    ]:
        assert "{{" + variable + "}}" in prompt
    assert "only when a learner's name comes up" in prompt
