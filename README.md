# Plantilla de Despliegue Completo: LoRaWAN Full Stack
### ChirpStack v4 + LoRaWAN Monitor Backend (Plantilla 100% Limpia)

Esta plantilla contiene la arquitectura de software completa y aislada para desplegar **ChirpStack v4** y el **Backend de Supervisión LoRaWAN Monitor** en cualquier servidor sin incluir ningún dato sensible, personal, contraseñas de producción ni ubicaciones confidenciales.

---

## 🛡️ Características de Sanitización
- **Cero datos personales**: Sin correos electrónicos, sin cuentas ni usuarios personales.
- **Cero datos confidenciales**: Sin volcados de bases de datos de producción (`.sql` eliminado).
- **Cero ubicaciones privadas**: Coordenadas geográficas limpias (`{}`).
- **Secretos parametrizados**: Variables de entorno limpias con plantilla de configuración (`.env.example`).

---

## 📦 Componentes de la Arquitectura

1. **ChirpStack v4**:
   - Servidor LoRaWAN v4 (`chirpstack/chirpstack:4`).
   - Servidor REST API (`chirpstack/chirpstack-rest-api:4`).
   - Gateway Bridge UDP Semtech (`1700/udp`).
   - Gateway Bridge BasicStation (`3001`).
   - Broker MQTT Eclipse Mosquitto.
   - Redis para caché de sesiones y estado de red.
   - Base de datos PostgreSQL limpia con extensiones `pg_trgm` y `hstore` pre-habilitadas.

2. **LoRaWAN Monitor Backend**:
   - API REST en FastAPI optimizada para la app móvil de XOLOGIC.
   - Base de datos PostgreSQL operativa (`lorawan_monitor`).
   - Módulos de telemetría, alarmas operativas, usuarios y estado de conectividad.

3. **Orquestación**:
   - Todo orquestado bajo una sola red privada de Docker (`lorawan_full_network`) sin dependencias externas.

---

## 📋 Requisitos Previos en el Servidor Destino

- **Docker Engine** (20.10+)
- **Docker Compose** (v2.x+)

---

## 🚀 Despliegue en 1 Solo Paso

### En Servidor Linux (Ubuntu / Debian / CentOS):
```bash
unzip lorawan-full-stack.zip -d lorawan-stack
cd lorawan-stack
chmod +x start.sh
./start.sh
```
*(O directamente con: `docker compose up -d --build`)*

---

### En Servidor Windows / Windows Server:
1. Descomprime `lorawan-full-stack.zip` en una carpeta (ej. `C:\lorawan-stack`).
2. Abre PowerShell en esa carpeta y ejecuta:
   ```powershell
   .\start.ps1
   ```

---

## 🔑 Configuración Inicial del Sistema (Primer Inicio)

Al ser una plantilla limpia, los servicios arrancan en blanco listos para producción:

1. **Ingresa a la interfaz web de ChirpStack**:
   - Abre en tu navegador: `http://<IP-SERVIDOR>:8080`
   - Usuario por defecto: `admin`
   - Contraseña por defecto: `admin` *(ChirpStack te pedirá cambiarla)*.

2. **Genera tu API Key para el Monitor**:
   - En el menú lateral izquierdo de ChirpStack, ve a **API keys** -> **Add API key**.
   - Asigna un nombre (ej. `lorawan-monitor`) y marca la casilla de permisos globales.
   - Copia el token generado.

3. **Configura el archivo `.env` del Monitor**:
   - Abre el archivo `monitor/.env` en tu servidor.
   - Pega tu token en la variable `CHIRPSTACK_API_TOKEN`:
     ```bash
     CHIRPSTACK_API_TOKEN=tu_token_aqui
     ```
   - Actualiza tu contraseña de base de datos y clave secreta JWT en ese mismo archivo:
     ```bash
     POSTGRES_PASSWORD=tu_contrasena_segura
     JWT_SECRET_KEY=tu_clave_jwt_aleatoria
     ```

4. **Aplica los cambios reiniciando el backend**:
   ```bash
   docker compose restart monitor-backend
   ```

---

## 🌐 Servicios y Puertos Expuestos

| Servicio | Puerto en Servidor | URL / Acceso | Estado Inicial |
| :--- | :--- | :--- | :--- |
| **ChirpStack Web UI** | `8080` | `http://<IP-SERVIDOR>:8080` | `admin` / `admin` |
| **ChirpStack REST API** | `8090` | `http://<IP-SERVIDOR>:8090` | Disponible internamente |
| **LoRaWAN Monitor API** | `8000` | `http://<IP-SERVIDOR>:8000` | Listo para clientes |
| **Documentación Swagger** | `8000` | `http://<IP-SERVIDOR>:8000/docs` | Acceso libre |
| **MQTT Broker (Mosquitto)** | `1883` | `mqtt://<IP-SERVIDOR>:1883` | Listo para gateways |
| **Gateway Bridge (UDP)** | `1700/udp` | `<IP-SERVIDOR>:1700` | Packet Forwarder Semtech |
| **Gateway BasicStation** | `3001` | `ws://<IP-SERVIDOR>:3001` | Protocolo LNS |
| **PostgreSQL Monitor** | `5433` | `<IP-SERVIDOR>:5433` | Base de datos limpia |

---

## 🛠 Comandos Útiles de Administración

- **Ver estado de los contenedores**:
  ```bash
  docker compose ps
  ```

- **Ver logs del Monitor Backend**:
  ```bash
  docker compose logs -f monitor-backend
  ```

- **Ver logs de ChirpStack**:
  ```bash
  docker compose logs -f chirpstack
  ```

- **Detener el stack**:
  ```bash
  docker compose stop
  ```

- **Iniciar el stack**:
  ```bash
  docker compose start
  ```
