from flask import Flask, request, render_template_string, redirect, url_for
import sqlite3, requests, logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

DB = "irrigation.db"

# כתובות HTTP ל-Control4 לכל אזור
C4_COMMANDS = {
    "lawn":  {"start": "http://192.168.1.201:49792/grass1", "stop": "http://192.168.1.201:49792/grass0"},
    "trees": {"start": "http://192.168.1.201:49792/trees1", "stop": "http://192.168.1.201:49792/trees0"},
    "hedge": {"start": "http://192.168.1.201:49792/rocks1", "stop": "http://192.168.1.201:49792/rocks0"}
}

# שמות עברית לאזורים
ZONES = {
    "lawn":  "דשא",
    "trees": "עצים",
    "hedge": "גדר חיה"
}

logging.basicConfig(level=logging.INFO)

def send_to_control4(url):
    """שליחת בקשה ל-Control4 והחזרת הצלחה"""
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        logging.info(f"נשלחה בקשה ל-Control4: {url}, סטטוס {r.status_code}")
        return True
    except Exception as e:
        logging.error(f"שגיאה בשליחת בקשה ל-Control4: {e}")
        return False

def init_db():
    """יוצר את הטבלאות למסד, אם אינן קיימות"""
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
    # מוודא שלכל אזור יש מצב התחלתי "off"
    for z in ZONES.keys():
        c.execute("INSERT OR IGNORE INTO status (zone, state) VALUES (?, ?)", (z, "off"))
    conn.commit()
    conn.close()

def update_status(zone, state):
    """עדכון סטטוס של אזור ('on' או 'off')"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE status SET state=? WHERE zone=?", (state, zone))
    conn.commit()
    conn.close()

def get_status():
    """החזרת מילון של סטטוס לכל אזור"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT zone, state FROM status")
    rows = dict(c.fetchall())
    conn.close()
    return rows

def log_history(zone, duration):
    """רישום בהיסטוריה של הפעלת אזור"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO history (zone, start_ts, duration) VALUES (?, ?, ?)", (zone, ts, duration))
    conn.commit()
    conn.close()

def schedule_irrigation(job_id, zone, start_date, start_time, duration, interval_days, end_date):
    """תזמון השקיה עתידית"""
    dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")

    def job():
        run_irrigation(zone, duration)
        next_dt = datetime.now() + timedelta(days=interval_days)
        if not end_date or datetime.strptime(end_date, "%Y-%m-%d") >= next_dt.date():
            scheduler.add_job(job, 'date', run_date=next_dt)

    scheduler.add_job(job, 'date', run_date=dt, id=str(job_id))

def run_irrigation(zone, duration):
    """הפעלת השקיה לאזור נתון"""
    if send_to_control4(C4_COMMANDS[zone]["start"]):
        update_status(zone, "on")
        log_history(zone, duration)
    stop_time = datetime.now() + timedelta(minutes=duration)
    scheduler.add_job(lambda: stop_irrigation(zone), 'date', run_date=stop_time)

def stop_irrigation(zone):
    """כיבוי השקיה לאזור נתון"""
    if send_to_control4(C4_COMMANDS[zone]["stop"]):
        update_status(zone, "off")

def load_schedules():
    """טעינת כל תזמוני ההשקיה מהמסד אל מתזמן APScheduler"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    for row in c.execute("SELECT id, zone, start_date, start_time, duration, interval_days, end_date FROM schedules"):
        schedule_irrigation(*row)
    conn.close()

def compute_manual_timers(statuses):
    """
    עבור מצב ידני מחזיר מילון שבו לכל אזור פעיל (state='on') רשומה 'elapsed' – 
    מספר השניות שחלפו מאז שהתחיל להשקות (עד גבול משך ההשקיה).
    """
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
                # הגבלת השעון למשך שהוגדר
                max_seconds = duration * 60
                if elapsed < 0:
                    elapsed = 0
                if elapsed > max_seconds:
                    elapsed = max_seconds
                timers[zone] = elapsed
    return timers

