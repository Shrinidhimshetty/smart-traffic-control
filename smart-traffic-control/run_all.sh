#!/usr/bin/env bash
# ===========================================================================
#  run_all.sh -- full Phase 1 pipeline on Linux/macOS.
#  Usage:  bash run_all.sh
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo; echo "[0/6] Checking SUMO installation..."
python3 src/sumo_utils.py

echo; echo "[1/6] Building 3x3 grid networks..."
python3 src/build_network.py --size 3

echo; echo "[2/6] Writing traffic light programs..."
python3 src/generate_tls.py --green 31 --yellow 4

echo; echo "[3/6] Generating vehicle routes (takes 1-2 minutes)..."
python3 src/generate_routes.py --end 1800

echo; echo "[4/6] Placing detectors..."
python3 src/generate_detectors.py

echo; echo "[5/6] Running simulations..."
python3 src/run_simulation.py --controller static   --end 1800
python3 src/run_simulation.py --controller actuated --end 1800

echo; echo "[6/6] Building comparison plots..."
python3 src/plot_results.py

cat <<'MSG'

==========================================================
 DONE. Now look at:
   results/plots/comparison_timeseries.png
   results/comparison.csv

 For the visual demo, run:
   python3 src/run_simulation.py --controller static --gui --colorize
==========================================================
MSG
