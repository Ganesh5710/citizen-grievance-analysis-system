import sqlite3

conn = sqlite3.connect("complaints.db", check_same_thread=False)

cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS complaints (
    id TEXT,
    complaint TEXT,
    department TEXT,
    confidence TEXT,
    priority TEXT,
    location TEXT,
    status TEXT,
    time TEXT
)
""")

conn.commit()

# INSERT
def insert_complaint(data):

    cursor.execute("""
    INSERT INTO complaints VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["ID"],
        data["Complaint"],
        data["Department"],
        data["Confidence"],
        data["Priority"],
        data["Location"],
        data["Status"],
        data["Time"]
    ))

    conn.commit()

# FETCH
def fetch_complaints():

    cursor.execute("SELECT * FROM complaints")

    return cursor.fetchall()

# UPDATE STATUS
def update_status(status, complaint_id):

    cursor.execute("""
    UPDATE complaints
    SET status = ?
    WHERE id = ?
    """, (status, complaint_id))

    conn.commit()