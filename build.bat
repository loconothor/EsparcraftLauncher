@echo off
title EsparcraftLauncher - Build EXE
echo ================================
echo  EsparcraftLauncher
echo ================================
echo.

REM -------- CONFIGURACION --------
set PY_VERSION=3.11
set APP_NAME=EsparcraftLauncher
set MAIN_FILE=launcher.py
set ICON_FILE=icon.ico
REM --------------------------------

echo [1/4] Verificando Python %PY_VERSION%...
py -%PY_VERSION% --version >nul 2>&1 || (
    echo ERROR: Python %PY_VERSION% no esta instalado.
    pause
    exit /b 1
)


echo.
echo [2/3] Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del "%APP_NAME%.spec"

echo.
echo [3/3] Compilando EXE...
py -%PY_VERSION% -m PyInstaller ^
 --onefile ^
 --windowed ^
 --collect-all customtkinter ^
 --hidden-import=customtkinter ^
 --name "%APP_NAME%" ^
 %MAIN_FILE%

echo.
echo ================================
echo  COMPILACION FINALIZADA
echo ================================
echo dist\%APP_NAME%.exe
pause

