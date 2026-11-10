"""GenePulse dashboard - Streamlit UI over the change-detection engine.

A thin layer: every number on this page comes from dashboard.data, which is
unit-tested on its own. This file only lays out and renders what that module
returns.

Run with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import data as dashboard_data
from genepulse.config import QUERY_ENGINE

# Fixed roles from the project's validated palette (see the dataviz skill).
# Status colors carry clinical severity; never used for anything else.
_STATUS_COLORS = {
    "Pathogenic": "#d03b3b",  # critical
    "Uncertain significance": "#fab219",  # warning
    "Conflicting": "#ec835a",  # serious
    "Benign": "#0ca30c",  # good
    "Other": "#898781",  # muted, neutral
    "Not classified": "#c3c2b7",  # baseline, neutral
}
_SEQUENTIAL_BLUE = "#2a78d6"  # categorical slot 1, used single-hue for magnitude


def _category_breakdown_chart(breakdown) -> go.Figure:
    """A thin 100%-stacked horizontal bar - the 'part-to-whole, standing alone' form."""
    fig = go.Figure()
    for _, row in breakdown.iterrows():
        category = row["significance_category"]
        fig.add_bar(
            name=category,
            x=[row["share"]],
            y=["Latest snapshot"],
            orientation="h",
            marker_color=_STATUS_COLORS[category],
            text=f"{category}: {row['count']:,} ({row['share']:.0%})" if row["share"] >= 0.03 else None,
            textposition="inside",
            insidetextanchor="middle",
            textfont_color="#ffffff" if category != "Uncertain significance" else "#0b0b0b",
            hovertemplate=f"{category}: %{{customdata[0]:,}} (%{{customdata[1]:.1%}})<extra></extra>",
            customdata=[[row["count"], row["share"]]],
        )
    fig.update_layout(
        barmode="stack",
        showlegend=True,
        legend={"orientation": "h", "yanchor": "bottom", "y": -0.4},
        xaxis={"visible": False, "range": [0, 1]},
        yaxis={"visible": False},
        height=180,
        margin={"l": 0, "r": 0, "t": 10, "b": 10},
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
    )
    return fig


def _reclassification_chart(matrix) -> go.Figure:
    """Ranked horizontal bars, one hue - the 'compare magnitude across categories' form."""
    ranked = matrix.iloc[::-1]  # Plotly draws horizontal bars bottom-up; reverse for largest-on-top
    fig = go.Figure(
        go.Bar(
            x=ranked["count"],
            y=ranked["transition"],
            orientation="h",
            marker_color=_SEQUENTIAL_BLUE,
            text=ranked["count"].map("{:,}".format),
            textposition="outside",
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        )
    )
    fig.update_layout(
        showlegend=False,
        xaxis={"title": "Variants", "gridcolor": "#e1e0d9"},
        yaxis={"title": None},
        height=max(220, 32 * len(ranked)),
        margin={"l": 0, "r": 40, "t": 10, "b": 10},
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        font_color="#0b0b0b",
    )
    return fig


def main() -> None:
    st.set_page_config(page_title="GenePulse", layout="wide")
    st.title("GenePulse")
    st.caption("Tracking how ClinVar variant classifications change release to release.")

    engine = st.sidebar.selectbox("Query engine", options=["duckdb", "athena"], index=["duckdb", "athena"].index(QUERY_ENGINE))

    try:
        snapshot = dashboard_data.latest_snapshot(engine=engine)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        st.error(f"Couldn't load any processed releases: {exc}\n\nRun `python -m genepulse.extract` and `python -m genepulse.transform` first.")
        st.stop()

    labels = dashboard_data.release_labels(engine=engine)

    col1, col2, col3 = st.columns(3)
    col1.metric("Variants tracked", f"{len(snapshot):,}")
    col2.metric("Genes", f"{snapshot['gene_symbol'].nunique():,}")
    col3.metric("Releases loaded", len(labels))

    st.subheader("Current classification breakdown")
    breakdown = dashboard_data.category_breakdown(snapshot)
    st.plotly_chart(_category_breakdown_chart(breakdown), width="stretch")

    if len(labels) < 2:
        st.info("Load at least two releases to see change-detection between them.")
        return

    changes = dashboard_data.release_changes(engine=engine)
    summary = dashboard_data.change_summary(changes)

    st.subheader(f"Changes: {labels[1]} → {labels[0]}")
    col1, col2, col3 = st.columns(3)
    col1.metric("Added", f"{summary['added']:,}")
    col2.metric("Removed", f"{summary['removed']:,}")
    col3.metric("Reclassified", f"{summary['changed']:,}")

    matrix = dashboard_data.reclassification_matrix(changes)
    if not matrix.empty:
        st.subheader("Reclassification breakdown")
        st.plotly_chart(_reclassification_chart(matrix), width="stretch")

    st.subheader("Individual changes")
    change_types = st.multiselect("Change type", options=sorted(changes["change_type"].unique()), default=list(changes["change_type"].unique()))
    filtered = changes[changes["change_type"].isin(change_types)]
    st.dataframe(filtered, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
