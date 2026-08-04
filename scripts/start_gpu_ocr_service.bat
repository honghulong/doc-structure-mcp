@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
if "%GPU_OCR_PORT%"=="" set GPU_OCR_PORT=8769
echo [GPU OCR] starting on :%GPU_OCR_PORT%
python src\gpu_ocr_service.py
