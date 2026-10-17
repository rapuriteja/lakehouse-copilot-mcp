"""Run the Streamlit app headless and ask it a question."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).parents[1] / "src/lakehouse_copilot/app/streamlit_app.py"


def test_streamlit_chat_answers_a_question(serving_db: Path, monkeypatch) -> None:
    from lakehouse_copilot.config import get_settings

    monkeypatch.setenv("DUCKDB_PATH", str(serving_db))
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    get_settings.cache_clear()
    try:
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        assert not app.exception
        app.chat_input[0].set_value("What was the net revenue in 2025?").run()
        assert not app.exception
        texts = [m.value for m in app.markdown]
        assert any(t.startswith("Net revenue (year 2025): ₹") for t in texts)
        assert any("gold.fact_sales" in c.value for c in app.caption)
    finally:
        get_settings.cache_clear()
