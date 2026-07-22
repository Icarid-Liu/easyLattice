#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_VALUE="127.0.0.1"
PORT_VALUE="8000"
OPEN_BROWSER=1
SETUP_ARGS=()

usage() {
  cat <<'EOF'
Usage: ./start.sh [options]

Options:
  --host HOST          Host to bind. Default: 127.0.0.1.
  --port PORT          Port to bind. Default: 8000.
  --force              Regenerate the local configuration before starting.
  --with-estimator     Clone standard and enhanced estimators if missing.
  --no-open            Do not open the browser automatically.
  -h, --help           Show this help.

Estimator paths can be configured in the browser after the service starts.
Set EASYLATTICE_CONFIG to use a configuration file outside the repository.
EOF
}

require_value() {
  local option="$1"
  local count="$2"
  if [[ "$count" -lt 2 ]]; then
    echo "$option requires a value" >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      require_value "$1" "$#"
      HOST_VALUE="$2"
      SETUP_ARGS+=("$1" "$2")
      shift 2
      ;;
    --port)
      require_value "$1" "$#"
      PORT_VALUE="$2"
      SETUP_ARGS+=("$1" "$2")
      shift 2
      ;;
    --force|--with-estimator)
      SETUP_ARGS+=("$1")
      shift
      ;;
    --no-open)
      OPEN_BROWSER=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

browser_host() {
  case "$1" in
    0.0.0.0|::|'[::]')
      printf '%s' "127.0.0.1"
      ;;
    *:*)
      if [[ "$1" == \[*\] ]]; then
        printf '%s' "$1"
      else
        printf '[%s]' "$1"
      fi
      ;;
    *)
      printf '%s' "$1"
      ;;
  esac
}

health_ready() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --max-time 1 --noproxy '*' "$url" \
      >/dev/null 2>&1
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget --quiet --timeout=1 --tries=1 --no-proxy --output-document=/dev/null "$url" \
      >/dev/null 2>&1
    return
  fi

  local python_bin=""
  if command -v python3 >/dev/null 2>&1; then
    python_bin="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    python_bin="$(command -v python)"
  else
    return 1
  fi

  "$python_bin" - "$url" >/dev/null 2>&1 <<'PY'
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(sys.argv[1], timeout=1) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
}

launch_browser() {
  local url="$1"
  if command -v wslview >/dev/null 2>&1 && wslview "$url" >/dev/null 2>&1; then
    return 0
  fi
  if command -v powershell.exe >/dev/null 2>&1 && \
      printf '%s\n' "$url" | powershell.exe -NoProfile -Command \
        '$url = [Console]::In.ReadLine(); Start-Process -FilePath $url' \
        >/dev/null 2>&1; then
    return 0
  fi
  if command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1; then
    return 0
  fi
  if command -v open >/dev/null 2>&1 && open "$url" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

wait_and_open() {
  local health_url="$1"
  local app_url="$2"
  local server_pid="$3"

  while kill -0 "$server_pid" 2>/dev/null; do
    if health_ready "$health_url"; then
      if ! launch_browser "$app_url"; then
        printf 'Open easyLattice in your browser: %s\n' "$app_url"
      fi
      return
    fi
    sleep 0.2
  done
}

BROWSER_HOST="$(browser_host "$HOST_VALUE")"
APP_URL="http://$BROWSER_HOST:$PORT_VALUE/"
HEALTH_URL="${APP_URL}api/health"

if [[ "$OPEN_BROWSER" -eq 1 ]]; then
  wait_and_open "$HEALTH_URL" "$APP_URL" "$$" &
fi

exec "$ROOT_DIR/scripts/setup-local.sh" --start "${SETUP_ARGS[@]}"
