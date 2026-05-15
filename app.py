import streamlit as st
import pandas as pd
import plotly.express as px
import uuid
import random
from datetime import datetime

# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="Citizen Grievance",
    page_icon="",
    layout="wide"
)

# =========================================================
# SESSION STATE
# =========================================================

if "records" not in st.session_state:
    st.session_state.records = []

# =========================================================
# PREMIUM CSS
# =========================================================

st.markdown("""
<style>

@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap');

html, body, [class*="css"]{
    font-family:'Poppins',sans-serif;
}

.stApp{
    background:
    radial-gradient(circle at top left,#1d4ed8 0%,#0f172a 40%),
    linear-gradient(to bottom right,#020617,#111827);
    color:white;
}

/* METRICS */

div[data-testid="stMetric"]{
    background:rgba(15,23,42,0.75);
    border:1px solid rgba(255,255,255,0.06);
    padding:20px;
    border-radius:20px;
    backdrop-filter:blur(14px);
    box-shadow:0px 10px 30px rgba(0,0,0,0.25);
}

/* BUTTON */

.stButton > button{
    width:100%;
    height:55px;
    border:none;
    border-radius:18px;
    background:linear-gradient(135deg,#2563eb,#3b82f6);
    color:white;
    font-size:16px;
    font-weight:700;
}

.stButton > button:hover{
    transform:translateY(-3px);
    box-shadow:0px 15px 35px rgba(59,130,246,0.35);
}

/* TEXT AREA */

textarea{
    border-radius:18px !important;
    background:rgba(15,23,42,0.92) !important;
    color:white !important;
    border:1px solid rgba(255,255,255,0.08) !important;
}

/* HERO */

.hero-card{
    background:rgba(15,23,42,0.72);
    border:1px solid rgba(255,255,255,0.08);
    padding:28px;
    border-radius:26px;
    margin-bottom:30px;
    backdrop-filter:blur(18px);
    position:relative;
    overflow:hidden;
}

.hero-top-line{
    position:absolute;
    top:0;
    left:0;
    width:100%;
    height:5px;
    background:linear-gradient(
        to right,
        #2563eb,
        #38bdf8,
        #8b5cf6
    );
}

/* FEATURE BOX */

.feature-box{
    background:rgba(15,23,42,0.72);
    border:1px solid rgba(255,255,255,0.08);
    border-radius:22px;
    padding:25px;
    margin-bottom:20px;
}

/* HIDE */

#MainMenu{
    visibility:hidden;
}

footer{
    visibility:hidden;
}

header{
    visibility:hidden;
}

</style>
""", unsafe_allow_html=True)

# =========================================================
# TOP BAR
# =========================================================

st.markdown("""
<div class='hero-card'>

<div class='hero-top-line'></div>

<div style='
display:flex;
align-items:center;
justify-content:space-between;
flex-wrap:wrap;
gap:20px;
'>

<div>

<h2 style='
margin:0;
color:white;
font-size:36px;
font-weight:800;
'>

 AI Command Center

</h2>

<p style='
margin-top:12px;
color:#94a3b8;
font-size:16px;
line-height:1.7;
max-width:850px;
'>

AI-Powered Civic Intelligence,
Smart Governance Monitoring,
Real-Time Incident Analytics,
AI-Based Complaint Classification,
Emergency Priority Detection,
and Intelligent Department Routing Platform

</p>

</div>

<div style='
display:flex;
align-items:center;
gap:12px;
padding:12px 24px;
border-radius:40px;
background:rgba(34,197,94,0.12);
border:1px solid rgba(34,197,94,0.25);
color:#22c55e;
font-weight:700;
font-size:15px;
'>

<div style='
width:14px;
height:14px;
border-radius:100%;
background:#22c55e;
'></div>

Systems Operational

</div>

</div>

</div>
""", unsafe_allow_html=True)

# =========================================================
# HERO SECTION
# =========================================================

