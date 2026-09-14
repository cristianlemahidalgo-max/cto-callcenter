/**
 * CTO 119 — Bot WhatsApp con WPPConnect
 * ==========================================
 * Escucha mensajes entrantes al número de la cooperativa
 * y los reenvía al sistema CTO CallCenter en tiempo real.
 * 
 * CÓMO USAR:
 * 1. npm install
 * 2. node bot.js
 * 3. Escanear el QR que aparece en pantalla con WhatsApp del número 0999483918
 * 4. ¡Listo! Los mensajes llegan automáticamente al panel de la operadora.
 */

const wppconnect = require('@wppconnect-team/wppconnect');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

// ── CONFIGURACIÓN ─────────────────────────────────────────────────────────
const CONFIG = {
  // URL del servidor CTO (cambiar si el bot corre en otra máquina)
  CTO_URL: 'http://127.0.0.1:5199',

  // Número de la cooperativa (sin +, sin espacios)
  NUMERO_COOP: '593999483918',

  // Nombre de la sesión (se guarda para no escanear QR cada vez)
  SESSION_NAME: 'cto119-bot',

  // Responder automáticamente a los clientes
  AUTO_RESPONDER: true,

  // Tamaño físico del QR en cm por lado (igual en todos los lados)
  QR_TAMANO_CM: 9,
  QR_ARCHIVO: 'qr_cto119.png',

  // Ignorar mensajes propios / grupos
  IGNORAR_GRUPOS: true,
  IGNORAR_PROPIOS: true,
};

// ── VENTANA DEL QR (tamaño real en cm, no gigante en la consola) ──────────
function guardarQr(base64Qr) {
  const b64 = String(base64Qr || '')
    .replace(/^data:image\/png;base64,/i, '')
    .replace(/^data:image\/[a-z]+;base64,/i, '')
    .trim();
  if (!b64) return false;
  try {
    fs.writeFileSync(path.join(__dirname, CONFIG.QR_ARCHIVO), Buffer.from(b64, 'base64'));
    return true;
  } catch (e) {
    console.error('[BOT] No se pudo guardar el QR:', e.message);
    return false;
  }
}

function cerrarQr() {
  try {
    cp.execSync('taskkill /F /IM powershell.exe /FI "WINDOWTITLE eq CTO QR"', { stdio: 'ignore' });
  } catch (_) { /* ya estaba cerrado */ }
}

function mostrarQr() {
  cerrarQr();
  const script = path.join(__dirname, 'mostrar_qr.ps1');
  try {
    cp.spawn('powershell', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
      '-File', script,
      '-FileQR', path.join(__dirname, CONFIG.QR_ARCHIVO),
      '-Cm', String(CONFIG.QR_TAMANO_CM),
    ], { stdio: 'ignore', windowsHide: true, detached: false });
  } catch (e) {
    console.error('[BOT] No se pudo abrir la ventana del QR:', e.message);
  }
}

// ── RESPUESTAS AUTOMÁTICAS ─────────────────────────────────────────────────
const MENSAJE_BIENVENIDA = `🚕 *CTO Cooperativa de Taxis Occidental 119*

Hola! Recibimos tu mensaje.

Para pedir un taxi indícanos tu *dirección de recogida*:
_Ejemplo: "Necesito taxi en Av. América y Naciones Unidas"_

📞 También puedes llamar al *119*
⏰ Servicio 24 horas`;

const MENSAJE_SOLICITUD_OK = (nombre, direccion) =>
  `✅ *Pedido recibido!* ${nombre ? 'Hola ' + nombre + '!' : ''}

📍 Dirección: _${direccion}_

Una operadora está asignando tu taxi. Te informamos en breve.

📞 Si hay cambios: *119*
_CTO Cooperativa de Taxis Occidental 119_`;

const MENSAJE_SOLICITUD_ERROR = `Hola! 👋

Para pedir un taxi escríbenos tu *dirección completa*:

_"Taxi en [tu dirección]"_
_Ejemplo: "Taxi en La Prensa y Av. Occidental"_

📞 O llama al *119*`;

