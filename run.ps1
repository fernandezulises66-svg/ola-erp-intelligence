# Windows (PowerShell):  .\run.ps1
Set-Location $PSScriptRoot
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Creé .env a partir de .env.example: completá tus API keys y volvé a correr .\run.ps1"
    exit 1
}
docker compose up --build -d
Write-Host -NoNewline "Esperando a la API"
for ($i = 0; $i -lt 60; $i++) {
    try {
        $h = Invoke-RestMethod http://localhost:8000/health -TimeoutSec 2
        Write-Host ""; $h | ConvertTo-Json
        Write-Host "API: http://localhost:8000/docs · Phoenix: http://localhost:6006"
        exit 0
    } catch { Write-Host -NoNewline "."; Start-Sleep 2 }
}
Write-Host ""; Write-Host "La API no respondió: revisá 'docker compose logs api'"; exit 1
