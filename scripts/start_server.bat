@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
echo [Doc Service] PDF→DOCX starting on :8768
python src\doc_service.py
pause
