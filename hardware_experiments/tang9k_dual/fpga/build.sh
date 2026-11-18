#!/usr/bin/env bash
# C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL bitstream build
# with the open-source Gowin flow (Yosys, nextpnr-himbaechel, Apicula).
#   pip install apycula yowasp-yosys yowasp-nextpnr-himbaechel-gowin
#   hardware_experiments/tang9k_dual/fpga/build.sh dac
# Writes build/tang9k_dual_<out>.fs; flash with openFPGALoader -b tangnano9k.
set -euo pipefail
OUT="${1:-dac}"
case "$OUT" in dac) DEF=OUT_DAC ;; lcd) DEF=OUT_LCD ;; hdmi) DEF=OUT_HDMI ;;
    *) echo "usage: $0 dac|lcd|hdmi" >&2; exit 2 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
B="$HERE/build"; mkdir -p "$B"
YOSYS="${YOSYS:-$(command -v yowasp-yosys || command -v yosys)}"
NEXTPNR="${NEXTPNR:-$(command -v yowasp-nextpnr-himbaechel-gowin || command -v nextpnr-himbaechel)}"
"$YOSYS" -q -l "$B/yosys_$OUT.log" -p "
  read_verilog -D$DEF -I$HERE/rtl $HERE/rtl/top.v $HERE/rtl/align_b.v $HERE/rtl/mrc_core.v $HERE/rtl/cordic_angle.v $HERE/rtl/cordic_iter.v $HERE/rtl/mul_dsp.v;
  synth_gowin ${SYNTH_OPTS:--nodsp} -top top -json $B/tang9k_dual_$OUT.json"
"$NEXTPNR" -q -l "$B/nextpnr_$OUT.log" --json "$B/tang9k_dual_$OUT.json" --write "$B/tang9k_dual_${OUT}_pnr.json" \
  --device GW1NR-LV9QN88PC6/I5 --vopt family=GW1N-9C --vopt cst="$HERE/tang_nano_9k.cst" \
  --freq 40 --report "$B/report_$OUT.json"
gowin_pack -d GW1N-9C -o "$B/tang9k_dual_$OUT.fs" "$B/tang9k_dual_${OUT}_pnr.json"
echo "bitstream: $B/tang9k_dual_$OUT.fs"
