#!/bin/zsh
cd "${0:A:h}"
if curl --silent --fail --max-time 1 "http://127.0.0.1:8766/api/state" >/dev/null; then
  open "http://127.0.0.1:8766"
  exit 0
fi
/usr/bin/python3 -m binance_trader &
trader_pid=$!
trap 'kill "$trader_pid" 2>/dev/null' EXIT INT TERM
for attempt in {1..15}; do
  if curl --silent --fail --max-time 1 "http://127.0.0.1:8766/api/state" >/dev/null; then
    open "http://127.0.0.1:8766"
    break
  fi
  kill -0 "$trader_pid" 2>/dev/null || exit 1
  sleep 1
done
wait "$trader_pid"