@app.route("/", methods=["GET", "POST"])
def index():
    tab = request.args.get("tab", "schedule")
    theme = request.args.get("theme", "classic")
    edit_id = request.args.get("edit") or request.form.get("edit_id")
    if edit_id:
        edit_id = int(edit_id)

    # שמירת או עדכון תזמון
    if request.method == "POST" and tab == "schedule":
        zone = request.form["zone"]
        start_date = request.form["start_date"]
        start_time = request.form["start_time"]
        duration = int(request.form["duration"])
        interval_days = int(request.form["interval_days"])
        end_date = request.form.get("end_date") or None

        conn = sqlite3.connect(DB)
        c = conn.cursor()
        if edit_id:
            c.execute("""UPDATE schedules
                         SET zone=?, start_date=?, start_time=?, duration=?, interval_days=?, end_date=?
                         WHERE id=?""",
                      (zone, start_date, start_time, duration, interval_days, end_date, edit_id))
        else:
            c.execute("""INSERT INTO schedules
                         (zone, start_date, start_time, duration, interval_days, end_date)
                         VALUES (?,?,?,?,?,?)""",
                      (zone, start_date, start_time, duration, interval_days, end_date))
        conn.commit()
        conn.close()
        scheduler.remove_all_jobs()
        load_schedules()
        return redirect(f"/?tab=schedule&theme={theme}")

    # קריאת תזמונים
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, zone, start_date, start_time, duration, interval_days, end_date FROM schedules")
    rows = c.fetchall()
    conn.close()

    # סטטוסים
    statuses = get_status()

    # טיימרים למצב ידני (elapsed seconds)
    timers = {}
    if tab == "manual":
        timers = compute_manual_timers(statuses)

    # רשומות היסטוריה
    history_rows = []
    if tab == "history":
        conn = sqlite3.connect(DB)
        c = conn.cursor()
        c.execute("SELECT id, zone, start_ts, duration FROM history ORDER BY start_ts DESC LIMIT 100")
        history_rows = c.fetchall()
        conn.close()

    # חיפוש רשומת עריכה
    edit_row = None
    if edit_id:
        for r in rows:
            if str(r[0]) == str(edit_id):
                edit_row = r
                break

    # שמות תצוגה לשלושת המצבים
    theme_names = {
        "classic": "מצב רגיל",
        "modern":  "מצב מודרני",
        "mobile":  "מצב מובייל"
    }

    # CSS לכל תצוגה
    css_map = {
        "classic": """
            body { font-family: Arial, sans-serif; direction: rtl; background: #f8f9fa; margin: 20px; }
            nav a { margin: 10px; font-weight: bold; color: #007bff; text-decoration: none; }
            nav a.active { color: #28a745; }
            .theme-toggle a { margin: 5px; color: #8e44ad; text-decoration: none; }
            .theme-toggle a.active { font-weight: bold; }
            .zone-box { background: white; padding: 15px; margin: 15px 0;
                        border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            .status-on  { color: green; font-weight: bold; }
            .status-off { color: gray;  font-weight: bold; }
            button { margin-top: 10px; }
        """,
        "modern": """
            body { font-family: 'Helvetica Neue', Arial, sans-serif; direction: rtl;
                   background: #eef2f5; margin: 15px; }
            h1 { color: #344767; margin-bottom: 5px; }
            nav a { margin: 6px; padding: 4px 10px; border-radius: 4px; text-decoration: none; color: #3498db; }
            nav a.active { background-color: #3498db; color: white; }
            .theme-toggle a { margin: 4px; text-decoration: none; color: #8e44ad; }
            .theme-toggle a.active { font-weight: bold; text-decoration: underline; }
            .zone-box { background: white; padding: 20px; margin: 12px 0;
                        border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
            form select, form input[type="date"], form input[type="time"],
            form input[type="number"] {
                width: 100%; padding: 8px; margin-bottom: 8px;
                border: 1px solid #ccc; border-radius: 6px;
            }
            button { background-color: #3498db; color: white; border: none;
                     padding: 10px 16px; border-radius: 6px; cursor: pointer; }
            button:hover { background-color: #2980b9; }
            .status-on  { color: #27ae60; font-weight: bold; }
            .status-off { color: #7f8c8d; font-weight: bold; }
        """,
        "mobile": """
            body { font-family: Arial, sans-serif; direction: rtl; background: #f7f9fb; margin: 10px;
                   font-size: 1.15em; }
            h1 { margin-top: 0; margin-bottom: 12px; font-weight: bold; }
            nav a { display: block; padding: 8px 12px; margin: 6px 0;
                    background: #e0e7ff; border-radius: 8px; text-decoration: none;
                    color: #364fc7; text-align: center; }
            nav a.active { background: #364fc7; color: white; }
            .theme-toggle { margin-bottom: 12px; }
            .theme-toggle a { margin: 4px; padding: 5px 10px; text-decoration: none;
                              border-radius: 6px; background: #f0f0f8; color: #5f27cd; }
            .theme-toggle a.active { background: #5f27cd; color: white; }
            .zone-box { background: white; padding: 18px; margin: 16px 0;
                        border-radius: 14px; box-shadow: 0 3px 6px rgba(0,0,0,0.12); }
            form label { display: block; margin-bottom: 4px; font-weight: bold; }
            form select, form input[type="date"], form input[type="time"],
            form input[type="number"] {
                width: 100%; padding: 12px; margin-bottom: 10px;
                border: 1px solid #d0d4db; border-radius: 8px; font-size: 1em;
            }
            button { width: 100%; padding: 14px 0; border-radius: 8px; background: #364fc7;
                     color: white; border: none; font-size: 1.05em; cursor: pointer; }
            button:hover { background: #2c3d8b; }
            .status-on  { color: #1e7e34; font-weight: bold; }
            .status-off { color: #6c757d; font-weight: bold; }
        """
    }
    css = css_map.get(theme, css_map["classic"])

    template = """
    <!DOCTYPE html>
    <html lang="he">
    <head>
        <meta charset="utf-8">
        <title>ניהול השקיה</title>
        <style>{{ css }}</style>
        <script>
        // פונקציה להפעלת סטופר באזור ידני
        function startStopwatch(id, elapsed) {
            var el = document.getElementById(id);
            function tick() {
                var m = Math.floor(elapsed / 60);
                var s = elapsed % 60;
                el.textContent = m + ":" + (s < 10 ? "0" + s : s);
                elapsed++;
                setTimeout(tick, 1000);
            }
            tick();
        }
        </script>
    </head>
    <body>
    <h1>ניהול השקיה</h1>
    <!-- מתגי תצוגה -->
    <div class="theme-toggle">
        {% for tname, display_name in theme_names.items() %}
          <a href="?tab={{ tab }}&theme={{ tname }}" class="{{ 'active' if theme == tname else '' }}">
            {{ display_name }}
          </a>
        {% endfor %}
    </div>
    <nav>
        <a href="/?tab=schedule&theme={{ theme }}" class="{{ 'active' if tab=='schedule' else '' }}">תזמון</a>
        <a href="/?tab=manual&theme={{ theme }}"   class="{{ 'active' if tab=='manual'   else '' }}">מצב ידני</a>
        <a href="/?tab=history&theme={{ theme }}"  class="{{ 'active' if tab=='history'  else '' }}">היסטוריה</a>
    </nav>

    {% if tab == 'schedule' %}
    <form method="post">
        <input type="hidden" name="edit_id" value="{{ edit_row[0] if edit_row else '' }}">
        <label>בחר אזור:</label>
        <select name="zone">
            {% for key, name in zones.items() %}
              <option value="{{ key }}" {% if edit_row and edit_row[1] == key %}selected{% endif %}>{{ name }}</option>
            {% endfor %}
        </select>
        <label>תאריך התחלה:</label>
        <input type="date" name="start_date" value="{{ edit_row[2] if edit_row else '' }}" required>
        <label>שעת התחלה:</label>
        <input type="time" name="start_time" value="{{ edit_row[3] if edit_row else '' }}" required>
        <label>משך (בדקות):</label>
        <input type="number" name="duration" min="1" value="{{ edit_row[4] if edit_row else '' }}" required>
        <label>תדירות (כל כמה ימים):</label>
        <input type="number" name="interval_days" min="1" value="{{ edit_row[5] if edit_row else '1' }}" required>
        <label>תאריך סיום (ריק = אינסוף):</label>
        <input type="date" name="end_date" value="{{ edit_row[6] if edit_row else '' }}">
        <button type="submit">{{ 'עדכן' if edit_row else 'שמור' }}</button>
    </form>

    <h2>תזמונים קיימים</h2>
    {% for r in rows %}
      <div class="zone-box">
        <b>{{ zones.get(r[1], r[1]) }}</b><br>
        התחלה: {{ r[2] }} {{ r[3] }} | משך: {{ r[4] }} דקות | כל {{ r[5] }} ימים{% if r[6] %} | עד {{ r[6] }}{% endif %}
        <br>
        <span class="status-{{ statuses[r[1]] }}">{{ 'משקה עכשיו' if statuses[r[1]] == 'on' else 'כבוי' }}</span>
        <br>
        <a href="{{ url_for('index', tab='schedule', edit=r[0], theme=theme) }}">ערוך</a> |
        <a href="javascript:void(0);" style="color:red;"
           onclick="if(confirm('האם למחוק?')) window.location.href='{{ url_for('delete_schedule', sched_id=r[0]) }}';">
           מחק
        </a>
      </div>
    {% endfor %}

    {% elif tab == 'manual' %}
    <h2>מצב ידני</h2>
    {% for key, name in zones.items() %}
      <div class="zone-box">
        <b>{{ name }}</b><br>
        <span class="status-{{ statuses[key] }}">{{ 'משקה עכשיו' if statuses[key] == 'on' else 'כבוי' }}</span>
        <!-- הצגת סטופר רק אם האזור פעיל -->
        {% if timers.get(key) %}
          <br>
          <span>זמן ריצה: <span id="timer-{{ key }}"></span></span>
          <script> startStopwatch('timer-{{ key }}', {{ timers[key] }}); </script>
        {% endif %}
        <br>
        <a href="{{ url_for('manual_start', zone=key) }}">התחל</a> |
        <a href="{{ url_for('manual_stop', zone=key) }}" style="color:red;">עצור</a>
      </div>
    {% endfor %}

    {% elif tab == 'history' %}
    <h2>היסטוריית השקיה</h2>
    <p><a href="{{ url_for('clear_history') }}" style="color:red;"
          onclick="return confirm('למחוק את כל ההיסטוריה?');">מחק הכל</a></p>
    <ul>
      {% for h in history_rows %}
        <li>
          {{ zones.get(h[1], h[1]) }} – {{ h[2] }} – {{ h[3] }} דקות
          <a href="{{ url_for('delete_history', hist_id=h[0]) }}" style="color:red;"
             onclick="return confirm('למחוק רשומה זו?');">מחק</a>
        </li>
      {% endfor %}
    </ul>
    {% endif %}

    </body>
    </html>
    """

    return render_template_string(template,
                                  css=css,
                                  tab=tab,
                                  theme=theme,
                                  theme_names=theme_names,
                                  rows=rows,
                                  zones=ZONES,
                                  statuses=statuses,
                                  edit_row=edit_row,
                                  history_rows=history_rows,
                                  timers=timers)

@app.route("/manual_start/<zone>")
def manual_start(zone):
    run_irrigation(zone, 5)
    return redirect("/?tab=manual")

@app.route("/manual_stop/<zone>")
def manual_stop(zone):
    stop_irrigation(zone)
    return redirect("/?tab=manual")

@app.route("/delete/<int:sched_id>")
def delete_schedule(sched_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
    conn.commit()
    conn.close()
    scheduler.remove_all_jobs()
    load_schedules()
    return redirect("/?tab=schedule")

@app.route("/delete_history/<int:hist_id>")
def delete_history(hist_id):
    """מחיקת רשומה בודדת מהיסטוריה"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE id=?", (hist_id,))
    conn.commit()
    conn.close()
    return redirect("/?tab=history")

@app.route("/clear_history")
def clear_history():
    """מחיקת כל היסטוריית ההשקיה"""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM history")
    conn.commit()
    conn.close()
    return redirect("/?tab=history")

if __name__ == "__main__":
    init_db()
    load_schedules()
    app.run(host="0.0.0.0", port=5080)
