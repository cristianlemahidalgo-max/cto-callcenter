# MANUAL DEL SISTEMA — CTO CallCenter v11.0
## Cooperativa de Taxis Occidental 119 — Quito, Ecuador

---

## MODULOS DEL SISTEMA

| Modulo | Descripcion |
|---|---|
| Dashboard | Panel principal con viajes activos, grid QAP, mapa GPS en tiempo real |
| Llamada Entrante (F3) | Registro de llamadas, busqueda de clientes, asignacion de unidad |
| Registro QAP (F2) | Registrar una unidad disponible manualmente desde la central |
| Bot WhatsApp | Solicitudes automaticas via WhatsApp al numero de la cooperativa |
| Reservas | Reservas programadas de taxis con calendario y fechas |
| Clientes | Directorio de 17,800+ clientes con historial de viajes |
| Conductores | Lista de 79 conductores + Colaboradores por Unidad |
| Contabilidad | Tiques mensuales, multas, pagos, exportar Excel |
| Calificaciones | Sistema de estrellas para calificar el servicio de cada conductor |
| Tarifas | Tarifas por zona con recargos pico/nocturno y feriados |
| Documentos | Control de documentos de conductores (licencia, seguro, revision tecnica) |
| Soporte/Tickets | Sistema de tickets para reportar problemas y seguimiento |
| GPS Playback | Reproduccion historica de recorridos GPS de las unidades |
| SOS/Panico | Boton de emergencia en la app del conductor con alerta a central |
| Jornada del Dia | Resumen y cierre del turno diario |
| Reportes | Historial de viajes, QAP, clientes, WhatsApp enviados |
| Estadisticas | Graficas mensuales, horas pico, top unidades, KPIs |
| Bitacora | Registro de todas las acciones del sistema (73+ eventos) |
| Administracion | Gestion de usuarios operadoras/admin, permisos por rol |
| Panel Unidades | Pantalla aparte con mapa de unidades en tiempo real |
| TV Central | Vista panoramica para pantalla grande en la central |

---

## CREDENCIALES DE ACCESO

| Usuario | Cedula | Clave | Rol |
|---|---|---|---|
| Administrador | 1717794208 | CTO2025! | admin |
| Operadora Principal | 1798000001 | cto2024 | operadora |

Para crear mas operadoras: Menu Administracion > "Nuevo Usuario"

---

## SISTEMA DE LICENCIA

- **Licencia de prueba:** El sistema viene con una licencia de prueba de 30 dias al iniciar por primera vez. La licencia actual expira el 15 de diciembre de 2026 (~116 dias). Al vencer, el sistema se bloquea completamente y muestra "Sistema Bloqueado". Solo el administrador puede renovar la licencia.

- **Generar licencia:**

```
python generar_licencia.py                           # Trial 30 dias desde hoy
python generar_licencia.py --expira 2026-12-15       # Hasta 15/12/2026
python generar_licencia.py --verificar               # Verificar licencia actual
```

- **Proteccion:** La licencia esta firmada con HMAC-SHA256 (no se puede editar la fecha sin cambiar la firma). El servidor verifica la licencia en cada peticion HTTP. Si la licencia vence en menos de 30 dias, aparece un banner de advertencia amarillo.

---

## BOT DE WHATSAPP — COMO FUNCIONA

Numero: +593 99 948 3918

Flujo automatico:

```
CLIENTE escribe WhatsApp -> BOT detecta keywords de taxi -> Sistema extrae DIRECCION
-> ALERTA en tiempo real a operadora -> OPERADORA asigna unidad -> Viaje registrado
-> WhatsApp al cliente "Su taxi esta en camino"
```

Palabras clave: `taxi`, `servicio`, `necesito`, `quiero`, `pedir`, `carro`, `unidad`, `carrera`, `llevar`, `recoger`, `pasar`, `vengan`, `manden`, `enviar`, `solicito`, `direccion`

Panel Bot WhatsApp: solicitudes pendientes, despachar, responder WA, ignorar, historial.

Integracion tecnica:

```
POST http://[IP]:5199/api/bot/mensaje
Body: {"numero":"593991234567","mensaje":"texto","nombre":"Cliente"}
```

