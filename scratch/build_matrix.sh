#!/bin/bash
set -e

echo "=== 1. Building clean Golden 16K (no telemetry) ==="
rm -rf build-golden-notel
idf.py -B build-golden-notel -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.golden_notel" build

echo "=== 2. Building clean Golden 8K (no telemetry) ==="
rm -rf build-golden-8k-notel
idf.py -B build-golden-8k-notel -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.golden_8k_notel" build

echo "=== 3. Building clean Golden 32K (no telemetry) ==="
rm -rf build-golden-32k-notel
idf.py -B build-golden-32k-notel -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.golden_32k_notel" build

echo "=== 4. Building clean Two-Stage 40->40 Interleaved Phase5 ==="
rm -rf build-interleaved40-notel
idf.py -B build-interleaved40-notel -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.interleaved40_notel" build

echo "=== ALL BUILDS COMPLETE SUCCESSFULLY! ==="
