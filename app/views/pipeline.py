"""Pipeline page: generate -> ingest (file / API) -> clean -> load, with the batch audit."""
import subprocess
import sys
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from src import config
from src.generate_data import generate_all
from src.ingest_api import ApiUnavailableError, api_is_up, ingest_api
from src.ingest_files import ingest_file, list_raw_files, save_uploaded_csv
from src.load_db import load_batch, read_table
from src.pipeline import run_api, run_file
from src.transform import clean, save_outputs
from views.common import clear_caches, to_csv_bytes

S = st.session_state


def _start_api():
    port = str(urlparse(config.API_URL).port or 8000)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "src.api_server:app", "--port", port],
                     cwd=config.ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _show_report(report):
    a, b, c, d = st.columns(4)
    a.metric("Raw rows", f"{report['raw_rows']:,}")
    b.metric("Cleaned", f"{report['cleaned_rows']:,}")
    c.metric("Rejected", f"{report['rejected_rows']:,}")
    d.metric("raw = cleaned + rejected", "Yes" if report["reconciled"] else "NO")
    left, right = st.columns(2)
    left.caption("Rejected by reason")
    left.dataframe(pd.DataFrame(report["rejected_by_reason"].items(), columns=["reason", "rows"]),
                   hide_index=True)
    right.caption("Fixed by rule")
    right.dataframe(pd.DataFrame(report["fixed_by_rule"].items(), columns=["rule", "rows"]),
                    hide_index=True)


def _steps():
    st.subheader("Step by step")
    files = sorted((p.name for p in list_raw_files()), key=lambda n: ("dirty" in n, n))
    c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")
    if c1.button("1. Generate data", width="stretch"):
        with st.spinner("Generating synthetic tickets..."):
            _, written = generate_all()
        st.success("Wrote " + ", ".join(p.name for p in written))
        st.rerun()
    chosen = c2.selectbox("Raw file to ingest", files) if files else None
    if c3.button("2a. Ingest file", width="stretch", disabled=not chosen):
        df, batch = ingest_file(config.RAW_DIR / chosen)
        S.update(ingested=(df, batch, chosen), cleaned=None)
    if c4.button("2b. Ingest from API", width="stretch"):
        try:
            df, batch = ingest_api()
            S.update(ingested=(df, batch, config.API_URL), cleaned=None)
        except ApiUnavailableError as exc:
            st.error(str(exc))
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Start mock API", width="stretch", disabled=api_is_up(),
                 help=f"Runs uvicorn src.api_server:app on {config.API_URL}"):
        _start_api()
        st.info("Starting the API... wait 3 seconds, then click Ingest from API.")
    if c2.button("3. Run cleaning", width="stretch", disabled=not S.get("ingested")):
        df, batch, _ = S["ingested"]
        cleaned, rejected, report = clean(df, batch)
        save_outputs(cleaned, rejected, report)
        S.update(cleaned=(cleaned, rejected, report))
    if c3.button("4. Load to DB", width="stretch", disabled=not S.get("cleaned")):
        cleaned, rejected, report = S["cleaned"]
        try:
            load_batch(cleaned, rejected, report["batch_id"])
            S["flash"] = f"Loaded batch {report['batch_id']}"
            clear_caches()
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))
    if S.get("ingested"):
        df, batch, source = S["ingested"]
        st.caption(f"Current batch: **{batch}** - {len(df):,} rows from {source}")


def _upload_section():
    """Upload your own ticket CSV; it is stored in data/raw/ and can be loaded in one click."""
    st.subheader("Upload your own CSV")
    st.caption("Needs at least these columns: ticket_id, opened_at, priority, category. Optional ones "
               "(description, resolution_notes, ...) make the similar-incident search much better. "
               "See sample_data/ for a ready-made file.")
    up = st.file_uploader("Ticket CSV", type=["csv"], key="csv_upload", label_visibility="collapsed")
    if up is None:  # file removed from the widget: forget it so the same file can be re-uploaded
        S.pop("upload_name", None)
        S.pop("upload_info", None)
        return
    if S.get("upload_name") != (up.name, up.size):
        try:
            info = save_uploaded_csv(up.name, up.getvalue())
        except ValueError as exc:
            st.error(str(exc))
            return
        S.update(upload_info=info, upload_name=(up.name, up.size))
    info = S.get("upload_info")
    if not info or S.get("upload_name") is None:
        return
    st.success(f"Saved **{info['path'].name}** to data/raw/ - {info['rows']:,} rows.")
    if info["missing_required"]:
        st.error("Missing required columns: " + ", ".join(info["missing_required"])
                 + ". Every row would be rejected, so fix the file first.")
        return
    if info["missing_optional"]:
        st.caption("Optional columns not in your file (left blank): " + ", ".join(info["missing_optional"]))
    if st.button(f"Ingest, clean and load {info['path'].name}", type="primary"):
        with st.spinner("Running ingest -> clean -> load..."):
            report = run_file(info["path"])
        S.update(last_reports=[report], cleaned=None, upload_info=None,
                 flash=f"Loaded {info['path'].name} as batch {report['batch_id']}")
        clear_caches()
        st.rerun()


def render():
    st.header("Pipeline")
    st.caption("Ingest -> clean -> load. Every run creates a new batch, and every batch must "
               "reconcile: raw rows = loaded rows + rejected rows.")
    if st.button("Run full pipeline (main file + dirty test file + API if running)", type="primary"):
        with st.spinner("Running pipeline..."):
            if not list(config.RAW_DIR.glob("incidents_[0-9]*.csv")):
                generate_all()
            reports = [run_file(), run_file(config.RAW_DIR / "incidents_dirty_test.csv")]
            if api_is_up():
                reports.append(run_api())
        S.update(last_reports=reports, cleaned=None,
                 flash=f"Loaded {len(reports)} batches: " + ", ".join(r["batch_id"] for r in reports))
        clear_caches()
        st.rerun()  # refresh the sidebar ticket count
    if S.get("flash"):
        st.success(S.pop("flash"))
    _upload_section()
    _steps()

    if S.get("cleaned"):
        cleaned, rejected, report = S["cleaned"]
        st.subheader(f"Cleaning report - {report['batch_id']}")
        _show_report(report)
        if not rejected.empty:
            st.download_button("Download rejected records (this batch)", to_csv_bytes(rejected),
                               f"{report['batch_id']}_rejected.csv", "text/csv")
    elif S.get("last_reports"):
        st.subheader("Cleaning reports - last full run")
        for tab, report in zip(st.tabs([f"{r['batch_id']} ({r['raw_rows']:,} rows)"
                                        for r in S["last_reports"]]), S["last_reports"]):
            with tab:
                _show_report(report)

    st.subheader("Batches")
    batches = read_table("SELECT * FROM batches ORDER BY batch_id DESC")
    if batches.empty:
        st.info("No batches yet.")
        return
    loaded = batches["status"] == "loaded"
    batches["reconciled"] = (batches["input_rows"] == batches["loaded_rows"].fillna(0)
                             + batches["rejected_rows"].fillna(0)).where(loaded)
    st.dataframe(batches, hide_index=True)
    all_rejected = read_table("SELECT batch_id, raw_ticket_id, reason_code, raw_payload "
                              "FROM rejected_records ORDER BY reject_id")
    if not all_rejected.empty:
        st.download_button("Download all rejected records (CSV)", to_csv_bytes(all_rejected),
                           "rejected_records.csv", "text/csv")
