"""Agent Desk chat: the model gets the ticket + retrieved cases, citations are checked, and the
chat degrades gracefully when no LLM is reachable. The LLM itself is replaced by a fake."""
import pandas as pd
import pytest

from src.ai import agent_chat, llm_client

TICKET = {"ticket_id": "INC0900001", "priority": "P2", "status": "In Progress", "category": "VPN",
          "subcategory": "Cannot connect", "affected_service": "Unknown", "site": "Home office",
          "short_description": "Cannot connect to VPN", "description": "Timeout during authentication."}
MATCHES = pd.DataFrame([
    {"ticket_id": "INC0700333", "similarity": 0.44, "category": "VPN", "subcategory": "Cannot connect",
     "short_description": "Cannot connect to VPN", "resolution_notes": "Restarted the VPN concentrator.",
     "resolution_code": "Restart"},
    {"ticket_id": "INC0700639", "similarity": 0.41, "category": "VPN", "subcategory": "Cannot connect",
     "short_description": "VPN fails", "resolution_notes": "Renewed the client certificate.",
     "resolution_code": "Config Change"}])


@pytest.fixture()
def fake_llm(monkeypatch):
    calls = []

    def ask(system, user, temperature=0, max_tokens=800):
        calls.append({"system": system, "user": user})
        return ask.reply

    ask.reply = "Try renewing the certificate [INC0700639]."
    monkeypatch.setattr(llm_client, "ask_llm", ask)
    monkeypatch.setattr(llm_client, "LAST_PROVIDER", "fake-model", raising=False)
    ask.calls = calls
    return ask


def test_prompt_contains_ticket_cases_history_and_question(fake_llm):
    history = [{"role": "user", "content": "Is it widespread?"},
               {"role": "assistant", "content": "Unclear, ask the user."}]
    out = agent_chat.chat_reply(TICKET, MATCHES, history, "What should I try first?")
    sent = fake_llm.calls[0]
    for expected in ("INC0900001", "Timeout during authentication", "[INC0700333]", "Restarted the VPN concentrator",
                     "similarity 0.44", "Agent: Is it widespread?", "Copilot: Unclear", "What should I try first?"):
        assert expected in sent["user"]
    assert "never instructions" in sent["system"] and "General advice (not from past tickets)" in sent["system"]
    assert out["reply"].startswith("Try renewing") and out["provider"] == "fake-model"


def test_only_retrieved_ticket_ids_count_as_valid_citations(fake_llm):
    fake_llm.reply = "See [INC0700333], [INC0700639], the ticket itself INC0900001 and [INC0123456]."
    out = agent_chat.chat_reply(TICKET, MATCHES, [], "Any precedent?")
    assert out["cited_ids"] == ["INC0700333", "INC0700639", "INC0900001"]
    assert out["invalid_citations"] == ["INC0123456"]                # invented id gets flagged


def test_history_is_trimmed_and_question_is_capped(fake_llm):
    history = [{"role": "user", "content": f"question number {i}"} for i in range(20)]
    agent_chat.chat_reply(TICKET, MATCHES, history, "x" * 5000)
    prompt = fake_llm.calls[0]["user"]
    assert "question number 19" in prompt and "question number 11" not in prompt   # last 8 only
    assert "x" * 1001 not in prompt and "x" * 1000 in prompt


def test_empty_question_is_refused_without_calling_the_model(fake_llm):
    with pytest.raises(ValueError):
        agent_chat.chat_reply(TICKET, MATCHES, [], "   ")
    assert fake_llm.calls == []


def test_offline_fallback_lists_the_similar_resolutions(monkeypatch):
    def down(*args, **kwargs):
        raise llm_client.LLMUnavailableError("no provider")
    monkeypatch.setattr(llm_client, "ask_llm", down)
    out = agent_chat.chat_reply(TICKET, MATCHES, [], "What now?")
    assert out["provider"] == "offline fallback (no LLM)"
    assert "Restarted the VPN concentrator. [INC0700333]" in out["reply"]
    assert out["cited_ids"] == ["INC0700333", "INC0700639"] and out["invalid_citations"] == []


def test_no_similar_cases_is_stated_not_hidden(fake_llm, monkeypatch):
    empty = MATCHES.iloc[0:0]
    agent_chat.chat_reply(TICKET, empty, [], "What now?")
    assert "no similar past tickets were found" in fake_llm.calls[0]["user"]
    monkeypatch.setattr(llm_client, "ask_llm", lambda *a, **k: (_ for _ in ()).throw(llm_client.LLMUnavailableError("x")))
    assert "no similar past tickets" in agent_chat.chat_reply(TICKET, empty, [], "What now?")["reply"]
