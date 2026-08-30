@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
"%PYTHON%" -m crism_pipeline qgis-project --input "%CD%\data\maps" --kind browse
if errorlevel 1 (
    echo.
    echo No se pudo crear el proyecto QGIS.
    pause
    exit /b 1
)

if exist "%CD%\data\maps\caves_grupos.qgz" (
    start "" "%CD%\data\maps\caves_grupos.qgz"
)
endlocal
