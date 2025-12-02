# coding: utf-8
import os
import sqlite3
import requests
import logging
import traceback
import time
from flask import Flask, request, render_template, redirect, url_for, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_SCHEDULER_START, EVENT_SCHEDULER_SHUTDOWN
from datetime import datetime, timedelta

from config import PUSHOVER_APP_TOKEN, PUSHOVER_USER_KEY

VERSION = "6.2.3"
BUILD_DATE = datetime.now().strftime("%Y-%m-%d %H:%M")

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, template_folder=os.path.join(basedir, 'templates'))

DB = "irrigation.db"
# השארתי grace = 3600 לשמירה על תאימות. אם תרצה כיסוי ארוך יותר, אפשר להגדיל ל־21600
scheduler = BackgroundScheduler(job_defaults={'misfire_grace_time': 3600, 'coalesce': True})

ZONES = {"lawn": "דשא", "trees": "עצים", "hedge": "גדר חיה"}
C4_COMMANDS = {
    "lawn":  {"start": "http://192.168.1.201:49792/grass1", "stop": "http://192.168.1.201:49792/grass0"},
    "trees": {"start": "http://192.168.1.201:49792/trees1", "stop": "http://192.168.1.201:49792/trees0"},
    "hedge": {"start": "http://192.168.1.201:49792/rocks1", "stop": "http://192.168.1.201:49792/rocks0"}
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_db_conn():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def send_to_control4(url, retries=3, backoff=1.0):
    """שליחת פקודת HTTP ל-Control4 עם נסיונות חוזרים ו-backoff רך"""
    for attempt in range(1, retries + 1):
        try:
            requests.get(url, timeout=5).raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Error sending command to C4 ({url}): {e}")
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 1.5
    return False


def send_pushover_notification(message):
    try:
        requests.post("https://api.pushover.net/1/messages.json", data={
            "token": PUSHOVER_APP_TOKEN, "user": PUSHOVER_USER_KEY, "message": message
        }, timeout=5)
    except Exception as e:
        logging.error(f"Error sending Pushover notification: {e}")


def _safe_int(val, default=0):
    try:
        if val is None:
            return default
        if isinstance(val, int):
            return val
        s = str(val).strip()
        if s == "":
            return default
        return int(s)
    except Exception:
        return default


# ---------------- סטטוס מדווח מ-Control4 ----------------
def _core_update_status(zone, state):
    with get_db_conn() as conn:
        row = conn.execute("SELECT state, start_ts FROM status WHERE zone=?", (zone,)).fetchone()
        current_state = row['state'] if row else 'off'

    if current_state == state:
        logging.info(f"State for {zone} already {state}")
        return

    if state == 'on':
        start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_conn() as conn:
            conn.execute("UPDATE status SET state='on', start_ts=? WHERE zone=?", (start_ts, zone))
            conn.commit()
        send_pushover_notification(f"💧 השקיית {ZONES[zone]} החלה.")
    else:
        duration = 0
        if row and row['start_ts']:
            start_time = datetime.strptime(row['start_ts'], "%Y-%m-%d %H:%M:%S")
            duration = round((datetime.now() - start_time).total_seconds() / 60)
        with get_db_conn() as conn:
            if row and row['start_ts']:
                conn.execute("INSERT INTO history (zone, start_ts, duration) VALUES (?, ?, ?)",
                             (zone, row['start_ts'], duration))
            conn.execute("UPDATE status SET state='off', start_ts=NULL WHERE zone=?", (zone,))
            conn.commit()
        send_pushover_notification(f"✅ השקיית {ZONES[zone]} הסתיימה לאחר {duration} דקות.")


# ---------------- תזמונים ----------------
def run_schedule(schedule_id):
    try:
        with get_db_conn() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not row:
            logging.error(f"Schedule {schedule_id} not found")
            return

        zone = row['zone']
        duration = _safe_int(row['duration'], 0)
        ok = send_to_control4(C4_COMMANDS[zone]['start'])
        if not ok:
            logging.error(f"Schedule {schedule_id} failed to start zone {zone}")
            return

        stop_at = datetime.now() + timedelta(minutes=duration)
        scheduler.add_job(lambda z=zone: send_to_control4(C4_COMMANDS[z]['stop']),
                          'date', run_date=stop_at, id=f"timed_stop_{zone}", replace_existing=True)
        logging.info(f"Schedule {schedule_id} started zone {zone} for {duration} minutes")
    except Exception:
        logging.error("run_schedule exception:\n" + traceback.format_exc())


def schedule_job(row):
    try:
        start_dt = datetime.strptime(f"{row['start_date']} {row['start_time']}", "%Y-%m-%d %H:%M")
        interval_days = _safe_int(row['interval_days'], 0)

        if interval_days > 0:
            scheduler.add_job(
                run_schedule, 'interval',
                id=f"sched_{row['id']}", replace_existing=True,
                start_date=start_dt,
                end_date=row['end_date'] or None,
                kwargs={'schedule_id': row['id']},
                days=interval_days
            )
        else:
            scheduler.add_job(
                run_schedule, 'date',
                id=f"sched_{row['id']}", replace_existing=True,
                run_date=start_dt,
                kwargs={'schedule_id': row['id']}
            )
        logging.info(f"Loaded job sched_{row['id']}")
    except Exception:
        logging.error("schedule_job exception:\n" + traceback.format_exc())


def load_schedules():
    with get_db_conn() as conn:
        rows = conn.execute("SELECT * FROM schedules").fetchall()
    for r in rows:
        schedule_job(r)


# ---------------- UI ----------------
@app.route("/", methods=["GET", "POST"])
def index():
    try:
        if request.method == "POST":
            action = request.form.get("action")
            zone = request.form.get("zone")

            if action == "start":
                duration = _safe_int(request.form.get("duration"), 30)
                if send_to_control4(C4_COMMANDS[zone]['start']):
                    stop_time = datetime.now() + timedelta(minutes=duration)
                    scheduler.add_job(lambda z=zone: send_to_control4(C4_COMMANDS[z]['stop']),
                                      'date', run_date=stop_time, id=f"timed_stop_{zone}", replace_existing=True)

            elif action == "stop":
                send_to_control4(C4_COMMANDS[zone]['stop'])
                try:
                    scheduler.remove_job(f"timed_stop_{zone}")
                except Exception:
                    pass

            elif action == "schedule":
                start_date = request.form.get("start_date")           # YYYY-MM-DD
                start_time = request.form.get("start_time")           # HH:MM
                duration = _safe_int(request.form.get("duration"), 0)
                interval_days = _safe_int(request.form.get("interval_days"), 0)
                end_date = request.form.get("end_date") or None

                with get_db_conn() as conn:
                    cur = conn.execute("""INSERT INTO schedules
                                          (zone, start_date, start_time, duration, interval_days, end_date)
                                          VALUES (?, ?, ?, ?, ?, ?)""",
                                       (zone, start_date, start_time, duration, interval_days, end_date))
                    conn.commit()
                    row_id = cur.lastrowid
                    row = conn.execute("SELECT * FROM schedules WHERE id=?", (row_id,)).fetchone()
                schedule_job(row)

            return redirect(url_for('index'))

        with get_db_conn() as conn:
            statuses = {r['zone']: dict(r) for r in conn.execute("SELECT * FROM status").fetchall()}
            schedules = conn.execute("SELECT * FROM schedules ORDER BY id DESC").fetchall()
            history = conn.execute("SELECT * FROM history ORDER BY id DESC").fetchall()

        upcoming = {}
        now = datetime.now()
        for s in schedules:
            try:
                start_dt = datetime.strptime(f"{s['start_date']} {s['start_time']}", "%Y-%m-%d %H:%M")
                interval_days = _safe_int(s['interval_days'], 0)
                if interval_days > 0:
                    while start_dt <= now:
                        start_dt += timedelta(days=interval_days)
                if s['end_date']:
                    end_dt = datetime.strptime(s['end_date'], "%Y-%m-%d")
                    if start_dt.date() > end_dt.date():
                        upcoming[s['id']] = "עבר תוקף"
                        continue
                upcoming[s['id']] = start_dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                upcoming[s['id']] = "N/A"

        return render_template('index.html',
                               zones=ZONES, statuses=statuses,
                               schedules=schedules, history=history,
                               upcoming=upcoming, version=VERSION, build_date=BUILD_DATE)
    except Exception:
        logging.error("index exception:\n" + traceback.format_exc())
        return "Internal error", 500


# ---------------- API ----------------
@app.route("/api/report_status/<string:zone>/<string:state>")
def api_report_status(zone, state):
    try:
        if zone not in ZONES or state not in ['on', 'off']:
            return jsonify({"error": "Invalid zone or state"}), 400
        logging.info(f"Received status report from Control4: {zone} -> {state}")
        _core_update_status(zone, state)
        return jsonify({"status": "reported"})
    except Exception:
        logging.error("api_report_status exception:\n" + traceback.format_exc())
        return jsonify({"error": "internal"}), 500


@app.route("/api/schedule", methods=["POST"])
def api_create_schedule():
    try:
        data = request.get_json(force=True, silent=True) or {}
        zone = data.get("zone")
        if zone not in ZONES:
            return jsonify({"error": "invalid zone"}), 400
        start_date = data.get("start_date")
        start_time = data.get("start_time")
        duration = _safe_int(data.get("duration"), 0)
        interval_days = _safe_int(data.get("interval_days"), 0)
        end_date = data.get("end_date")

        with get_db_conn() as conn:
            cur = conn.execute("""INSERT INTO schedules
                                  (zone, start_date, start_time, duration, interval_days, end_date)
                                  VALUES (?, ?, ?, ?, ?, ?)""",
                               (zone, start_date, start_time, duration, interval_days, end_date))
            conn.commit()
            row_id = cur.lastrowid
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (row_id,)).fetchone()
        schedule_job(row)
        return jsonify({"status": "created", "id": row_id})
    except Exception:
        logging.error("api_create_schedule exception:\n" + traceback.format_exc())
        return jsonify({"error": "internal"}), 500


