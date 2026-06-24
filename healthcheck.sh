#!/bin/sh
set -eu

SOCKET_PATH="${APP_SOCKET:-/tmp/exapp.sock}"
HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-23000}"

if [ -S "$SOCKET_PATH" ]; then
	python - "$SOCKET_PATH" <<'PY'
import socket
import sys

sock_path = sys.argv[1]

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
    sock.settimeout(5)
    sock.connect(sock_path)
PY
	exit 0
fi

python - "$HOST" "$PORT" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

if host == "0.0.0.0":
    host = "127.0.0.1"

with socket.create_connection((host, port), timeout=5):
    pass
PY