// ── PALABRAS CLAVE ─────────────────────────────────────────────────────────
const KEYWORDS_TAXI = [
  'taxi', 'servicio', 'necesito', 'quiero', 'pedir', 'carro',
  'unidad', 'carrera', 'llevar', 'recoger', 'pasar', 'vengan',
  'manden', 'enviar', 'solicito', 'dirección', 'direccion',
  'hola', 'buenas', 'buen dia', 'buenas tardes', 'buenas noches',
  'ocupo', 'requiero', 'por favor', 'urgente'
];

// ── FUNCIONES ──────────────────────────────────────────────────────────────

function esSolicitudTaxi(texto) {
  const t = texto.toLowerCase();
  return KEYWORDS_TAXI.some(k => t.includes(k));
}

function extraerDireccion(texto) {
  const t = texto.toLowerCase();
  // Patrones para extraer dirección
  const patrones = [
    /(?:en|desde|en la|dirección|direccion|estoy en|me encuentro en|estoy en|recójanme en|me encuentro)\s+(.+)/i,
    /(?:taxi|servicio)\s+(?:en|para|a|hacia)?\s*(.+)/i,
    /(?:necesito|quiero|pedir|ocupo)\s+(?:un\s+)?(?:taxi|carro|servicio)\s+(?:en|para|desde)?\s*(.+)/i,
  ];
  for (const p of patrones) {
    const m = texto.match(p);
    if (m && m[1] && m[1].length > 3) return m[1].trim();
  }
  // Si no encuentra patrón, usar el mensaje completo
  return texto.trim();
}

async function enviarAlSistema(numero, nombre, mensaje, direccion) {
  try {
    const res = await axios.post(`${CONFIG.CTO_URL}/api/bot/mensaje`, {
      numero: numero,
      nombre: nombre || '',
      mensaje: mensaje,
      direccion: direccion,
      body: mensaje,
      from: numero,
      pushName: nombre || '',
    }, { timeout: 5000 });
    return res.data;
  } catch (e) {
    console.error('[BOT] Error enviando al sistema CTO:', e.message);
    return null;
  }
}

// ── LATIDO / ESTADO EN LÍNEA ───────────────────────────────────────────────
// El panel de la operadora muestra "bot en línea" mientras este latido
// llegue al servidor cada 60 segundos. Si se apaga el bot, el panel
// lo marca como desconectado a los 3 minutos.
let fase = 'conectando';

async function enviarHeartbeat() {
  try {
    await axios.post(`${CONFIG.CTO_URL}/api/bot/heartbeat`, {
      info: {
        session: CONFIG.SESSION_NAME,
        numero: CONFIG.NUMERO_COOP,
        fase: fase,
      },
    }, { timeout: 5000 });
  } catch (e) {
    console.error('[BOT] Heartbeat falló (¿servidor CTO apagado?):', e.message);
  }
}

setInterval(enviarHeartbeat, 60000);

// ── INICIO DEL BOT ─────────────────────────────────────────────────────────
console.log('');
console.log('══════════════════════════════════════════════');
console.log('  CTO 119 — Bot WhatsApp');
console.log('  Cooperativa de Taxis Occidental');
console.log('══════════════════════════════════════════════');
console.log('');