@app.route("/api/delete/<table>/<int:item_id>", methods=["POST"])
def api_delete(table, item_id):
    if table not in ['schedules', 'history']:
        return jsonify({"error": "Invalid table"}), 400
    if table == 'schedules':
        try:
            scheduler.remove_job(f"sched_{item_id}")
        except Exception:
            pass
    with get_db_conn() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
        conn.commit()
    return jsonify({"status": "deleted", "id": item_id})


@app.route("/api/clear/<table>", methods=["POST"])
def api_clear(table):
    if table not in ['schedules', 'history']:
        return jsonify({"error": "Invalid table"}), 400
    if table == 'schedules':
        # הסרה בטוחה של כל ה-jobs מסוג sched_
        try:
            for job in scheduler.get_jobs():
                if str(job.id).startswith("sched_"):
                    scheduler.remove_job(job.id)
        except Exception:
            pass
    with get_db_conn() as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.commit()
    return jsonify({"status": "cleared"})


@app.route("/health")
def health():
    try:
        with get_db_conn() as conn:
            conn.execute("SELECT 1")
        try:
            jobs = scheduler.get_jobs()
            payload = {
                "ok": True,
                "version": VERSION,
                "jobs_count": len(jobs),
                "next_runs": [
                    {
                        "id": j.id,
                        "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                        if j.next_run_time else None
                    }
                    for j in jobs[:10]
                ]
            }
        except Exception:
            payload = {"ok": True, "version": VERSION, "jobs_count": None, "next_runs": None}
        return jsonify(payload)
    except Exception:
        return jsonify({"ok": False}), 500


