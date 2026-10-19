# Guía de Migración del Backend LoRaWAN Monitor

Este paquete contiene todo el backend de **LoRaWAN Monitor** completamente pre-configurado para desplegarse en cualquier servidor (Linux, Ubuntu, Debian, Windows Server, etc.) con un solo comando.

---

## 📦 Contenido de este Paquete

- **`docker-compose.yml`**: Orquestación de contenedores (API FastAPI + base de datos PostgreSQL).
- **`.example.env`**: Archivo de variables de entorno pre-configurado con credenciales JWT, configuración de base de datos y token de API de ChirpStack. Se debe cambiar a un .env con tus credenciales propias.
- **`backend/`**: Código fuente de la API (Python 3.12, FastAPI, endpoints de supervisión, Leaflet/OSM support).
  - **`backend/data/custom_locations.json`**: Coordenadas geográficas guardadas para los nodos y gateways.
  - **`backend/data/users.json`**: Base de datos de usuarios y operadores creada (`operador.demo`, etc.).
- **`database/migrations/001_initial_schema.sql`**: Esquema relacional inicial para PostgreSQL.
- **`start.sh`**: Script de arranque automatizado para Linux / macOS.
- **`start.ps1`**: Script de arranque automatizado para Windows PowerShell.

---

## 🛠️ Requisitos Previos en el Nuevo Servidor

1. Tener instalado **Docker** y **Docker Compose** (versión 2 o superior).
   - En Linux Ubuntu/Debian: `sudo apt update && sudo apt install -y docker.io docker-compose-v2`
   - En Windows: Tener Docker Desktop o el motor Docker instalado.

---

## 🌐 Escenarios de Conexión con ChirpStack v4

Antes de levantar el backend, verifica dónde correrá ChirpStack v4:

### Escenario A: ChirpStack está en el MISMO servidor con Docker
Si en este nuevo servidor también corre la carpeta `chirpstack-docker`:
- El backend se comunicará automáticamente mediante la red interna de Docker `chirpstack-docker-master_default` en `http://chirpstack-rest-api:8090`.
- No necesitas cambiar nada en `.env`.

### Escenario B: ChirpStack está en OTRO servidor o en una IP distinta
Si ChirpStack se encuentra en otra máquina o IP (ejemplo: `http://192.168.4.202:8090` o `http://midominio.com:8090`):
1. Abre el archivo `.env` y ajusta la URL:
   ```env
   CHIRPSTACK_API_URL=http://IP_DE_CHIRPSTACK:8090
   ```
2. En `docker-compose.yml`, puedes cambiar la variable de entorno `CHIRPSTACK_API_URL` si lo prefieres.

---

## 🚀 Pasos para Desplegar

### En Linux / Ubuntu / Debian:

1. Copia o transfiere esta carpeta al servidor (por SSH / SCP / SFTP).
2. Entra al directorio:
   ```bash
   cd lorawan-backend-deploy
   ```
3. Dale permisos de ejecución al script y arranca:
   ```bash
   chmod +x start.sh
   ./start.sh
   ```
   *(O directamente: `docker compose up -d --build`)*

### En Windows Server / Windows con PowerShell:

1. Abre PowerShell como Administrador.
2. Entra al directorio:
   ```powershell
   cd lorawan-backend-deploy
   ```
3. Ejecuta el script:
   ```powershell
   .\start.ps1
   ```
   *(O directamente: `docker compose up -d --build`)*

---

## ✅ Comprobación del Servicio

Una vez arrancado, puedes probarlo desde el navegador o terminal:

1. **Documentación Swagger UI:**
   Abre en tu navegador:
   ```
   http://IP_DEL_NUEVO_SERVIDOR:8000/docs
   ```
2. **Estado operativo del sistema (Healthcheck):**
   ```bash
   curl http://localhost:8000/api/dashboard
   ```
3. **Credenciales pre-configuradas:**
   - **Usuario:** `operador.demo`
   - **Contraseña:** `demopassword`
   - *(O cualquier otro usuario que hayas creado previamente, ya que están guardados en `backend/data/users.json`)*.

---

## 📱 Conectar la App Móvil al Nuevo Servidor

Una vez que tu nuevo servidor esté activo en su nueva IP (ejemplo: `http://192.168.1.50:8000`):
1. Abre la app móvil **LoRaWAN Monitor**.
2. En la pantalla de Login, presiona **⚙ Configuración de conexión / servidor**.
3. Escribe la nueva URL: `http://192.168.1.50:8000` (reemplazando por la IP del nuevo servidor).
4. Inicia sesión con tus credenciales habituales.
