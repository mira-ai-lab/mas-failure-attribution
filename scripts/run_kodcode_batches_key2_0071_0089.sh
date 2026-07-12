#!/usr/bin/env bash
set -u

cd /mnt/d/code_re/mas-failure-attribution

VENV=/home/humenzhou/venvs/mas-gaia-owl/bin/python
RUN_ROOT=/home/humenzhou/mas_runs/kodcode_full_batches
BATCH_DIR="$RUN_ROOT/batches"
OUT_ROOT="$RUN_ROOT/output"
WS_ROOT="$RUN_ROOT/workspace_key2"
ENV_FILE=/mnt/d/code_re/mas-failure-attribution/owl/owl/.env.key2

mkdir -p "$BATCH_DIR" "$OUT_ROOT" "$WS_ROOT" "$RUN_ROOT/logs"

set -a
source "$ENV_FILE"
set +a

for batch in "$BATCH_DIR"/kodcode_batch_*.parquet; do
    name=$(basename "$batch" .parquet)
    batch_index=${name#kodcode_batch_}
    batch_index=${batch_index%%_*}
    if (( 10#$batch_index < 71 || 10#$batch_index >= 90 )); then
        continue
    fi
    if [[ -f "$OUT_ROOT/$name/kodcode/round_3/eval_kodcode.json" ]]; then
        echo "===== KEY2 SKIP COMPLETE $name $(date -Is) ====="
        continue
    fi

    echo "===== KEY2 START $name $(date -Is) ====="
    "$VENV" main.py \
        --dataset "$batch" \
        --backend OWL \
        --workspace "$WS_ROOT/$name" \
        --output "$OUT_ROOT/$name" \
        --max_rounds 3 \
        --skip_existing
    status=$?
    echo "===== KEY2 END $name status=$status $(date -Is) ====="
done
