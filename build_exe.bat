@echo off
REM Builds a single-file Windows executable: dist\SmartFileManagerPro.exe
cd /d "%~dp0"
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconsole --onefile --name SmartFileManagerPro --icon icon.ico --add-data "fonts;fonts" app.py
echo.
echo Done. Your app is in dist\SmartFileManagerPro.exe
pause
