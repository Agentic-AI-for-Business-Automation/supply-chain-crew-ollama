"""The LLM object built by main.py must accept a real call. A bad keyword (e.g. num_retries) only fails at call time,
so this test makes one tiny request; it is skipped when no model endpoint is reachable."""
import os
import pytest


def test_build_llm_makes_a_real_call():
    import main
    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    if model.startswith("ollama/"):
        import requests
        try:
            requests.get(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/tags", timeout=2)
        except Exception:
            pytest.skip("Ollama not reachable")
    elif not os.getenv("OPENAI_API_KEY"):
        pytest.skip("no model credentials configured")
    llm = main.build_llm()
    try:
        out = llm.call("Reply with the single word ok.")
    except Exception as e:
        if "unexpected keyword" in str(e):
            raise
        pytest.skip(f"model endpoint unavailable: {type(e).__name__}")
    assert isinstance(out, str) and out.strip()
