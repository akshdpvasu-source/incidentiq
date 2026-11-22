"""Shared helpers for the Streamlit pages: data checks, cached queries, colors."""
import pandas as pd
import streamlit as st

from src import analytics
from src.catalog import TEAMS
from src.load_db import read_table

# Validated colorblind-safe categorical order (dataviz reference palette), fixed per entity.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEAM_COLORS = dict(zip(TEAMS, SERIES))
BLUE = SERIES[0]
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
STATUS_BADGE = {"Valid": "green", "Blocked": "red", "Failed": "orange"}


def ticket_count():
    """Number of tickets in the database (0 if it does not exist yet)."""
    try:
        df = read_table("SELECT COUNT(*) AS n FROM tickets")
        return 0 if df.empty else int(df["n"].iloc[0])
    except Exception:
        return 0


def require_data():
    """Show friendly guidance and return False when the database is empty."""
    if ticket_count() == 0:
        st.info("No tickets in the database yet. Open the **Pipeline** page and click "
                "**Run full pipeline** (or run `python src/pipeline.py`).")
        return False
    return True


@st.cache_data(show_spinner=False)
def query(name, filters=None):
    return analytics.run_query(name, dict(filters or {}))


@st.cache_data(show_spinner=False)
def table(sql, params=()):
    return read_table(sql, params)


def clear_caches():
    st.cache_data.clear()
    st.cache_resource.clear()


def badge(status):
    st.markdown(f":{STATUS_BADGE.get(status, 'gray')}-background[**{status}**]")


def to_csv_bytes(df: pd.DataFrame):
    return df.to_csv(index=False).encode("utf-8")


def style(fig, height=360):
    """Recessive grid, readable margins, no chart junk."""
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), legend_title_text="",
                      hoverlabel=dict(font_size=12))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    return fig

