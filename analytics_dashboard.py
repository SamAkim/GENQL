def analytics_dashboard_page():
    import sqlite3
    import pandas as pd
    import streamlit as st
    import plotly.express as px
    from pathlib import Path

    # ----------------------------
    # DB Connection
    # ----------------------------
    BASE_DIR = Path(__file__).resolve().parent
    DB_PATH = BASE_DIR / "app_data.db"

    @st.cache_data(ttl=60)
    def load_data():
        if not DB_PATH.exists():
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        conn = sqlite3.connect(DB_PATH)
        try:
            queries = pd.read_sql("SELECT * FROM queries", conn)
        except Exception:
            queries = pd.DataFrame()
        try:
            feedback = pd.read_sql("SELECT * FROM feedback", conn)
        except Exception:
            feedback = pd.DataFrame()
        try:
            projects = pd.read_sql("SELECT * FROM projects", conn)
        except Exception:
            projects = pd.DataFrame()
        try:
            documents = pd.read_sql("SELECT * FROM documents", conn)
        except Exception:
            documents = pd.DataFrame()
        try:
            users = pd.read_sql("SELECT * FROM users", conn)
        except Exception:
            users = pd.DataFrame()
        conn.close()
        return queries, feedback, projects, documents, users

    queries, feedback, projects, documents, users = load_data()

    # Merge usernames into queries
    if not queries.empty and "user_id" in queries.columns and not users.empty and "id" in users.columns:
        queries = queries.merge(
            users[["id", "username"]], left_on="user_id", right_on="id", how="left"
        )

    # ----------------------------
    # Page Layout
    # ----------------------------
    st.set_page_config(page_title="📊 Analytics Dashboard", layout="wide")
    st.title("📊 SQL Assistant Analytics Dashboard")

    # ----------------------------
    # Overview KPIs
    # ----------------------------
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Projects", len(projects))
    col2.metric("Total Documents", len(documents))
    col3.metric("Total Queries", len(queries))
    unique_convs = queries["conversation_id"].nunique() if not queries.empty and "conversation_id" in queries.columns else 0
    col4.metric("Unique Conversations", unique_convs)
    col5.metric("Feedback Collected", len(feedback))

    st.markdown("---")

    # ----------------------------
    # Collapse/Expand Toggle
    # ----------------------------
    expand_all = st.checkbox("🔽 Expand All Sections", value=True)

    # =====================================================
    # 🧑‍🤝‍🧑 USER ANALYTICS
    # =====================================================
    with st.expander("🧑‍🤝‍🧑 User Analytics", expanded=expand_all):
        if not queries.empty and "username" in queries.columns:
            st.subheader("Queries per User")
            queries_per_user = (
                queries.groupby("username")["query"]
                .count()
                .reset_index(name="query_count")
            )
            if not queries_per_user.empty:
                fig_user_queries = px.bar(
                    queries_per_user,
                    x="username",
                    y="query_count",
                    title="Number of Queries per User",
                    labels={"username": "User", "query_count": "Queries"},
                    text_auto=True,
                )
                st.plotly_chart(fig_user_queries, use_container_width=True)

            st.subheader("Sessions per User")
            if "conversation_id" in queries.columns:
                sessions_per_user = (
                    queries.groupby("username")["conversation_id"]
                    .nunique()
                    .reset_index(name="session_count")
                )
                if not sessions_per_user.empty:
                    fig_sessions = px.bar(
                        sessions_per_user,
                        x="username",
                        y="session_count",
                        title="Number of Sessions per User",
                        labels={"username": "User", "session_count": "Sessions"},
                        text_auto=True,
                    )
                    st.plotly_chart(fig_sessions, use_container_width=True)

                st.subheader("Average Queries per Session")
                queries_per_session = queries.groupby("conversation_id")["query"].count()
                avg_queries_per_session = queries_per_session.mean() if not queries_per_session.empty else 0.0
                st.write(
                    f"On average, users ask **{avg_queries_per_session:.2f} queries** per session."
                )
        else:
            st.info("No query data available yet.")

    # =====================================================
    # 📂 QUERY ANALYTICS
    # =====================================================
    with st.expander("📂 Query Analytics", expanded=expand_all):
        if not queries.empty and "project_id" in queries.columns:
            st.subheader("📂 Queries per Project")
            queries_per_project = (
                queries.groupby("project_id")["query"]
                .count()
                .reset_index(name="query_count")
            )
            if not projects.empty and "id" in projects.columns and "name" in projects.columns:
                queries_per_project = queries_per_project.merge(
                    projects[["id", "name"]], left_on="project_id", right_on="id", how="left"
                ).rename(columns={"name": "project_name"})
            else:
                queries_per_project["project_name"] = queries_per_project["project_id"].astype(str)

            fig1 = px.bar(
                queries_per_project,
                x="project_name",
                y="query_count",
                title="Number of Queries per Project",
                labels={"project_name": "Project", "query_count": "Queries"},
                color="query_count",
                text_auto=True,
            )
            st.plotly_chart(fig1, use_container_width=True)

            if "conversation_id" in queries.columns:
                st.subheader("💬 Conversation Depth")
                conv_depth = queries.groupby("conversation_id")["query"].count()
                avg_depth = conv_depth.mean() if not conv_depth.empty else 0.0
                st.write(f"On average, users ask **{avg_depth:.2f} queries** per conversation.")

                if not conv_depth.empty:
                    fig2 = px.histogram(
                        conv_depth, nbins=10, title="Distribution of Queries per Conversation"
                    )
                    st.plotly_chart(fig2, use_container_width=True)

            if "created_at" in queries.columns:
                st.subheader("⏳ Queries Over Time")
                try:
                    queries_time = queries.copy()
                    queries_time["created_at"] = pd.to_datetime(queries_time["created_at"])
                    trend = (
                        queries_time.groupby(queries_time["created_at"].dt.date)["query"]
                        .count()
                        .reset_index()
                    )
                    trend.columns = ["date", "total_queries"]

                    fig4 = px.line(
                        trend,
                        x="date",
                        y="total_queries",
                        markers=True,
                        title="Query Trends Over Time",
                    )
                    st.plotly_chart(fig4, use_container_width=True)
                except Exception as e:
                    st.warning(f"Could not render time trend: {e}")
        else:
            st.info("No query analytics data available yet.")

    # =====================================================
    # ⚡ PERFORMANCE & QUALITY METRICS
    # =====================================================
    with st.expander("⚡ Performance & Quality Metrics", expanded=expand_all):
        if not queries.empty:
            if "response_time" in queries.columns:
                avg_response_time = queries["response_time"].dropna().mean()
                if not pd.isna(avg_response_time):
                    st.metric("Avg Response Time (sec)", f"{avg_response_time:.2f}")

                    st.subheader("Latency Trend")
                    try:
                        latency_trend = queries.dropna(subset=["response_time"]).copy()
                        latency_trend["created_at"] = pd.to_datetime(latency_trend["created_at"])
                        latency_trend = (
                            latency_trend.groupby(latency_trend["created_at"].dt.date)[
                                "response_time"
                            ]
                            .mean()
                            .reset_index()
                        )

                        fig_latency = px.line(
                            latency_trend,
                            x="created_at",
                            y="response_time",
                            markers=True,
                            title="Average Response Time Over Time",
                        )
                        st.plotly_chart(fig_latency, use_container_width=True)
                    except Exception:
                        pass

            if "attempts" in queries.columns:
                retry_rate = (queries["attempts"] > 1).mean() * 100
                st.metric("Retry Rate", f"{retry_rate:.2f}%")

            if "is_fallback" in queries.columns:
                fallback_rate = queries["is_fallback"].mean() * 100
                st.metric("Fallback Rate", f"{fallback_rate:.2f}%")
        else:
            st.info("No performance metrics available yet.")

    # =====================================================
    # 👍👎 FEEDBACK ANALYTICS
    # =====================================================
    with st.expander("👍👎 Feedback Analytics", expanded=expand_all):
        if not feedback.empty and "feedback" in feedback.columns:
            fb_counts = feedback["feedback"].value_counts().reset_index()
            fb_counts.columns = ["feedback_type", "count"]

            fig_fb = px.pie(
                fb_counts,
                names="feedback_type",
                values="count",
                title="Positive vs Negative Feedback",
            )
            st.plotly_chart(fig_fb, use_container_width=True)
        else:
            st.info("No feedback collected yet.")

    # =====================================================
    # 🔍 CONVERSATION EXPLORER
    # =====================================================
    with st.expander("🔍 Conversation Explorer", expanded=expand_all):
        if not queries.empty and "username" in queries.columns:
            user_list = [u for u in queries["username"].unique() if u is not None]
            if user_list:
                selected_user = st.selectbox("Select a User", user_list)
                user_convs = queries[queries["username"] == selected_user][
                    "conversation_id"
                ].unique()

                if len(user_convs) > 0:
                    selected_conv = st.selectbox("Select a Conversation", user_convs)

                    conv_history = queries[
                        (queries["username"] == selected_user)
                        & (queries["conversation_id"] == selected_conv)
                    ].sort_values("created_at")

                    st.subheader(f"Conversation {selected_conv} (User: {selected_user})")

                    for _, row in conv_history.iterrows():
                        with st.chat_message("user"):
                            st.markdown(row.get("query", ""))
                        with st.chat_message("assistant"):
                            st.markdown(row.get("answer", ""))
                else:
                    st.info("No conversations found for this user.")
            else:
                st.info("No user conversation records found.")
        else:
            st.info("No conversations available to explore.")
