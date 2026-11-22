"""Data Quality & Audit page: rejected reasons, batch reconciliation, AI query log."""
import plotly.express as px
import streamlit as st

from src.load_db import read_table
from views.common import BLUE, style, to_csv_bytes


def render():
    st.header("Data Quality & Audit")
    reasons = read_table("SELECT reason_code, COUNT(*) AS rows FROM rejected_records "
                         "GROUP BY reason_code ORDER BY rows DESC")
    if reasons.empty:
        st.info("No rejected records yet. Load the dirty test file on the Pipeline page.")
    else:
        fig = px.bar(reasons, x="rows", y="reason_code", orientation="h", text="rows",
                     color_discrete_sequence=[BLUE], title="Rejected records by reason")
        st.plotly_chart(style(fig.update_yaxes(autorange="reversed", title="")))

    st.subheader("Reconciliation per batch")
    batches = read_table(
        "SELECT b.batch_id, b.source_name, b.status, b.input_rows, b.loaded_rows, b.rejected_rows, "
        "(SELECT COUNT(*) FROM tickets t WHERE t.batch_id = b.batch_id) AS tickets_now_in_db, "
        "(SELECT COUNT(*) FROM rejected_records r WHERE r.batch_id = b.batch_id) AS rejects_in_db "
        "FROM batches b ORDER BY b.batch_id DESC")
    if not batches.empty:
        batches["raw = loaded + rejected"] = (batches["input_rows"] == batches["loaded_rows"]
                                              + batches["rejected_rows"]).where(batches.status == "loaded")
        st.dataframe(batches, hide_index=True)
        st.caption("tickets_now_in_db can be lower than loaded_rows when a later batch re-delivered "
                   "the same ticket_ids (upsert: the latest extract wins).")

    st.subheader("AI query log")
    log = read_table("SELECT log_id, asked_at, question, validation_status, row_count, generated_sql, "
                     "answer_summary FROM ai_query_log ORDER BY log_id DESC")
    if log.empty:
        st.info("No AI questions asked yet.")
        return
    a, b, c = st.columns(3)
    counts = log["validation_status"].value_counts()
    a.metric("Valid", int(counts.get("Valid", 0)), border=True)
    b.metric("Blocked", int(counts.get("Blocked", 0)), border=True)
    c.metric("Failed", int(counts.get("Failed", 0)), border=True)
    st.dataframe(log, hide_index=True)
    st.download_button("Download AI query log (CSV)", to_csv_bytes(log), "ai_query_log.csv", "text/csv")