# ---------------- DB init + מיגרציה ----------------
def _table_columns(conn, table):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [r['name'] for r in cur.fetchall()]


def ensure_schema():
    with get_db_conn() as conn:
        # status
        conn.execute('''CREATE TABLE IF NOT EXISTS status (
                            zone TEXT PRIMARY KEY,
                            state TEXT,
                            start_ts TEXT)''')
        # schedules
        conn.execute('''CREATE TABLE IF NOT EXISTS schedules (
                            id INTEGER PRIMARY KEY,
                            zone TEXT,
                            start_date TEXT,
                            start_time TEXT,
                            duration INTEGER,
                            interval_days INTEGER,
                            end_date TEXT)''')
        # history
        conn.execute('''CREATE TABLE IF NOT EXISTS history (
                            id INTEGER PRIMARY KEY,
                            zone TEXT,
                            start_ts TEXT,
                            duration INTEGER)''')
        # אימות שהסכמה של schedules מלאה
        cols = _table_columns(conn, "schedules")
        needed = ["id", "zone", "start_date", "start_time", "duration", "interval_days", "end_date"]
        if any(c not in cols for c in needed) or len(cols) != len(needed):
            logging.warning(f"Schedules schema mismatch. Migrating. found={cols}")
            conn.execute('''CREATE TABLE IF NOT EXISTS schedules_new (
                                id INTEGER PRIMARY KEY,
                                zone TEXT,
                                start_date TEXT,
                                start_time TEXT,
                                duration INTEGER,
                                interval_days INTEGER,
                                end_date TEXT)''')
            common = [c for c in cols if c in needed and c != "id"]
            if common:
                insert_cols = ",".join(common)
                select_cols = ",".join(common)
                conn.execute(f"INSERT INTO schedules_new ({insert_cols}) SELECT {select_cols} FROM schedules")
            conn.execute("DROP TABLE schedules")
            conn.execute("ALTER TABLE schedules_new RENAME TO schedules")
        # seed status
        for z in ZONES.keys():
            conn.execute("INSERT OR IGNORE INTO status (zone, state) VALUES (?, ?)", (z, "off"))
        conn.commit()


