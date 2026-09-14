# 🚀 GUÍA DE DESPLIEGUE EN SERVIDORES GRATUITOS Y APP NATIVA ANDROID
## CTO CallCenter v11.0 — Cooperativa de Taxis Occidental 119

Esta guía explica paso a paso cómo subir todo el sistema **CTO CallCenter** a la web usando servidores cloud **100% gratuitos** (Render.com, Koyeb, Docker) y cómo instalar la **App Nativa Android (.apk)** en los celulares de los conductores **sin necesidad de publicar en Google Play Store**.

---

## 📱 PARTE 1: INSTALACIÓN DE LA APP NATIVA EN CELULARES DE CONDUCTORES (Sin Play Store)

Los conductores pueden instalar la aplicación oficial nativa directamente en sus teléfonos Android sin pasar por Play Store.

### Métodos de Instalación:

#### Método A: Descarga Directa del APK
1. Abre el navegador del celular (Chrome) e ingresa a:
   ```
   https://tu-servidor.onrender.com/descargar-apk
   ```
2. El celular descargará el archivo `CTO_Conductor.apk`.
3. Al tocar "Abrir", si Android muestra el aviso:  
   *“Por seguridad, tu teléfono no tiene permitido instalar aplicaciones desconocidas”*, presiona **Ajustes / Configuración** y activa la casilla **"Permitir desde esta fuente"**.
4. Presiona **Instalar**. ¡Listo! El ícono de CTO 119 aparecerá en el menú principal del teléfono.

#### Método B: Escaneo de Código QR
1. La central abre o proyecta la pantalla de instalación:
   ```
   https://tu-servidor.onrender.com/descargar-app
   ```
2. El conductor escanea el **Código QR** con la cámara de su teléfono.
3. Se abre la página interactiva que le guiará para descargar e instalar la App Nativa o la versión Web PWA.

---

## 🌐 PARTE 2: SUBIR TODO EL SISTEMA A UN SERVIDOR GRATUITO (Para Pruebas y Producción Web)

### Opción Recomendada: Render.com (100% Gratuito y Certificado SSL HTTPS)

Render.com ofrece alojamiento web gratuito con certificado HTTPS automático y soporte para Python/Flask.

#### Paso 1: Subir el proyecto a GitHub
1. Entra a [github.com](https://github.com) y crea una cuenta gratuita (si no tienes una).
2. Crea un nuevo repositorio (ejemplo: `cto-callcenter`).
3. Sube todos los archivos del sistema desde la carpeta `d:\cto` a tu repositorio de GitHub.

#### Paso 2: Crear el servicio en Render.com
1. Ingresa a [render.com](https://render.com) y regístrate gratis con tu cuenta de GitHub.
2. En el panel de control, haz clic en **New +** y selecciona **Web Service**.
3. Conecta tu repositorio `cto-callcenter`.
4. Rena el formulario con estos datos:
   - **Name:** `cto-callcenter-119`
   - **Region:** Ohio (US East) u Oregon (US West).
   - **Branch:** `main` (o `master`).
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 wsgi:app`
   - **Instance Type:** `Free`
5. Haz clic en **Create Web Service**.

En 2-3 minutos, Render compilará tu aplicación y te otorgará un enlace HTTPS público gratuito como:
```
https://cto-callcenter-119.onrender.com
```

---

### Opción 2: Despliegue con Docker (Koyeb / Railway / Fly.io / VPS)

Si prefieres usar contenedores Docker:

1. El repositorio incluye un `Dockerfile` y `docker-compose.yml` listos para usar.
2. Si tienes un VPS propio o servidor local, simplemente ejecuta:
   ```bash
   docker-compose up -d --build
   ```
3. El sistema estará corriendo en la Web y en el puerto `5199`.

---

## 📊 PARTE 3: RUTAS Y DIRECCIONES DEL SISTEMA WEB

Una vez subido al servidor gratuito (ej. `https://cto-callcenter-119.onrender.com`), las direcciones para todos los usuarios son:

| Tipo de Usuario | Dirección Web / Enlace | Descripción |
| :--- | :--- | :--- |
| **Operadoras (Central)** | `https://tu-servidor.onrender.com/` | Panel principal de despacho y recepción de llamadas |
| **Conductores (App / Web)** | `https://tu-servidor.onrender.com/conductor` | Pantalla de turnos QAP, viajes asignados y estado |
| **Descarga APK Conductores** | `https://tu-servidor.onrender.com/descargar-apk` | Descarga directa del instalador Android nativo |
| **Pantalla de QR e Instalación** | `https://tu-servidor.onrender.com/descargar-app` | Guía interactiva con QR para conductores |
| **Panel de Unidades en Vivo** | `https://tu-servidor.onrender.com/admin/unidades` | Monitoreo en tiempo real de las 79 unidades |

---

## 🔐 RESPALDOS Y SEGURIDAD

- **Respaldo Automático:** La base de datos SQLite se respalda automáticamente cada 6 horas.
- **Acceso:** Las credenciales predeterminadas para los conductores son su número de unidad (1-79) y los últimos 4 dígitos de su teléfono registrado.
