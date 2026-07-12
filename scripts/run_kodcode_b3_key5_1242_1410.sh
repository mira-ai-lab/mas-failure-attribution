#!/usr/bin/env bash
set -u

cd /mnt/d/code_re/mas-failure-attribution

VENV=/home/humenzhou/venvs/mas-gaia-owl/bin/python
RUN_ROOT=/home/humenzhou/mas_runs/kodcode_full_batches
DATASET=/mnt/d/code_re/mas-failure-attribution/dataset/code/kodcode-light-rl-10k-hard.parquet
BATCH_DIR="$RUN_ROOT/batches_b3_key5"
OUT_ROOT="$RUN_ROOT/output"
WS_ROOT="$RUN_ROOT/workspace_key5"
ENV_FILE=/mnt/d/code_re/mas-failure-attribution/owl/owl/.env.key5
START_ROW=1242
END_ROW=1410
BATCH_SIZE=3

mkdir -p "$BATCH_DIR" "$OUT_ROOT" "$WS_ROOT" "$RUN_ROOT/logs"

set -a
source "$ENV_FILE"
set +a

"$VENV" - <<PY
from pathlib import Path
import pandas as pd

src = Path("$DATASET")
out = Path("$BATCH_DIR")
out.mkdir(parents=True, exist_ok=True)

df = pd.read_parquet(src)
start = int("$START_ROW")
end_limit = min(int("$END_ROW"), len(df))
batch_size = int("$BATCH_SIZE")
seq = 0
for i in range(start, end_limit, batch_size):
    end = min(i + batch_size, end_limit)
    path = out / f"kodcode_b3_key5_{seq:04d}_{i:04d}_{end:04d}.parquet"
    if not path.exists():
        df.iloc[i:end].to_parquet(path, index=False)
    seq += 1
print(f"prepared_rows={len(df)} key5_rows={start}:{end_limit} prepared_batches={seq}", flush=True)
PY

for batch in "$BATCH_DIR"/kodcode_b3_key5_*.parquet; do
    name=$(basename "$batch" .parquet)
    if [[ -f "$OUT_ROOT/$name/kodcode/round_3/eval_kodcode.json" ]]; then
        echo "===== KEY5 SKIP COMPLETE $name $(date -Is) ====="
        continue
    fi

    echo "===== KEY5 START $name $(date -Is) ====="
    "$VENV" main.py \
        --dataset "$batch" \
        --backend OWL \
        --workspace "$WS_ROOT/$name" \
        --output "$OUT_ROOT/$name" \
        --max_rounds 3 \
        --skip_existing
    status=$?
    echo "===== KEY5 END $name status=$status $(date -Is) ====="
done
