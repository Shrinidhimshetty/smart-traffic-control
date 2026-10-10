@echo off
REM ==========================================================================
REM  run_all.bat -- full Phase 1 pipeline on Windows.
REM  Usage:  double-click, or from cmd:  run_all.bat
REM ==========================================================================
setlocal

echo.
echo [0/6] Checking SUMO installation...
python src\sumo_utils.py
if errorlevel 1 goto :error

echo.
echo [1/6] Building 3x3 grid networks...
python src\build_network.py --size 3
if errorlevel 1 goto :error

echo.
echo [2/6] Writing traffic light programs...
python src\generate_tls.py --green 31 --yellow 4
if errorlevel 1 goto :error

echo.
echo [3/6] Generating vehicle routes (this takes 1-2 minutes)...
python src\generate_routes.py --end 1800
if errorlevel 1 goto :error

echo.
echo [4/6] Placing detectors...
python src\generate_detectors.py
if errorlevel 1 goto :error

echo.
echo [5/6] Running simulations (fixed-timer, then actuated)...
python src\run_simulation.py --controller static   --end 1800
python src\run_simulation.py --controller actuated --end 1800
if errorlevel 1 goto :error

echo.
echo [6/6] Building comparison plots...
python src\plot_results.py
if errorlevel 1 goto :error

echo.
echo ==========================================================
echo  DONE. Now look at:
echo    results\plots\comparison_timeseries.png
echo    results\comparison.csv
echo.
echo  For the visual demo, run:
echo    python src\run_simulation.py --controller static --gui --colorize
echo ==========================================================
goto :eof

:error
echo.
echo BUILD FAILED. Read the error above.
echo Most common cause: SUMO not installed or SUMO_HOME not set.
echo See README.md section "Installing SUMO on Windows".
exit /b 1
