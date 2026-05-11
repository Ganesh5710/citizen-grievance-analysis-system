import streamlit as st
import pickle
import pandas as pd
import uuid
from datetime import datetime

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Civic Intelligence System", layout="wide")

# ---------------- LOAD MODEL ----------------
model = pickle.load(open("model/complaint_classifier.pkl", "rb"))
vectorizer = pickle.load(open("model/vectorizer.pkl", "rb"))
label_encoder = pickle.load(open("model/label_encoder.pkl", "rb"))

# ---------------- SESSION STORAGE ----------------
if "complaints" not in st.session_state:
    st.session_state.complaints = []

# ---------------- HEADER ----------------
st.markdown("<h1 style='text-align:center;'>🏛️ Smart Civic Complaint Intelligence System</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;'>AI-powered complaint analysis, routing & tracking</p>", unsafe_allow_html=True)

st.divider()

# ---------------- INPUT SECTION ----------------
st.subheader("📝 Register New Complaint")

col1, col2 = st.columns([2,1])

with col1:
    user_input = st.text_area("Enter Complaint")

with col2:
    location = st.selectbox("Select Location", ["Zone A", "Zone B", "Zone C", "Zone D"])
    submit = st.button("🚀 Analyze & Register")

# ---------------- PROCESS ----------------
if submit and user_input.strip():

    vec = vectorizer.transform([user_input])
    pred = model.predict(vec)
    dept = label_encoder.inverse_transform(pred)[0]

    # Confidence
    confidence = max(model.predict_proba(vec)[0]) * 100

    # Priority logic
    text = user_input.lower()
    if any(word in text for word in ["urgent", "not working", "immediately"]):
        priority = "High"
    elif any(word in text for word in ["delay", "late"]):
        priority = "Medium"
    else:
        priority = "Low"

    # Keywords
    keywords = [w for w in user_input.split() if len(w) > 4][:3]

    # Complaint ID
    complaint_id = "CG-" + str(uuid.uuid4())[:6].upper()

    # Timestamp
    time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Save complaint
    complaint_data = {
        "ID": complaint_id,
        "Complaint": user_input,
        "Department": dept,
        "Confidence": f"{confidence:.2f}%",
        "Priority": priority,
        "Location": location,
        "Status": "Pending",
        "Time": time
    }

    st.session_state.complaints.append(complaint_data)

    # ---------------- AI REPORT ----------------
    st.divider()
    st.subheader("📄 AI Complaint Analysis Report")

    col1, col2, col3 = st.columns(3)

    col1.metric("🏢 Department", dept)
    col2.metric("📈 Confidence", f"{confidence:.2f}%")
    col3.metric("⚡ Priority", priority)

    st.info(f"🧠 Reason: detected keywords → {', '.join(keywords)}")

    st.success(f"📌 Complaint Registered Successfully | ID: {complaint_id}")

# ---------------- DASHBOARD ----------------
st.divider()
st.subheader("📊 System Dashboard")

if st.session_state.complaints:

    df = pd.DataFrame(st.session_state.complaints)

    col1, col2, col3 = st.columns(3)

    col1.metric("Total Complaints", len(df))
    col2.metric("High Priority", len(df[df["Priority"]=="High"]))
    col3.metric("Pending Cases", len(df[df["Status"]=="Pending"]))

    col4, col5 = st.columns(2)

    with col4:
        st.write("📊 Department Distribution")
        st.bar_chart(df["Department"].value_counts())

    with col5:
        st.write("⚡ Priority Distribution")
        st.bar_chart(df["Priority"].value_counts())

else:
    st.info("No complaints registered yet.")

# ---------------- TRACKING SYSTEM ----------------
st.divider()
st.subheader("📍 Complaint Tracking System")

if st.session_state.complaints:

    df = pd.DataFrame(st.session_state.complaints)

    selected_id = st.selectbox("Select Complaint ID", df["ID"])

    selected_row = df[df["ID"] == selected_id].iloc[0]

    st.write("### Complaint Details")
    st.write(selected_row)

    # Update status
    new_status = st.selectbox("Update Status", ["Pending", "In Progress", "Resolved"])

    if st.button("Update Status"):
        for item in st.session_state.complaints:
            if item["ID"] == selected_id:
                item["Status"] = new_status
        st.success("Status Updated!")

else:
    st.info("No complaints available for tracking.")

# ---------------- HISTORY ----------------
st.divider()
st.subheader("📜 Complaint Records")

if st.session_state.complaints:
    st.dataframe(pd.DataFrame(st.session_state.complaints))