"""IncidentIQ - Streamlit dashboard (Phase 7).

Run from the project folder:  streamlit run app/streamlit_app.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project root -> import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent))      # app/ -> import views.*

import streamlit as st

from src.ai.llm_client import llm_status
from views import ask, assistant, cloud, common, dashboard, pipeline, quality

st.set_page_config(page_title="IncidentIQ", page_icon=":material/insights:", layout="wide")

PAGES = {"Pipeline": pipeline, "Dashboard": dashboard, "Ask IncidentIQ (AI)": ask,
         "Ticket Assistant": assistant, "Data Quality & Audit": quality, "Cloud": cloud}


@st.cache_data(ttl=30, show_spinner=False)
def _llm_status():
    return llm_status()


with st.sidebar:
    st.title("IncidentIQ")
    st.caption("AI-powered IT incident intelligence - all data is synthetic.")
    page = st.radio("Go to", list(PAGES), label_visibility="collapsed")
    st.divider()
    st.caption(f"Tickets in database: **{common.ticket_count():,}**")
    status = _llm_status()
    cloud_llm = ("ollama (local only)" if status["provider"] == "ollama" else
                 f"{status['provider']} ({'key set' if status['provider_key_set'] else 'no key - using Ollama'})")
    ollama = f"Ollama {status['ollama_model']}: {'running' if status['ollama_up'] else 'not running'}"
    st.caption(f"LLM: {cloud_llm}  \n{ollama}")

PAGES[page].render()
