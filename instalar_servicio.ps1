# CTO CallCenter — Instalador de Servicio Windows
# Ejecutar como Administrador una sola vez
# Registra el servidor como tarea programada que arranca con Windows

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $AppDir "python\python.exe"
$AppPy = Join-Path $AppDir "app\main.py"
$TaskName = "CTO_CallCenter_Servidor"

# Verificar que python embebido existe
if (-not (Test-Path $PythonExe)) {
    Write-Host "  ERROR: Python no encontrado en $PythonExe" -ForegroundColor Red
    Write-Host "  Ejecuta CTO_CallCenter.bat primero para instalar Python." -ForegroundColor Yellow
    exit 1
}

# Eliminar tarea anterior si existe
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Eliminando tarea anterior..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Crear tarea programada: arranca con Windows, se reinicia si falla
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$AppPy`"" -WorkingDirectory $AppDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "CTO CallCenter v9.0 — Servidor de despacho" `
    -RunLevel Highest

Write-Host ""
Write-Host "  Servicio instalado correctamente." -ForegroundColor Green
Write-Host "  Nombre: $TaskName" -ForegroundColor Gray
Write-Host "  Arranca: Al encender la PC" -ForegroundColor Gray
Write-Host "  Reinicio: Hasta 3 veces si falla" -ForegroundColor Gray
Write-Host ""
Write-Host "  Para iniciar ahora:  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "  Para detener:        Stop-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "  Para eliminar:       Unregister-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "  Para ver estado:     Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host ""

# Preguntar si desea iniciar ahora
$respuesta = Read-Host "  Desea iniciar el servicio ahora? (s/n)"
if ($respuesta -eq 's' -or $respuesta -eq 'S') {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    try {
        $info = Invoke-RestMethod "http://127.0.0.1:5199/api/info" -TimeoutSec 5
        Write-Host "  Servidor corriendo correctamente (puerto 5199)" -ForegroundColor Green
    } catch {
        Write-Host "  Servidor iniciando... puede tardar unos segundos" -ForegroundColor Yellow
    }
}
