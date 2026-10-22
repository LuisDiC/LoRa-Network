#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "   Iniciando LoRaWAN Monitor Backend en nuevo servidor   "
echo "=========================================================="

# 1. Asegurar carpetas de datos
if [ -d "monitor/backend" ]; then
    mkdir -p monitor/backend/data
    chmod -R 775 monitor/backend/data 2>/dev/null || true
else
    mkdir -p backend/data
    chmod -R 775 backend/data 2>/dev/null || true
fi

# 2. Verificar/crear red externa si no existe
NET_NAME="chirpstack-docker-master_default"
if ! docker network inspect "$NET_NAME" >/dev/null 2>&1; then
    echo "Configurando red Docker: $NET_NAME..."
    docker network create "$NET_NAME" || true
fi

# 3. Levantar contenedores con Docker Compose
echo "Construyendo imágenes y levantando servicios..."
docker compose up -d --build

echo ""
echo "=========================================================="
echo " Backend iniciado con éxito!"
echo " - API REST: http://localhost:8000"
echo " - Swagger UI: http://localhost:8000/docs"
echo " - Healthcheck: http://localhost:8000/api/dashboard"
echo "=========================================================="