Compatible con WPPConnect, CallMeBot, Twilio, UltraMsg, WA-Business API.

---

## APP DEL CONDUCTOR

URL: `http://[IP]:5199/conductor`

- Login Socio: unidad + ultimos 4 digitos telefono
- Login Colaborador: unidad + codigo (ej: C49-01)

Funciones: Reportar QAP, Ver viaje, Iniciar viaje (3 pasos), Llegue al destino, Entregado, SOS/Panico, Terminar turno, Estadisticas del dia.

PWA: funciona offline, service worker, se instala en celular.

---

## COLABORADORES

Colaborador = persona que maneja la misma unidad en turno diferente. Tiene codigo unico (ej: C49-01). Se registra en menu Conductores > Colaboradores por Unidad. El codigo se entrega al colaborador para ingresar a la App.

---

## DASHBOARD PRINCIPAL

- Tarjetas: Unidades QAP, Viajes Asignados, Activos Ahora, Carreras Perdidas, Reservas Hoy, Bot WhatsApp.
- Panel izquierdo: Viajes activos con tiempos coloreados.
- Panel derecho: Grid QAP en tiempo real.
- Mapa GPS: Leaflet con unidades en tiempo real.

---

## LLAMADA ENTRANTE (F3)

`F3` -> telefono -> buscar cliente -> direccion -> `F4` tabla unidades -> confirmar viaje.

---

## RESERVAS

Crear en Calendario. Estados: Pendiente, Confirmada, En curso, Completada, Cancelada. Filtros por fecha.

---

## CALIFICACIONES

Estrellas 1-5. Top 5 unidades mejor calificadas. Promedio general. Minimo requerido: 4.0.

---

## TARIFAS

Base $2.50 + $0.50/km. Recargos: pico +20%, nocturno +30%, feriados +15%. Endpoint: `/api/tarifas/calcular?zona=Centro&km=3`

---

## DOCUMENTOS

Licencia, revision tecnica, seguro. Estados: Vigente, Por vencer, Vencido. Alertas automaticas.

---

## SOS/PANICO

Boton rojo en app conductor. Envia: unidad, conductor, GPS, hora. Aparece alerta en dashboard central.

---

## SOPORTE

Tickets con prioridad (baja/media/alta/critica). Seguimiento y asignacion.

---

## CONTABILIDAD

Tiques mensuales ($50 socio / $30 concesionario). Multas bloquean conductor. Exportar Excel y PDF.

---

## ESTADISTICAS

KPIs del dia. Reportes: viajes, QAP, clientes, WhatsApp, auditoria nocturna.

---

## GPS

Tracking cada 30 segundos en `gps_historial`. GPS Playback en mapa Leaflet. Panel Unidades en `/admin/unidades` con colores.

---

## RED LOCAL

| Servicio | URL |
|---|---|
| Panel | http://[IP]:5199 |
| Conductor | http://[IP]:5199/conductor |
| Unidades | http://[IP]:5199/admin/unidades |
| TV | http://[IP]:5199/tv |

HTTPS opcional:

```
python generar_certificado.py -> set CTO_HTTPS=1 -> python app\main.py
```

---

## ACCESOS DIRECTOS

| Tecla | Accion |
|---|---|
| F2 | QAP |
| F3 | Llamada |
| F4 | Tabla unidades |
| Esc | Cerrar modal |

---

## RESPALDOS

Automatico cada 6 horas (30 max). Manual en Admin. DB en `C:\Users\[usuario]\CTO_CallCenter_Datos\callcenter.db`

---

## TABLAS SQL (24)

kv_store, clientes, viajes, unidades_actividad, pagos_mensuales, multas, tipos_multa, usuarios, bitacora, reasignaciones, mensajes_whatsapp, colaboradores, bot_mensajes, turnos_operadores, reclamos, reservas, gps_historial, documentos_conductor, calificaciones, alertas_sos, soporte_tickets, tarifas, feriados, sqlite_sequence

---

## VERSION

CTO CallCenter v11.0 — Licencia hasta 15/12/2026
