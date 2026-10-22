# Script de arranque para Windows Server / Windows con Docker Desktop
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Iniciando LoRaWAN Monitor Backend en nuevo servidor   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Asegurar carpeta de datos
$dataPath = if (Test-Path "monitor\backend") { "monitor\backend\data" } else { "backend\data" }
if (-not (Test-Path $dataPath)) {
    New-Item -ItemType Directory -Path $dataPath -Force | Out-Null
}

# 2. Crear red docker si no existe
$netName = "chirpstack-docker-master_default"
$exists = docker network ls --filter name=^$netName$ --format "{{.Name}}"
if (-not $exists) {
    Write-Host "Configurando red Docker: $netName..." -ForegroundColor Yellow
    docker network create $netName | Out-Null
}

# 3. Levantar contenedores
Write-Host "Construyendo imágenes y levantando servicios..." -ForegroundColor Green
docker compose up -d --build

Write-Host "`nBackend iniciado con éxito!" -ForegroundColor Green
Write-Host " - API REST: http://localhost:8000" -ForegroundColor Green
Write-Host " - Swagger UI: http://localhost:8000/docs" -ForegroundColor Green
Write-Host " - Healthcheck: http://localhost:8000/api/dashboard" -ForegroundColor Green
