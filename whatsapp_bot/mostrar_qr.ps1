param(
    [string]$FileQR = 'qr_cto119.png',
    [double]$Cm = 9
)

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

$dpi = 96
$px = [int]($Cm / 2.54 * $dpi)

$form = New-Object System.Windows.Forms.Form
$form.Text = 'CTO QR'
$form.ClientSize = New-Object System.Drawing.Size($px + 24, $px + 84)
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowInTaskbar = $true
$form.AutoScaleMode = 'None'

$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = "Escanee el QR con WhatsApp del numero +593 99 948 3918 (Configuracion -> Dispositivos vinculados -> Vincular dispositivo)"
$lbl.SetBounds(12, 8, $px, 32)
$lbl.AutoSize = $false
$lbl.Font = New-Object System.Drawing.Font('Segoe UI', 8)
$form.Controls.Add($lbl)

$lblDims = New-Object System.Windows.Forms.Label
$lblDims.Text = "Tamano del QR: $Cm cm x $Cm cm"
$lblDims.SetBounds(12, 42, $px, 16)
$lblDims.AutoSize = $false
$lblDims.Font = New-Object System.Drawing.Font('Segoe UI', 8)
$form.Controls.Add($lblDims)

$pic = New-Object System.Windows.Forms.PictureBox
$pic.SetBounds(12, 62, $px, $px)
$pic.SizeMode = 'Zoom'
$pic.BackColor = [System.Drawing.Color]::White
$form.Controls.Add($pic)

$lastWrite = [datetime]::MinValue
if (Test-Path $FileQR) {
    $lastWrite = (Get-Item $FileQR).LastWriteTimeUtc
    try { $pic.Image = [System.Drawing.Image]::FromFile($FileQR) } catch { }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({
    if (Test-Path $FileQR) {
        $w = (Get-Item $FileQR).LastWriteTimeUtc
        if ($w -ne $lastWrite) {
            $lastWrite = $w
            try {
                if ($pic.Image) { $pic.Image.Dispose() }
                $pic.Image = [System.Drawing.Image]::FromFile($FileQR)
            } catch { }
        }
    }
    $edad = ((Get-Date).ToUniversalTime()) - $lastWrite
    if ($lastWrite -ne [datetime]::MinValue -and $edad.TotalSeconds -gt 100) {
        $timer.Stop()
        $form.Close()
    }
})
$timer.Start()

$form.Add_Shown({ $form.Activate() })
[System.Windows.Forms.Application]::Run($form)