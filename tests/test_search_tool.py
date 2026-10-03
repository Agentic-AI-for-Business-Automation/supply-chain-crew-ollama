import pytest
import tools.search_tool as st


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Isolate global toggle + env between tests so order never matters."""
    st._state.update(calls=0, simulate=False)
    monkeypatch.delenv("SIMULATE_SEARCH_FAILURE", raising=False)
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    yield
    st._state.update(calls=0, simulate=False)


def test_formats_serper_results(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "x")

    class FakeSerper:
        def __init__(self, **kw): pass
        def results(self, q): return {"news": [{"title": "Typhoon shuts Kaohsiung", "link": "https://n/1",
                                                "snippet": "Port closed", "date": "1 day ago", "source": "Reuters"}]}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", FakeSerper)
    out = st.web_search.run(query="Kaohsiung typhoon")
    assert "Typhoon shuts Kaohsiung" in out and "https://n/1" in out


def test_output_block_format(monkeypatch):
    """Each hit must render as '- title | date | source', snippet, URL."""
    monkeypatch.setenv("SERPER_API_KEY", "x")

    class FakeSerper:
        def __init__(self, **kw): pass
        def results(self, q):
            return {"news": [{"title": "T", "link": "https://u",
                              "snippet": "S", "date": "D", "source": "SRC"}]}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", FakeSerper)
    out = st.web_search.run(query="q")
    assert "- T | D | SRC" in out
    assert "https://u" in out


def test_simulated_failure_then_recovers(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "x")

    class FakeSerper:
        def __init__(self, **kw): pass
        def results(self, q): return {"news": [{"title": "OK", "link": "u"}]}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", FakeSerper)
    st.enable_simulated_failure(True)
    assert "OK" in st.web_search.run(query="q")
    st.enable_simulated_failure(False)


def test_duckduckgo_fallback_success(monkeypatch):
    """No SERPER key -> DDG fallback returns formatted news."""
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    import ddgs

    class FakeDDGS:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def news(self, *a, **k):
            return [{"title": "DDG hit", "url": "https://d/1",
                     "body": "fallback body", "date": "today", "source": "DDG"}]
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    out = st.web_search.run(query="Kaohsiung port")
    assert "DDG hit" in out and "https://d/1" in out


def test_all_providers_down_returns_message(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    import ddgs

    class Broken:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def news(self, *a, **k): raise RuntimeError("down")
    monkeypatch.setattr(ddgs, "DDGS", Broken)
    out = st.web_search.run(query="q")
    assert out.startswith("SEARCH UNAVAILABLE")
    assert "never invent" in out.lower()


def test_empty_query_never_crashes():
    assert st.web_search.run(query="").startswith("SEARCH UNAVAILABLE")


def test_malformed_hits_never_crash(monkeypatch):
    """None / non-dict hits are skipped, never raise."""
    monkeypatch.setenv("SERPER_API_KEY", "x")

    class FakeSerper:
        def __init__(self, **kw): pass
        def results(self, q): return {"news": [None, "x", {}, {"title": "Good", "link": "u"}]}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", FakeSerper)
    out = st.web_search.run(query="q")
    assert "Good" in out


def test_env_var_simulate_triggers_retry(monkeypatch, capsys):
    """SIMULATE_SEARCH_FAILURE=true (no programmatic toggle) still retries then recovers."""
    monkeypatch.setenv("SERPER_API_KEY", "x")
    monkeypatch.setenv("SIMULATE_SEARCH_FAILURE", "true")

    class FakeSerper:
        def __init__(self, **kw): pass
        def results(self, q): return {"news": [{"title": "Recovered", "link": "u"}]}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", FakeSerper)
    out = st.web_search.run(query="q")
    assert "Recovered" in out
    assert "RECOVERY" in capsys.readouterr().out


def test_none_results_and_empty_news(monkeypatch):
    """Serper returning None or empty news never crashes."""
    monkeypatch.setenv("SERPER_API_KEY", "x")

    class NoneSerper:
        def __init__(self, **kw): pass
        def results(self, q): return None
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", NoneSerper)
    assert "NO RESULTS" in st.web_search.run(query="q")

    class EmptySerper:
        def __init__(self, **kw): pass
        def results(self, q): return {"news": []}
    monkeypatch.setattr(st, "GoogleSerperAPIWrapper", EmptySerper)
    assert "NO RESULTS" in st.web_search.run(query="q")


def test_legacy_ddgs_without_context_manager(monkeypatch):
    """Very old DDG clients without __enter__ still work via fallback path."""
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    import ddgs

    class OldDDGS:
        def news(self, *a, **k):
            return [{"title": "Old client hit", "url": "https://o/1", "body": "b"}]
    monkeypatch.setattr(ddgs, "DDGS", OldDDGS)
    out = st.web_search.run(query="q")
    assert "Old client hit" in out and "https://o/1" in out
