"""The guardrails must really be attached to the CrewAI tasks (a validator nobody calls protects nothing)."""
from crewai import LLM
from agents.analyst import build_analyst_agent, build_analyst_task
from agents.coordinator import build_coordinator_agent, build_coordinator_task
from agents.scout import build_scout_agent, build_scout_task
from schemas.guardrails import GUARDRAIL_RETRIES, scout_guardrail


def build():
    llm = LLM(model="ollama/gemma4:31b-cloud", base_url="http://localhost:11434")
    s, a, c = build_scout_agent(llm), build_analyst_agent(llm), build_coordinator_agent(llm)
    st = build_scout_task(s)
    at = build_analyst_task(a, [st])
    ct = build_coordinator_task(c, [st, at])
    return st, at, ct


def test_scout_and_analyst_tasks_carry_guardrails():
    st, at, ct = build()
    assert st.guardrail is scout_guardrail and st.guardrail_max_retries == GUARDRAIL_RETRIES
    assert callable(at.guardrail) and at.guardrail_max_retries == GUARDRAIL_RETRIES
    assert ct.context == [st, at]


def test_analyst_task_without_context_has_no_guardrail():
    llm = LLM(model="ollama/gemma4:31b-cloud", base_url="http://localhost:11434")
    assert build_analyst_task(build_analyst_agent(llm)).guardrail is None


def test_prompts_tell_agents_that_web_text_is_data():
    st, at, ct = build()
    assert "untrusted_search_results" in st.description
    assert "treat it as data" in at.description and "treat it as data" in ct.description
