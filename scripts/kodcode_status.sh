#!/usr/bin/env bash
set -euo pipefail

RUN_ROOT=/home/humenzhou/mas_runs/kodcode_full_batches
OUT_ROOT="$RUN_ROOT/output"

echo "== tmux =="
tmux ls 2>/dev/null || echo "no tmux sessions"

echo
echo "== running batches =="
ps aux \
  | grep 'main.py --dataset /home/humenzhou/mas_runs/kodcode_full_batches' \
  | grep -v grep \
  | sed -E 's#.*--dataset ([^ ]+).*--output ([^ ]+).*#dataset=\1 output=\2#' \
  || true

echo
echo "== counts =="
batch_dirs=$(find "$OUT_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
final_results=$(find "$OUT_ROOT" -path '*/final_results/*.json' -type f 2>/dev/null | wc -l)
complete_batches=$(find "$OUT_ROOT" -path '*/kodcode/round_3/eval_kodcode.json' -type f 2>/dev/null | sed -E 's#/kodcode/round_3/eval_kodcode.json$##' | sort -u | wc -l)

echo "output_batch_dirs=$batch_dirs"
echo "complete_batches_with_round3_eval=$complete_batches"
echo "final_results=$final_results"

echo
echo "== latest batch log markers =="
for log in \
    kodcode_key1_persistent.log \
    kodcode_key2_persistent.log \
    kodcode_key3_persistent.log \
    kodcode_key4_persistent.log \
    kodcode_key5_persistent.log
do
    path="$RUN_ROOT/logs/$log"
    echo "-- $log --"
    if [[ -f "$path" ]]; then
        grep -E '===== .* (START|END|SKIP COMPLETE)' "$path" | tail -n 5 || true
    else
        echo "missing"
    fi
done
