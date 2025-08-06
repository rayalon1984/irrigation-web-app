from flask import Flask, request, render_template_string, redirect, url_for
import sqlite3, requests, logging, os
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from dotenv import load_dotenv

# טעינת קובץ .env
load_dotenv()

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# מסד נתונים
DB = os.getenv("DB_PATH", "irrigation.db")

# כתובות HTTP ל-Control4 מתוך .env
C4_COMMANDS = {
    "lawn":  {"start": os.getenv("C4_LAWN_START"), "stop": os.getenv("C4_LAWN_STOP")},
    "trees": {"start": os.getenv("C4_TREES_START"), "stop": os.getenv("C4_TREES_STOP")},
    "hedge": {"start": os.getenv("C4_HEDGE_START"), "stop": os.getenv("C4_HEDGE_STOP")}
}

# שמות עברית לאזורים
ZONES = {
    "lawn":  "דשא",
    "trees": "עצים",
    "hedge": "גדר חיה"
}

logging.basicConfig(level=logging.INFO)

def send_to_control4(url):
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        logging.info(f"נשלחה בקשה ל-Control4: {url}, סטטוס {r.status_code}")
        return True
    except Exception as e:
        logging.error(f"שגיאה בשליחת בקשה ל-Control4: {e}")
        return False

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone TEXT,
            start_date TEXT,
            start_time TEXT,
            duration INT,
            interval_days INT,
            end_date TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS status (
            zone TEXT PRIMARY KEY,
            state TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone TEXT,
            start_ts TEXT,
            duration INT
        )
    ''')
    for z in ZONES.keys():
        c.execute("INSERT OR IGNORE INTO status (zone, state) VALUES (?, ?)", (z, "off"))
    conn.commit()
    conn.close()

# ---- שאר הקוד שלך נשאר ללא שינוי ----
# (כאן תדביק את כל הפונקציות הקיימות: update_status, get_status, log_history, וכו')

if __name__ == "__main__":
    init_db()
    # טעינת תזמונים קיימים
    try:
        load_schedules()
    except Exception as e:
        logging.error(f"שגיאה בטעינת תזמונים: {e}")
    app.run(host="0.0.0.0", port=5080)
