#!/usr/bin/env bash
# Watch CPU, temperature and throttling, remembering the worst values.
#
# `top` shows you the moment you happen to be looking at. This keeps a
# running peak, so a one-second spike while you were tapping a phone is
# still on screen afterwards.
#
# Also watches vcgencmd get_throttled, which matters more than CPU% on a
# Pi: once it throttles, frames drop while CPU looks FINE, because the
# clock dropped underneath you. Without this the symptom is baffling.
#
#     ./monitor.sh            # 1s samples until Ctrl-C
#     ./monitor.sh 0.5        # faster sampling, catches shorter spikes
#
# Ctrl-C prints the summary.

set -u
INTERVAL="${1:-1}"
PROC="${PROC:-pi-webrtc}"

NCORES=$(nproc)
CLK_TCK=$(getconf CLK_TCK 2>/dev/null || echo 100)
prev_proc_ticks=0

peak_total=0
peak_proc=0
peak_temp=0
ever_throttled=0
throttle_seen=""
samples=0

# Previous /proc/stat counters, per core.
declare -a prev_idle prev_total

read_cpu() {
    # Fills cur_idle[] / cur_total[]; index 0 is the aggregate.
    local i=0 line
    cur_idle=(); cur_total=()
    while read -r line; do
        case "$line" in
            cpu*)
                set -- $line
                local name=$1; shift
                local idle=$4 total=0 v
                for v in "$@"; do total=$((total + v)); done
                cur_idle[$i]=$idle
                cur_total[$i]=$total
                i=$((i + 1))
                [ $i -gt $NCORES ] && break
                ;;
            *) break ;;
        esac
    done < /proc/stat
}

summary() {
    echo
    echo "================= peak over $samples samples ================="
    printf "  total CPU : %5.1f%%  (of 100%% across %d cores)\n" \
           "$peak_total" "$NCORES"
    printf "  %-9s : %5.1f%%  (of %d00%% -- can exceed 100 when threaded)\n" \
           "$PROC" "$peak_proc" "$NCORES"
    [ "$peak_temp" != "0" ] && printf "  temperature: %5.1f C\n" "$peak_temp"
    if [ "$ever_throttled" = "1" ]; then
        echo
        echo "  THROTTLED during this run: $throttle_seen"
        echo "  The Pi reduced its clock, so frame drops are expected even"
        echo "  where CPU%% looks healthy. Check cooling and the PSU."
    else
        echo "  throttling : none"
    fi
    echo "============================================================"
    exit 0
}
trap summary INT TERM

echo "Sampling every ${INTERVAL}s across $NCORES cores. Ctrl-C for the summary."
echo
printf "%7s  %-26s %8s %7s  %s\n" \
       "total" "per-core %" "$PROC" "peak" "temp"

read_cpu
prev_idle=("${cur_idle[@]}")
prev_total=("${cur_total[@]}")

# Seed the process counter too. Without this the first delta spans the
# process's entire lifetime, reads absurdly high, and that bogus value
# becomes the reported peak.
for pid in $(pgrep -x "$PROC" 2>/dev/null); do
    if [ -r "/proc/$pid/stat" ]; then
        set -- $(cut -d' ' -f14,15 "/proc/$pid/stat" 2>/dev/null)
        prev_proc_ticks=$((prev_proc_ticks + ${1:-0} + ${2:-0}))
    fi
done

while true; do
    sleep "$INTERVAL"
    read_cpu

    # Aggregate (index 0) and per-core deltas.
    bars=""
    total_pct=0
    for i in $(seq 0 "$NCORES"); do
        di=$(( ${cur_idle[$i]:-0} - ${prev_idle[$i]:-0} ))
        dt=$(( ${cur_total[$i]:-0} - ${prev_total[$i]:-0} ))
        [ "$dt" -le 0 ] && dt=1
        pct=$(( (100 * (dt - di)) / dt ))
        if [ "$i" = "0" ]; then
            total_pct=$pct
        else
            # Per-core percentage, with a marker when a core is pegged.
            # One saturated core beside three idle ones means
            # single-thread bound, which is a different problem from
            # being genuinely out of CPU.
            if [ "$pct" -ge 90 ]; then
                bars="$bars$(printf '%4d!' "$pct")"
            else
                bars="$bars$(printf '%4d ' "$pct")"
            fi
        fi
    done
    prev_idle=("${cur_idle[@]}")
    prev_total=("${cur_total[@]}")

    # Target process, as a share of ONE core -- a multithreaded encoder
    # legitimately reads above 100.
    #
    # Computed from /proc/<pid>/stat deltas, NOT `ps -o %cpu`: ps reports
    # the average over the process's whole lifetime, so a long-running
    # encoder that is busy right now still reads low. That understates
    # exactly the spike this script exists to catch.
    proc_ticks=0
    for pid in $(pgrep -x "$PROC" 2>/dev/null); do
        if [ -r "/proc/$pid/stat" ]; then
            set -- $(cut -d' ' -f14,15 "/proc/$pid/stat" 2>/dev/null)
            proc_ticks=$((proc_ticks + ${1:-0} + ${2:-0}))
        fi
    done
    proc_pct=$(awk -v cur="$proc_ticks" -v prev="$prev_proc_ticks" \
                   -v hz="$CLK_TCK" -v dt="$INTERVAL" \
        'BEGIN { d = cur - prev; if (d < 0) d = 0;
                 printf "%.1f", (d / hz) / dt * 100 }')
    prev_proc_ticks=$proc_ticks

    temp=$(vcgencmd measure_temp 2>/dev/null \
           | sed 's/temp=//; s/'"'"'C//')
    if [ -z "$temp" ]; then
        temp=0; temp_s="  -- "      # not a Pi, or vcgencmd unavailable
    else
        temp_s=$(printf '%4.1fC' "$temp")
    fi

    thr=$(vcgencmd get_throttled 2>/dev/null | sed 's/throttled=//')
    if [ -n "$thr" ] && [ "$thr" != "0x0" ]; then
        ever_throttled=1
        throttle_seen="$thr"
    fi

    # Peaks.
    [ "$total_pct" -gt "${peak_total%.*}" ] && peak_total=$total_pct
    awk -v a="$proc_pct" -v b="$peak_proc" 'BEGIN{exit !(a>b)}' \
        && peak_proc=$proc_pct
    awk -v a="$temp" -v b="$peak_temp" 'BEGIN{exit !(a>b)}' \
        && peak_temp=$temp

    samples=$((samples + 1))

    flag=""
    [ "$ever_throttled" = "1" ] && flag="  THROTTLED"
    printf "%6s%%  %-26s %7s%% %6s%%  %s%s\n" \
           "$total_pct" "$bars" "$proc_pct" "$peak_proc" "$temp_s" "$flag"
done
