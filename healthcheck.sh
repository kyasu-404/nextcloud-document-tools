#!/bin/sh
set -eu

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-23000}"

if [ "$HOST" = "0.0.0.0" ]; then
	HOST="127.0.0.1"
fi

python - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

with socket.create_connection((host, port), timeout=5):
    pass
PY
