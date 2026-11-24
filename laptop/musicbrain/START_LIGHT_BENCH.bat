@echo off
title ledctl music brain
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% -c "import numpy, pyaudiowpatch, onnxruntime, serial, bleak" 2>nul || (
  echo Installing numpy, pyaudiowpatch and onnxruntime, one time only...
  %PY% -m pip install --user --disable-pip-version-check numpy pyaudiowpatch onnxruntime pyserial bleak
)
echo.
echo Starting the music brain. Play something on Spotify, the bench opens at http://localhost:8766
echo Close this window to stop.
%PY% brain.py %*
pause
