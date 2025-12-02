#!/bin/bash
set -euo pipefail

URL="http://127.0.0.1:5080/health"
OUT="$(curl -s --max-time 5 "$URL" || true)"

# פרסור גמיש: עובד גם בלי jq
OK="$(echo "$OUT" | grep -oE '"ok":\s*(true|false)' | head -n1 | cut -d: -f2 | tr -d '[:space:],')"
JOBS="$(echo "$OUT" | grep -oE '"jobs_count":\s*[0-9]+' | awk -F: '{print $2}' | tr -d '[:space:],')"

log() {
  logger -t irrigation-health "$*"
}

if [ "$OK" != "true" ] || { [ -n "${JOBS:-}" ] && [ "$JOBS" = "0" ]; }; then
  log "health not ok. payload=$OUT. restarting irrigation.service"
  systemctl restart irrigation.service || true

  # התראה אופציונלית ב-Pushover אם הוגדרו מפתחות בסביבה
  if [ -n "${PUSHOVER_APP_TOKEN:-}" ] && [ -n "${PUSHOVER_USER_KEY:-}" ]; then
    curl -s -X POST https://api.pushover.net/1/messages.json \
      -d "token=$PUSHOVER_APP_TOKEN" \
      -d "user=$PUSHOVER_USER_KEY" \
      -d "message=Irrigation healthcheck triggered a restart. Payload: $OUT" >/dev/null || true
  fi
else
  log "health ok. payload=$OUT"
fi
