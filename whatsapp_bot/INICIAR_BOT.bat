@echo off
title CTO 119 - Bot WhatsApp ACTIVO
color 0A
echo.
echo ================================================
echo   CTO 119 - Bot WhatsApp
echo   Cooperativa de Taxis Occidental 119
echo ================================================
echo.
echo Iniciando bot...
echo.
echo IMPORTANTE:
echo - El codigo QR aparecera en una ventana pequena
echo   de unos 9 cm por lado (no en la consola).
echo - Abre WhatsApp en el celular del numero
echo   +593 99 948 3918
echo - Ve a: Configuracion / Ajustes
echo          Dispositivos vinculados
echo          Vincular dispositivo
echo - Escanea el QR con la camara
echo.
echo El bot recordara la sesion y no pedira QR de nuevo.
echo.
echo Para detener el bot: Ctrl + C
echo.
echo ================================================
echo.

node bot.js

echo.
echo [BOT DETENIDO]
pause
