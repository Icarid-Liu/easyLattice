#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="$ROOT_DIR/config.local.json"
HOST_VALUE="127.0.0.1"
PORT_VALUE="8000"
START_SERVER=0
FORCE_CONFIG=0
WITH_ESTIMATOR=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-local.sh [options]

Options:
  --start              Start the local web service after setup.
  --host HOST          Host for --start. Default: 127.0.0.1.
  --port PORT          Port for --start. Default: 8000.
  --force              Regenerate config.local.json even if it already exists.
  --with-estimator     Clone standard and enhanced estimators into .external/ if missing.
  -h, --help           Show this help.

Default setup is lightweight: it creates config.local.json, detects optional
Sage and both lattice-estimator profiles if present, and keeps LLM disabled.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start)
      START_SERVER=1
      shift
      ;;
    --host)
      HOST_VALUE="${2:?--host requires a value}"
      shift 2
      ;;
    --port)
      PORT_VALUE="${2:?--port requires a value}"
      shift 2
      ;;
    --force)
      FORCE_CONFIG=1
      shift
      ;;
    --with-estimator)
      WITH_ESTIMATOR=1
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

cd "$ROOT_DIR"

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "Python 3 is required but was not found." >&2
  exit 1
}

PYTHON_BIN="$(find_python)"

SAGE_BIN="sage"
if command -v sage >/dev/null 2>&1; then
  SAGE_BIN="$(command -v sage)"
fi

ESTIMATOR_PATH="${LATTICE_ESTIMATOR_PATH:-}"
if [[ -z "$ESTIMATOR_PATH" ]]; then
  for candidate in \
    "$ROOT_DIR/.external/lattice-estimator" \
    "$ROOT_DIR/lattice-estimator" \
    "$ROOT_DIR/../lattice-estimator" \
    "$HOME/lattice-estimator" \
    "/opt/lattice-estimator"; do
    if [[ -d "$candidate/estimator" ]]; then
      ESTIMATOR_PATH="$candidate"
      break
    fi
  done
fi

ENHANCED_ESTIMATOR_PATH="${ENHANCED_LATTICE_ESTIMATOR_PATH:-}"
if [[ -z "$ENHANCED_ESTIMATOR_PATH" ]]; then
  for candidate in \
    "$ROOT_DIR/.external/enhanced-lattice-estimator" \
    "$ROOT_DIR/.external/enhanced_lattice-estimator" \
    "$ROOT_DIR/enhanced_lattice-estimator" \
    "$ROOT_DIR/enhanced-lattice-estimator" \
    "$ROOT_DIR/../enhanced_lattice-estimator" \
    "$ROOT_DIR/../enhanced-lattice-estimator" \
    "$HOME/enhanced_lattice-estimator" \
    "$HOME/enhanced-lattice-estimator" \
    "/opt/enhanced-lattice-estimator"; do
    if [[ -d "$candidate/estimator" ]]; then
      ENHANCED_ESTIMATOR_PATH="$candidate"
      break
    fi
  done
fi

if [[ "$WITH_ESTIMATOR" -eq 1 && ( -z "$ESTIMATOR_PATH" || -z "$ENHANCED_ESTIMATOR_PATH" ) ]]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required for --with-estimator but was not found." >&2
    exit 1
  fi
  mkdir -p "$ROOT_DIR/.external"
fi

if [[ "$WITH_ESTIMATOR" -eq 1 && -z "$ESTIMATOR_PATH" ]]; then
  git clone --depth=1 https://github.com/malb/lattice-estimator.git "$ROOT_DIR/.external/lattice-estimator"
  ESTIMATOR_PATH="$ROOT_DIR/.external/lattice-estimator"
fi

if [[ "$WITH_ESTIMATOR" -eq 1 && -z "$ENHANCED_ESTIMATOR_PATH" ]]; then
  git clone --depth=1 https://github.com/identitymapping/enhanced_lattice-estimator.git "$ROOT_DIR/.external/enhanced-lattice-estimator"
  ENHANCED_ESTIMATOR_PATH="$ROOT_DIR/.external/enhanced-lattice-estimator"
fi

if [[ -f "$CONFIG_PATH" && "$FORCE_CONFIG" -ne 1 ]]; then
  echo "Keeping existing config.local.json. Use --force to regenerate it."
else
  EASYLATTICE_SETUP_ROOT="$ROOT_DIR" \
  EASYLATTICE_SETUP_CONFIG="$CONFIG_PATH" \
  EASYLATTICE_SETUP_SAGE="$SAGE_BIN" \
  EASYLATTICE_SETUP_ESTIMATOR="$ESTIMATOR_PATH" \
  EASYLATTICE_SETUP_ENHANCED_ESTIMATOR="$ENHANCED_ESTIMATOR_PATH" \
  "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

config_path = Path(os.environ["EASYLATTICE_SETUP_CONFIG"])
estimator_path = os.environ.get("EASYLATTICE_SETUP_ESTIMATOR") or None
enhanced_estimator_path = os.environ.get("EASYLATTICE_SETUP_ENHANCED_ESTIMATOR") or None
config = {
    "estimator": {
        "sage_binary": os.environ["EASYLATTICE_SETUP_SAGE"],
        "lattice_estimator_path": estimator_path,
        "enhanced_lattice_estimator_path": enhanced_estimator_path,
        "default_timeout_seconds": 16,
        "per_attack_timeout_seconds": 12,
        "remote_url": None,
        "remote_timeout_seconds": 240,
        "remote_poll_interval_seconds": 2,
    },
    "llm": {
        "enabled": False,
        "provider": "openai-compatible",
        "base_url": "http://localhost:11434/v1",
        "model": "local-model",
        "api_key_env": "EASYLATTICE_LLM_API_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "timeout_seconds": 30,
    },
    "scripts": {
        "decrypt_error": [],
        "signature_smoothing": [],
    },
}
config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY
  echo "Wrote config.local.json."
fi

echo "Python: $PYTHON_BIN"
echo "Sage: $SAGE_BIN"
if [[ -n "$ESTIMATOR_PATH" ]]; then
  echo "Standard lattice-estimator: $ESTIMATOR_PATH"
else
  echo "Standard lattice-estimator: not configured; fast-screen mode still works."
fi
if [[ -n "$ENHANCED_ESTIMATOR_PATH" ]]; then
  echo "Enhanced lattice-estimator: $ENHANCED_ESTIMATOR_PATH"
else
  echo "Enhanced lattice-estimator: not configured; enhanced validation is unavailable."
fi

"$PYTHON_BIN" - <<'PY'
from app.agent import recommend_with_agent

result = recommend_with_agent({"targetSecurity": 128, "maxQBits": 24, "useEstimator": False})
candidate = result["recommendation"]
print(f"Smoke test: n={candidate['ring']['n']}, q={candidate['modulus']['q']}")
PY

if [[ "$START_SERVER" -eq 1 ]]; then
  echo "Starting easyLattice at http://$HOST_VALUE:$PORT_VALUE"
  exec env HOST="$HOST_VALUE" PORT="$PORT_VALUE" "$PYTHON_BIN" -m app.server
fi

echo "Setup complete. Start the app with:"
echo "  HOST=$HOST_VALUE PORT=$PORT_VALUE $PYTHON_BIN -m app.server"
