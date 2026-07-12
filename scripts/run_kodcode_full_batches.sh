#!/usr/bin/env bash
set -u

cd /mnt/d/code_re/mas-failure-attribution

VENV=/home/humenzhou/venvs/mas-gaia-owl/bin/python
RUN_ROOT=/home/humenzhou/mas_runs/kodcode_full_batches
DATASET=/mnt/d/code_re/mas-failure-attribution/dataset/code/kodcode-light-rl-10k-hard.parquet
BATCH_DIR="$RUN_ROOT/batches"
OUT_ROOT="$RUN_ROOT/output"
WS_ROOT="$RUN_ROOT/workspace"

mkdir -p "$BATCH_DIR" "$OUT_ROOT" "$WS_ROOT" "$RUN_ROOT/logs"

"$VENV" - <<PY
from pathlib import Path
import pandas as pd

src = Path("$DATASET")
out = Path("$BATCH_DIR")
out.mkdir(parents=True, exist_ok=True)

df = pd.read_parquet(src)
for i in range(0, len(df), 10):
    end = min(i + 10, len(df))
    path = out / f"kodcode_batch_{i // 10:04d}_{i:04d}_{end:04d}.parquet"
    if not path.exists():
        df.iloc[i:end].to_parquet(path, index=False)

print(f"prepared_rows={len(df)} prepared_batches={(len(df) + 9) // 10}", flush=True)
PY

for batch in "$BATCH_DIR"/kodcode_batch_*.parquet; do
    name=$(basename "$batch" .parquet)
    batch_index=${name#kodcode_batch_}
    batch_index=${batch_index%%_*}
    if (( 10#$batch_index >= 71 )); then
        continue
    fi
    if [[ -f "$OUT_ROOT/$name/kodcode/round_3/eval_kodcode.json" ]]; then
        echo "===== SKIP COMPLETE $name $(date -Is) ====="
        continue
    fi

    echo "===== START $name $(date -Is) ====="
    "$VENV" main.py \
        --dataset "$batch" \
        --backend OWL \
        --workspace "$WS_ROOT/$name" \
        --output "$OUT_ROOT/$name" \
        --max_rounds 3 \
        --skip_existing
    status=$?
    echo "===== END $name status=$status $(date -Is) ====="
done
