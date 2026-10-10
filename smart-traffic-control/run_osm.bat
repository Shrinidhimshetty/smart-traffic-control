@echo off
REM ==========================================================================
REM  run_osm.bat -- full OpenStreetMap pipeline (Stage 6).
REM  Usage:  run_osm.bat            (downloads Mangaluru)
REM          run_osm.bat udupi
REM ==========================================================================
setlocal
set PLACE=%1
if "%PLACE%"=="" set PLACE=mangaluru

echo.
echo [1/6] Importing OpenStreetMap network (%PLACE%)...
python src\osm_import.py --place %PLACE%
if errorlevel 1 goto :offline

:imported
echo.
echo [2/6] Writing traffic light programs...
python src\generate_tls.py --map osm
if errorlevel 1 goto :error

echo.
echo [3/6] Placing detectors...
python src\generate_detectors.py --map osm
if errorlevel 1 goto :error

echo.
echo [4/6] Generating routes (this takes 1-3 minutes)...
python src\osm_routes.py --end 1800
if errorlevel 1 goto :error

echo.
echo [5/6] Running simulations on the real network...
python src\run_simulation.py --map osm --controller static       --end 1800
python src\run_simulation.py --map osm --controller actuated     --end 1800
python src\run_simulation.py --map osm --controller max_pressure --end 1800
if errorlevel 1 goto :error

echo.
echo [6/6] Building comparison...
python src\plot_results.py --map osm
if errorlevel 1 goto :error

echo.
echo ==========================================================
echo  DONE. Results:
echo    results\osm_comparison.csv
echo    results\plots\osm_comparison_timeseries.png
echo.
echo  Visual demo:
echo    python src\run_simulation.py --map osm --controller max_pressure --gui --colorize
echo ==========================================================
goto :eof

:offline
echo.
echo Download failed. Falling back to the offline sample map.
echo For a real download, run osmWebWizard.py and use:
echo    python src\osm_import.py --osm-file path\to\osm_bbox.osm.xml
python src\make_sample_osm.py
python src\osm_import.py --osm-file osm\maps\osm.osm
if errorlevel 1 goto :error
goto :imported

:error
echo.
echo BUILD FAILED. Read the error above.
exit /b 1
