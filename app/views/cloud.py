"""Cloud page: S3 status, uploads of synthetic outputs, and object links."""
import pandas as pd
import streamlit as st

from src import config
from src.cloud_s3 import list_objects, s3_status, upload_file

FOLDERS = {"raw/": config.RAW_DIR, "processed/": config.CLEANED_DIR,
           "rejected/": config.REJECTED_DIR, "reports/": config.REPORTS_DIR}


def render():
    st.header("Cloud (Amazon S3) - optional")
    status = s3_status()
    if not status["enabled"]:
        st.info(f"{status['reason']}. Everything else works locally. "
                "See docs/aws_setup.md to enable it (set an AWS Budget alert first).")
        return
    st.success(f"S3 enabled - bucket **{status['bucket']}** ({status['region']})")

    prefix = st.selectbox("Upload to prefix", list(FOLDERS))
    files = sorted(p for p in FOLDERS[prefix].glob("*") if p.is_file() and p.name != ".gitkeep")
    chosen = st.multiselect("Files", [p.name for p in files])
    if st.button("Upload selected", type="primary", disabled=not chosen):
        for name in chosen:
            result = upload_file(FOLDERS[prefix] / name, prefix)
            (st.success if result["ok"] else st.error)(result["message"])

    st.subheader("Objects in the bucket")
    objects = list_objects()
    if objects:
        st.dataframe(pd.DataFrame(objects), hide_index=True,
                     column_config={"console_url": st.column_config.LinkColumn("Open in console")})
    else:
        st.caption("Bucket is empty.")
    st.caption("The Lambda in lambda/handler.py logs name, size and row count of each upload to CloudWatch.")
