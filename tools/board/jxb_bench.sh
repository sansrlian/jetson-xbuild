#!/usr/bin/env bash
# Inference benchmark matrix on the board, run by `jxb trace bench` (inside the measurement lock): every model with
# ONNX Runtime (CPU, CUDA EP, TensorRT EP fp32/fp16, INT8 for Q/DQ models) and as native TensorRT engine
# (trtexec fp16, INT8 for Q/DQ models). Results: OUTDIR/results.jsonl (one JSON object per run), raw logs in
# OUTDIR/logs; trtexec's engines go to ENGINE_DIR (kept, e.g. for the SAM node's `model:=<...>.engine`).
#   jxb_bench.sh OUTDIR ENGINE_DIR ORT_BENCH_DIR TRT_CACHE MODEL.onnx...
# Env: JXB_BENCH_BACKENDS (default: all of the list below), JXB_BENCH_ITERS (200), JXB_BENCH_CPU_ITERS (10)
set -uo pipefail
out=$1 engines=$2 ob=$3 cache=$4
shift 4
backends=${JXB_BENCH_BACKENDS:-"ort-cpu ort-cuda ort-trt ort-trt-fp16 ort-trt-int8 trtexec-fp32 trtexec-fp16 trtexec-int8"}
iters=${JXB_BENCH_ITERS:-200}
cpu_iters=${JXB_BENCH_CPU_ITERS:-10}
mkdir -p "$out/logs" "$engines" "$cache"
export LD_LIBRARY_PATH="$ob/lib:${LD_LIBRARY_PATH:-}"
trtexec=$(command -v trtexec || echo /usr/src/tensorrt/bin/trtexec)

trtexec_json() {   # trtexec log -> JSON fields (latency incl. copies, GPU compute time, throughput)
    awk '
        function v(s, key,   m) { if (match(s, key " = [0-9.]+")) { m = substr(s, RSTART, RLENGTH); sub(/.* = /, "", m); return m } return "null" }
        /\] Latency: min/          { lat = sprintf("\"p50_ms\":%s,\"p90_ms\":%s,\"p99_ms\":%s,\"mean_ms\":%s", v($0, "median"), v($0, "percentile\\(90%\\)"), v($0, "percentile\\(99%\\)"), v($0, "mean")) }
        /\] GPU Compute Time: min/ { gpu = sprintf("\"gpu_p50_ms\":%s,\"gpu_mean_ms\":%s", v($0, "median"), v($0, "mean")) }
        /\] Throughput:/           { match($0, /Throughput: [0-9.]+/); fps = substr($0, RSTART + 12, RLENGTH - 12) }
        END { if (lat == "") exit 1; printf "%s,%s,\"fps\":%s", lat, (gpu == "" ? "\"gpu_p50_ms\":null" : gpu), (fps == "" ? "null" : fps) }
    ' "$1"
}

for m in "$@"; do
    name=$(basename "$m" .onnx)
    qdq=0
    grep -q QuantizeLinear "$m" && qdq=1   # Q/DQ nodes: explicitly quantized (QAT or PTQ) model
    for b in $backends; do
        log="$out/logs/$name.$b.log"
        case $b in
            ort-cpu)       args=(--ep cpu --iters "$cpu_iters" --warmup 2) ;;
            ort-cuda)      args=(--ep cuda --iters "$iters" --warmup 20) ;;
            ort-trt)       args=(--ep tensorrt --iters "$iters" --warmup 20 --cache "$cache") ;;
            ort-trt-fp16)  args=(--ep tensorrt --fp16 --iters "$iters" --warmup 20 --cache "$cache") ;;
            ort-trt-int8)  [[ $qdq == 1 ]] || continue; args=(--ep tensorrt --fp16 --int8 --iters "$iters" --warmup 20 --cache "$cache") ;;
            trtexec-fp32)  args=() ;;
            trtexec-fp16)  args=(--fp16) ;;
            trtexec-int8)  [[ $qdq == 1 ]] || continue; args=(--fp16 --int8) ;;
            *) echo "unknown backend $b" >&2; continue ;;
        esac
        echo "[jxb_bench] $name $b" >&2
        t0=$(date +%s.%N)
        if [[ $b == ort-* ]]; then
            "$ob/bin/ort_bench" "$m" "${args[@]}" --json > "$log" 2>&1
            rc=$?
            json=$(grep '^{' "$log" | tail -1)
            body=${json#\{}
            body=${body%\}}
        else
            engine="$engines/$name.${b#trtexec-}.engine"
            "$trtexec" --onnx="$m" "${args[@]}" --saveEngine="$engine" --timingCacheFile="$cache/trtexec.timing" \
                --iterations="$iters" --warmUp=500 --percentile=90,99 > "$log" 2>&1
            rc=$?
            body=$(trtexec_json "$log") || body=""
            [[ -n $body ]] && body="$body,\"engine\":\"$engine\""
        fi
        secs=$(echo "$(date +%s.%N) - $t0" | bc)
        if [[ $rc == 0 && -n $body ]]; then
            echo "{\"model\":\"$name\",\"backend\":\"$b\",\"qdq\":$qdq,\"wall_s\":$secs,$body}" >> "$out/results.jsonl"
        else
            echo "{\"model\":\"$name\",\"backend\":\"$b\",\"qdq\":$qdq,\"wall_s\":$secs,\"error\":\"rc=$rc, see logs/$name.$b.log\"}" >> "$out/results.jsonl"
        fi
    done
done
