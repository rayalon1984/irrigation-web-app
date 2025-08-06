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

def update_status(zone, state):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE status SET state=? WHERE zone=?", (state, zone))
    conn.commit()
    conn.close()

def get_status():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT zone, state FROM status")
    rows = dict(c.fetchall())
    conn.close()
    return rows

def log_history(zone, duration):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO history (zone, start_ts, duration) VALUES (?, ?, ?)", (zone, ts, duration))
    conn.commit()
    conn.close()

def schedule_irrigation(job_id, zone, start_date, start_time, duration, interval_days, end_date):
    dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")

    def job():
        run_irrigation(zone, duration)
        next_dt = datetime.now() + timedelta(days=interval_days)
        if not end_date or datetime.strptime(end_date, "%Y-%m-%d") >= next_dt.date():
            scheduler.add_job(job, 'date', run_date=next_dt)

    scheduler.add_job(job, 'date', run_date=dt, id=str(job_id))

def run_irrigation(zone, duration):
    if send_to_control4(C4_COMMANDS[zone]["start"]):
        update_status(zone, "on")
        log_history(zone, duration)
    stop_time = datetime.now() + timedelta(minutes=duration)
    scheduler.add_job(lambda: stop_irrigation(zone), 'date', run_date=stop_time)

def stop_irrigation(zone):
    if send_to_control4(C4_COMMANDS[zone]["stop"]):
        update_status(zone, "off")

def load_schedules():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    for row in c.execute("SELECT id, zone, start_date, start_time, duration, interval_days, end_date FROM schedules"):
        schedule_irrigation(*row)
    conn.close()

def compute_manual_timers(statuses):
    timers = {}
    for zone, state in statuses.items():
        if state == "on":
            conn = sqlite3.connect(DB)
            c = conn.cursor()
            c.execute("SELECT start_ts, duration FROM history WHERE zone=? ORDER BY id DESC LIMIT 1", (zone,))
            row = c.fetchone()
            conn.close()
            if row:
                start_ts_str, duration = row
                start_dt = datetime.strptime(start_ts_str, "%Y-%m-%d %H:%M:%S")
                elapsed = int((datetime.now() - start_dt).total_seconds())
                max_seconds = duration * 60
                elapsed = max(0, min(elapsed, max_seconds))
                timers[zone] = elapsed
    return timers

# ----- פה משאיר את התבנית שלך בדיוק כמו שהייתה -----
# (כולל ה־HTML עם ה־CSS לשלושת ה־themes וכל הלוגיקה)

# בסוף:
if __name__ == "__main__":
    init_db()
    try:
        load_schedules()
    except Exception as e:
        logging.error(f"שגיאה בטעינת תזמונים: {e}")
    app.run(host="0.0.0.0", port=5080)