def _sched_listener(event):
    # לוג בסיסי לאירועי scheduler
    try:
        logging.error(f"Scheduler event: {event}")
    except Exception:
        pass


def recover_stuck_zones():
    """Check for zones stuck in 'on' state and recover them on startup"""
    with get_db_conn() as conn:
        stuck = conn.execute("SELECT zone, start_ts FROM status WHERE state='on'").fetchall()

    for row in stuck:
        zone = row['zone']
        start_ts = row['start_ts']
        logging.warning(f"Found stuck zone '{zone}' in 'on' state since {start_ts}")

        # Calculate how long it's been running
        duration = 0
        if start_ts:
            try:
                start_time = datetime.strptime(start_ts, "%Y-%m-%d %H:%M:%S")
                duration = round((datetime.now() - start_time).total_seconds() / 60)
                logging.warning(f"Zone '{zone}' has been on for {duration} minutes - stopping it")
            except Exception:
                pass

        # Send stop command
        ok = send_to_control4(C4_COMMANDS[zone]['stop'])
        if ok:
            # Update database
            with get_db_conn() as conn:
                if start_ts:
                    conn.execute("INSERT INTO history (zone, start_ts, duration) VALUES (?, ?, ?)",
                                (zone, start_ts, duration))
                conn.execute("UPDATE status SET state='off', start_ts=NULL WHERE zone=?", (zone,))
                conn.commit()
            logging.info(f"Recovered stuck zone '{zone}' - added to history with {duration} minutes")
            send_pushover_notification(f"⚠️ השקיית {ZONES[zone]} הופסקה בעת אתחול המערכת לאחר {duration} דקות.")


def init_db():
    ensure_schema()
    recover_stuck_zones()


if __name__ == "__main__":
    init_db()
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        scheduler.add_listener(_sched_listener, EVENT_JOB_ERROR | EVENT_SCHEDULER_START | EVENT_SCHEDULER_SHUTDOWN)
        scheduler.start()
        load_schedules()
    app.run(host="0.0.0.0", port=5080, debug=False)
