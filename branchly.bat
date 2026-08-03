@echo off
rem Starts Branchly, preferring the project's own virtual environment.
setlocal

set HERE=%~dp0
set VENV_PYTHON=%HERE%.venv\Scripts\pythonw.exe
if not exist "%VENV_PYTHON%" set VENV_PYTHON=%HERE%.venv\Scripts\python.exe

if exist "%VENV_PYTHON%" (
    start "" "%VENV_PYTHON%" "%HERE%run.py" %*
    goto :eof
)

echo No virtual environment found. Run: python install_dependencies.py 1>&2
python "%HERE%run.py" %*
