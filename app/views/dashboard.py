"""Dashboard page: KPI cards, filters and charts for the 8 analytics queries."""
import plotly.express as px
import streamlit as st

from src.ai.report import write_report
from src.analytics import QUERIES
from views.common import BLUE, SEQUENTIAL, TEAM_COLORS, query, require_data, style, table


def _filters():
    bounds = table("SELECT MIN(DATE(opened_at)) AS lo, MAX(DATE(opened_at)) AS hi FROM tickets").iloc[0]
    teams = ["All"] + table("SELECT team_name FROM teams ORDER BY team_name")["team_name"].tolist()
    cats = ["All"] + table("SELECT DISTINCT category FROM categories ORDER BY 1")["category"].tolist()
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    dates = c1.date_input("Opened between", (bounds["lo"], bounds["hi"]),
                          min_value=bounds["lo"], max_value=bounds["hi"])
    start, end = (dates if len(dates) == 2 else (dates[0], dates[0]))
    return (("start_date", str(start)), ("end_date", str(end)),
            ("team", c2.selectbox("Team", teams)),
            ("priority", c3.selectbox("Priority", ["All", "P1", "P2", "P3", "P4"])),
            ("category", c4.selectbox("Category", cats)))


def _kpis(f):
    k = query("kpi_summary", f).iloc[0]
    a, b, c, d = st.columns(4)
    a.metric("Total tickets", f"{int(k.total_tickets):,}", border=True)
    b.metric("Open tickets", f"{int(k.open_tickets or 0):,}", border=True)
    c.metric("SLA breach %", f"{k.sla_breach_pct or 0:.1f}%", border=True)
    d.metric("MTTR (hours)", f"{k.mttr_hours or 0:.1f}", border=True)


def render():
    st.header("Dashboard")
    if not require_data():
        return
    f = _filters()
    if query("kpi_summary", f).iloc[0].total_tickets == 0:
        st.warning("No tickets match these filters.")
        return
    _kpis(f)
    tabs = st.tabs(["Top issues", "SLA by team", "MTTR", "Peaks", "Services", "Trend", "At risk", "Workload"])

    with tabs[0]:
        df = query("q1_top_categories", f).head(15)
        df["label"] = df["subcategory"] + " (" + df["category"] + ")"
        fig = px.bar(df, x="ticket_count", y="label", orientation="h", color_discrete_sequence=[BLUE],
                     hover_data=["share_pct", "avg_resolution_hours"], title=QUERIES["q1_top_categories"])
        st.plotly_chart(style(fig.update_yaxes(autorange="reversed", title=""), 480))
        st.dataframe(df.drop(columns="label"), hide_index=True)

    with tabs[1]:
        df = query("q2_sla_breach_by_team", f)
        team = df.groupby("team_name")[["tickets", "breached"]].sum()
        team["breach_rate_pct"] = (100 * team["breached"] / team["tickets"]).round(2)
        team = team.sort_values("breach_rate_pct", ascending=False).reset_index()
        fig = px.bar(team, x="team_name", y="breach_rate_pct", color="team_name",
                     color_discrete_map=TEAM_COLORS, title="SLA breach rate by team (%)")
        st.plotly_chart(style(fig.update_layout(showlegend=False).update_xaxes(title="")))
        heat = df.pivot(index="team_name", columns="priority", values="breach_rate_pct")
        st.plotly_chart(style(px.imshow(heat, text_auto=".1f", color_continuous_scale=SEQUENTIAL,
                                        aspect="auto", title="Breach rate % by team x priority")))

    with tabs[2]:
        df = query("q3_mttr_by_category_month", f)
        heat = df.pivot(index="category", columns="month", values="mttr_hours")
        st.plotly_chart(style(px.imshow(heat, text_auto=".0f", color_continuous_scale=SEQUENTIAL,
                                        aspect="auto", title="MTTR (hours) by category and month"), 420))

    with tabs[3]:
        df = query("q4_peaks_hour_weekday", f)
        heat = df.pivot_table(index=["weekday_num", "weekday"], columns="hour", values="tickets",
                              fill_value=0).droplevel(0)
        st.plotly_chart(style(px.imshow(heat, color_continuous_scale=SEQUENTIAL, aspect="auto",
                                        labels=dict(x="Hour of day", y="", color="Tickets"),
                                        title="Tickets opened by weekday and hour"), 380))

    with tabs[4]:
        df = query("q5_repeat_offender_services", f)
        st.caption("Services whose monthly tickets reached at least 2.5x the previous month "
                   "(and at least 20 tickets) are flagged.")
        st.dataframe(df[df.flagged == 1] if (df.flagged == 1).any() else df.head(10), hide_index=True)

    with tabs[5]:
        df = query("q6_mom_trend", f)
        left, right = st.columns(2)
        left.plotly_chart(style(px.bar(df, x="month", y="tickets", color_discrete_sequence=[BLUE],
                                       title="Tickets per month")))
        right.plotly_chart(style(px.line(df, x="month", y="breach_rate_pct", markers=True,
                                         color_discrete_sequence=[BLUE],
                                         title="SLA breach rate per month (%)")))
        st.dataframe(df, hide_index=True)

    with tabs[6]:
        st.dataframe(query("q7_oldest_open_at_risk", f), hide_index=True)

    with tabs[7]:
        df = query("q8_workload", f)
        fig = px.scatter(df, x="tickets_handled", y="avg_resolution_hours", color="team_name",
                         color_discrete_map=TEAM_COLORS, hover_data=["agent_name", "breach_rate_pct"],
                         title="Agent workload vs average resolution time")
        st.plotly_chart(style(fig.update_traces(marker_size=10), 420))
        st.dataframe(df, hide_index=True)

    with st.expander("Weekly executive report (AI writes prose from SQL numbers only)"):
        if st.button("Generate weekly report"):
            with st.spinner("Writing report..."):
                markdown, path, provider = write_report()
            st.caption(f"Saved to {path.name} - prose by {provider}")
            st.markdown(markdown)
