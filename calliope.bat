@echo off
setlocal

if not exist venv (
  python -m venv venv
)

call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
start "" pythonw src/main.py
exit /b 0
