#!/usr/bin/env bash
set -euo pipefail

cd /mnt/d/code_re/mas-failure-attribution

RUN_ROOT=/home/humenzhou/mas_runs/kodcode_full_batches
LOG_DIR="$RUN_ROOT/logs"
mkdir -p "$LOG_DIR"

if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux is required in WSL. Install it with: sudo apt-get install tmux"
    exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx 'sandbox-fusion-kodcode'; then
    echo "sandbox-fusion-kodcode is not running. Start Docker Desktop and run:"
    echo "docker run -d --rm --name sandbox-fusion-kodcode -p 8080:8080 volcengine/sandbox-fusion:server-20250609"
    exit 1
fi

start_lane() {
    local session="$1"
    local script="$2"
    local log="$3"

    if tmux has-session -t "$session" 2>/dev/null; then
        echo "already running: $session"
        return
    fi

    tmux new-session -d -s "$session" \
        "cd /mnt/d/code_re/mas-failure-attribution && bash $script >> $LOG_DIR/$log 2>&1"
    echo "started: $session -> $LOG_DIR/$log"
}

start_lane kodcode_key1 scripts/run_kodcode_full_batches.sh kodcode_key1_persistent.log
start_lane kodcode_key2 scripts/run_kodcode_batches_key2_0071_0089.sh kodcode_key2_persistent.log
start_lane kodcode_key3 scripts/run_kodcode_b3_key3_0900_1071.sh kodcode_key3_persistent.log
start_lane kodcode_key4 scripts/run_kodcode_b3_key4_1071_1242.sh kodcode_key4_persistent.log
start_lane kodcode_key5 scripts/run_kodcode_b3_key5_1242_1410.sh kodcode_key5_persistent.log

echo
echo "Use 'tmux ls' to inspect sessions."
echo "Use 'bash scripts/kodcode_status.sh' to check progress."
