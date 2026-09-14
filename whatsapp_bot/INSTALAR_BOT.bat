@echo off
title CTO 119 - Instalacion Bot WhatsApp
color 0B
echo.
echo ================================================
echo   CTO 119 - Bot WhatsApp - Instalacion
echo ================================================
echo.

REM Verificar Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js no esta instalado!
    echo.
    echo Por favor descarga Node.js desde:
    echo   https://nodejs.org/es/download
    echo.
    echo Instala la version LTS y vuelve a ejecutar este archivo.
    echo.
    pause
    exit /b 1
)

echo [OK] Node.js encontrado:
node --version
echo.

echo Instalando dependencias (puede tardar 2-3 minutos)...
npm install

if errorlevel 1 (
    echo.
    echo [ERROR] Fallo la instalacion. Verifica tu conexion a internet.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   Instalacion completada!
echo ================================================
echo.
echo Para iniciar el bot ejecuta: INICIAR_BOT.bat
echo.
pause