left_hero, right_hero = st.columns([3,1])

with left_hero:

    st.markdown("""
    <div class='hero-card'>

    <div class='hero-top-line'></div>

    <h1 style='
    color:white;
    font-size:52px;
    margin-bottom:10px;
    '>

    Smart Governance Intelligence Hub

    </h1>

    <p style='
    color:#cbd5e1;
    font-size:18px;
    line-height:1.8;
    '>

    AI-powered complaint classification,
    smart department routing,
    urgency detection,
    governance analytics,
    and intelligent monitoring platform.

    </p>

    </div>
    """, unsafe_allow_html=True)

with right_hero:

    st.metric("Monitoring", "24/7")
    st.metric("Departments", "18")
    st.metric("AI Models", "4")

# =========================================================
# FEATURES
# =========================================================

f1, f2, f3 = st.columns(3)

with f1:

    st.markdown("""
    <div class='feature-box'>
    <h3>AI Routing</h3>
    <p style='color:#cbd5e1;'>
    Smart NLP-based routing system.
    </p>
    </div>
    """, unsafe_allow_html=True)

with f2:

    st.markdown("""
    <div class='feature-box'>
    <h3>Priority Detection</h3>
    <p style='color:#cbd5e1;'>
    Detects emergency incidents instantly.
    </p>
    </div>
    """, unsafe_allow_html=True)

with f3:

    st.markdown("""
    <div class='feature-box'>
    <h3>Governance Analytics</h3>
    <p style='color:#cbd5e1;'>
    Real-time civic intelligence monitoring.
    </p>
    </div>
    """, unsafe_allow_html=True)

# =========================================================
# INPUT
# =========================================================

st.markdown("##  Incident Intelligence Engine")

complaints_input = st.text_area(
    "Enter  Complaints (One Complaint Per Line)",
    height=250
)

analyze = st.button(
    "Analyze Complaints"
)

# =========================================================
# PRIORITY KEYWORDS
# =========================================================

high_keywords = [
    "urgent",
    "critical",
    "emergency",
    "danger",
    "fire",
    "death",
    "blast",
    "accident",
    "hospital",
    "sparking",
    "explosion",
    "collapsed",
    "flood",
    "electrocution",
    "injury"
]

medium_keywords = [
    "problem",
    "issue",
    "damaged",
    "delay",
    "overflow",
    "blocked",
    "leakage",
    "traffic",
    "garbage",
    "pothole",
    "dirty",
    "pollution",
    "crack",
    "repair"
]

# =========================================================
# AI ENGINE
# =========================================================