wppconnect
  .create({
    session: CONFIG.SESSION_NAME,
    catchQR: (base64Qr, asciiQR, attempts) => {
      console.log('\n📱 Escanea este QR con WhatsApp del número de la cooperativa:');
      console.log('   (Abre WhatsApp → ⋮ → Dispositivos vinculados → Vincular dispositivo)');
      console.log(`   Intento ${attempts}/8. El QR expira en 60 segundos.`);
      if (guardarQr(base64Qr)) {
        mostrarQr();
        console.log(`   QR abierto en una ventana de ~${CONFIG.QR_TAMANO_CM} cm por lado.`);
      } else {
        console.log('');
      }
    },
    statusFind: (statusSession, session) => {
      console.log(`[BOT] Estado: ${statusSession} — Sesión: ${session}`);
      if (statusSession === 'isLogged') { fase = 'listo'; cerrarQr(); }
      else if (statusSession === 'qrReadSuccess' || statusSession === 'inChat') { fase = 'conectado'; cerrarQr(); }
      else fase = 'conectando';
    },
    headless: true,
    devtools: false,
    useChrome: true,
    debug: false,
    logQR: false,
    browserWSEndpoint: undefined,
    autoClose: 0,
    tokenStore: 'file',
    folderNameToken: './tokens',
    puppeteerOptions: {
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ],
    },
  })
  .then(async (client) => {
    console.log('\n✅ Bot conectado a WhatsApp!');
    console.log(`📞 Número: +${CONFIG.NUMERO_COOP}`);
    console.log(`🔗 Sistema CTO: ${CONFIG.CTO_URL}`);
    console.log('\n🟢 Escuchando mensajes entrantes...\n');

    // Reportar al servidor que el bot está en línea
    enviarHeartbeat();

    // ── Escuchar mensajes entrantes ────────────────────────────────────
    client.onMessage(async (message) => {
      try {
        // Ignorar mensajes propios
        if (CONFIG.IGNORAR_PROPIOS && message.fromMe) return;

        // Ignorar grupos
        if (CONFIG.IGNORAR_GRUPOS && message.isGroupMsg) return;

        // Ignorar estados y notificaciones
        if (message.type !== 'chat' && message.type !== 'image') return;

        const numero = message.from.replace('@c.us', '').replace(/[^0-9]/g, '');
        const nombre = message.sender?.pushname || message.notifyName || '';
        const texto = (message.body || message.caption || '').trim();

        if (!texto) return;

        console.log(`[BOT] 📨 Mensaje de +${numero} (${nombre || 'Sin nombre'}): "${texto.substring(0, 60)}"`);

        const esTaxi = esSolicitudTaxi(texto);
        const direccion = esTaxi ? extraerDireccion(texto) : texto;

        // Enviar al sistema CTO
        const resultado = await enviarAlSistema(numero, nombre, texto, direccion);

        // Responder automáticamente al cliente — SIEMPRE usar respuesta del server
        if (CONFIG.AUTO_RESPONDER) {
          let respuesta;
          if (resultado && resultado.respuesta) {
            // El server genera la respuesta correcta (solicitud o genérica)
            respuesta = resultado.respuesta;
          } else {
            // Fallback si el server no responde
            respuesta = MENSAJE_BIENVENIDA;
          }

          await client.sendText(message.from, respuesta);
          console.log(`[BOT] ✅ Respondido a +${numero}`);
        }

        if (esTaxi) {
          console.log(`[BOT] 🚕 SOLICITUD DE TAXI → Dirección: "${direccion}"`);
          console.log(`[BOT] 📡 Alerta enviada al panel de operadoras`);
        }

      } catch (err) {
        console.error('[BOT] Error procesando mensaje:', err.message);
      }
    });

    // ── Estado de la conexión ──────────────────────────────────────────
    client.onStateChange((state) => {
      console.log(`[BOT] Estado WhatsApp: ${state}`);
      if (state === 'CONFLICT' || state === 'UNLAUNCHED') {
        client.useHere();
      }
      if (state === 'CONNECTED') { fase = 'conectado'; cerrarQr(); }
      if (state === 'DISCONNECTED' || state === 'CLOSED') fase = 'desconectado';
      enviarHeartbeat();
    });

    // ── Desconexión ────────────────────────────────────────────────────
    client.onIncomingCall(async (call) => {
      console.log(`[BOT] 📞 Llamada entrante de ${call.peerJid} — Rechazando (solo mensajes)`);
      await client.rejectCall(call.id, call.peerJid);
    });

  })
  .catch((err) => {
    console.error('[BOT] ❌ Error al iniciar:', err.message);
    console.log('\n🔧 POSIBLES CAUSAS:');
    console.log('   1. No tienes Node.js instalado (descargar en nodejs.org)');
    console.log('   2. Falta ejecutar: npm install');
    console.log('   3. Chrome/Chromium no está instalado');
    console.log('   4. El número ya está conectado en otro dispositivo');
    process.exit(1);
  });
