@echo off
rem Сборка NOVA в один EXE (dist\NOVA.exe). Запускать на Windows из любой папки.
setlocal
cd /d "%~dp0\.."
set ROOT=%CD%

rem Prefer the Python 3.13 launcher (py) if installed; fall back to python on PATH.
set "PYTHON_CMD=python"
py -3.13 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.13"
%PYTHON_CMD% --version || goto :fail

echo [1/3] Установка зависимостей...
%PYTHON_CMD% -m pip install --upgrade pip || goto :fail
%PYTHON_CMD% -m pip install -r requirements-build.txt || goto :fail

echo [2/3] Иконка...
if not exist "%ROOT%\assets\icons\nova.ico" %PYTHON_CMD% "%ROOT%\build\make_icon.py" || goto :fail

echo [3/3] PyInstaller...
%PYTHON_CMD% -m PyInstaller --noconfirm --clean --onefile --windowed --name NOVA ^
  --icon "%ROOT%\assets\icons\nova.ico" ^
  --add-data "%ROOT%\assets;assets" ^
  --collect-submodules winrt ^
  --collect-submodules comtypes ^
  --hidden-import pycaw.pycaw ^
  --workpath "%ROOT%\build\pyinstaller" --distpath "%ROOT%\dist" --specpath "%ROOT%\build" ^
  "%ROOT%\main.py" || goto :fail

echo.
echo Готово: %ROOT%\dist\NOVA.exe
exit /b 0

:fail
echo.
echo Сборка не удалась. См. сообщения выше.
exit /b 1