if analyze:

    if complaints_input.strip() == "":

        st.warning(
            "Please enter complaints"
        )

    else:

        complaints = [
            c.strip()
            for c in complaints_input.split("\n")
            if c.strip()
        ]

        for complaint in complaints:

            text = complaint.lower()

            # =====================================================
            # DEPARTMENT ROUTING
            # =====================================================

            if any(word in text for word in [
                "water",
                "pipeline",
                "tap",
                "water supply",
                "no water"
            ]):

                dept = "Water"

            elif any(word in text for word in [
                "road",
                "roads",
                "pothole",
                "damaged road"
            ]):

                dept = "Roads"

            elif any(word in text for word in [
                "electricity",
                "power",
                "transformer",
                "no electricity"
            ]):

                dept = "Electricity"

            elif any(word in text for word in [
                "drainage",
                "garbage",
                "waste",
                "sewage"
            ]):

                dept = "Sanitation"

            elif any(word in text for word in [
                "traffic",
                "signal",
                "accident"
            ]):

                dept = "Traffic"

            else:

                dept = "General"

            # =====================================================
            # PRIORITY
            # =====================================================

            high_score = sum(
                word in text
                for word in high_keywords
            )

            medium_score = sum(
                word in text
                for word in medium_keywords
            )

            if high_score >= 1:

                priority = "High"

            elif medium_score >= 1:

                priority = "Medium"

            else:

                priority = "Low"

            # =====================================================
            # STATUS ENGINE
            # =====================================================

            if priority == "High":

                status = random.choice([
                    "Assigned",
                    "In Progress"
                ])

            elif priority == "Medium":

                status = random.choice([
                    "Pending",
                    "In Progress"
                ])

            else:

                status = random.choice([
                    "Pending",
                    "Resolved"
                ])

            confidence = round(
                random.uniform(90, 99),
                2
            )

            incident_id = (
                f"CIV-{str(uuid.uuid4())[:8]}"
            )

            # =====================================================
            # SAVE RECORD
            # =====================================================

            st.session_state.records.append({

                "Incident ID": incident_id,
                "Complaint": complaint,
                "Department": dept,
                "Confidence": confidence,
                "Priority": priority,
                "Status": status,
                "Assigned Officer": random.choice([
                    "Officer Rahul",
                    "Officer Priya",
                    "Officer Arjun",
                    "Officer Kavya",
                    "Officer Ramesh"
                ]),
                "Resolution ETA": random.choice([
                    "2 Hours",
                    "6 Hours",
                    "12 Hours",
                    "1 Day",
                    "2 Days"
                ]),
                "Location": random.choice([
                    "North Zone",
                    "South Zone",
                    "East Zone",
                    "West Zone",
                    "Central Zone"
                ]),
                "Timestamp": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            })

        st.success(
            f"{len(complaints)} complaints analyzed successfully"
        )

# =========================================================
# DASHBOARD
# =========================================================

if len(st.session_state.records) > 0:

    df = pd.DataFrame(
        st.session_state.records
    )

    st.markdown("## Governance Dashboard")

    k1, k2, k3, k4 = st.columns(4)

    with k1:
        st.metric(
            "Total Complaints",
            len(df)
        )

    with k2:
        st.metric(
            "Departments",
            df["Department"].nunique()
        )

    with k3:
        st.metric(
            "Critical Cases",
            len(df[df["Priority"] == "High"])
        )

    with k4:
        st.metric(
            "Avg Confidence",
            f"{round(df['Confidence'].mean(),2)}%"
        )

    st.markdown("##")

    s1, s2, s3, s4 = st.columns(4)

    with s1:
        st.metric(
            "Pending Cases",
            len(df[df["Status"] == "Pending"])
        )

    with s2:
        st.metric(
            "Resolved Cases",
            len(df[df["Status"] == "Resolved"])
        )

    with s3:
        st.metric(
            "In Progress",
            len(df[df["Status"] == "In Progress"])
        )

    with s4:
        st.metric(
            "Assigned Cases",
            len(df[df["Status"] == "Assigned"])
        )

    st.markdown("##")

    left, right = st.columns([2,1])

    with left:

        dept_counts = (
            df["Department"]
            .value_counts()
            .reset_index()
        )

        dept_counts.columns = [
            "Department",
            "Count"
        ]

        fig = px.bar(
            dept_counts,
            x="Department",
            y="Count",
            text="Count"
        )

        fig.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            height=500
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    with right:

        st.markdown("###  Recent Incidents")

        recent = df.tail(5)

        for _, row in recent.iterrows():

            st.markdown(f"""
            <div class='feature-box'>

            <h4 style='color:white;'>
            {row['Department']}
            </h4>

            <p style='color:#cbd5e1;'>
            {row['Complaint']}
            </p>

            <small style='color:#94a3b8;'>

            Priority: {row['Priority']}<br>
            Status: {row['Status']}<br>
            Officer: {row['Assigned Officer']}<br>
            ETA: {row['Resolution ETA']}<br>
            Zone: {row['Location']}<br>
            ID: {row['Incident ID']}

            </small>

            </div>
            """, unsafe_allow_html=True)

    st.markdown("##")

    p1, p2 = st.columns(2)

    with p1:

        fig2 = px.pie(
            df,
            names="Department",
            hole=0.5
        )

        fig2.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="white"
        )

        st.plotly_chart(
            fig2,
            use_container_width=True
        )

    with p2:

        priority_counts = (
            df["Priority"]
            .value_counts()
            .reset_index()
        )

        priority_counts.columns = [
            "Priority",
            "Count"
        ]

        fig3 = px.bar(
            priority_counts,
            x="Priority",
            y="Count",
            color="Priority",
            text="Count"
        )

        fig3.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="white"
        )

        st.plotly_chart(
            fig3,
            use_container_width=True
        )

    

    ## =====================================================
