#!/usr/bin/env bash
# C5VRX by Twotoz and the C5VRX contributors: TANG9K_DUAL bitstream build
# with the open-source Gowin flow (Yosys, nextpnr-himbaechel, Apicula).
#   pip install apycula yowasp-yosys yowasp-nextpnr-himbaechel-gowin
#   hardware_experiments/tang9k_dual/fpga/build.sh dac        Tang Nano 9K, two C5s, DAC
#   hardware_experiments/tang9k_dual/fpga/build.sh 20k        Tang Nano 20K, Tripleversity
#                                                             (three C5s, 3-bit I/Q; fit stage:
#                                                             code on the LCD's green lines)
#   hardware_experiments/tang9k_dual/fpga/build.sh 1k-single  Tang Nano 1K, one C5, DAC
#   hardware_experiments/tang9k_dual/fpga/build.sh 1k         Tang Nano 1K, two C5s, lite
#                                                             combiner: does NOT fit (121% LUT)
# (lcd and hdmi stop with "not implemented yet".) Writes build/<name>.fs;
# flash with openFPGALoader -b tangnano9k / -b tangnano20k / -b tangnano1k.
set -euo pipefail
OUT="${1:-dac}"
HERE="$(cd "$(dirname "$0")" && pwd)"
R="$HERE/rtl"
B="$HERE/build"; mkdir -p "$B"
YOSYS="${YOSYS:-$(command -v yowasp-yosys || command -v yosys)}"
NEXTPNR="${NEXTPNR:-$(command -v yowasp-nextpnr-himbaechel-gowin || command -v nextpnr-himbaechel)}"
case "$OUT" in
    dac|lcd|hdmi)
        DEF="-DOUT_$(echo "$OUT" | tr a-z A-Z)"; TOP=top
        SRC="$R/top.v $R/align_b.v $R/mrc_core.v $R/cordic_angle.v $R/cordic_iter.v $R/mul_dsp.v $R/channel_buttons.v $R/channel_link_tx.v"
        DEV=GW1NR-LV9QN88PC6/I5; FAM=GW1N-9C; CST="$HERE/tang_nano_9k.cst"; NAME="tang9k_dual_$OUT" ;;
    20k)
        DEF=""; TOP=top3_20k
        SRC="$R/top3_20k.v $R/align_b.v $R/mrc3_core.v $R/cordic_angle.v $R/cordic_iter.v $R/mul_dsp.v $R/channel_buttons.v $R/channel_link_tx.v"
        DEV=GW2AR-LV18QN88C8/I7; FAM=GW2A-18C; CST="$HERE/tang_nano_20k.cst"; NAME="tang20k_tripleversity" ;;
    1k|1k-single)
        DEF=""; [ "$OUT" = 1k-single ] && DEF="-DSINGLE"; TOP=top_1k
        SRC="$R/top_1k.v $R/align_lite.v $R/lite_core.v"
        DEV=GW1NZ-LV1QN48C6/I5; FAM=GW1NZ-1; CST="$HERE/tang_nano_1k.cst"; NAME="tang1k_$OUT" ;;
    *) echo "usage: $0 dac|lcd|hdmi|20k|1k|1k-single" >&2; exit 2 ;;
esac
EXTRA=""; [ "$FAM" = GW1NZ-1 ] && EXTRA="--vopt sspi_as_gpio"
SFAM=""; [ "$FAM" = GW2A-18C ] && SFAM="-family gw2a"
"$YOSYS" -q -l "$B/yosys_$NAME.log" -p "
  read_verilog $DEF -I$R $SRC;
  synth_gowin $SFAM ${SYNTH_OPTS:--nodsp} -top $TOP -json $B/$NAME.json"
"$NEXTPNR" -q -l "$B/nextpnr_$NAME.log" --json "$B/$NAME.json" --write "$B/${NAME}_pnr.json" \
  --device "$DEV" --vopt family="$FAM" --vopt cst="$CST" $EXTRA --freq 40 --report "$B/report_$NAME.json"
gowin_pack -d "$FAM" -o "$B/$NAME.fs" "$B/${NAME}_pnr.json"
echo "bitstream: $B/$NAME.fs"
