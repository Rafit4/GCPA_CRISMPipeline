@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
"%PYTHON%" -m crism_pipeline.gui
if errorlevel 1 (
    echo.
    echo Error al abrir la GUI. Verifica que el entorno este instalado:
    echo   .venv\Scripts\activate
    echo   pip install -e .
    pause
)
endlocal
