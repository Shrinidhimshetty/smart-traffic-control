#!/usr/bin/env bash
# ===========================================================================
#  run_osm.sh -- full OpenStreetMap pipeline (Stage 6).
#  Usage:  bash run_osm.sh [place]
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"
PLACE="${1:-mangaluru}"

echo; echo "[1/6] Importing OpenStreetMap network ($PLACE)..."
if ! python3 src/osm_import.py --place "$PLACE"; then
  echo
  echo "Download failed. Falling back to the offline sample map."
  echo "For a real download, run osmWebWizard.py then:"
  echo "   python3 src/osm_import.py --osm-file path/to/osm_bbox.osm.xml"
  python3 src/make_sample_osm.py
  python3 src/osm_import.py --osm-file osm/maps/osm.osm
fi

echo; echo "[2/6] Writing traffic light programs..."
python3 src/generate_tls.py --map osm

echo; echo "[3/6] Placing detectors..."
python3 src/generate_detectors.py --map osm

echo; echo "[4/6] Generating routes (1-3 minutes)..."
python3 src/osm_routes.py --end 1800

echo; echo "[5/6] Running simulations on the real network..."
python3 src/run_simulation.py --map osm --controller static       --end 1800
python3 src/run_simulation.py --map osm --controller actuated     --end 1800
python3 src/run_simulation.py --map osm --controller max_pressure --end 1800

echo; echo "[6/6] Building comparison..."
python3 src/plot_results.py --map osm

cat <<'MSG'

==========================================================
 DONE. Results:
   results/osm_comparison.csv
   results/plots/osm_comparison_timeseries.png

 Visual demo:
   python3 src/run_simulation.py --map osm --controller max_pressure --gui --colorize
==========================================================
MSG