# COMPLAINT RECORDS
# =====================================================

if len(st.session_state.records) > 0:

    df = pd.DataFrame(
        st.session_state.records
    )

    st.markdown("## Complaint Records")

    # =====================================================
    # SEARCH BAR FOR RECORDS
    # =====================================================

    record_search = st.text_input(
        " Search Complaint Records",
        key="record_search"
    )

    filtered_records_df = df.copy()

    if record_search:

        search_value = record_search.lower()

        filtered_records_df = df[
            df.apply(
                lambda row:
                search_value in str(row["Complaint"]).lower()
                or search_value in str(row["Incident ID"]).lower()
                or search_value in str(row["Department"]).lower()
                or search_value in str(row["Assigned Officer"]).lower()
                or search_value in str(row["Status"]).lower(),
                axis=1
            )
        ]

    # =====================================================
    # DATAFRAME
    # =====================================================

    st.dataframe(
        filtered_records_df,
        width="stretch",
        hide_index=True
    )

    # =====================================================
    # SEARCH + OFFICER MANAGEMENT
    # =====================================================

    st.markdown("##  Search Complaints")

    search_query = st.text_input(
        "Search by Complaint / ID / Department / Officer / Status",
        key="officer_search"
    )

    filtered_df = df.copy()

    if search_query:

        search_query = search_query.lower()

        filtered_df = df[
            df.apply(
                lambda row:
                search_query in str(row["Complaint"]).lower()
                or search_query in str(row["Incident ID"]).lower()
                or search_query in str(row["Department"]).lower()
                or search_query in str(row["Assigned Officer"]).lower()
                or search_query in str(row["Status"]).lower(),
                axis=1
            )
        ]

    # =====================================================
    # OFFICER STATUS MANAGEMENT PANEL
    # =====================================================

    st.markdown("  Officer Complaint Management")

    for i, row in filtered_df.iterrows():

        st.markdown(f"""
        <div class='feature-box'>

        <h4 style='color:white;'>
        {row['Department']} Complaint
        </h4>

        <p style='color:#cbd5e1;'>
        {row['Complaint']}
        </p>

        <small style='color:#94a3b8;'>

        Current Status: {row['Status']}<br>
        Officer: {row['Assigned Officer']}<br>
        Priority: {row['Priority']}<br>
        ETA: {row['Resolution ETA']}<br>
        Zone: {row['Location']}<br>
        ID: {row['Incident ID']}

        </small>

        </div>
        """, unsafe_allow_html=True)

        col1, col2 = st.columns(
    [5,1],
    vertical_alignment="bottom"
)

        with col1:

            new_status = st.selectbox(

                f"Update Status - {row['Incident ID']}",

                [
                    "Pending",
                    "Assigned",
                    "In Progress",
                    "Resolved"
                ],

                index=[
                    "Pending",
                    "Assigned",
                    "In Progress",
                    "Resolved"
                ].index(row["Status"]),

                key=f"status_{i}"
            )

        with col2:

            st.markdown("<br>", unsafe_allow_html=True)

            if st.button(
                f"Update {row['Incident ID']}",
                key=f"btn_{i}"
            ):

                st.session_state.records[i]["Status"] = new_status

                st.success(
                    f"{row['Incident ID']} updated to {new_status}"
                )

                st.rerun()