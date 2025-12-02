# Irrigation Web App

A Flask-based web application for managing and scheduling irrigation zones through Control4 smart home integration.

## Version

**Current Version**: 6.2.3
**HTML UI Version**: 1.3.1
**Last Updated**: December 2, 2025

## Features

- **Manual Control**: Start/stop irrigation for three zones (Lawn, Trees, Hedge)
- **Scheduled Irrigation**: Create one-time or recurring irrigation schedules
- **Smart Recovery**: Automatically detects and fixes stuck irrigation zones on startup
- **History Tracking**: Complete irrigation history with duration tracking
- **Push Notifications**: Pushover integration for irrigation status updates
- **Control4 Integration**: Direct HTTP commands to Control4 system
- **Responsive UI**: Hebrew RTL interface with dark mode support
- **Health Monitoring**: Built-in health check endpoint for monitoring

## Architecture

### Tech Stack
- **Backend**: Flask (Python 3.11)
- **Scheduler**: APScheduler with background job execution
- **Database**: SQLite3
- **Notifications**: Pushover API
- **Smart Home**: Control4 HTTP API

### Database Schema
- `status` - Current state of each irrigation zone
- `schedules` - Recurring and one-time irrigation schedules
- `history` - Complete irrigation event log with durations

## Installation

1. Clone the repository:
```bash
git clone git@github.com:rayalon1984/irrigation-web-app.git
cd irrigation-web-app
```

2. Create virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install flask apscheduler requests
```

3. Configure environment variables in `.env`:
```bash
DB_PATH=irrigation.db
C4_LAWN_START=http://192.168.1.201:49792/grass1
C4_LAWN_STOP=http://192.168.1.201:49792/grass0
C4_TREES_START=http://192.168.1.201:49792/trees1
C4_TREES_STOP=http://192.168.1.201:49792/trees0
C4_HEDGE_START=http://192.168.1.201:49792/rocks1
C4_HEDGE_STOP=http://192.168.1.201:49792/rocks0
```

4. Configure Pushover credentials in `config.py`:
```python
PUSHOVER_APP_TOKEN = "your_app_token"
PUSHOVER_USER_KEY = "your_user_key"
```

5. Run the application:
```bash
python irrigation_app.py
```

The app will be available at `http://localhost:5080`

## API Endpoints

### Status Reporting
- `GET /api/report_status/<zone>/<state>` - Control4 callback for status updates

### Schedule Management
- `POST /api/schedule` - Create new irrigation schedule
- `POST /api/delete/<table>/<id>` - Delete schedule or history entry
- `POST /api/clear/<table>` - Clear all schedules or history

### Health Check
- `GET /health` - Application health and scheduler status

## Configuration

### Irrigation Zones
Edit the `ZONES` dictionary in `irrigation_app.py`:
```python
ZONES = {"lawn": "דשא", "trees": "עצים", "hedge": "גדר חיה"}
```

### Control4 Commands
Edit the `C4_COMMANDS` dictionary for your Control4 setup:
```python
C4_COMMANDS = {
    "lawn":  {"start": "http://...", "stop": "http://..."},
    "trees": {"start": "http://...", "stop": "http://..."},
    "hedge": {"start": "http://...", "stop": "http://..."}
}
```

## Critical Bug Fix (v6.2.3)

### Issue
**Discovered**: December 2, 2025
**Severity**: Critical

Irrigation zones could become stuck in "on" state indefinitely if the application restarted before a scheduled stop time. This occurred because:

1. Manual irrigation starts created APScheduler jobs to stop irrigation after the specified duration
2. These scheduled jobs were stored only in memory
3. If the app restarted (crash, deployment, power loss), the stop job was lost
4. Irrigation would continue running indefinitely
5. Database status remained "on" with no automatic recovery

**Real Impact**: The lawn zone was stuck in "on" state for **3 days (4,389 minutes)** from November 29-December 2, 2025.

### Root Cause
Located in `irrigation_app.py:175-178`:
```python
scheduler.add_job(lambda z=zone: send_to_control4(C4_COMMANDS[z]['stop']),
                  'date', run_date=stop_time, id=f"timed_stop_{zone}",
                  replace_existing=True)
```

The scheduled stop job was not persisted across application restarts.

### Solution
Added `recover_stuck_zones()` function (v6.2.3) that runs on every application startup:

1. **Detection**: Queries database for zones in "on" state
2. **Calculation**: Computes actual runtime duration from start timestamp
3. **Recovery**: Sends stop command to Control4 system
4. **Logging**: Adds entry to history with calculated duration
5. **Notification**: Sends Pushover alert about recovery action
6. **Cleanup**: Updates database status to "off"

Implementation in `irrigation_app.py:402-438`:
```python
def recover_stuck_zones():
    """Check for zones stuck in 'on' state and recover them on startup"""
    with get_db_conn() as conn:
        stuck = conn.execute("SELECT zone, start_ts FROM status WHERE state='on'").fetchall()

    for row in stuck:
        zone = row['zone']
        start_ts = row['start_ts']
        logging.warning(f"Found stuck zone '{zone}' in 'on' state since {start_ts}")

        # Calculate duration and send stop command
        # Add to history and update status
        # Send notification
```

### Prevention
The recovery function now ensures that:
- No irrigation zone can remain stuck after application restart
- All irrigation events are properly logged with accurate durations
- Users receive notifications about automatic recovery actions
- System self-heals on every startup

## Monitoring

### Health Check
```bash
curl http://192.168.1.25:5080/health
```

Response:
```json
{
  "ok": true,
  "version": "6.2.3",
  "jobs_count": 0,
  "next_runs": []
}
```

### System Status
Check running process:
```bash
ps aux | grep irrigation_app
```

Check logs:
```bash
journalctl -u irrigation -n 50
```

## Backup

Included `backup_sqlite.sh` script for automated database backups:
```bash
./backup_sqlite.sh
```

## Contributing

This is a personal home automation project. Feel free to fork and adapt for your own use.

## License

Private use only.

## Changelog

### v6.2.3 (2025-12-02)
- **Critical Fix**: Added automatic recovery for stuck irrigation zones on startup
- **Enhancement**: Comprehensive logging for recovery operations
- **Enhancement**: Pushover notifications for recovery events
- Fixed 3-day stuck irrigation issue

### v6.2.2
- Previous stable version
- Basic irrigation control and scheduling

## Support

For issues or questions, please open an issue on GitHub.

---
**Built with Flask** | **Powered by Control4** | **Deployed on Raspberry Pi**
