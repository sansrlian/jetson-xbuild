#!/bin/sh
# Kernel tracing with plain ftrace (tracefs), run as root on the board by `jxb trace kernel`. Needs nothing beyond
# the stock JetPack kernel.
#   jxb-ftrace.sh record SECONDS OUTFILE EVENT...   trace for SECONDS (0: until SIGINT), write the ftrace text to OUTFILE
#   jxb-ftrace.sh stream EVENT...                   stream trace_pipe until SIGINT (live view)
# EVENT is a tracefs event (sched/sched_switch) or a whole subsystem (irq). The text output opens in Perfetto
# (ui.perfetto.dev) as a systrace file. The previous tracing state is restored on exit: boards are shared.
set -eu
T=${JXB_TRACEFS:-/sys/kernel/tracing}
# older kernels: tracefs only below debugfs (both are unreadable without root, then keep the default)
if [ ! -e "$T/events" ] && [ -e /sys/kernel/debug/tracing/events ]; then T=/sys/kernel/debug/tracing; fi
if [ ! -w "$T/tracing_on" ]; then
    echo "jxb-ftrace: $T is not writable (needs root)" >&2
    exit 77
fi

mode=${1:?usage: jxb-ftrace.sh record SECONDS OUTFILE EVENT... | stream EVENT...}
shift

old_on=$(cat "$T/tracing_on")
old_buf=$(cut -d' ' -f1 "$T/buffer_size_kb")
old_tracer=$(cat "$T/current_tracer")
old_events=$(cat "$T/set_event")
old_tgid=$(cat "$T/options/record-tgid" 2>/dev/null || true)

restore() {
    echo 0 > "$T/tracing_on"
    echo > "$T/set_event"
    for e in $old_events; do echo "$e" >> "$T/set_event"; done
    echo "$old_tracer" > "$T/current_tracer"
    echo "$old_buf" > "$T/buffer_size_kb"
    if [ -n "$old_tgid" ]; then echo "$old_tgid" > "$T/options/record-tgid"; fi
    echo > "$T/trace"
    echo "$old_on" > "$T/tracing_on"
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

start() {
    echo 0 > "$T/tracing_on"
    echo nop > "$T/current_tracer"
    echo "${JXB_FTRACE_BUFFER_KB:-8192}" > "$T/buffer_size_kb"
    # thread -> process mapping in the output (ROS nodes are processes with many threads)
    if [ -n "$old_tgid" ]; then echo 1 > "$T/options/record-tgid"; fi
    echo > "$T/set_event"
    for e in "$@"; do
        case "$e" in
            */*) e=$(echo "$e" | tr / :) ;;
            *) e="$e:*" ;;
        esac
        echo "$e" >> "$T/set_event" 2>/dev/null || echo "jxb-ftrace: event $e not available, skipped" >&2
    done
    echo > "$T/trace"
    echo 1 > "$T/tracing_on"
}

case "$mode" in
    record)
        secs=${1:?SECONDS}
        out=${2:?OUTFILE}
        shift 2
        start "$@"
        trap ':' INT TERM
        if [ "$secs" -gt 0 ]; then
            echo "jxb-ftrace: tracing $* for ${secs} s" >&2
            sleep "$secs" &
        else
            echo "jxb-ftrace: tracing $* until Ctrl-C" >&2
            sleep 2147483647 &
        fi
        wait $! || kill $! 2>/dev/null || true
        echo 0 > "$T/tracing_on"
        cat "$T/trace" > "$out"
        echo "jxb-ftrace: $(grep -vc '^#' "$out") events -> $out" >&2
        ;;
    stream)
        start "$@"
        cat "$T/trace_pipe"
        ;;
    *)
        echo "jxb-ftrace: unknown mode $mode" >&2
        exit 2
        ;;
esac
