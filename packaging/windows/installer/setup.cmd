@echo off
rem Builds TT Research Terminal's own Python 3.12 and libraries inside the install folder.
rem Run by the installer as: setup.cmd <wheel file name>  (output goes to setup.log).
rem Everything stays under this folder, so a tester's own Python or uv is never touched.
setlocal
set "APP=%~dp0"
set "APP=%APP:~0,-1%"
set "VENV=%APP%\venv"
set "UV_PYTHON_INSTALL_DIR=%APP%\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_NO_CONFIG=1"
set "UV_NO_CACHE=1"

if "%~1"=="" (
  echo Usage: setup.cmd ^<wheel^>
  exit /b 2
)

rem A fresh environment on every install and upgrade.
if exist "%VENV%" rmdir /s /q "%VENV%"
if exist "%VENV%" (
  echo Could not replace %VENV%: close TT Research Terminal and run the installer again.
  exit /b 10
)

echo === Python 3.12
"%APP%\uv.exe" venv --python 3.12 "%VENV%" || exit /b 11

echo === Libraries (exact versions, hash-checked)
"%APP%\uv.exe" pip install --python "%VENV%\Scripts\python.exe" --require-hashes -r "%APP%\requirements.txt" || exit /b 12

echo === TT Research Terminal
"%APP%\uv.exe" pip install --python "%VENV%\Scripts\python.exe" --no-deps "%APP%\%~1" || exit /b 13

echo === Check
"%VENV%\Scripts\python.exe" -c "import obb_terminal.app, obb_terminal.alerts.checker, openbb_yfinance, PySide6.QtWidgets, pyqtgraph; print('imports ok')" || exit /b 14

echo === Done
exit /b 0
