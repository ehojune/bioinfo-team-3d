@echo off
setlocal
set "PYTHONUTF8=1"
set "LABHQ_LAUNCHER=.\labhq"
set "LABHQ_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%LABHQ_PYTHON%" (
  echo labhq: .venv is missing. Create it and install labhq first. 1>&2
  exit /b 1
)
"%LABHQ_PYTHON%" -m labhq.cli %*
exit /b %ERRORLEVEL%
