
# CTO CallCenter v11.0 — Instalador y lanzador
# Doble clic en CTO_CallCenter.bat (ejecuta este script automáticamente)

$ErrorActionPreference = "Continue"
$AppDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonDir = Join-Path $AppDir "python"
$PythonExe = Join-Path $PythonDir "python.exe"
$AppPy     = Join-Path $AppDir "app\main.py"

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "    CTO CallCenter v11.0" -ForegroundColor White
Write-Host "    Cooperativa Taxis Occidental 119" -ForegroundColor Gray
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

# ── PASO 1: Instalar Python embebido si no existe (solo primera vez) ───────
if (-not (Test-Path $PythonExe)) {
    Write-Host "  [1/3] Descargando Python (solo la primera vez, ~2 min)..." -ForegroundColor Yellow

    $PythonZip = Join-Path $AppDir "python_embed.zip"
    $PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip -UseBasicParsing
        Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force
        Remove-Item $PythonZip -Force
        Write-Host "  Python instalado correctamente." -ForegroundColor Green
    } catch {
        Write-Host "  ERROR descargando Python: $_" -ForegroundColor Red
        Write-Host "  Verifica tu conexion a internet e intenta de nuevo." -ForegroundColor Red
        Read-Host "Presiona Enter para salir"
        exit 1
    }

    # Habilitar import de site-packages (necesario para pip funcione)
    $PthFile = Get-ChildItem $PythonDir -Filter "python3*._pth" | Select-Object -First 1
    if ($PthFile) {
        $content = Get-Content $PthFile.FullName -Raw
        $content = $content -replace "#import site", "import site"
        Set-Content $PthFile.FullName $content -NoNewline
    }

    # Instalar pip
    Write-Host "  [2/3] Instalando pip..." -ForegroundColor Yellow
    $GetPipUrl  = "https://bootstrap.pypa.io/get-pip.py"
    $GetPipFile = Join-Path $AppDir "get-pip.py"
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipFile -UseBasicParsing
    & $PythonExe $GetPipFile --quiet
    Remove-Item $GetPipFile -Force

    # Instalar Flask + openpyxl (Excel)
    Write-Host "  [3/3] Instalando Flask y modulo de Excel..." -ForegroundColor Yellow
    & $PythonExe -m pip install flask openpyxl --quiet

    # Verificar que todo quedó bien
    $depsOK = & $PythonExe -c "import flask, openpyxl; print('OK')" 2>&1
    if ($depsOK -notmatch 'OK') {
        Write-Host ""
        Write-Host "  ERROR: Las dependencias no se instalaron correctamente." -ForegroundColor Red
        Write-Host "  Detalle: $depsOK" -ForegroundColor DarkGray
        Write-Host "  Verifica tu conexion a internet." -ForegroundColor Red
        Read-Host "  Presiona Enter para salir"
        exit 1
    }

    Write-Host ""
    Write-Host "  Configuracion completada." -ForegroundColor Green
    Write-Host ""
} else {
    # ── Python ya existe: verificar que tenga TODAS las dependencias ─────
    Write-Host "  Verificando dependencias..." -ForegroundColor Gray

    # Asegurar que import site esté habilitado en el ._pth
    $PthFile = Get-ChildItem $PythonDir -Filter "python3*._pth" | Select-Object -First 1
    if ($PthFile) {
        $content = Get-Content $PthFile.FullName -Raw
        if ($content -match '#import site') {
            $content = $content -replace "#import site", "import site"
            Set-Content $PthFile.FullName $content -NoNewline
            Write-Host "  Python ._pth corregido." -ForegroundColor Gray
        }
    }

    # Verificar flask + openpyxl de una sola pasada
    $depsOK = & $PythonExe -c "import flask, openpyxl; print('OK')" 2>&1
    if ($depsOK -notmatch 'OK') {
        Write-Host "  Faltan modulos — instalando..." -ForegroundColor Yellow
        & $PythonExe -m pip install flask openpyxl --quiet 2>&1 | Out-Null
        # Re-verificar después de instalar
        $depsOK2 = & $PythonExe -c "import flask, openpyxl; print('OK')" 2>&1
        if ($depsOK2 -notmatch 'OK') {
            Write-Host ""
            Write-Host "  ERROR: No se pudieron instalar las dependencias necesarias." -ForegroundColor Red
            Write-Host "  Verifica tu conexion a internet y vuelve a ejecutar." -ForegroundColor Red
            Write-Host "  Detalle: flask=$depsOK2" -ForegroundColor DarkGray
            Write-Host ""
            Read-Host "  Presiona Enter para salir"
            exit 1
        }
        Write-Host "  Dependencias instaladas correctamente." -ForegroundColor Green
    }
}

# ── PASO 2: Abrir puerto 5199 en el Firewall de Windows (multi-operadora) ──
# Necesario para que otras PC de la central y celulares de conductores conecten
$ruleName = "CTO CallCenter (puerto 5199)"
$ruleExists = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $ruleExists) {
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort 5199 -Action Allow -ErrorAction Stop | Out-Null
        Write-Host "  Firewall configurado para red local." -ForegroundColor Green
    } catch {
        Write-Host "  Aviso: no se pudo configurar el firewall automaticamente." -ForegroundColor Yellow
        Write-Host "  (Puede requerir ejecutar como Administrador la primera vez)" -ForegroundColor Yellow
    }
}

# ── PASO 3: Arrancar el sistema ─────────────────────────────────────────────
# main.py hace TODO: arranca el servidor, espera que responda, y abre la ventana.
# El script PS1 NO abre su propia ventana para evitar duplicados.
Write-Host "  Iniciando CTO CallCenter..." -ForegroundColor White
Write-Host ""

& $PythonExe $AppPy

Write-Host ""
Write-Host "  Sistema detenido." -ForegroundColor Gray
Read-Host "Presiona Enter para cerrar esta ventana"
