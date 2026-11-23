"""A configured LLM that fails must say why on screen. Otherwise a wrong key, or an account with
no credits, looks exactly like "no key given" and the LLM half seems to work when it does not."""
from pathlib import Path

import pytest
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

import llm
from providers import fueleconomy

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


EDA_TAB = ":material/analytics: EDA"


def run_app(monkeypatch, *, key_works, open_tab=None):
    monkeypatch.setattr(fueleconomy, "_get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    # Disable Groq so _llm_provider() falls through to Anthropic (the subject under test).
    monkeypatch.setattr(llm.GroqProvider, "available", lambda self: False)
    monkeypatch.setattr(llm.AnthropicProvider, "available", lambda self: True)
    if key_works:
        monkeypatch.setattr(llm.AnthropicProvider, "generate",
                            lambda self, facts: llm.template_explanation(facts))
        monkeypatch.setattr(llm.AnthropicProvider, "complete",
                            lambda self, prompt, max_tokens=300: "Nothing numeric here.")
    else:
        def boom(*a, **k):
            raise RuntimeError("Error code: 401 - authentication_error")
        monkeypatch.setattr(llm.AnthropicProvider, "generate", boom)
        monkeypatch.setattr(llm.AnthropicProvider, "complete", boom)
    app = AppTest.from_file(str(APP), default_timeout=30)
    if open_tab:  # tab bodies only run when their tab is open (st.tabs on_change="rerun")
        app.session_state["main_tabs"] = open_tab
    app.run()
    return app


def click(app, label):
    next(b for b in app.button if b.label == label).click().run()
    return app


def test_a_rejected_key_is_reported_on_the_explain_button(monkeypatch):
    app = click(run_app(monkeypatch, key_works=False, open_tab=None), "Explain this recommendation")
    assert any("401" in e.value for e in app.error)
    assert not app.exception


def test_a_rejected_key_is_reported_on_the_eda_summary(monkeypatch):
    app = click(run_app(monkeypatch, key_works=False, open_tab=EDA_TAB), "Summarize the EDA")
    assert any("401" in e.value for e in app.error)
    assert not app.exception


def test_a_working_key_shows_no_error(monkeypatch):
    app = click(run_app(monkeypatch, key_works=True, open_tab=None), "Explain this recommendation")
    assert not app.error and not app.exception


def test_each_answer_reports_how_many_numbers_were_traced(monkeypatch):
    """The report promises the share of traceable numbers on screen: it is what makes a
    hallucination measurable, so the assistant must keep showing it."""
    app = click(run_app(monkeypatch, key_works=True), "Explain this recommendation")
    assert any("All numbers traced" in m.value for m in app.markdown)
