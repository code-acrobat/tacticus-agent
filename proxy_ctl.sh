#!/usr/bin/env bash
# proxy_ctl.sh - up / down / status for the Tacticus Swagger UI proxy.
#
#   ./proxy_ctl.sh [up|down|restart|status]
#
# Default is `status`. `status` exits 0 only when the proxy is running AND
# answering on the port, so it works as a health check in scripts.
set -u

cd "$(dirname "$0")" || exit 1

LOG=/tmp/opencode/proxy.log
PATTERN='tacticus_proxy[.]py'   # bracket form: never matches this script's own cmdline
URL=http://127.0.0.1:8124/

action="${1:-status}"

pid_of()   { pgrep -f "$PATTERN" | head -n 1; }
is_up()    { [ "$(health)" = 200 ]; }

# HTTP code of the front page, or 000 when nothing is listening.
health() {
    local code
    code="$(curl -sS -m 2 -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null)" || true
    printf '%s' "${code:-000}"
}

do_up() {
    local pid pid_after i
    pid="$(pid_of)"
    if [ -n "$pid" ] && is_up; then
        echo "proxy already up (pid $pid) → $URL"
        return 0
    fi
    if [ -n "$pid" ]; then
        # process exists but the port does not answer: replace it
        echo "stale proxy process (pid $pid) not answering; restarting"
        pkill -f "$PATTERN"; sleep 1
    fi
    mkdir -p "$(dirname "$LOG")"
    setsid nohup python3 tacticus_proxy.py > "$LOG" 2>&1 < /dev/null &
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if is_up; then
            echo "proxy up (pid $(pid_of)) → $URL"
            return 0
        fi
        sleep 0.5
    done
    echo "proxy failed to start - last log lines ($LOG):" >&2
    tail -n 20 "$LOG" >&2
    return 1
}

do_down() {
    local pid i
    pid="$(pid_of)"
    if [ -z "$pid" ]; then
        echo "proxy not running"
        return 0
    fi
    pkill -f "$PATTERN"
    for i in 1 2 3 4 5 6; do
        pid="$(pid_of)"
        [ -z "$pid" ] && { echo "proxy stopped"; return 0; }
        sleep 0.5
    done
    echo "proxy still alive (pid $pid) after 3s; sending SIGKILL" >&2
    pkill -9 -f "$PATTERN"
    return 1
}

do_status() {
    local pid code
    pid="$(pid_of)"
    code="$(health)"
    if [ -n "$pid" ] && [ "$code" = "200" ]; then
        echo "up   pid $pid  $URL  (HTTP 200)"
        return 0
    elif [ -n "$pid" ]; then
        echo "down pid $pid exists but $URL did not answer (HTTP $code)" >&2
        return 1
    fi
    echo "down nothing listening on $URL (HTTP $code)" >&2
    return 1
}

case "$action" in
    up)      do_up ;;
    down)    do_down ;;
    restart) do_down && do_up ;;
    status)  do_status ;;
    -h|--help|help)
        sed -n '2,7p' "$0"; exit 0 ;;
    *)
        echo "usage: $0 [up|down|restart|status]" >&2; exit 2 ;;
esac
