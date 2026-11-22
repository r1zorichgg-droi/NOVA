@echo off
setlocal
cd /d "%~dp0"

if not exist release mkdir release
rem Remove only generated build outputs so an older setup cannot be uploaded by mistake.
del /q release\NOVA_Setup_*.exe release\NOVA.exe release\latest.json 2>nul

echo [1/3] Building NOVA.exe...
call build\build_exe.bat
if errorlevel 1 goto :fail

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 was not found.
  echo Install Inno Setup 6 and run build_installer.bat again.
  start https://jrsoftware.org/isinfo.php
  exit /b 2
)

echo [2/3] Synchronizing version and compiling installer with Inno Setup 6...
powershell -NoProfile -ExecutionPolicy Bypass -File build\sync_version.ps1
if errorlevel 1 goto :fail
"%ISCC%" installer\NOVA.iss
if errorlevel 1 goto :fail

if exist dist\NOVA.exe copy /Y dist\NOVA.exe release\NOVA.exe >nul


echo [3/3] Creating update manifest...
powershell -NoProfile -ExecutionPolicy Bypass -File build\make_manifest.ps1
if errorlevel 1 goto :fail

echo.
echo Build complete. Files are in %CD%\release\
echo   NOVA.exe
echo   NOVA_Setup_[version from config.py].exe
echo   latest.json
echo.
exit /b 0

:fail
echo.
echo Build failed. Review the messages above.
exit /b 1
