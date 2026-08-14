import asyncio
import json

from local_server.pipeline import openai_chains


def test_triage_article_uses_openai_yes(monkeypatch):
    calls = []

    async def fake_chat(system_prompt, user_prompt, *, json_mode=False, timeout=60.0):
        calls.append((system_prompt, user_prompt, json_mode, timeout))
        return "YES"

    monkeypatch.setattr(openai_chains, "_openai_chat", fake_chat)

    is_relevant, session_id = asyncio.run(
        openai_chains.triage_article("Police arrested a man for murder in Chennai.")
    )

    assert is_relevant is True
    assert session_id == "openai"
    assert calls[0][2] is False
    assert "reply YES or NO" in calls[0][0]


def test_extract_incident_parses_json_and_applies_validator(monkeypatch):
    responses = [
        json.dumps(
            {
                "title": "Man arrested for Chennai murder",
                "district": "Chennai city",
                "category": "Homicide",
                "summary": "A man was arrested after a murder in Chennai. Police said the probe is ongoing.",
                "sentiment": "negative",
                "viral_score": 75,
                "confidence": 0.83,
            }
        ),
        json.dumps({"corrections": {"district": "chennai", "viral_score": 80}}),
    ]

    async def fake_chat(system_prompt, user_prompt, *, json_mode=False, timeout=60.0):
        assert json_mode is True
        return responses.pop(0)

    monkeypatch.setattr(openai_chains, "_openai_chat", fake_chat)

    extracted, session_id = asyncio.run(
        openai_chains.extract_incident(
            "A man was arrested after a murder in Chennai.",
            {"detected_district": None, "detected_category": "Homicide", "entities": {}},
        )
    )

    assert session_id == "openai"
    assert extracted["district"] == "chennai"
    assert extracted["viral_score"] == 80
    assert extracted["category"] == "Homicide"


def test_process_article_returns_none_when_openai_key_missing(monkeypatch):
    monkeypatch.setattr(openai_chains.settings, "openai_api_key", "")
    monkeypatch.setattr(openai_chains.settings, "openai_base_url", "https://api.openai.com/v1")

    result = asyncio.run(
        openai_chains.process_article(
            {
                "source_id": "test",
                "source_name": "Test",
                "url": "https://example.com/crime",
                "title": "Murder reported in Chennai",
                "text": "Police arrested a suspect after a murder in Chennai city.",
                "language": "en",
            }
        )
    )

    assert result is None


def test_process_article_allows_missing_key_for_local_hermes_proxy(monkeypatch):
    monkeypatch.setattr(openai_chains.settings, "openai_api_key", "")
    monkeypatch.setattr(openai_chains.settings, "openai_base_url", "http://127.0.0.1:8645/v1")

    async def fake_triage(text):
        return False, "openai-compatible"

    monkeypatch.setattr(openai_chains, "triage_article", fake_triage)

    result = asyncio.run(
        openai_chains.process_article(
            {
                "source_id": "test",
                "source_name": "Test",
                "url": "https://example.com/crime",
                "title": "Murder reported in Chennai",
                "text": "Police arrested a suspect after a murder in Chennai city.",
                "language": "en",
            }
        )
    )

    assert result is None
