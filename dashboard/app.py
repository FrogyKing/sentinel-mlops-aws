"""Read-only lifecycle dashboard backed by actual local execution artifacts."""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Sentinel | MLOps", page_icon="◈", layout="wide")
st.markdown(
    "<style>.stApp {background:#0b1220} [data-testid=stMetric] "
    "{background:#152238;padding:20px;border-radius:12px}</style>",
    unsafe_allow_html=True,
)
st.caption("SENTINEL / MODEL OPERATIONS")
st.title("Fraud intelligence, with evidence.")
st.write("Training, quality signals and controlled model releases in one place.")
root = Path(st.sidebar.text_input("Artifact directory", "artifacts/demo"))


def read(name: str) -> dict:
    path = root / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


evaluation, registry, demonstration = (
    read("evaluation.json"),
    read("registry.json"),
    read("demo.json"),
)
if not evaluation:
    st.info("Run `uv run sentinel demo --output artifacts/demo` to generate real local evidence.")
    st.stop()
st.warning(
    "Synthetic local demonstration · no AWS operational events"
    if evaluation.get("synthetic")
    else "Public benchmark evaluation · offline evidence, no AWS operational events"
)
metrics = evaluation.get("metrics", {})
columns = st.columns(4)
for col, label, value in zip(
    columns,
    ["PR-AUC", "Recall", "FPR", "Cost / tx"],
    [
        metrics.get("pr_auc"),
        metrics.get("recall"),
        metrics.get("false_positive_rate"),
        metrics.get("business_cost"),
    ],
    strict=True,
):
    col.metric(label, f"{value:.3f}" if value is not None else "Unavailable")
overview, monitoring, releases = st.tabs(["Model intelligence", "Monitoring", "Release audit"])
with overview:
    left, right = st.columns([2, 1])
    with left:
        st.subheader("Validation model comparison")
        validation = pd.DataFrame(evaluation.get("validation", {})).T
        if not validation.empty:
            chart_data = (
                validation[["pr_auc", "recall"]]
                .reset_index()
                .melt(id_vars="index", var_name="metric", value_name="score")
            )
            chart = px.bar(
                chart_data,
                x="index",
                y="score",
                color="metric",
                barmode="group",
                template="plotly_dark",
                color_discrete_sequence=["#38bdf8", "#34d399"],
            )
            chart.update_layout(
                yaxis_range=[0, 1],
                xaxis_title=None,
                legend_title=None,
                paper_bgcolor="#0b1220",
                plot_bgcolor="#0b1220",
            )
            st.plotly_chart(chart, width="stretch")
            st.dataframe(validation, width="stretch")
    with right:
        st.subheader("Deployment state")
        st.write("Champion", registry.get("champion") or "Awaiting approval")
        st.write("Latest training", evaluation.get("version"))
        st.write("Selected estimator", evaluation.get("selected_model"))
        st.write("Threshold", evaluation.get("threshold"))
        st.caption("Registry approval is local. AWS alias state requires cloud verification.")
    st.subheader("Reproducibility record")
    st.json(
        {
            k: evaluation[k]
            for k in ["dataset_sha256", "seed", "split_rows", "selection"]
            if k in evaluation
        }
    )
with monitoring:
    report = read("monitor.json")
    if not report:
        report = demonstration.get("unlabeled_drift", {}).get("monitoring", {})
    if report:
        st.subheader("Latest distribution comparison")
        st.json(report)
    else:
        st.info("No monitoring window has been recorded.")
    predictions = root / "predictions.jsonl"
    if predictions.exists():
        records = pd.read_json(predictions, lines=True)
        if "probability" in records:
            st.line_chart(records[["probability"]])
    else:
        st.caption("Prediction trends appear when metadata-only predictions.jsonl is supplied.")
    st.caption("Statistical drift calls for investigation; it does not prove concept drift.")
with releases:
    st.subheader("Version registry")
    st.json(registry.get("models", {}))
    st.subheader("Promotion and rollback history")
    st.dataframe(pd.DataFrame(registry.get("history", [])), width="stretch")
    if demonstration:
        st.subheader("Executed local scenarios")
        st.json(demonstration)
st.sidebar.caption("Health: artifacts loaded. Serving/cloud health is not inferred from files.")
