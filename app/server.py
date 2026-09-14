"""
CTO CallCenter v11.0 — Servidor Backend Completo
Cooperativa de Taxis Occidental 119 — Quito, Ecuador
"""
import os, sys, json, sqlite3, shutil, hashlib, secrets, logging, time, hmac
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request, send_from_directory, session

def get_base_dir():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR   = get_base_dir()
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DATA_DIR   = os.environ.get('DATA_DIR', os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos'))
DB_PATH    = os.path.join(DATA_DIR, 'callcenter.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'respaldos')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

log_handlers = [logging.StreamHandler()]
try:
    log_file = os.path.join(DATA_DIR, 'cto.log')
    log_handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
except Exception as e:
    print(f"[CTO] Advertencia: No se pudo crear FileHandler para log ({e})")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger('CTO')

DB_INICIAL = os.path.join(BASE_DIR, 'callcenter_inicial.db')
if not os.path.exists(DB_PATH) and os.path.exists(DB_INICIAL):
    shutil.copy2(DB_INICIAL, DB_PATH)
    print(f"[CTO] BD inicial copiada → {DB_PATH}")

SECRET_FILE = os.path.join(DATA_DIR, '.cto_secret')

def _load_or_create_secret():
    try:
        if os.path.exists(SECRET_FILE):
            with open(SECRET_FILE, 'r') as f:
                k = f.read().strip()
            if len(k) >= 32:
                return k
    except Exception as e:
        logger.warning(f"Secret file read failed: {e}")
    k = secrets.token_hex(32)
    try:
        with open(SECRET_FILE, 'w') as f:
            f.write(k)
    except Exception as e:
        logger.warning(f"Secret file write failed: {e}")
    return k

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
app.secret_key = _load_or_create_secret()
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 28800  # 8 horas

# ── RATE LIMITING ───────────────────────────────────────────────────────
import collections
_rate_limits = collections.defaultdict(list)  # ip -> [timestamps]

def rate_limit(ip, max_per_minute=60):
    now = datetime.now().timestamp()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < 60]
    if len(_rate_limits[ip]) >= max_per_minute:
        return True
    _rate_limits[ip].append(now)
    return False

def _cleanup_rate_limits():
    """Limpia IPs que llevan más de 2 minutos sin actividad."""
    while True:
        time.sleep(120)
        now = datetime.now().timestamp()
        with threading.Lock():
            stale = [ip for ip, ts in _rate_limits.items() if not ts or now - max(ts) > 120]
            for ip in stale: del _rate_limits[ip]

@app.before_request
def check_rate_limit():
    ip = request.remote_addr or '127.0.0.1'
    if request.endpoint == 'login' and rate_limit(ip, 10):
        logger.warning(f"Rate limit exceeded for login from {ip}")
        return jsonify({'ok': False, 'error': 'Demasiados intentos. Espere 1 minuto.'}), 429
    if request.endpoint == 'api_save' and rate_limit(ip, 120):
        logger.warning(f"Rate limit exceeded for save from {ip}")
        return jsonify({'ok': False, 'error': 'Demasiadas peticiones.'}), 429

# ── SISTEMA DE LICENCIA ───────────────────────────────────────────────────
LICENSE_KEY = b'CTO_CC_v11_LIC_2026_COOP119'
_license_cache = {'valid': True, 'expires': '', 'tipo': '', 'checked': 0, 'mtime': 0}

def _validar_licencia():
    """Valida la licencia del sistema. Cachea resultado 1 vez por hora o si cambia el archivo."""
    now = time.time()
    lic_path = os.path.join(DATA_DIR, 'licencia.json')
    try:
        mtime = os.path.getmtime(lic_path) if os.path.exists(lic_path) else 0
    except: mtime = 0
    if now - _license_cache['checked'] < 3600 and _license_cache['checked'] > 0 and mtime == _license_cache['mtime']:
        return _license_cache['valid']
    _license_cache['checked'] = now
    _license_cache['mtime'] = mtime
    if not os.path.exists(lic_path):
        logger.warning("Sin licencia — generando trial 30 días")
        try:
            import subprocess
            script = os.path.join(BASE_DIR, '..', 'generar_licencia.py')
            if os.path.exists(script):
                subprocess.run([sys.executable, script, '--dias', '30'], timeout=10)
            else:
                # Generar trial directamente
                exp = (date.today() + timedelta(days=30)).isoformat()
                firma = hmac.new(LICENSE_KEY, f'trial|{exp}|Cooperativa Occidental 119'.encode(), hashlib.sha256).hexdigest()
                lic = {'tipo': 'trial', 'expira': exp, 'empresa': 'Cooperativa Occidental 119',
                       'firma': firma, 'generado': datetime.now().isoformat()}
                with open(lic_path, 'w', encoding='utf-8') as f: json.dump(lic, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error generando licencia trial: {e}")
            _license_cache.update({'valid': False, 'expires': '', 'tipo': 'none'})
            return False
    try:
        with open(lic_path, 'r', encoding='utf-8') as f:
            lic = json.load(f)
        tipo = lic.get('tipo', '')
        expira = lic.get('expira', '')
        empresa = lic.get('empresa', '')
        firma = lic.get('firma', '')
        firma_esperada = hmac.new(LICENSE_KEY, f'{tipo}|{expira}|{empresa}'.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(firma, firma_esperada):
            logger.error("Licencia: firma inválida — archivo fue modificado")
            _license_cache.update({'valid': False, 'expires': expira, 'tipo': tipo})
            return False
        fecha_exp = date.fromisoformat(expira)
        if date.today() > fecha_exp:
            dias = (date.today() - fecha_exp).days
            logger.warning(f"Licencia VENCIDA hace {dias} días (expiró {expira})")
            _license_cache.update({'valid': False, 'expires': expira, 'tipo': tipo})
            return False
        dias_rest = (fecha_exp - date.today()).days
        if dias_rest < 30:
            logger.warning(f"⚠️ Licencia por vencer: {dias_rest} días restantes (expira {expira})")
        _license_cache.update({'valid': True, 'expires': expira, 'tipo': tipo})
        return True
    except Exception as e:
        logger.error(f"Error validando licencia: {e}")
        _license_cache.update({'valid': False, 'expires': '', 'tipo': 'error'})
        return False

@app.before_request
def check_license():
    """Bloquea todas las peticiones si la licencia está vencida."""
    # Permitir siempre: estáticos, licencia status, ping
    free_endpoints = ['license_status', 'static', 'ping', 'info']
    if request.endpoint in free_endpoints:
        return None
    if request.path.startswith('/static') or request.path.endswith('.html') or request.path.endswith('.js') or request.path.endswith('.css'):
        return None
    if not _validar_licencia():
        return jsonify({'ok': False, 'error': 'Licencia vencida. Contacte al administrador.',
                       'license_expired': True}), 403
    return None

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def rows_to_list(rows): return [dict(r) for r in rows]
def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    conn = get_db(); c = conn.cursor()

    # ── PASO 1: Crear TODAS las tablas (IF NOT EXISTS — seguro siempre) ──────
    c.execute("""CREATE TABLE IF NOT EXISTS kv_store(
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        updated_at TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS clientes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
        telefono TEXT, telefono2 TEXT, observaciones TEXT,
        fecha_registro TEXT DEFAULT(datetime('now','localtime')), fecha_mod TEXT)""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cli_tel ON clientes(telefono)")
    c.execute("""CREATE TABLE IF NOT EXISTS viajes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, fecha_solicitud TEXT,
        id_cliente INTEGER, cliente_nombre TEXT, cliente_tel TEXT,
        direccion_recogida TEXT, referencia_recogida TEXT, destino TEXT, observaciones TEXT,
        numero_unidad INTEGER, id_conductor INTEGER, conductor_nombre TEXT, conductor_tel TEXT,
        fecha_asignacion TEXT, estado TEXT DEFAULT 'asignado')""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_viaje_fecha ON viajes(fecha_solicitud)")
    c.execute("""CREATE TABLE IF NOT EXISTS unidades_actividad(
        id INTEGER PRIMARY KEY AUTOINCREMENT, numero_unidad INTEGER NOT NULL,
        accion TEXT NOT NULL, fecha_hora TEXT DEFAULT(datetime('now','localtime')), obs TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ua_u ON unidades_actividad(numero_unidad)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ua_f ON unidades_actividad(fecha_hora)")
    c.execute("""CREATE TABLE IF NOT EXISTS pagos_mensuales(
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_conductor INTEGER NOT NULL,
        anio INTEGER NOT NULL, mes INTEGER NOT NULL,
        monto_base REAL DEFAULT 0, multas REAL DEFAULT 0, total REAL DEFAULT 0,
        estado TEXT DEFAULT 'pendiente', fecha_pago TEXT,
        UNIQUE(id_conductor,anio,mes))""")
    c.execute("""CREATE TABLE IF NOT EXISTS multas(
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_conductor INTEGER NOT NULL,
        concepto TEXT, monto REAL DEFAULT 0, anio INTEGER, mes INTEGER,
        pagada INTEGER DEFAULT 0, fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS tipos_multa(
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
        monto_default REAL DEFAULT 0, activo INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios(
        id INTEGER PRIMARY KEY AUTOINCREMENT, cedula TEXT UNIQUE, nombre TEXT,
        password_hash TEXT DEFAULT '', rol TEXT DEFAULT 'operadora',
        activo INTEGER DEFAULT 1, ultimo_acceso TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bitacora(
        id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, usuario_nombre TEXT,
        accion TEXT NOT NULL, detalle TEXT, modulo TEXT,
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS reasignaciones(
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_viaje INTEGER,
        id_conductor_anterior INTEGER, id_conductor_nuevo INTEGER,
        fecha_hora TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("""CREATE TABLE IF NOT EXISTS mensajes_whatsapp(
        id INTEGER PRIMARY KEY AUTOINCREMENT, id_viaje INTEGER, id_conductor INTEGER,
        numero TEXT, contenido TEXT, estado TEXT DEFAULT 'pendiente',
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    # ── TABLA: Conductores colaboradores ──────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS colaboradores(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_conductor_socio INTEGER NOT NULL,
        unidad INTEGER NOT NULL,
        nombre TEXT NOT NULL, cedula TEXT, telefono TEXT,
        codigo_colaborador TEXT UNIQUE NOT NULL,
        activo INTEGER DEFAULT 1,
        turno_activo INTEGER DEFAULT 0,
        fecha_registro TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_colab_unidad ON colaboradores(unidad)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_colab_cod ON colaboradores(codigo_colaborador)")
    # ── TABLA: Bot WhatsApp mensajes entrantes ────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS bot_mensajes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_cliente TEXT NOT NULL,
        nombre_cliente TEXT DEFAULT '',
        mensaje TEXT NOT NULL,
        respuesta TEXT DEFAULT '',
        estado TEXT DEFAULT 'pendiente',
        id_viaje_generado INTEGER,
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bot_fecha ON bot_mensajes(fecha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bot_estado ON bot_mensajes(estado)")
    # ── TABLA: Turnos de operadoras (control de horas trabajadas) ────────────
    c.execute("""CREATE TABLE IF NOT EXISTS turnos_operadores(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        usuario_nombre TEXT,
        rol TEXT,
        fecha TEXT,
        hora_ingreso TEXT,
        hora_salida TEXT,
        horas REAL DEFAULT 0,
        viajes_despachados INTEGER DEFAULT 0,
        viajes_perdidos INTEGER DEFAULT 0,
        estado TEXT DEFAULT 'abierto',
        obs TEXT DEFAULT '',
        cerrado_fecha TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_to_u ON turnos_operadores(usuario_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_to_f ON turnos_operadores(fecha)")
    # ── TABLA: Reclamos de clientes (quejas por viaje) ───────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS reclamos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_viaje INTEGER,
        id_cliente INTEGER,
        cliente_nombre TEXT,
        numero_unidad INTEGER,
        motivo TEXT NOT NULL,
        estado TEXT DEFAULT 'abierto',
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reclamo_cli ON reclamos(id_cliente)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_reclamo_viaje ON reclamos(id_viaje)")
    # ── TABLA: Rechazos de unidad (cuando una unidad no quiere ir) ──────────
    c.execute("""CREATE TABLE IF NOT EXISTS rechazos_unidad(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_unidad INTEGER,
        conductor TEXT,
        motivo TEXT,
        observaciones TEXT NOT NULL,
        id_viaje INTEGER,
        cliente_nombre TEXT,
        operadora TEXT,
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rechazo_unidad ON rechazos_unidad(numero_unidad)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rechazo_viaje ON rechazos_unidad(id_viaje)")
    # ── TABLA: Reservas de taxis ────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS reservas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_cliente INTEGER,
        cliente_nombre TEXT NOT NULL,
        cliente_tel TEXT,
        fecha_viaje TEXT NOT NULL,
        hora_viaje TEXT NOT NULL,
        direccion_recogida TEXT NOT NULL,
        referencia TEXT DEFAULT '',
        destino TEXT DEFAULT '',
        observaciones TEXT DEFAULT '',
        numero_unidad INTEGER,
        estado TEXT DEFAULT 'pendiente',
        usuario_id INTEGER,
        usuario_nombre TEXT,
        fecha_creacion TEXT DEFAULT(datetime('now','localtime')),
        fecha_modificacion TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_res_f ON reservas(fecha_viaje)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_res_e ON reservas(estado)")
    # ── TABLA: Historial GPS (posiciones cada 30s, se limpia después de 30 días) ──
    c.execute("""CREATE TABLE IF NOT EXISTS gps_historial(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_unidad INTEGER NOT NULL,
        lat REAL NOT NULL, lng REAL NOT NULL,
        velocidad REAL DEFAULT 0,
        id_viaje INTEGER,
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gps_u ON gps_historial(numero_unidad)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gps_f ON gps_historial(fecha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_gps_v ON gps_historial(id_viaje)")
    # ── TABLA: Documentos de conductores (licencia, seguro, revision tecnica) ──
    c.execute("""CREATE TABLE IF NOT EXISTS documentos_conductor(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_conductor INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        numero TEXT DEFAULT '',
        fecha_emision TEXT,
        fecha_vencimiento TEXT,
        archivos TEXT DEFAULT '[]',
        observaciones TEXT DEFAULT '',
        fecha_registro TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doccond ON documentos_conductor(id_conductor)")
    # ── TABLA: Calificaciones de servicio ──
    c.execute("""CREATE TABLE IF NOT EXISTS calificaciones(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        id_viaje INTEGER, id_cliente INTEGER,
        cliente_nombre TEXT, numero_unidad INTEGER,
        id_conductor INTEGER, conductor_nombre TEXT,
        puntuacion INTEGER DEFAULT 5,
        comentario TEXT DEFAULT '',
        fecha TEXT DEFAULT(datetime('now','localtime')))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cal_v ON calificaciones(id_viaje)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cal_u ON calificaciones(numero_unidad)")
    # ── TABLA: Alertas de panico/SOS ──
    c.execute("""CREATE TABLE IF NOT EXISTS alertas_sos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero_unidad INTEGER NOT NULL,
        id_conductor INTEGER,
        conductor_nombre TEXT,
        tipo TEXT DEFAULT 'sos',
        lat REAL, lng REAL,
        estado TEXT DEFAULT 'activa',
        fecha TEXT DEFAULT(datetime('now','localtime')),
        atendida_fecha TEXT, atendida_usuario TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sos_e ON alertas_sos(estado)")
    # ── TABLA: Soporte/Tickets interno ──
    c.execute("""CREATE TABLE IF NOT EXISTS soporte_tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER, usuario_nombre TEXT,
        modulo TEXT, titulo TEXT NOT NULL, descripcion TEXT,
        prioridad TEXT DEFAULT 'normal',
        estado TEXT DEFAULT 'abierto',
        fecha TEXT DEFAULT(datetime('now','localtime')),
        resuelto_fecha TEXT, resuelto_usuario TEXT)""")
    # ── TABLA: Tarifas/zonas de cobro ──
    c.execute("""CREATE TABLE IF NOT EXISTS tarifas(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zona TEXT NOT NULL,
        tarifa_base REAL DEFAULT 0,
        precio_por_km REAL DEFAULT 0,
        minimo REAL DEFAULT 0,
        recargo_pico REAL DEFAULT 0,
        recargo_nocturno REAL DEFAULT 0,
        activa INTEGER DEFAULT 1)""")
    # ── TABLA: Feriados (para tarifa dinamica) ──
    c.execute("""CREATE TABLE IF NOT EXISTS feriados(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT UNIQUE NOT NULL,
        nombre TEXT, recargo REAL DEFAULT 0.15)""")

    # ── PASO 2: Índices (siempre IF NOT EXISTS) ──────────────────────────────
    c.execute("CREATE INDEX IF NOT EXISTS idx_bit_f   ON bitacora(fecha)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bit_mod ON bitacora(modulo)")

    # ── PASO 3: Migración — agrega columnas a tablas existentes si faltan ────
    usuarios_cols = [r[1] for r in c.execute("PRAGMA table_info(usuarios)").fetchall()]
    if 'password_hash' not in usuarios_cols:
        c.execute("ALTER TABLE usuarios ADD COLUMN password_hash TEXT DEFAULT ''")
    if 'ultimo_acceso' not in usuarios_cols:
        c.execute("ALTER TABLE usuarios ADD COLUMN ultimo_acceso TEXT")
    # Migración usuarios: horario de acceso (roles/permisos)
    if 'horario_inicio' not in usuarios_cols:
        c.execute("ALTER TABLE usuarios ADD COLUMN horario_inicio TEXT")
    if 'horario_fin' not in usuarios_cols:
        c.execute("ALTER TABLE usuarios ADD COLUMN horario_fin TEXT")
    # Migración colaboradores (por si la tabla ya existía sin turno_activo)
    try:
        colab_cols = [r[1] for r in c.execute("PRAGMA table_info(colaboradores)").fetchall()]
        if 'turno_activo' not in colab_cols:
            c.execute("ALTER TABLE colaboradores ADD COLUMN turno_activo INTEGER DEFAULT 0")
    except Exception as e: logger.warning(f"Migracion colaboradores: {e}")
    # Migración D ✕ códigos de colaboradores normalizados a {unidad}D (ej: 49D, 1D)
    try:
        c.execute("""UPDATE conductores SET unidad_codigo = CAST(unidad_base AS TEXT)||'D'
            WHERE tipo='colaborador' AND unidad_base>0
            AND (unidad_codigo IS NULL OR CAST(unidad_codigo AS TEXT)!=CAST(unidad_base AS TEXT)||'D')""")
        c.execute("""UPDATE colaboradores SET codigo_colaborador = CAST(unidad AS TEXT)||'D'
            WHERE unidad>0 AND (codigo_colaborador IS NULL OR codigo_colaborador != CAST(unidad AS TEXT)||'D')""")
        logger.info("Migracion codigos colaboradores a formato {unidad}D aplicada")
    except Exception as e: logger.warning(f"Migracion codigo D: {e}")
    # Migración viajes: quién creó/despachó el viaje (para el reporte de operadoras)
    try:
        viajes_cols = [r[1] for r in c.execute("PRAGMA table_info(viajes)").fetchall()]
        if 'usuario_id' not in viajes_cols:
            c.execute("ALTER TABLE viajes ADD COLUMN usuario_id INTEGER")
        if 'usuario_nombre' not in viajes_cols:
            c.execute("ALTER TABLE viajes ADD COLUMN usuario_nombre TEXT")
    except Exception as e: logger.warning(f"Migracion viajes: {e}")
    # Migración bot_mensajes: dirección extraída del mensaje del cliente
    try:
        bot_cols = [r[1] for r in c.execute("PRAGMA table_info(bot_mensajes)").fetchall()]
        if 'direccion_extraida' not in bot_cols:
            c.execute("ALTER TABLE bot_mensajes ADD COLUMN direccion_extraida TEXT DEFAULT ''")
    except Exception as e: logger.warning(f"Migracion bot_mensajes: {e}")
    # Migración mensajes_whatsapp: agregar columna tipo
    try:
        wa_cols = [r[1] for r in c.execute("PRAGMA table_info(mensajes_whatsapp)").fetchall()]
        if 'tipo' not in wa_cols:
            c.execute("ALTER TABLE mensajes_whatsapp ADD COLUMN tipo TEXT DEFAULT 'viaje'")
    except Exception as e: logger.warning(f"Migracion mensajes_whatsapp: {e}")

    # ── TABLA: Conductores (socio / colaborador / concesionario) ──
    c.execute("""CREATE TABLE IF NOT EXISTS conductores(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL DEFAULT 'socio',
        unidad_base INTEGER DEFAULT 0,
        unidad_codigo INTEGER DEFAULT 0,
        nombre TEXT NOT NULL,
        cedula TEXT DEFAULT '',
        telefono TEXT DEFAULT '',
        placa TEXT DEFAULT '',
        codigo_radio TEXT DEFAULT '',
        marca TEXT DEFAULT '',
        modelo TEXT DEFAULT '',
        color TEXT DEFAULT 'amarillo',
        registro_municipal TEXT DEFAULT '',
        estado_civil TEXT DEFAULT '',
        fecha_ingreso TEXT DEFAULT '',
        estado TEXT DEFAULT 'activo',
        bloqueado INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cond_tipo ON conductores(tipo)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cond_unidad ON conductores(unidad_base)")

    # ── Migración: copiar CONDUCTORES_EMBEDDED a tabla conductores como socios ──
    try:
        existing = c.execute("SELECT COUNT(*) FROM conductores").fetchone()[0]
        if existing == 0 and CONDUCTORES_EMBEDDED:
            for cond in CONDUCTORES_EMBEDDED:
                ub = cond.get('unidad', 0)
                uc = ub
                c.execute("""INSERT OR IGNORE INTO conductores(
                    tipo,unidad_base,unidad_codigo,nombre,cedula,telefono,placa,
                    codigo_radio,marca,modelo,color,registro_municipal,
                    estado_civil,fecha_ingreso,estado,bloqueado)
                    VALUES('socio',?,?, ?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ub, uc, cond.get('nombre',''), cond.get('cedula',''),
                     cond.get('telefono',''), cond.get('placa',''),
                     cond.get('codigo_radio',''), cond.get('marca',''),
                     cond.get('modelo',''), cond.get('color','amarillo'),
                     cond.get('registro_municipal',''), cond.get('estado_civil',''),
                     cond.get('fecha_ingreso',''), cond.get('estado','activo'),
                     1 if cond.get('bloqueado') else 0))
            logger.info(f"Migrados {len(CONDUCTORES_EMBEDDED)} conductores socios a tabla SQL")
    except Exception as e:
        logger.warning(f"Migracion conductores: {e}")

    # ── PASO 4: Datos iniciales ───────────────────────────────────────────────
    for nm, mo in [
        ('Multa por no asistencia a asamblea', 10),
        ('Multa por deportes', 20),
        ('Multa por mal servicio al cliente', 15),
        ('Multa por incumplimiento de turno', 25),
        ('Multa por mal uso de frecuencia', 10),
    ]:
        c.execute("INSERT OR IGNORE INTO tipos_multa(nombre,monto_default) VALUES(?,?)", (nm, mo))

    # Solo INSERT si no existe (nunca forzar contraseña al reiniciar)
    c.execute("""INSERT OR IGNORE INTO usuarios(cedula,nombre,password_hash,rol,activo)
        VALUES('1717794208','Cristian Lema — Administrador',?,'admin',1)""", (hash_pass('CTO2025!'),))
    c.execute("""INSERT OR IGNORE INTO usuarios(cedula,nombre,password_hash,rol,activo)
        VALUES('1798000001','Operadora Principal',?,'operadora',1)""", (hash_pass('cto2024'),))

    conn.commit(); conn.close()

def log_action(accion, detalle='', modulo=''):
    try:
        uid=session.get('uid',0); unom=session.get('nombre','Sistema')
        conn=get_db()
        conn.execute("INSERT INTO bitacora(usuario_id,usuario_nombre,accion,detalle,modulo) VALUES(?,?,?,?,?)",
                     (uid,unom,accion,detalle,modulo))
        conn.commit(); conn.close()
    except Exception as e: logger.error(f"Bitacora write failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# TURNOS DE OPERADORAS — control de horas trabajadas por turno
# ═══════════════════════════════════════════════════════════════════════════
def _viaje_local_time(iso):
    """Convierte fecha_solicitud de un viaje (UTC, con Z) a datetime local-naive."""
    import datetime as _dt
    try:
        dt = _dt.datetime.fromisoformat(iso.replace('Z', '+00:00'))
        return dt.astimezone().replace(tzinfo=None)
    except Exception:
        return None

def abrir_turno_operador():
    """Registra el ingreso de la operadora. Cierra cualquier turno previo abierto."""
    uid = session.get('uid')
    if not uid: return
    conn = get_db()
    # Si quedó un turno abierto de una sesión anterior → cerrarlo automáticamente
    conn.execute("""UPDATE turnos_operadores SET
        hora_salida=COALESCE(hora_salida,datetime('now','localtime')),
        horas=ROUND(MAX(0,(julianday(COALESCE(hora_salida,datetime('now','localtime')))
            -julianday(hora_ingreso))*24),2),
        estado='cerrado', cerrado_fecha=datetime('now','localtime'),
        obs=CASE WHEN obs='' THEN 'Cierre automático' ELSE obs END
        WHERE usuario_id=? AND estado='abierto'""", (uid,))
    conn.execute("""INSERT INTO turnos_operadores(usuario_id,usuario_nombre,rol,fecha,hora_ingreso,estado)
        VALUES(?,?,?,?,datetime('now','localtime'),'abierto')""",
        (uid, session.get('nombre',''), session.get('rol',''), datetime.now().strftime('%Y-%m-%d')))
    conn.commit(); conn.close()

def cerrar_turno_operador(obs=''):
    """Cierra el turno abierto de la operadora actual: horas y viajes del turno."""
    uid = session.get('uid')
    if not uid: return None
    conn = get_db()
    row = conn.execute("""SELECT * FROM turnos_operadores
        WHERE usuario_id=? AND estado='abierto' ORDER BY id DESC LIMIT 1""", (uid,)).fetchone()
    if not row:
        conn.close()
        return None
    ahora = datetime.now()
    horas = _datetime_delta(row['hora_ingreso'])
    # Contar viajes despachados y perdidos dentro del rango del turno
    desp = perd = 0
    viajes_rows = conn.execute("SELECT fecha_solicitud,estado FROM viajes WHERE usuario_id=?", (uid,)).fetchall()
    for v in viajes_rows:
        lt = _viaje_local_time(v['fecha_solicitud'])
        if lt is None: continue
        if lt.date().isoformat() == row['fecha']:   # mismo día del turno
            desp += 1
            if v['estado'] == 'perdido': perd += 1
    conn.execute("""UPDATE turnos_operadores SET estado='cerrado',
        hora_salida=datetime('now','localtime'), horas=?, viajes_despachados=?, viajes_perdidos=?,
        cerrado_fecha=datetime('now','localtime'), obs=? WHERE id=?""",
        (horas, desp, perd, obs, row['id']))
    conn.commit(); conn.close()
    return {'id': row['id'], 'horas': horas, 'viajes_despachados': desp, 'viajes_perdidos': perd}

def _datetime_delta(iso_local):
    """Diferencia en horas entre una fecha/hora local y ahora."""
    import datetime as _dt
    try:
        naive = _dt.datetime.fromisoformat(iso_local)
        return (datetime.now() - naive).total_seconds() / 3600.0
    except Exception:
        return 0.0


def _generar_reporte_cierre(conn, fecha):
    """
    Genera reporte completo de cierre del día y lo guarda en JSON.
    """
    import os

    # Recopilar datos del día
    viajes = rows_to_list(conn.execute(
        "SELECT * FROM viajes WHERE date(fecha_solicitud)=?", (fecha,)).fetchall())
    total = len(viajes)
    completados = sum(1 for v in viajes if v.get('estado') in ('asignado', 'completado'))
    perdidos = sum(1 for v in viajes if v.get('estado') == 'perdido')
    cancelados = sum(1 for v in viajes if v.get('estado') in ('cancelado_cliente', 'cancelado_operador'))
    efectividad = round((completados / total * 100)) if total else 0

    # Top unidades
    topo = {}
    for v in viajes:
        n = v.get('numero_unidad')
        if n: topo[n] = topo.get(n, 0) + 1
    top_unidades = sorted(topo.items(), key=lambda x: -x[1])[:10]

    # Conductores QAP
    qap_list = rows_to_list(conn.execute(
        "SELECT DISTINCT numero_unidad FROM unidades_actividad WHERE date(fecha_hora)=? AND accion='qap'",
        (fecha,)).fetchall())
    unidades_qap = [a['numero_unidad'] for a in qap_list]

    # Alertas SOS
    sos = rows_to_list(conn.execute(
        "SELECT * FROM alertas_sos WHERE date(fecha)=?", (fecha,)).fetchall())

    # Reservas pendientes para mañana
    from datetime import timedelta
    manana = (date.fromisoformat(fecha) + timedelta(days=1)).isoformat()
    reservas_manana = rows_to_list(conn.execute(
        "SELECT * FROM reservas WHERE date(fecha_viaje)=? AND estado IN ('pendiente','confirmada')",
        (manana,)).fetchall())

    # Calificaciones del día
    califs = rows_to_list(conn.execute(
        "SELECT AVG(puntuacion) as promedio, COUNT(*) as total FROM calificaciones WHERE date(fecha)=?",
        (fecha,)).fetchall())
    calif_data = califs[0] if califs else {'promedio': 0, 'total': 0}

    # WhatsApp enviados
    wa_count = conn.execute(
        "SELECT COUNT(*) as n FROM mensajes_whatsapp WHERE date(fecha)=?", (fecha,)).fetchone()
    wa_total = wa_count['n'] if wa_count else 0

    # Construir reporte
    reporte = {
        'fecha': fecha,
        'generado': datetime.now().isoformat(),
        'resumen': {
            'total_viajes': total,
            'completados': completados,
            'perdidos': perdidos,
            'cancelados': cancelados,
            'efectividad_pct': efectividad,
            'unidades_qap': len(unidades_qap),
            'alertas_sos': len(sos),
            'calificacion_promedio': round(calif_data.get('promedio', 0) or 0, 2),
            'calificaciones_total': calif_data.get('total', 0),
            'whatsapp_enviados': wa_total,
        },
        'top_unidades': [{'unidad': u, 'viajes': c} for u, c in top_unidades],
        'unidades_qap': sorted(unidades_qap),
        'alertas_sos': [{'unidad': s.get('numero_unidad'), 'conductor': s.get('conductor_nombre'), 'hora': s.get('fecha')} for s in sos],
        'reservas_manana': [{'id': r.get('id'), 'cliente': r.get('cliente_nombre'), 'hora': r.get('hora_viaje')} for r in reservas_manana],
    }

    # Guardar archivo
    try:
        reportes_dir = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos', 'reportes_cierre')
        os.makedirs(reportes_dir, exist_ok=True)
        filepath = os.path.join(reportes_dir, f'reporte_{fecha}.json')
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(reporte, f, indent=2, ensure_ascii=False)
        print(f"[CTO] Reporte de cierre guardado: {filepath}")
    except Exception as e:
        logger.error(f"Error guardando reporte de cierre: {e}")

    # Log en bitácora
    log_action('REPORTE_CIERRE', f"Fecha: {fecha} | Viajes: {total} | Efectividad: {efectividad}%", 'sistema')

    return reporte


# ── CIERRE AUTOMÁTICO A MEDIANOCHE (QRT TODOS LOS DÍAS 00:00) ─────────────
def _cierre_automatico_medianoche():
    """Cada día: 23:58 reporte de cierre, 00:00 QRT cierre."""
    import time as _time
    from datetime import timedelta
    print("[CTO] Hilo de cierre medianoche activo (Reporte + QRT).")
    while True:
        conn = None
        try:
            # --- FASE 1: Generar reporte a las 23:58 ---
            now = datetime.now()
            prox_repo = now.replace(hour=23, minute=58, second=5, microsecond=0)
            if prox_repo <= now:
                prox_repo += timedelta(days=1)
            seg_repo = (prox_repo - now).total_seconds()
            print(f"[CTO] Reporte cierre en {int(seg_repo//3600)}h {int((seg_repo%3600)//60)}m")
            _time.sleep(max(seg_repo, 1))

            try:
                conn = get_db()
                fecha_hoy = date.today().isoformat()
                reporte = _generar_reporte_cierre(conn, fecha_hoy)
                sse_broadcast('cierre_reporte', {
                    'fecha': fecha_hoy,
                    'resumen': reporte.get('resumen', {}),
                    'ts': datetime.now().isoformat()
                })
            except Exception as e:
                print(f"[CTO] Error generando reporte de cierre: {e}")
            finally:
                if conn:
                    try: conn.close()
                    except: pass
                conn = None

            # --- FASE 2: Cierre QRT a las 00:00:05 ---
            now2 = datetime.now()
            prox_qrt = now2.replace(hour=0, minute=0, second=5, microsecond=0)
            if prox_qrt <= now2:
                prox_qrt += timedelta(days=1)
            seg_qrt = (prox_qrt - now2).total_seconds()
            print(f"[CTO] QRT automatico en {int(seg_qrt//3600)}h {int((seg_qrt%3600)//60)}m")
            _time.sleep(max(seg_qrt, 1))

            try:
                conn = get_db()
                kv = conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
                actividad = json.loads(kv['value']) if kv else []
                by_unit = {}
                for a in actividad:
                    u = a['numero_unidad']
                    if u not in by_unit or a['fecha_hora'] > by_unit[u]['fecha_hora']:
                        by_unit[u] = a
                activas_qap = [u for u, a in by_unit.items() if a['accion'] == 'qap']
                if activas_qap:
                    ahora = datetime.now().isoformat()
                    max_id = max((a.get('id',0) for a in actividad), default=0)
                    for i, u in enumerate(activas_qap):
                        actividad.append({
                            'id': max_id + i + 1, 'numero_unidad': u,
                            'accion': 'salida', 'fecha_hora': ahora,
                            'obs': 'QRT - Cierre automatico medianoche'
                        })
                    conn.execute(
                        "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",
                        (json.dumps(actividad, ensure_ascii=False),))
                    conn.execute("DELETE FROM unidades_actividad")
                    for a in actividad:
                        conn.execute(
                            "INSERT OR REPLACE INTO unidades_actividad(id,numero_unidad,accion,fecha_hora,obs) VALUES(?,?,?,?,?)",
                            (a.get('id'), a.get('numero_unidad'), a.get('accion'), a.get('fecha_hora'), a.get('obs','')))
                    conn.execute(
                        "INSERT INTO bitacora(usuario_id,usuario_nombre,accion,detalle,modulo) VALUES(0,'Sistema','CIERRE_QRT_AUTO',?,?)",
                        (f"{len(activas_qap)} unidades cerradas: {activas_qap}", 'sistema'))
                    conn.commit()
                    try:
                        sse_broadcast('update', {'key': 'unidades_actividad', 'ts': ahora})
                    except Exception: pass
                    print(f"[CTO] QRT automatico: {len(activas_qap)} unidades cerradas")
                else:
                    print("[CTO] Medianoche: ninguna unidad en QAP, nada que cerrar.")
            except Exception as e:
                print(f"[CTO] Error en cierre QRT: {e}")
            finally:
                if conn:
                    try: conn.close()
                    except: pass
                conn = None

        except Exception as e:
            print(f"[CTO] Error en hilo de cierre: {e}")
            _time.sleep(3600)



# ── RECUPERACIÓN AL ARRANCAR: QAP DE DÍAS ANTERIORES ────────────────────────
def _recuperar_qap_dia_anterior():
    """Al encender, retira automáticamente unidades que quedaron QAP de días
    anteriores (guardia para cuando la PC estuvo apagada a medianoche)."""
    print("[CTO] Verificando QAP de días anteriores...")
    conn = None
    try:
        conn = get_db()
        hoy = date.today().isoformat()
        kv = conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
        actividad = json.loads(kv['value']) if kv else []
        by_unit = {}
        for a in actividad:
            u = a['numero_unidad']
            if u not in by_unit or a['fecha_hora'] > by_unit[u]['fecha_hora']:
                by_unit[u] = a
        viejas = sorted([u for u, a in by_unit.items()
                         if a['accion'] == 'qap' and a['fecha_hora'][:10] < hoy])
        if viejas:
            ahora = datetime.now().isoformat()
            max_id = max((a.get('id', 0) for a in actividad), default=0)
            for i, u in enumerate(viejas):
                actividad.append({
                    'id': max_id + i + 1, 'numero_unidad': u,
                    'accion': 'salida', 'fecha_hora': ahora,
                    'obs': 'QRT - Día anterior al encender'
                })
            conn.execute(
                "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",
                (json.dumps(actividad, ensure_ascii=False),))
            conn.execute("DELETE FROM unidades_actividad")
            for a in actividad:
                conn.execute(
                    "INSERT OR REPLACE INTO unidades_actividad(id,numero_unidad,accion,fecha_hora,obs) VALUES(?,?,?,?,?)",
                    (a.get('id'), a.get('numero_unidad'), a.get('accion'), a.get('fecha_hora'), a.get('obs', '')))
            conn.execute(
                "INSERT INTO bitacora(usuario_id,usuario_nombre,accion,detalle,modulo) VALUES(0,'Sistema','RECUPERACION_QAP_ARRANQUE',?,?)",
                (f"{len(viejas)} unidades retiradas del día anterior: {viejas}", 'sistema'))
            conn.commit()
            try:
                sse_broadcast('update', {'key': 'unidades_actividad', 'ts': ahora})
            except Exception:
                pass
            print(f"[CTO] Recuperación arranque: {len(viejas)} unidades retiradas (QAP anterior): {viejas}")
        else:
            print("[CTO] Recuperación arranque: sin QAP de días anteriores.")
    except Exception as e:
        print(f"[CTO] Error en recuperación QAP al arrancar: {e}")
    finally:
        if conn:
            try: conn.close()
            except: pass



@app.route('/') 
def index():
    resp = send_from_directory(STATIC_DIR,'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ── AUTH ───────────────────────────────────────────────────────────────────
@app.route('/api/login',methods=['POST'])
def login():
    d=request.get_json(force=True) or {}
    cedula=str(d.get('cedula','')).strip(); pwd=str(d.get('password','')).strip()
    if not cedula or not pwd: return jsonify({'ok':False,'error':'Ingrese cédula y contraseña'}),400
    conn=get_db()
    row=conn.execute("SELECT * FROM usuarios WHERE cedula=? AND activo=1",(cedula,)).fetchone()
    if not row: conn.close(); return jsonify({'ok':False,'error':'Usuario no encontrado. Verifique la cédula'}),401
    ph=row['password_hash']
    if ph and ph!=hash_pass(pwd): conn.close(); return jsonify({'ok':False,'error':'Contraseña incorrecta'}),401
    # Validar horario de acceso configurado (roles/permisos)
    hi=row['horario_inicio']; hf=row['horario_fin']
    if hi and hf:
        ahora=datetime.now().strftime('%H:%M')
        dentro = (ahora>=hi and ahora<=hf) if hi<=hf else (ahora>=hi or ahora<=hf)
        if not dentro:
            conn.close()
            return jsonify({'ok':False,'error':f'Fuera de su horario de acceso ({hi} - {hf}). Actual: {ahora}'}),403
    conn.execute("UPDATE usuarios SET ultimo_acceso=datetime('now','localtime') WHERE id=?",(row['id'],))
    conn.commit(); conn.close()
    # Guardar en sesión Flask (cookies) Y devolver datos completos
    session['uid']=row['id']; session['nombre']=row['nombre']; session['rol']=row['rol']
    session.permanent = True
    log_action('LOGIN','Ingreso al sistema','auth')
    abrir_turno_operador()   # registro de turno (horas trabajadas)
    return jsonify({'ok':True,'nombre':row['nombre'],'rol':row['rol'],'id':row['id'],'cedula':row['cedula']})

@app.route('/api/logout',methods=['POST'])
def logout():
    cerrar_turno_operador('Cierre por cierre de sesión')
    log_action('LOGOUT','','auth'); session.clear(); return jsonify({'ok':True})

@app.route('/api/session')
def get_session():
    if 'uid' not in session:
        return jsonify({'logged': False})
    return jsonify({'logged':True,'nombre':session.get('nombre'),'rol':session.get('rol'),'uid':session.get('uid')})

@app.route('/api/usuarios',methods=['GET'])
def get_usuarios():
    conn=get_db()
    rows=conn.execute("SELECT id,cedula,nombre,rol,activo,ultimo_acceso,horario_inicio,horario_fin FROM usuarios ORDER BY id").fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

@app.route('/api/usuarios',methods=['POST'])
def create_usuario():
    d=request.get_json(force=True) or {}
    cedula=str(d.get('cedula','')).strip(); nombre=str(d.get('nombre','')).strip()
    if not cedula or not nombre: return jsonify({'ok':False,'error':'Datos incompletos'}),400
    conn=get_db()
    try:
        conn.execute("INSERT INTO usuarios(cedula,nombre,password_hash,rol,activo,horario_inicio,horario_fin) VALUES(?,?,?,?,1,?,?)",
                     (cedula,nombre,hash_pass(d.get('password','cto2024')),d.get('rol','operadora'),
                      d.get('horario_inicio','') or '', d.get('horario_fin','') or ''))
        conn.commit(); conn.close()
        log_action('CREAR_USUARIO',f"{nombre}",'admin')
        return jsonify({'ok':True})
    except sqlite3.IntegrityError: conn.close(); return jsonify({'ok':False,'error':'Cédula ya registrada'}),409

@app.route('/api/usuarios/<int:uid>',methods=['PUT'])
def update_usuario(uid):
    d=request.get_json(force=True) or {}; conn=get_db()
    # Leer valores actuales para no pisar con None
    row = conn.execute("SELECT * FROM usuarios WHERE id=?",(uid,)).fetchone()
    if not row: conn.close(); return jsonify({'ok':False,'error':'Usuario no encontrado'}),404
    nombre  = d.get('nombre')  if d.get('nombre')  else row['nombre']
    rol     = d.get('rol')     if d.get('rol')     else row['rol']
    activo  = (1 if d['activo'] else 0) if 'activo' in d else row['activo']
    hi = d.get('horario_inicio') if 'horario_inicio' in d else row['horario_inicio']
    hf = d.get('horario_fin')    if 'horario_fin'    in d else row['horario_fin']
    if d.get('password'):
        conn.execute("UPDATE usuarios SET nombre=?,rol=?,activo=?,password_hash=?,horario_inicio=?,horario_fin=? WHERE id=?",
                     (nombre, rol, activo, hash_pass(d['password']), hi or '', hf or '', uid))
    else:
        conn.execute("UPDATE usuarios SET nombre=?,rol=?,activo=?,horario_inicio=?,horario_fin=? WHERE id=?",
                     (nombre, rol, activo, hi or '', hf or '', uid))
    conn.commit(); conn.close()
    log_action('EDITAR_USUARIO',f"id={uid}",'admin')
    return jsonify({'ok':True})

# ── KV STORE ───────────────────────────────────────────────────────────────
@app.route('/api/load/<key>')
def api_load(key):
    # Conductores: desde tabla SQL con tipo/unidad_base/unidad_codigo
    if key == 'conductores':
        conn=get_db()
        rows=conn.execute("SELECT * FROM conductores WHERE estado='activo' ORDER BY unidad_base").fetchall()
        conn.close()
        if rows:
            result=[]
            for r in rows:
                d=dict(r)
                d['unidad']=d['unidad_base']
                d['tipo']=d.get('tipo','socio')
                d['unidad_codigo']=d.get('unidad_codigo',d['unidad_base'])
                result.append(d)
            return jsonify(result)
        return jsonify(CONDUCTORES_EMBEDDED)
    conn=get_db(); row=conn.execute("SELECT value FROM kv_store WHERE key=?",(key,)).fetchone(); conn.close()
    if row:
        try: return jsonify(json.loads(row['value']))
        except Exception as e: logger.warning(f"JSON parse error kv_store: {e}")
    return jsonify([])

def _normalizar_viajes(data, conn):
    """Autoridad única de IDs para los viajes.
    - Actualización de un viaje YA conocido (mismo id Y misma fecha_solicitud):
      conserva su id y fusiona campos (sin revertir estados 'completado').
    - Un viaje con un id que EN ESTE ALMACÉN pertenece a otro registro
      (fecha_solicitud distinta) o menor/igual al máximo actual: es numeración
      reiniciada o colisión entre pantallas → se REUBICA a un id fresco.
    Devuelve la lista definitiva (kv + tabla) sin duplicados ni sobrescrituras."""
    if not isinstance(data, list):
        return []

    def _es_el_mismo(a, b):
        return a.get('id') == b.get('id') and (a.get('fecha_solicitud') or '') == (b.get('fecha_solicitud') or '')

    actuales = {}
    row = conn.execute("SELECT value FROM kv_store WHERE key='viajes'").fetchone()
    if row and row['value']:
        try:
            for v in json.loads(row['value']):
                if isinstance(v, dict) and v.get('id') is not None:
                    actuales[int(v['id'])] = v
        except Exception as e:
            logger.warning(f"_normalizar_viajes kv parse: {e}")
    maxid = max(actuales, default=0)
    usados = set(actuales)
    pendientes = []
    for v in data:
        if not isinstance(v, dict):
            continue
        try:
            vid = int(v['id'])
        except Exception:
            vid = None
        fecha = v.get('fecha_asignacion') or v.get('fecha_solicitud') or v.get('fecha') or ''
        if vid is None:
            pendientes.append(('', v))
        elif vid in actuales:
            existe = actuales[vid]
            if _es_el_mismo(existe, v):
                # Mismo viaje: update sin revertir un 'completado'
                if existe.get('estado') == 'completado' and v.get('estado') != 'completado':
                    v['estado'] = 'completado'
                if existe.get('fecha_completado') and not v.get('fecha_completado'):
                    v['fecha_completado'] = existe['fecha_completado']
                actuales[vid] = v
            else:
                pendientes.append((fecha, vid, v))
        else:
            pendientes.append((fecha, vid, v))
    pendientes.sort(key=lambda x: (x[0], x[1] if len(x) == 3 else 0))
    base = max(usados, default=0)
    for item in pendientes:
        if len(item) == 2:
            v = item[1]
            maxid += 1
            while maxid in usados:
                maxid += 1
            v['id'] = maxid
            usados.add(maxid)
            actuales[maxid] = v
        else:
            _, vid, v = item
            if vid in usados or vid <= base:
                # id ocupado por otro viaje, o numeración reiniciada → id fresco
                maxid += 1
                while maxid in usados:
                    maxid += 1
                v['id'] = maxid
                usados.add(maxid)
                actuales[maxid] = v
            else:
                usados.add(vid)
                actuales[vid] = v
    return list(actuales.values())

@app.route('/api/save/<key>',methods=['POST'])
def api_save(key):
    data=request.get_json(force=True)
    if data is None: return jsonify({'ok':False,'error':'No JSON'}),400
    conn=get_db()
    if key=='viajes':
        data=_normalizar_viajes(data, conn)
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 (key,json.dumps(data,ensure_ascii=False)))
    _sync_rel(conn,key,data); conn.commit(); conn.close()
    return jsonify({'ok':True})

def _sync_rel(conn,key,data):
    if key=='viajes':
        incoming_ids=set(v.get('id') for v in data if v.get('id'))
        if incoming_ids:
            placeholders=','.join('?'*len(incoming_ids))
            conn.execute(f"DELETE FROM viajes WHERE id IN ({placeholders})",list(incoming_ids))
        for v in data:
            conn.execute("""INSERT OR REPLACE INTO viajes(id,fecha_solicitud,id_cliente,cliente_nombre,cliente_tel,
                direccion_recogida,referencia_recogida,destino,observaciones,numero_unidad,id_conductor,
                conductor_nombre,conductor_tel,fecha_asignacion,estado,usuario_id,usuario_nombre) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (v.get('id'),v.get('fecha_solicitud'),v.get('id_cliente'),v.get('cliente_nombre'),v.get('cliente_tel'),
                 v.get('direccion_recogida'),v.get('referencia_recogida'),v.get('destino'),v.get('observaciones'),
                 v.get('numero_unidad'),v.get('id_conductor'),v.get('conductor_nombre'),v.get('conductor_tel'),
                 v.get('fecha_asignacion'),v.get('estado'),v.get('usuario_id'),v.get('usuario_nombre')))
    elif key=='unidades_actividad':
        incoming_ids=set(a.get('id') for a in data if a.get('id'))
        if incoming_ids:
            placeholders=','.join('?'*len(incoming_ids))
            conn.execute(f"DELETE FROM unidades_actividad WHERE id IN ({placeholders})",list(incoming_ids))
        for a in data:
            conn.execute("INSERT OR REPLACE INTO unidades_actividad(id,numero_unidad,accion,fecha_hora,obs) VALUES(?,?,?,?,?)",
                         (a.get('id'),a.get('numero_unidad'),a.get('accion'),a.get('fecha_hora'),a.get('obs','')))
    elif key=='pagos_mensuales':
        incoming_ids=set(p.get('id') for p in data if p.get('id'))
        if incoming_ids:
            placeholders=','.join('?'*len(incoming_ids))
            conn.execute(f"DELETE FROM pagos_mensuales WHERE id IN ({placeholders})",list(incoming_ids))
        for p in data:
            conn.execute("INSERT OR REPLACE INTO pagos_mensuales(id,id_conductor,anio,mes,monto_base,multas,total,estado,fecha_pago) VALUES(?,?,?,?,?,?,?,?,?)",
                         (p.get('id'),p.get('id_conductor'),p.get('anio'),p.get('mes'),p.get('monto_base',0),p.get('multas',0),p.get('total',0),p.get('estado','pendiente'),p.get('fecha_pago')))
    elif key=='multas':
        incoming_ids=set(m.get('id') for m in data if m.get('id'))
        if incoming_ids:
            placeholders=','.join('?'*len(incoming_ids))
            conn.execute(f"DELETE FROM multas WHERE id IN ({placeholders})",list(incoming_ids))
        for m in data:
            conn.execute("INSERT OR REPLACE INTO multas(id,id_conductor,concepto,monto,anio,mes,pagada,fecha) VALUES(?,?,?,?,?,?,?,?)",
                         (m.get('id'),m.get('id_conductor'),m.get('concepto'),m.get('monto',0),m.get('anio'),m.get('mes'),1 if m.get('pagada') else 0,m.get('fecha')))

# ── CLIENTES ───────────────────────────────────────────────────────────────
@app.route('/api/clientes/buscar')
def buscar_clientes():
    import re as _re
    q=request.args.get('q','').strip()
    if len(q)<2: return jsonify([])
    conn=get_db()
    ql='%'+q.lower()+'%'
    sql_matches=conn.execute(
        "SELECT id,nombre,telefono,telefono2,observaciones FROM clientes WHERE lower(nombre) LIKE ? OR lower(telefono) LIKE ? OR lower(telefono2) LIKE ? LIMIT 8",
        (ql,ql,ql)).fetchall()
    row=conn.execute("SELECT value FROM kv_store WHERE key='clientes'").fetchone()
    kv_by_id={}
    qlow=q.lower()
    q_digits=_re.sub(r'[^0-9]','',q)
    q_suf=q_digits[-7:] if len(q_digits)>=7 else ''
    def _norm(t): return _re.sub(r'[^0-9]','', str(t or ''))
    def _match(c):
        tel=_norm(c.get('telefono','')); tel2=_norm(c.get('telefono2',''))
        if qlow in c.get('telefono','').lower() or qlow in c.get('telefono2','').lower() or qlow in c.get('nombre','').lower():
            return True
        if q_suf:
            if tel and (tel.endswith(q_suf) or (len(tel)>=7 and q_suf.endswith(tel[-7:]))): return True
            if tel2 and (tel2.endswith(q_suf) or (len(tel2)>=7 and q_suf.endswith(tel2[-7:]))): return True
            if q_digits and tel and (q_digits in tel or tel in q_digits): return True
            if q_digits and tel2 and (q_digits in tel2 or tel2 in q_digits): return True
        return False
    if row and row['value']:
        try:
            for c in json.loads(row['value']):
                if _match(c):
                    kv_by_id[c['id']]=c
        except Exception as e: logger.warning(f"Contact search parse error: {e}")
    # Si no hay suficientes coincidencias y la búsqueda es por teléfono, ampliar con suffix scan (tolera 0 inicial)
    if len(sql_matches)<8 and q_suf:
        try:
            extra=conn.execute("SELECT id,nombre,telefono,telefono2,observaciones FROM clientes WHERE telefono LIKE ? OR telefono2 LIKE ? LIMIT 8", ('%'+q_suf+'%','%'+q_suf+'%')).fetchall()
            # merge extra into sql_matches avoiding duplicates
            seen=set(r['id'] for r in sql_matches)
            for r in extra:
                if r['id'] not in seen:
                    sql_matches=list(sql_matches)+[r]
                    seen.add(r['id'])
                    if len(sql_matches)>=8: break
        except Exception: pass
    results=[]
    seen_ids=set()
    for r in sql_matches:
        c=dict(r)
        if c['id'] in kv_by_id:
            merged=dict(kv_by_id[c['id']])
            merged['telefono']=c.get('telefono',merged.get('telefono',''))
            if not merged.get('direcciones'):
                merged['direcciones']=[{'dir':'Sin dirección registrada','ref':'Actualizar desde Clientes','tipo':'general','principal':True}]
            results.append(merged)
        else:
            last_viaje=conn.execute(
                "SELECT direccion_recogida, referencia_recogida FROM viajes WHERE id_cliente=? AND direccion_recogida IS NOT NULL AND direccion_recogida != '' ORDER BY id DESC LIMIT 1",
                (c['id'],)).fetchone()
            if last_viaje and last_viaje['direccion_recogida']:
                c['direcciones']=[{'dir':last_viaje['direccion_recogida'],'ref':last_viaje['referencia_recogida'] or '','tipo':'general','principal':True}]
            else:
                c['direcciones']=[{'dir':'Sin dirección registrada','ref':'Actualizar desde Clientes','tipo':'general','principal':True}]
            results.append(c)
        seen_ids.add(c['id'])
    conn.close()
    for c in kv_by_id.values():
        if c.get('id') not in seen_ids:
            if not c.get('direcciones'):
                c['direcciones']=[{'dir':'Sin dirección registrada','ref':'Actualizar desde Clientes','tipo':'general','principal':True}]
            results.append(c)
    return jsonify(results[:8])

@app.route('/api/clientes/total')
def total_clientes():
    conn=get_db(); row=conn.execute("SELECT COUNT(*) as n FROM clientes").fetchone(); conn.close()
    return jsonify({'total':row['n'] if row else 0})

@app.route('/api/clientes',methods=['POST'])
def crear_cliente():
    d=request.get_json(force=True) or {}
    nombre=d.get('nombre','').strip(); tel=d.get('telefono','').strip()
    if not nombre or not tel: return jsonify({'ok':False,'error':'Nombre y teléfono requeridos'}),400
    conn=get_db()
    row=conn.execute("SELECT value FROM kv_store WHERE key='clientes'").fetchone()
    clientes=json.loads(row['value']) if row else []
    nuevo_id=max((c['id'] for c in clientes),default=0)+1
    nuevo={'id':nuevo_id,'nombre':nombre,'telefono':tel,'telefono2':d.get('telefono2',''),
           'observaciones':d.get('observaciones',''),'fecha_registro':datetime.now().isoformat(),
           'direcciones':d.get('direcciones',[])}
    clientes.append(nuevo)
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('clientes',?,datetime('now','localtime'))",
                 (json.dumps(clientes,ensure_ascii=False),))
    conn.execute("INSERT OR REPLACE INTO clientes(id,nombre,telefono,telefono2,observaciones) VALUES(?,?,?,?,?)",
                 (nuevo_id,nombre,tel,d.get('telefono2',''),d.get('observaciones','')))
    conn.commit(); conn.close()
    log_action('CREAR_CLIENTE',f"{nombre}|{tel}",'clientes')
    return jsonify({'ok':True,'id':nuevo_id,'cliente':nuevo})

# ── CONTABILIDAD ───────────────────────────────────────────────────────────
@app.route('/api/contabilidad/generar-mes',methods=['POST'])
def generar_mes():
    d=request.get_json(force=True) or {}
    anio=d.get('anio',datetime.now().year); mes=d.get('mes',datetime.now().month)
    conn=get_db()
    conductores = _get_conductores(conn, solo_activos=False)
    creados=0
    for c in conductores:
        if c.get('estado')!='activo' or c.get('tipo') not in ('socio','concesionario'): continue
        base=50.0 if c.get('tipo')=='socio' else 30.0
        try:
            conn.execute("INSERT OR IGNORE INTO pagos_mensuales(id_conductor,anio,mes,monto_base,multas,total,estado) VALUES(?,?,?,?,0,?,'pendiente')",
                         (c['id'],anio,mes,base,base))
            if conn.execute("SELECT changes()").fetchone()[0]>0: creados+=1
        except Exception as e: logger.error(f"Payment generation failed for conductor: {e}")
    conn.commit()
    rows=conn.execute("SELECT * FROM pagos_mensuales ORDER BY id").fetchall()
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('pagos_mensuales',?,datetime('now','localtime'))",
                 (json.dumps(rows_to_list(rows),ensure_ascii=False),))
    conn.commit(); conn.close()
    log_action('GENERAR_PAGOS',f"{creados} pagos {mes}/{anio}",'contabilidad')
    return jsonify({'ok':True,'creados':creados,'mes':mes,'anio':anio})

@app.route('/api/contabilidad/registrar-pago',methods=['POST'])
def registrar_pago():
    d=request.get_json(force=True) or {}; conn=get_db()
    conn.execute("UPDATE pagos_mensuales SET estado='pagado',fecha_pago=date('now','localtime') WHERE id=?",(d.get('id'),))
    row=conn.execute("SELECT id_conductor FROM pagos_mensuales WHERE id=?",(d.get('id'),)).fetchone()
    if row:
        pend=conn.execute("SELECT COUNT(*) as n FROM pagos_mensuales WHERE id_conductor=? AND estado!='pagado'",(row['id_conductor'],)).fetchone()
        if pend and pend['n']==0:
            conn.execute("UPDATE conductores SET bloqueado=? WHERE id=?", (0, row['id_conductor']))
    rows=conn.execute("SELECT * FROM pagos_mensuales ORDER BY id").fetchall()
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('pagos_mensuales',?,datetime('now','localtime'))",
                 (json.dumps(rows_to_list(rows),ensure_ascii=False),))
    conn.commit(); conn.close()
    log_action('REGISTRAR_PAGO',f"Pago id={d.get('id')}",'contabilidad')
    return jsonify({'ok':True})

@app.route('/api/contabilidad/estado/<anio>/<mes>')
def estado_contabilidad(anio,mes):
    conn=get_db()
    pagos=rows_to_list(conn.execute("SELECT * FROM pagos_mensuales WHERE anio=? AND mes=?",(int(anio),int(mes))).fetchall())
    multas=rows_to_list(conn.execute("SELECT * FROM multas WHERE anio=? AND mes=?",(int(anio),int(mes))).fetchall())
    recaudado=conn.execute("SELECT COALESCE(SUM(total),0) as s FROM pagos_mensuales WHERE anio=? AND mes=? AND estado='pagado'",(int(anio),int(mes))).fetchone()['s']
    pendiente=conn.execute("SELECT COALESCE(SUM(total),0) as s FROM pagos_mensuales WHERE anio=? AND mes=? AND estado!='pagado'",(int(anio),int(mes))).fetchone()['s']
    conn.close()
    return jsonify({'pagos':pagos,'multas':multas,'total_recaudado':recaudado,'total_pendiente':pendiente})

@app.route('/api/contabilidad/semanas/<int:anio>')
def contabilidad_semanas(anio):
    """Recaudado por semana del año (basado en fecha_pago) para la gráfica semanal."""
    conn=get_db()
    semanas=rows_to_list(conn.execute("""
        SELECT CAST(strftime('%W',fecha_pago) AS INTEGER)+1 AS semana,
               SUM(total) AS recaudado, COUNT(*) AS pagos
        FROM pagos_mensuales
        WHERE anio=? AND estado='pagado' AND fecha_pago IS NOT NULL
        GROUP BY CAST(strftime('%W',fecha_pago) AS INTEGER)
        ORDER BY semana""",(int(anio),)).fetchall())
    total=conn.execute("SELECT COALESCE(SUM(total),0) as s FROM pagos_mensuales WHERE anio=? AND estado='pagado' AND fecha_pago IS NOT NULL",(int(anio),)).fetchone()['s']
    conn.close()
    return jsonify({'anio':int(anio),'semanas':semanas,'total':total})

@app.route('/api/contabilidad/multa',methods=['POST'])
def aplicar_multa():
    d=request.get_json(force=True) or {}
    idc=d.get('id_conductor'); monto=float(d.get('monto',0))
    anio=d.get('anio',datetime.now().year); mes=d.get('mes',datetime.now().month)
    if not idc or monto<=0: return jsonify({'ok':False,'error':'Datos incompletos'}),400
    conn=get_db()
    conn.execute("INSERT INTO multas(id_conductor,concepto,monto,anio,mes,pagada) VALUES(?,?,?,?,?,0)",
                 (idc,d.get('concepto',''),monto,anio,mes))
    conn.execute("UPDATE pagos_mensuales SET multas=multas+?,total=monto_base+multas+?,estado=CASE WHEN estado='pagado' THEN 'pendiente' ELSE estado END WHERE id_conductor=? AND anio=? AND mes=?",
                 (monto,monto,idc,anio,mes))
    conn.execute("UPDATE conductores SET bloqueado=? WHERE id=?", (1, idc))
    rows=conn.execute("SELECT * FROM multas ORDER BY id").fetchall()
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('multas',?,datetime('now','localtime'))",
                 (json.dumps(rows_to_list(rows),ensure_ascii=False),))
    conn.commit(); conn.close()
    log_action('APLICAR_MULTA',f"U{idc}: {d.get('concepto')} ${monto}",'contabilidad')
    return jsonify({'ok':True})

@app.route('/api/contabilidad/tipos-multa')
def tipos_multa():
    conn=get_db()
    rows=conn.execute("SELECT * FROM tipos_multa WHERE activo=1 ORDER BY nombre").fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── ESTADÍSTICAS ───────────────────────────────────────────────────────────
@app.route('/api/estadisticas/resumen')
def estadisticas_resumen():
    anio=request.args.get('anio',str(datetime.now().year)); conn=get_db()
    viajes_mes=rows_to_list(conn.execute("""SELECT strftime('%m',fecha_solicitud) as mes,COUNT(*) as total,
        SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as completados,
        SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) as perdidos
        FROM viajes WHERE strftime('%Y',fecha_solicitud)=? GROUP BY mes ORDER BY mes""",(anio,)).fetchall())
    horas_pico=rows_to_list(conn.execute("""SELECT strftime('%H',fecha_solicitud) as hora,COUNT(*) as total
        FROM viajes WHERE strftime('%Y',fecha_solicitud)=? GROUP BY hora ORDER BY hora""",(anio,)).fetchall())
    top_u=rows_to_list(conn.execute("""SELECT numero_unidad,conductor_nombre,COUNT(*) as viajes
        FROM viajes WHERE strftime('%Y',fecha_solicitud)=? AND estado='asignado'
        GROUP BY numero_unidad ORDER BY viajes DESC LIMIT 10""",(anio,)).fetchall())
    top_c=rows_to_list(conn.execute("""SELECT cliente_nombre,cliente_tel,COUNT(*) as viajes
        FROM viajes WHERE strftime('%Y',fecha_solicitud)=? AND estado='asignado'
        GROUP BY cliente_tel ORDER BY viajes DESC LIMIT 10""",(anio,)).fetchall())
    tots=dict(conn.execute("""SELECT COUNT(*) as total,
        SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as completados,
        SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) as perdidos,
        SUM(CASE WHEN estado LIKE 'cancelado%' THEN 1 ELSE 0 END) as cancelados
        FROM viajes WHERE strftime('%Y',fecha_solicitud)=?""",(anio,)).fetchone() or {})
    conn.close()
    return jsonify({'viajes_mes':viajes_mes,'horas_pico':horas_pico,'top_unidades':top_u,'top_clientes':top_c,'totales':tots,'anio':anio})

@app.route('/api/estadisticas/hoy')
def estadisticas_hoy():
    hoy=date.today().isoformat(); conn=get_db()
    v=dict(conn.execute("SELECT COUNT(*) as total,SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as completados,SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) as perdidos FROM viajes WHERE date(fecha_solicitud)=?",(hoy,)).fetchone() or {})
    q=conn.execute("SELECT COUNT(DISTINCT numero_unidad) as n FROM unidades_actividad WHERE date(fecha_hora)=? AND accion='qap'",(hoy,)).fetchone()
    r=conn.execute("SELECT COUNT(*) as n FROM reasignaciones rv JOIN viajes vj ON rv.id_viaje=vj.id WHERE date(vj.fecha_solicitud)=?",(hoy,)).fetchone()
    conn.close()
    return jsonify({'viajes':v,'unidades_qap':q['n'] if q else 0,'reasignaciones':r['n'] if r else 0,'fecha':hoy})

@app.route('/api/estadisticas/horas-hoy')
def estadisticas_horas_hoy():
    """Carreras por hora del día actual, para el gráfico del dashboard."""
    hoy=date.today().isoformat(); conn=get_db()
    rows=rows_to_list(conn.execute("""SELECT strftime('%H',fecha_solicitud) as hora,COUNT(*) as total,
        SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as completados,
        SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) as perdidos
        FROM viajes WHERE date(fecha_solicitud)=? GROUP BY hora ORDER BY hora""",(hoy,)).fetchall())
    conn.close()
    return jsonify({'fecha':hoy,'horas':rows})

# ── CIERRE DE TURNO ────────────────────────────────────────────────────────
@app.route('/api/turno/cerrar',methods=['POST'])
def cerrar_turno():
    hoy=date.today().isoformat(); conn=get_db()
    kv=conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    actividad=json.loads(kv['value']) if kv else []
    by_unit={}
    for a in actividad:
        u=a['numero_unidad']
        if u not in by_unit or a['fecha_hora']>by_unit[u]['fecha_hora']: by_unit[u]=a
    activas=[u for u,a in by_unit.items() if a['accion']=='qap']
    ahora=datetime.now().isoformat()
    max_id=max((a['id'] for a in actividad),default=0)
    for i,u in enumerate(activas):
        actividad.append({'id':max_id+i+1,'numero_unidad':u,'accion':'salida','fecha_hora':ahora,'obs':'Cierre de turno'})
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",
                 (json.dumps(actividad,ensure_ascii=False),))
    conn.execute("DELETE FROM unidades_actividad")
    for a in actividad:
        conn.execute("INSERT OR REPLACE INTO unidades_actividad(id,numero_unidad,accion,fecha_hora,obs) VALUES(?,?,?,?,?)",
                     (a['id'],a['numero_unidad'],a['accion'],a['fecha_hora'],a.get('obs','')))
    v=dict(conn.execute("SELECT COUNT(*) as total,SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as completados FROM viajes WHERE date(fecha_solicitud)=?",(hoy,)).fetchone() or {})
    conn.commit(); conn.close()
    log_action('CIERRE_TURNO',f"{len(activas)} unidades retiradas. Viajes: {v}",'operacion')
    return jsonify({'ok':True,'unidades_retiradas':len(activas),'resumen':v})

# ── BITÁCORA ───────────────────────────────────────────────────────────────
@app.route('/api/bitacora')
def get_bitacora():
    limit=int(request.args.get('limit',100)); modulo=request.args.get('modulo',''); conn=get_db()
    if modulo: rows=conn.execute("SELECT * FROM bitacora WHERE modulo=? ORDER BY fecha DESC LIMIT ?",(modulo,limit)).fetchall()
    else: rows=conn.execute("SELECT * FROM bitacora ORDER BY fecha DESC LIMIT ?",(limit,)).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── REPORTES ───────────────────────────────────────────────────────────────
@app.route('/api/reporte/viajes')
def reporte_viajes():
    """Reporte completo de viajes con filtros avanzados."""
    conn = get_db()
    params = []
    where = []

    # Filtros de fecha
    desde = request.args.get('desde', '')
    hasta = request.args.get('hasta', '')
    fecha = request.args.get('fecha', '')  # compat: una sola fecha
    if fecha:
        where.append("date(fecha_solicitud)=?")
        params.append(fecha)
    elif desde or hasta:
        if desde:
            where.append("date(fecha_solicitud)>=?")
            params.append(desde)
        if hasta:
            where.append("date(fecha_solicitud)<=?")
            params.append(hasta)

    # Filtro por unidad
    unidad = request.args.get('unidad', '')
    if unidad:
        where.append("numero_unidad=?")
        params.append(int(unidad))

    # Filtro por cliente (nombre o teléfono)
    cliente = request.args.get('cliente', '').strip()
    if cliente:
        where.append("(cliente_nombre LIKE ? OR cliente_tel LIKE ?)")
        params.extend([f'%{cliente}%', f'%{cliente}%'])

    # Filtro por operadora (usuario_nombre)
    operadora = request.args.get('operadora', '').strip()
    if operadora:
        where.append("usuario_nombre LIKE ?")
        params.append(f'%{operadora}%')

    # Filtro por estado
    estado = request.args.get('estado', '').strip()
    if estado:
        where.append("estado=?")
        params.append(estado)

    sql = "SELECT * FROM viajes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fecha_solicitud DESC LIMIT 5000"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify(rows_to_list(rows))

@app.route('/api/reporte/cliente/<int:id_cliente>')
def reporte_cliente(id_cliente):
    conn=get_db()
    rows=conn.execute("SELECT * FROM viajes WHERE id_cliente=? ORDER BY fecha_solicitud DESC LIMIT 100",(id_cliente,)).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── REPORTE WHATSAPP ──────────────────────────────────────────────────────
@app.route('/api/reporte/whatsapp')
def reporte_whatsapp():
    conn=get_db()
    desde=request.args.get('desde','')
    hasta=request.args.get('hasta','')
    tipo=request.args.get('tipo','')
    numero=request.args.get('numero','')
    q="SELECT * FROM mensajes_whatsapp WHERE 1=1"
    params=[]
    if desde: q+=" AND date(fecha)>=?"; params.append(desde)
    if hasta: q+=" AND date(fecha)<=?"; params.append(hasta)
    if tipo: q+=" AND tipo=?"; params.append(tipo)
    if numero: q+=" AND numero LIKE ?"; params.append(f'%{numero}%')
    q+=" ORDER BY fecha DESC LIMIT 5000"
    rows=conn.execute(q,params).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── RECLAMOS DE CLIENTES ───────────────────────────────────────────────────
@app.route('/api/reclamos', methods=['POST'])
def crear_reclamo():
    d=request.get_json(force=True) or {}
    id_viaje=d.get('id_viaje'); id_cliente=d.get('id_cliente'); motivo=(d.get('motivo') or '').strip()
    if not id_cliente or not motivo:
        return jsonify({'ok':False,'error':'Cliente y motivo requeridos'}),400
    conn=get_db()
    cliente=None
    try:
        row=conn.execute("SELECT * FROM clientes WHERE id=?",(id_cliente,)).fetchone()
        if row: cliente=dict(row)
    except Exception as e: logger.warning(f"Client lookup failed: {e}")
    if not cliente:
        kv=conn.execute("SELECT value FROM kv_store WHERE key='clientes'").fetchone()
        lista=json.loads(kv['value']) if (kv and kv['value']) else []
        cliente=next((x for x in lista if x.get('id')==id_cliente),None)
    num=None
    if id_viaje:
        v=conn.execute("SELECT numero_unidad FROM viajes WHERE id=?",(id_viaje,)).fetchone()
        num=v['numero_unidad'] if v else None
    cur=conn.execute("INSERT INTO reclamos(id_viaje,id_cliente,cliente_nombre,numero_unidad,motivo,estado) VALUES(?,?,?,?,?,'abierto')",
                     (id_viaje,id_cliente,(cliente or {}).get('nombre',''),num,motivo))
    conn.commit(); conn.close()
    log_action('CREAR_RECLAMO',f"Cliente: {(cliente or {}).get('nombre','')} — {motivo[:60]}",'reportes')
    return jsonify({'ok':True,'id':cur.lastrowid})

@app.route('/api/reclamos/cliente/<int:id_cliente>')
def reclamos_cliente(id_cliente):
    conn=get_db()
    rows=conn.execute("SELECT * FROM reclamos WHERE id_cliente=? ORDER BY fecha DESC, id DESC",(id_cliente,)).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── RECHAZOS DE UNIDAD ─────────────────────────────────────────────────────
@app.route('/api/rechazos/registrar',methods=['POST'])
def registrar_rechazo():
    d=request.get_json(force=True) or {}
    numero_unidad=d.get('numero_unidad')
    obs=str(d.get('observaciones','')).strip()
    if not numero_unidad or not obs:
        return jsonify({'ok':False,'error':'Unidad y observación son requeridos'}),400
    conn=get_db()
    conn.execute("""INSERT INTO rechazos_unidad(numero_unidad,conductor,motivo,observaciones,id_viaje,cliente_nombre,operadora)
        VALUES(?,?,?,?,?,?,?)""",
        (int(numero_unidad), d.get('conductor',''), d.get('motivo',''), obs,
         d.get('id_viaje'), d.get('cliente_nombre',''), d.get('operadora','')))
    conn.commit(); conn.close()
    log_action('RECHAZO', f"Unidad {numero_unidad} — {obs}", 'operaciones')
    return jsonify({'ok':True})

@app.route('/api/rechazos',methods=['GET'])
def listar_rechazos():
    conn=get_db()
    desde=request.args.get('desde','')
    hasta=request.args.get('hasta','')
    unidad=request.args.get('unidad','')
    q="SELECT * FROM rechazos_unidad WHERE 1=1"
    params=[]
    if desde: q+=" AND fecha>=?"; params.append(desde)
    if hasta: q+=" AND fecha<=?"; params.append(hasta+' 23:59:59')
    if unidad: q+=" AND numero_unidad=?"; params.append(int(unidad))
    q+=" ORDER BY fecha DESC LIMIT 5000"
    rows=conn.execute(q,params).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

# ── RESERVAS DE TAXIS ──────────────────────────────────────────────────────
@app.route('/api/reservas',methods=['GET'])
def get_reservas():
    conn=get_db()
    fecha=request.args.get('fecha','')
    estado=request.args.get('estado','')
    q="SELECT * FROM reservas WHERE 1=1"
    params=[]
    if fecha:
        q+=" AND fecha_viaje=?"; params.append(fecha)
    if estado:
        q+=" AND estado=?"; params.append(estado)
    q+=" ORDER BY fecha_viaje ASC, hora_viaje ASC"
    rows=conn.execute(q,params).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

@app.route('/api/reservas',methods=['POST'])
def crear_reserva():
    d=request.get_json(force=True) or {}
    cliente_nombre=str(d.get('cliente_nombre','')).strip()
    cliente_tel=str(d.get('cliente_tel','')).strip()
    fecha_viaje=str(d.get('fecha_viaje','')).strip()
    hora_viaje=str(d.get('hora_viaje','')).strip()
    direccion=str(d.get('direccion_recogida','')).strip()
    if not cliente_nombre or not fecha_viaje or not hora_viaje or not direccion:
        return jsonify({'ok':False,'error':'Nombre, fecha, hora y dirección son requeridos'}),400
    conn=get_db()
    uid=session.get('uid',0); uname=session.get('nombre','')
    cur=conn.execute("""INSERT INTO reservas(id_cliente,cliente_nombre,cliente_tel,fecha_viaje,hora_viaje,
        direccion_recogida,referencia,destino,observaciones,usuario_id,usuario_nombre)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (d.get('id_cliente'),cliente_nombre,cliente_tel,fecha_viaje,hora_viaje,
         direccion,str(d.get('referencia','')),str(d.get('destino','')),str(d.get('observaciones','')),
         uid,uname))
    conn.commit()
    rid=cur.lastrowid
    conn.close()
    log_action('CREAR_RESERVA',f"{cliente_nombre} - {fecha_viaje} {hora_viaje}",'reservas')
    sse_broadcast('update',{'key':'reservas','ts':datetime.now().isoformat()})
    return jsonify({'ok':True,'id':rid})

@app.route('/api/reservas/<int:rid>',methods=['PUT'])
def actualizar_reserva(rid):
    d=request.get_json(force=True) or {}
    conn=get_db()
    reserva=conn.execute("SELECT * FROM reservas WHERE id=?",(rid,)).fetchone()
    if not reserva: conn.close(); return jsonify({'ok':False,'error':'Reserva no encontrada'}),404
    updates=[]; params=[]
    for f in ['cliente_nombre','cliente_tel','fecha_viaje','hora_viaje','direccion_recogida',
              'referencia','destino','observaciones','numero_unidad','estado']:
        if f in d:
            updates.append(f+"=?"); params.append(d[f])
    if updates:
        updates.append("fecha_modificacion=datetime('now','localtime')")
        params.append(rid)
        conn.execute("UPDATE reservas SET "+",".join(updates)+" WHERE id=?",params)
        conn.commit()
    conn.close()
    sse_broadcast('update',{'key':'reservas','ts':datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/reservas/<int:rid>',methods=['DELETE'])
def eliminar_reserva(rid):
    conn=get_db()
    conn.execute("DELETE FROM reservas WHERE id=?",(rid,))
    conn.commit(); conn.close()
    sse_broadcast('update',{'key':'reservas','ts':datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/reservas/pendientes')
def reservas_pendientes():
    hoy=date.today().isoformat()
    conn=get_db()
    rows=conn.execute("SELECT * FROM reservas WHERE fecha_viaje=? AND estado IN ('pendiente','confirmada') ORDER BY hora_viaje",(hoy,)).fetchall()
    conn.close(); return jsonify(rows_to_list(rows))

@app.route('/api/reporte/contabilidad')
def reporte_contabilidad():
    anio=request.args.get('anio',datetime.now().year); mes=request.args.get('mes',datetime.now().month); conn=get_db()
    return jsonify({'pagos':rows_to_list(conn.execute("SELECT * FROM pagos_mensuales WHERE anio=? AND mes=?",(anio,mes)).fetchall()),
                    'multas':rows_to_list(conn.execute("SELECT * FROM multas WHERE anio=? AND mes=?",(anio,mes)).fetchall())})

# ── WHATSAPP ───────────────────────────────────────────────────────────────
# Anti-duplicado: evita reabrir la misma URL de WhatsApp dos veces en menos de 4s
# (protege contra doble clic, doble submit, o reintentos accidentales del frontend)
_wa_ultimo_envio = {}

@app.route('/api/whatsapp/abrir',methods=['POST'])
def whatsapp_abrir():
    import webbrowser, hashlib, time as _time
    data=request.get_json(force=True) or {}
    url=data.get('url','')
    numero=data.get('numero','')
    skip_open=data.get('skip_browser_open', False)
    if not url:
        return jsonify({'ok':False,'error':'URL vacía'}),400

    dedup_key = numero or hashlib.md5(url.encode()).hexdigest()
    ahora = _time.time()
    ultimo = _wa_ultimo_envio.get(dedup_key)
    if ultimo and (ahora - ultimo) < 4:
        log_action('WHATSAPP_DEDUP', f"Evitado doble envío a {numero or url[:40]}", 'whatsapp')
        return jsonify({'ok':True,'deduplicado':True})

    try:
        if not skip_open:
            webbrowser.open(url, new=0)
        _wa_ultimo_envio[dedup_key] = ahora
        log_action('WHATSAPP_ABIERTO', f"Destino: {numero or url[:60]}", 'whatsapp')
        return jsonify({'ok':True})
    except Exception as e:
        log_action('WHATSAPP_ERROR', f"{numero}: {str(e)}", 'whatsapp')
        return jsonify({'ok':False,'error':str(e)}),500

# ── BOT WHATSAPP COOPERATIVA ───────────────────────────────────────────────
# Número de la cooperativa: +593 99 948 3918
# Los clientes escriben al número → el bot detecta la solicitud de taxi
# → crea una pre-solicitud → la operadora la aprueba con un click → se despacha
WA_COOP_NUMERO = '+593999483918'  # Número de la cooperativa

def _carrera_wa_existente(viajes, solo10, excluir=None):
    """Busca una carrera del mismo número, sin unidad asignada y reciente (≤15 min)."""
    limite = (datetime.now() - timedelta(minutes=15)).isoformat()
    for v in viajes:
        if v.get('estado') != 'asignado':
            continue
        if v.get('id_conductor'):
            continue
        if v.get('id') == excluir:
            continue
        if v.get('fecha_solicitud', '') < limite:
            continue
        if solo10 and solo10 in str(v.get('cliente_tel', '')):
            return v
    return None

def _auto_viaje_desde_wa(conn, numero_limpio, solo10, nombre, direccion, id_bot, cliente_existente):
    """Crea la carrera (viaje) en el sistema al recibir una solicitud por WhatsApp.
    Si el mismo numero ya tiene una carrera reciente sin unidad asignada,
    actualiza la direccion en lugar de duplicar. Devuelve (viaje_id, ya_existia)."""
    viajes = rows_to_list(conn.execute("SELECT * FROM viajes ORDER BY id").fetchall())
    previo = _carrera_wa_existente(viajes, solo10)
    if previo:
        conn.execute("UPDATE viajes SET direccion_recogida=? WHERE id=?",
                     (direccion, previo['id']))
        conn.execute("UPDATE bot_mensajes SET id_viaje_generado=? WHERE id=?",
                     (previo['id'], id_bot))
        viajes = rows_to_list(conn.execute("SELECT * FROM viajes ORDER BY id").fetchall())
        conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('viajes',?,datetime('now','localtime'))",
                     (json.dumps(viajes, ensure_ascii=False),))
        return previo['id'], True
    # Buscar/crear cliente (misma lógica que bot_despachar)
    id_cliente = 0
    if solo10:
        if cliente_existente:
            id_cliente = cliente_existente.get('id') or 0
        if not id_cliente:
            cli = conn.execute("SELECT id FROM clientes WHERE telefono=?", (numero_limpio,)).fetchone()
            if cli:
                id_cliente = cli['id']
        if not id_cliente:
            cur = conn.execute("INSERT INTO clientes(nombre,telefono) VALUES(?,?)",
                               (nombre or 'Cliente WhatsApp', numero_limpio))
            id_cliente = cur.lastrowid
    ts = datetime.now().isoformat()
    cur = conn.execute("""INSERT INTO viajes(fecha_solicitud,id_cliente,cliente_nombre,cliente_tel,
        direccion_recogida,referencia_recogida,destino,observaciones,numero_unidad,
        id_conductor,conductor_nombre,conductor_tel,fecha_asignacion,estado,usuario_id,usuario_nombre)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, id_cliente, nombre or 'Cliente WhatsApp', numero_limpio,
         direccion, '', '', 'Solicitud por WhatsApp', 0,
         0, '', '', ts, 'asignado', 0, 'Bot WhatsApp'))
    viaje_id = cur.lastrowid
    conn.execute("UPDATE bot_mensajes SET id_viaje_generado=? WHERE id=?", (viaje_id, id_bot))
    viajes = rows_to_list(conn.execute("SELECT * FROM viajes ORDER BY id").fetchall())
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('viajes',?,datetime('now','localtime'))",
                 (json.dumps(viajes, ensure_ascii=False),))
    return viaje_id, False

@app.route('/api/bot/mensaje', methods=['POST'])
def bot_recibir_mensaje():
    """
    Endpoint para recibir mensajes de clientes vía WhatsApp Business API o webhook.
    El bot analiza el mensaje, extrae la dirección y crea una pre-solicitud.
    La operadora ve la alerta en tiempo real y despacha con un click.
    """
    import re as _re
    d = request.get_json(force=True) or {}
    numero = str(d.get('numero', d.get('from', ''))).strip()
    mensaje = str(d.get('mensaje', d.get('body', d.get('text', '')))).strip()
    nombre = str(d.get('nombre', d.get('pushName', ''))).strip()
    if not numero or not mensaje:
        return jsonify({'ok': False, 'error': 'Datos incompletos'}), 400

    # Limpiar número (quitar +, espacios)
    numero_limpio = _re.sub(r'[^0-9]', '', numero)

    # Analizar intent del mensaje
    msg_lower = mensaje.lower()
    keywords_taxi = ['taxi', 'servicio', 'necesito', 'quiero', 'pedir', 'carro',
                     'unidad', 'carrera', 'llevar', 'recoger', 'pasar', 'vengan',
                     'manden', 'enviar', 'solicito', 'dirección', 'direccion']
    is_taxi_request = any(k in msg_lower for k in keywords_taxi)

    # Detectar intención de carrera por FRASE (no solo palabras sueltas):
    # cubre "recojame en X", "pasen por X", "estoy en X", "necesito taxi en X", etc.
    patrones_carrera = [
        _re.compile(r'(?:en|desde|en la|direcci[oó]n|direccion|estoy en|me encuentro en|rec[oó]jame en|rec[oó]janme en|rec[oó]jeme en|rec[oó]jommen en|vengan por|vengan a|pasen por|pase por|manden|mandame|ubicaci[oó]n|ubicacion)\s+.+'),
        _re.compile(r'(?:necesito|quiero|pedir|requiero|ocupo|solicito)\s+(?:un\s+)?(?:tax[ií]|carro|servicio|unidad|carrera)\s*(?:en|para|desde|a|hacia)?\s*.+'),
    ]
    es_carrera = len(mensaje) >= 12 and any(p.search(msg_lower) for p in patrones_carrera)

    respuesta = ''
    estado = 'pendiente'

    if is_taxi_request or es_carrera:
        # Extraer dirección del mensaje
        dir_extraida = mensaje
        for patron in [r'(?:en|desde|en la|dirección|direccion|estoy en|me encuentro en)\s+(.+)',
                       r'(?:necesito|quiero|pedir)\s+(?:un\s+)?taxi\s+(?:en|desde)?\s*(.+)']:
            m = _re.search(patron, msg_lower)
            if m:
                pos = m.start(1)
                dir_extraida = mensaje[pos:pos+len(m.group(1))].strip()
                break

        conn = get_db()
        row_kv = conn.execute("SELECT value FROM kv_store WHERE key='clientes'").fetchone()
        clientes = json.loads(row_kv['value']) if row_kv else []
        tel_buscar = numero_limpio[-10:] if len(numero_limpio) >= 10 else numero_limpio
        cliente_existente = next((c for c in clientes if tel_buscar in str(c.get('telefono', ''))
                                  or tel_buscar in str(c.get('telefono2', ''))), None)

        # Crear registro en bot_mensajes
        conn.execute("""INSERT INTO bot_mensajes(numero_cliente, nombre_cliente, mensaje, respuesta, estado, direccion_extraida)
            VALUES(?,?,?,?,?,?)""",
            (numero, nombre, mensaje,
             f'Solicitud detectada. Dirección: {dir_extraida}',
             'pendiente_despacho' if es_carrera else 'respondido', dir_extraida))
        id_bot = conn.execute("SELECT last_insert_rowid() as id").fetchone()['id']

        # Cargar la carrera automáticamente al sistema
        viaje_id = 0
        if es_carrera:
            viaje_id, ya_existia = _auto_viaje_desde_wa(
                conn, numero_limpio, tel_buscar, nombre, dir_extraida, id_bot, cliente_existente)
        else:
            ya_existia = False
        conn.commit()
        conn.close()

        if es_carrera:
            respuesta = (f'Hola{" " + nombre if nombre else ""}! 🚕 Recibimos tu solicitud de taxi en *CTO 119*.\n\n'
                         f'📍 Dirección detectada: _{dir_extraida}_\n\n'
                         f'✅ Tu pedido está siendo procesado. Una operadora asignará tu unidad en breve.\n\n'
                         f'📞 Si hay algún cambio, llámanos al *119*.')
            # Broadcast SSE a operadoras
            sse_broadcast('bot_solicitud', {
                'id': id_bot,
                'numero': numero,
                'nombre': nombre or 'Cliente WhatsApp',
                'mensaje': mensaje,
                'direccion': dir_extraida,
                'telefono': tel_buscar,
                'cliente_id': cliente_existente['id'] if cliente_existente else None,
                'cliente_nombre': cliente_existente['nombre'] if cliente_existente else nombre or 'Nuevo cliente',
                'viaje_id': viaje_id,
                'ya_existia': ya_existia,
                'ts': datetime.now().isoformat()
            })
            sse_broadcast('update', {'key': 'viajes', 'ts': datetime.now().isoformat()})
            log_action('BOT_CARRERA_AUTO', f"WA:{numero} → viaje# {viaje_id} {dir_extraida[:50]}", 'bot_whatsapp')
        else:
            respuesta = ('Hola! 👋 Soy el bot de *CTO Cooperativa de Taxis Occidental 119*.\n\n'
                         '🚕 Para pedir un taxi, escríbeme tu *dirección* o escribe "TAXI" y tu ubicación.\n\n'
                         '📞 También puedes llamarnos al *119*.\n\n'
                         '_Ejemplo: "Necesito un taxi en Av. América y Colón"_')

    else:
        # Respuesta genérica para otros mensajes
        respuesta = ('Hola! 👋 Soy el bot de *CTO Cooperativa de Taxis Occidental 119*.\n\n'
                     '🚕 Para pedir un taxi, escríbeme tu *dirección* o escribe "TAXI" y tu ubicación.\n\n'
                     '📞 También puedes llamarnos al *119*.\n\n'
                     '_Ejemplo: "Necesito un taxi en Av. América y Colón"_')
        conn = get_db()
        conn.execute("INSERT INTO bot_mensajes(numero_cliente,nombre_cliente,mensaje,respuesta,estado) VALUES(?,?,?,?,?)",
                     (numero, nombre, mensaje, respuesta, 'respondido'))
        conn.commit(); conn.close()

    return jsonify({'ok': True, 'respuesta': respuesta, 'es_solicitud': is_taxi_request,
                    'numero_respuesta': WA_COOP_NUMERO})

@app.route('/api/bot/solicitudes')
def bot_listar_solicitudes():
    """Lista las solicitudes del bot pendientes de despacho para la operadora."""
    conn = get_db()
    rows = rows_to_list(conn.execute("""SELECT * FROM bot_mensajes
        WHERE estado='pendiente_despacho' ORDER BY fecha DESC LIMIT 50""").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/bot/solicitudes/historial')
def bot_historial():
    """Historial completo de mensajes del bot."""
    limit = int(request.args.get('limit', 100))
    conn = get_db()
    rows = rows_to_list(conn.execute("SELECT * FROM bot_mensajes ORDER BY fecha DESC LIMIT ?", (limit,)).fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/bot/despachar', methods=['POST'])
def bot_despachar():
    """La operadora aprueba y despacha una solicitud del bot — crea viaje REAL."""
    d = request.get_json(force=True) or {}
    id_bot = d.get('id_bot')
    numero_unidad = int(d.get('numero_unidad', 0))
    direccion = d.get('direccion', '')
    referencia = d.get('referencia', '')
    destino = d.get('destino', '')
    nombre_cliente = d.get('nombre_cliente', 'Cliente WhatsApp')
    telefono_cliente = d.get('telefono_cliente', '')
    observaciones = d.get('observaciones', '')
    if not id_bot or not numero_unidad:
        return jsonify({'ok': False, 'error': 'Datos incompletos'}), 400
    conn = get_db()
    # Buscar info del bot message
    bot_msg = conn.execute("SELECT * FROM bot_mensajes WHERE id=?", (id_bot,)).fetchone()
    viaje_existente = None
    if bot_msg:
        if not nombre_cliente or nombre_cliente == 'Cliente WhatsApp':
            nombre_cliente = bot_msg['nombre_cliente'] or 'Cliente WhatsApp'
        if not telefono_cliente:
            telefono_cliente = bot_msg['numero_cliente'] or ''
        if not direccion:
            direccion = bot_msg['direccion_extraida'] or bot_msg['mensaje'] or ''
        if bot_msg['id_viaje_generado']:
            viaje_existente = int(bot_msg['id_viaje_generado'])
    # Buscar conductor de la unidad
    conductores = _get_conductores(conn)
    cond = _find_active_conductor_server(conn, conductores, numero_unidad)
    # Buscar/crear cliente
    id_cliente = 0
    if telefono_cliente:
        cli = conn.execute("SELECT id FROM clientes WHERE telefono=?", (telefono_cliente,)).fetchone()
        if cli:
            id_cliente = cli['id']
        else:
            cur = conn.execute("INSERT INTO clientes(nombre,telefono) VALUES(?,?)",
                               (nombre_cliente, telefono_cliente))
            id_cliente = cur.lastrowid
    # Crear viaje REAL (o asignar unidad al ya creado automáticamente por WhatsApp)
    ts_now = datetime.now().isoformat()
    if viaje_existente:
        conn.execute("""UPDATE viajes SET numero_unidad=?, id_conductor=?, conductor_nombre=?,
            conductor_tel=?, fecha_asignacion=?, estado='asignado' WHERE id=?""",
            (numero_unidad,
             cond.get('id', 0) if cond else 0, cond.get('nombre', '') if cond else '',
             cond.get('telefono', '') if cond else '', ts_now, viaje_existente))
        viaje_id = viaje_existente
    else:
        cur = conn.execute("""INSERT INTO viajes(fecha_solicitud,id_cliente,cliente_nombre,cliente_tel,
            direccion_recogida,referencia_recogida,destino,observaciones,numero_unidad,
            id_conductor,conductor_nombre,conductor_tel,fecha_asignacion,estado,usuario_id,usuario_nombre)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts_now, id_cliente, nombre_cliente, telefono_cliente,
             direccion, referencia, destino, observaciones, numero_unidad,
             cond.get('id', 0) if cond else 0, cond.get('nombre', '') if cond else '',
             cond.get('telefono', '') if cond else '', ts_now, 'asignado',
             session.get('uid', 0), session.get('nombre', 'Bot WhatsApp')))
        viaje_id = cur.lastrowid
    # Sync to kv_store
    viajes = rows_to_list(conn.execute("SELECT * FROM viajes ORDER BY id").fetchall())
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('viajes',?,datetime('now','localtime'))",
                 (json.dumps(viajes, ensure_ascii=False),))
    # Marcar bot message como despachado
    conn.execute("UPDATE bot_mensajes SET estado='despachado', id_viaje_generado=? WHERE id=?",
                 (viaje_id, id_bot))
    conn.commit(); conn.close()
    log_action('BOT_DESPACHADO', f"id_bot={id_bot} → U{numero_unidad} viaje#{viaje_id} para {telefono_cliente}", 'bot_whatsapp')
    sse_broadcast('update', {'key': 'viajes', 'ts': ts_now})
    sse_broadcast('bot_despachado', {'id_bot': id_bot, 'numero_unidad': numero_unidad,
                                      'viaje_id': viaje_id, 'ts': ts_now})
    # Notificar al cliente por WhatsApp que su taxi está en camino
    if telefono_cliente:
        try:
            nombre_cond = cond.get('nombre', '').split(' ')[0] if cond else ''
            msg_cliente = (f'Estimado/a cliente, su taxi de la Cooperativa CTO 119 está en camino. '
                          f'Unidad: {numero_unidad} — Conductor: {nombre_cond}. '
                          f'Gracias por su preferencia.')
            conn2 = get_db()
            conn2.execute("INSERT INTO mensajes_whatsapp(numero,contenido,estado) VALUES(?,?,'enviado')",
                         (telefono_cliente, msg_cliente))
            conn2.commit(); conn2.close()
        except Exception as e: logger.warning(f"Notif cliente WA falló: {e}")
    return jsonify({'ok': True, 'viaje_id': viaje_id})

@app.route('/api/bot/rechazar', methods=['POST'])
def bot_rechazar():
    """La operadora rechaza/cierra una solicitud del bot."""
    d = request.get_json(force=True) or {}
    id_bot = d.get('id_bot')
    if not id_bot:
        return jsonify({'ok': False, 'error': 'id_bot requerido'}), 400
    conn = get_db()
    conn.execute("UPDATE bot_mensajes SET estado='rechazado' WHERE id=?", (id_bot,))
    conn.commit(); conn.close()
    log_action('BOT_RECHAZADO', f"id_bot={id_bot}", 'bot_whatsapp')
    return jsonify({'ok': True})

@app.route('/api/bot/heartbeat', methods=['POST'])
def bot_heartbeat():
    """El bot de WhatsApp reporta que sigue en línea (latido cada 60s)."""
    d = request.get_json(force=True) or {}
    info = d.get('info') or {}
    estado = {
        'online': True,
        'last_seen': datetime.now().isoformat(),
        'session': info.get('session', ''),
        'numero': info.get('numero', ''),
        'fase': info.get('fase', '')
    }
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('bot_estado',?,datetime('now','localtime'))",
                 (json.dumps(estado, ensure_ascii=False),))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/bot/estado')
def bot_estado():
    """Estado en línea del bot de WhatsApp (online si latido en los últimos 3 min)."""
    from datetime import datetime as _dt, timedelta
    conn = get_db()
    row = conn.execute("SELECT value FROM kv_store WHERE key='bot_estado'").fetchone()
    conn.close()
    estado = {'online': False, 'last_seen': None}
    if row:
        try:
            estado = json.loads(row['value'])
        except Exception:
            pass
    if estado.get('last_seen'):
        try:
            last = _dt.fromisoformat(estado['last_seen'])
            if (_dt.now() - last) > timedelta(seconds=180):
                estado['online'] = False
        except Exception:
            estado['online'] = False
    return jsonify(estado)

# ═══════════════════════════════════════════════════════════════════════════
# REPORTE DE OPERADORAS — turnos, horas trabajadas y cierre de turno
# ═══════════════════════════════════════════════════════════════════════════
@app.route('/api/operadores/turnos')
def operadores_turnos():
    """Consulta de turnos. Filtros: operador_id, desde, hasta, estado."""
    op  = request.args.get('operador_id', '')
    desde = request.args.get('desde', '')
    hasta = request.args.get('hasta', '')
    estado = request.args.get('estado', '')
    q = "SELECT * FROM turnos_operadores WHERE 1=1"
    args = []
    if op:
        q += " AND usuario_id=?"
        args.append(int(op))
    if desde:
        q += " AND fecha>=?"
        args.append(desde)
    if hasta:
        q += " AND fecha<=?"
        args.append(hasta)
    if estado:
        q += " AND estado=?"
        args.append(estado)
    q += " ORDER BY hora_ingreso DESC LIMIT 1000"
    conn = get_db()
    rows = rows_to_list(conn.execute(q, args).fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/operadores/resumen')
def operadores_resumen():
    """Resumen por operadora en un rango de fechas: turnos, horas, viajes."""
    desde = request.args.get('desde', '')
    hasta = request.args.get('hasta', '')
    q = "SELECT * FROM turnos_operadores WHERE 1=1"
    args = []
    if desde:
        q += " AND fecha>=?"; args.append(desde)
    if hasta:
        q += " AND fecha<=?"; args.append(hasta)
    conn = get_db()
    rows = rows_to_list(conn.execute(q, args).fetchall())
    conn.close()
    por_operadora = {}
    for t in rows:
        k = t['usuario_id']
        d = por_operadora.setdefault(k, {
            'usuario_id': k, 'usuario_nombre': t['usuario_nombre'] or '—',
            'rol': t['rol'] or '', 'turnos': 0, 'horas_total': 0.0,
            'viajes_despachados': 0, 'viajes_perdidos': 0,
            'ultimo_ingreso': None, 'turno_abierto': False})
        d['turnos'] += 1
        d['horas_total'] += (t['horas'] or 0)
        d['viajes_despachados'] += (t['viajes_despachados'] or 0)
        d['viajes_perdidos'] += (t['viajes_perdidos'] or 0)
        if t['estado'] == 'abierto': d['turno_abierto'] = True
        if d['ultimo_ingreso'] is None or (t['hora_ingreso'] or '') > (d['ultimo_ingreso'] or ''):
            d['ultimo_ingreso'] = t['hora_ingreso']
    lista = sorted(por_operadora.values(), key=lambda x: -x['horas_total'])
    for d in lista:
        d['horas_promedio'] = round(d['horas_total']/d['turnos'], 2) if d['turnos'] else 0
        d['horas_total'] = round(d['horas_total'], 2)
    total_horas = round(sum(d['horas_total'] for d in lista), 2)
    return jsonify({'desde': desde, 'hasta': hasta,
                    'operadores': lista, 'total_operadoras': len(lista),
                    'total_turnos': sum(d['turnos'] for d in lista),
                    'total_horas': total_horas})

@app.route('/api/operadores/semanales')
def operadores_semanales():
    """Métricas semanales por operadora: llamadas atendidas, viajes despachados,
    tiempo de respuesta (min) y horas trabajadas. Compara la semana actual
    (lunes-domingo) contra la anterior."""
    conn = get_db()
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    domingo = lunes + timedelta(days=6)
    ant_lunes = lunes - timedelta(days=7)
    ant_domingo = domingo - timedelta(days=7)
    periodos = [('actual', lunes, domingo), ('anterior', ant_lunes, ant_domingo)]
    metricas = {}
    for nombre, d1, d2 in periodos:
        rows = conn.execute("""
            SELECT usuario_id, usuario_nombre,
                   COUNT(*) AS llamadas,
                   SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) AS despachados,
                   SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) AS perdidos,
                   ROUND(AVG((julianday(COALESCE(fecha_asignacion,fecha_solicitud))-julianday(fecha_solicitud))*1440),1) AS respuesta_min
            FROM viajes
            WHERE usuario_id IS NOT NULL AND date(fecha_solicitud) BETWEEN ? AND ?
            GROUP BY usuario_id, usuario_nombre
        """, (d1.isoformat(), d2.isoformat())).fetchall()
        metricas[nombre] = {r['usuario_id']: dict(r) for r in rows}
        turnos = conn.execute("""
            SELECT usuario_id, COALESCE(SUM(horas),0) AS horas, COUNT(*) AS turnos
            FROM turnos_operadores
            WHERE date(fecha) BETWEEN ? AND ?
            GROUP BY usuario_id
        """, (d1.isoformat(), d2.isoformat())).fetchall()
        for t in turnos:
            m = metricas[nombre].get(t['usuario_id'])
            if m is None:
                metricas[nombre][t['usuario_id']] = {
                    'usuario_id': t['usuario_id'], 'usuario_nombre': '',
                    'llamadas': 0, 'despachados': 0, 'perdidos': 0, 'respuesta_min': None}
                m = metricas[nombre][t['usuario_id']]
            m['horas'] = round(t['horas'], 2)
            m['turnos'] = t['turnos']
    ids = set(metricas['actual']) | set(metricas['anterior'])
    operadoras = conn.execute("SELECT id,nombre,rol FROM usuarios WHERE activo=1 ORDER BY nombre").fetchall()
    conn.close()
    lista = []
    for op in operadoras:
        if op['id'] not in ids:
            continue
        a   = metricas['actual'].get(op['id'], {})
        ant = metricas['anterior'].get(op['id'], {})
        def v(dic, k, dflt=0):
            val = dic.get(k)
            return val if val is not None else dflt
        lista.append({
            'usuario_id': op['id'],
            'usuario_nombre': a.get('usuario_nombre') or op['nombre'],
            'rol': op['rol'],
            'llamadas': v(a, 'llamadas'), 'despachados': v(a, 'despachados'),
            'perdidos': v(a, 'perdidos'), 'respuesta_min': a.get('respuesta_min'),
            'horas': v(a, 'horas'), 'turnos': v(a, 'turnos'),
            'ant_llamadas': v(ant, 'llamadas'), 'ant_despachados': v(ant, 'despachados'),
            'ant_perdidos': v(ant, 'perdidos'), 'ant_respuesta_min': ant.get('respuesta_min'),
            'ant_horas': v(ant, 'horas'), 'ant_turnos': v(ant, 'turnos'),
        })
    lista.sort(key=lambda x: -x['llamadas'])
    return jsonify({
        'semana_actual':   f"{lunes.isoformat()} a {domingo.isoformat()}",
        'semana_anterior': f"{ant_lunes.isoformat()} a {ant_domingo.isoformat()}",
        'operadores': lista})

@app.route('/api/operadores/cerrar-turno', methods=['POST'])
def operadores_cerrar_turno():
    """Cierra el turno abierto de la operadora actual (botón Cerrar Turno)."""
    d = request.get_json(force=True) or {}
    obs = str(d.get('obs', '') or 'Cierre manual desde el sistema')
    res = cerrar_turno_operador(obs)
    log_action('CIERRE_TURNO_OPERADOR',
               f"Horas: {res['horas']}h · {res['viajes_despachados']} viajes" if res else "Sin turno abierto",
               'operacion')
    if res:
        # Devolver también el turno completo actualizado
        conn = get_db()
        turno = rows_to_list(conn.execute("SELECT * FROM turnos_operadores WHERE id=?", (res['id'],)).fetchall())
        conn.close()
        return jsonify({'ok': True, 'turno': turno[0] if turno else None, 'resultado': res})
    return jsonify({'ok': True, 'turno': None, 'resultado': None})

@app.route('/api/unidades/cerrar-turno', methods=['POST'])
def unidades_cerrar_turno():
    """Cierre de turno manual: marca SALIDA de todas las unidades activas en QAP."""
    conn = get_db()
    try:
        kv = conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
        actividad = json.loads(kv['value']) if kv else []
        by_unit = {}
        for a in actividad:
            u = a['numero_unidad']
            if u not in by_unit or a['fecha_hora'] > by_unit[u]['fecha_hora']:
                by_unit[u] = a
        activas_qap = sorted([u for u, a in by_unit.items() if a['accion'] == 'qap'])
        ahora = datetime.now().isoformat()
        if activas_qap:
            max_id = max((a.get('id', 0) for a in actividad), default=0)
            for i, u in enumerate(activas_qap):
                actividad.append({
                    'id': max_id + i + 1, 'numero_unidad': u,
                    'accion': 'salida', 'fecha_hora': ahora,
                    'obs': 'Cierre de turno manual'
                })
            conn.execute(
                "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",
                (json.dumps(actividad, ensure_ascii=False),))
            conn.execute("DELETE FROM unidades_actividad")
            for a in actividad:
                conn.execute(
                    "INSERT OR REPLACE INTO unidades_actividad(id,numero_unidad,accion,fecha_hora,obs) VALUES(?,?,?,?,?)",
                    (a.get('id'), a.get('numero_unidad'), a.get('accion'), a.get('fecha_hora'), a.get('obs', '')))
        log_action('CIERRE_TURNO', f"{len(activas_qap)} unidades puestas fuera de servicio: {activas_qap}", 'operacion')
        conn.commit()
        try:
            sse_broadcast('update', {'key': 'unidades_actividad', 'ts': ahora})
        except Exception:
            pass
        return jsonify({'ok': True, 'cerradas': len(activas_qap), 'unidades': activas_qap})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        conn.close()

# ── COLABORADORES ──────────────────────────────────────────────────────────

@app.route('/api/colaboradores', methods=['GET'])
def get_colaboradores():
    """Lista todos los colaboradores, opcionalmente filtrado por unidad."""
    unidad = request.args.get('unidad')
    conn = get_db()
    if unidad:
        rows = rows_to_list(conn.execute(
            "SELECT * FROM colaboradores WHERE unidad=? ORDER BY nombre", (int(unidad),)).fetchall())
    else:
        rows = rows_to_list(conn.execute(
            "SELECT * FROM colaboradores ORDER BY unidad, nombre").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/colaboradores', methods=['POST'])
def crear_colaborador():
    """Crear un nuevo colaborador para una unidad.
    C��digo ǧnico = {unidad}D  (socio 49 ��' colaborador 49D, socio 1 ��' 1D).
    La letra D identifica al colaborador. Se sincroniza en la tabla conductores
    para QAP, WhatsApp y login en la App."""
    d = request.get_json(force=True) or {}
    id_socio = d.get('id_conductor_socio')
    unidad = d.get('unidad')
    nombre = str(d.get('nombre', '')).strip()
    cedula = str(d.get('cedula', '')).strip()
    telefono = str(d.get('telefono', '')).strip()
    if not id_socio or not unidad or not nombre:
        return jsonify({'ok': False, 'error': 'Datos incompletos'}), 400
    conn = get_db()
    # Regla del sistema: c��digo del colaborador = unidad + letra D
    codigo = f'{int(unidad)}D'
    # Una sola unidad = un solo colaborador con ese c��digo
    if conn.execute("SELECT id,nombre FROM colaboradores WHERE codigo_colaborador=?", (codigo,)).fetchone():
        existente = conn.execute("SELECT nombre FROM colaboradores WHERE codigo_colaborador=?", (codigo,)).fetchone()
        nom = existente['nombre'] if existente else ''
        conn.close()
        return jsonify({'ok': False, 'error': f'La unidad {unidad} ya tiene un colaborador registrado (c��digo {codigo}: {nom}). Elim��nelo primero si desea reemplazarlo.'}), 400
    try:
        conn.execute("""INSERT INTO colaboradores(id_conductor_socio, unidad, nombre, cedula, telefono, codigo_colaborador)
            VALUES(?,?,?,?,?,?)""", (id_socio, unidad, nombre, cedula, telefono, codigo))
        # Sincronizar en conductores (QAP / WhatsApp / login App)
        rowc = conn.execute("SELECT id FROM conductores WHERE tipo='colaborador' AND unidad_base=?", (unidad,)).fetchone()
        if rowc:
            conn.execute("""UPDATE conductores SET nombre=?,cedula=?,telefono=?,unidad_codigo=?,estado='activo'
                WHERE id=?""", (nombre, cedula, telefono, codigo, rowc['id']))
        else:
            conn.execute("""INSERT INTO conductores(tipo,unidad_base,unidad_codigo,nombre,cedula,telefono,estado)
                VALUES('colaborador',?,?,?,?,?,'activo')""", (unidad, codigo, nombre, cedula, telefono))
        sse_broadcast('conductores', {'action':'created','tipo':'colaborador','codigo':codigo})
        conn.commit()
        nuevo_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()['id']
        conn.close()
        log_action('CREAR_COLABORADOR', f"U{unidad}: {nombre} [{codigo}]", 'conductores')
        return jsonify({'ok': True, 'id': nuevo_id, 'codigo': codigo})
    except Exception as e:
        conn.close()
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/colaboradores/<int:cid>', methods=['PUT'])
def editar_colaborador(cid):
    d = request.get_json(force=True) or {}
    conn = get_db()
    conn.execute("UPDATE colaboradores SET nombre=?,cedula=?,telefono=?,activo=? WHERE id=?",
                 (d.get('nombre'), d.get('cedula', ''), d.get('telefono', ''),
                  1 if d.get('activo', True) else 0, cid))
    conn.commit(); conn.close()
    log_action('EDITAR_COLABORADOR', f"id={cid}", 'conductores')
    return jsonify({'ok': True})

@app.route('/api/colaboradores/<int:cid>', methods=['DELETE'])
def eliminar_colaborador(cid):
    conn = get_db()
    row = conn.execute("SELECT unidad, nombre FROM colaboradores WHERE id=?", (cid,)).fetchone()
    conn.execute("DELETE FROM colaboradores WHERE id=?", (cid,))
    if row:
        # Eliminar también su ficha en conductores (mismo criterio por unidad)
        conn.execute("DELETE FROM conductores WHERE tipo='colaborador' AND unidad_base=?", (row['unidad'],))
        sse_broadcast('conductores', {'action':'deleted','tipo':'colaborador','unidad':row['unidad']})
    conn.commit(); conn.close()
    log_action('ELIMINAR_COLABORADOR', f"id={cid}: {row['nombre'] if row else ''}", 'conductores')
    return jsonify({'ok': True})

@app.route('/api/colaboradores/turno', methods=['POST'])
def colaborador_turno():
    """Un colaborador reporta inicio/fin de turno con su código único."""
    d = request.get_json(force=True) or {}
    codigo = str(d.get('codigo', '')).strip().upper()
    accion = str(d.get('accion', 'inicio')).strip()
    if not codigo:
        return jsonify({'ok': False, 'error': 'Código requerido'}), 400
    conn = get_db()
    colab = conn.execute("SELECT * FROM colaboradores WHERE codigo_colaborador=? AND activo=1", (codigo,)).fetchone()
    if not colab:
        conn.close()
        # No bloquear — devolver ok=True con aviso para que el QAP no falle
        return jsonify({'ok': True, 'advertencia': f'Código {codigo} no registrado, QAP registrado sin turno'})
    colab = dict(colab)
    unidad = colab['unidad']
    if accion == 'inicio':
        conn.execute("UPDATE colaboradores SET turno_activo=1 WHERE id=?", (colab['id'],))
        conn.commit(); conn.close()
        log_action('COLABORADOR_INICIO_TURNO', f"U{unidad}: {colab['nombre']} [{codigo}]", 'conductores')
        return jsonify({'ok': True, 'unidad': unidad, 'nombre': colab['nombre'],
                        'codigo': codigo, 'tipo': 'colaborador', 'accion': 'inicio'})
    else:
        conn.execute("UPDATE colaboradores SET turno_activo=0 WHERE id=?", (colab['id'],))
        conn.commit(); conn.close()
        log_action('COLABORADOR_FIN_TURNO', f"U{unidad}: {colab['nombre']} [{codigo}]", 'conductores')
        return jsonify({'ok': True, 'unidad': unidad, 'nombre': colab['nombre'],
                        'codigo': codigo, 'tipo': 'colaborador', 'accion': 'fin'})

@app.route('/api/colaboradores/unidad/<int:unidad>/activo')
def colaborador_activo(unidad):
    """Devuelve el colaborador que tiene el turno activo para una unidad, si existe."""
    conn = get_db()
    row = conn.execute("SELECT * FROM colaboradores WHERE unidad=? AND turno_activo=1 AND activo=1",
                       (unidad,)).fetchone()
    conn.close()
    if row:
        return jsonify({'tiene_colaborador': True, 'colaborador': dict(row)})
    return jsonify({'tiene_colaborador': False})



# ── RESPALDO ───────────────────────────────────────────────────────────────
@app.route('/api/backup',methods=['POST'])
def hacer_backup():
    ts=datetime.now().strftime('%Y%m%d_%H%M%S'); dest=os.path.join(BACKUP_DIR,f'callcenter_{ts}.db')
    try:
        shutil.copy2(DB_PATH,dest)
        bks=sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
        for old in bks[:-30]: os.remove(os.path.join(BACKUP_DIR,old))
        log_action('BACKUP',dest,'sistema')
        return jsonify({'ok':True,'archivo':dest})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),500

@app.route('/api/info')
def info():
    conn=get_db()
    tots={}
    for t in ['clientes','viajes','unidades_actividad','pagos_mensuales','multas','usuarios','bitacora']:
        try: tots[t]=conn.execute(f'SELECT COUNT(*) as n FROM {t}').fetchone()['n']
        except: tots[t]=0
    conn.close()
    return jsonify({'version':'CTO CallCenter v11.0','db_path':DB_PATH,'datos_dir':DATA_DIR,
                    'totales':tots,'hora':datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


@app.route('/api/license/status')
def license_status():
    """Estado de la licencia — pública (no requiere auth ni licencia)."""
    lic_path = os.path.join(DATA_DIR, 'licencia.json')
    if not os.path.exists(lic_path):
        return jsonify({'valid': False, 'expires': '', 'tipo': 'none',
                       'days_left': 0, 'message': 'Sin licencia'})
    try:
        with open(lic_path, 'r', encoding='utf-8') as f:
            lic = json.load(f)
        tipo = lic.get('tipo', '')
        expira = lic.get('expira', '')
        empresa = lic.get('empresa', '')
        firma = lic.get('firma', '')
        firma_ok = hmac.compare_digest(firma,
            hmac.new(LICENSE_KEY, f'{tipo}|{expira}|{empresa}'.encode(), hashlib.sha256).hexdigest())
        if not firma_ok:
            return jsonify({'valid': False, 'expires': expira, 'tipo': tipo,
                           'days_left': 0, 'message': 'Licencia dañada'})
        fecha_exp = date.fromisoformat(expira)
        dias = (fecha_exp - date.today()).days
        valid = dias >= 0
        msg = f'Licencia {tipo} — expira {expira}'
        if valid and dias < 30:
            msg += f' — Quedan {dias} días'
        elif not valid:
            msg = f'Licencia vencida el {expira}'
        return jsonify({'valid': valid, 'expires': expira, 'tipo': tipo,
                       'days_left': max(0, dias), 'message': msg, 'empresa': empresa})
    except Exception as e:
        return jsonify({'valid': False, 'expires': '', 'tipo': 'error',
                       'days_left': 0, 'message': str(e)})


# ── RUTAS PÁGINAS EXTRA ────────────────────────────────────────────────────
@app.route('/conductor')
def conductor_app():
    return send_from_directory(STATIC_DIR,'conductor.html')

@app.route('/conductor_sw.js')
def conductor_sw():
    """Service Worker del PWA conductor — servido desde la raíz para controlar todo el scope."""
    resp = send_from_directory(STATIC_DIR, 'conductor_sw.js')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Content-Type'] = 'text/javascript'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

@app.route('/conductor_manifest.json')
def conductor_manifest():
    resp = send_from_directory(STATIC_DIR, 'conductor_manifest.json')
    resp.headers['Content-Type'] = 'application/manifest+json'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp

@app.route('/tv')
def tv_mode():
    return send_from_directory(STATIC_DIR,'tv.html')

@app.route('/admin/unidades')
def admin_unidades():
    return send_from_directory(STATIC_DIR,'admin_unidades.html')

@app.route('/admin/install-conductores')
def install_conductores():
    return send_from_directory(STATIC_DIR,'install_conductores.html')

@app.route('/api/conductores/stats')
def conductores_stats():
    conn=get_db()
    conductores = _get_conductores(conn, solo_activos=False)
    total = len([c for c in conductores if c.get('estado')=='activo'])
    hoy = date.today().isoformat()
    activos_row = conn.execute("SELECT COUNT(DISTINCT numero_unidad) as n FROM unidades_actividad WHERE date(fecha_hora)=? AND accion='qap'",(hoy,)).fetchone()
    activos = activos_row['n'] if activos_row else 0
    conn.close()
    return jsonify({'total': total, 'activos_hoy': activos})

# ── API CONDUCTOR ──────────────────────────────────────────────────────────
CONDUCTORES_EMBEDDED = json.loads('[{"id":1,"nombre":"Julio Bolivar Castillo Bravo","cedula":"1104362767","telefono":"0987045164","placa":"PCW 6771","codigo_radio":"5130","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5130","estado_civil":"soltero","tipo":"socio","unidad":1,"bloqueado":false,"fecha_ingreso":"2022-09-03","estado":"activo"},{"id":2,"nombre":"Luis Alberto Chavez Caza","cedula":"1710455724","telefono":"0984777722","placa":"PDD4276","codigo_radio":"5113","marca":"Kia","modelo":"Rio","color":"amarillo","registro_municipal":"5113","estado_civil":"soltero","tipo":"socio","unidad":2,"bloqueado":false,"fecha_ingreso":"1997-04-22","estado":"activo"},{"id":3,"nombre":"Segundo David Rodriguez Tituana","cedula":"1711216646","telefono":"0994400640","placa":"PAC 3459","codigo_radio":"5140","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5140","estado_civil":"soltero","tipo":"socio","unidad":3,"bloqueado":false,"fecha_ingreso":"2009-04-30","estado":"activo"},{"id":4,"nombre":"Victor Santiago Muyolema Lema","cedula":"601270044","telefono":"0986522352","placa":"PCI 6188","codigo_radio":"5153","marca":"Chevrolet","modelo":"Sail","color":"amarillo","registro_municipal":"5153","estado_civil":"soltero","tipo":"socio","unidad":4,"bloqueado":false,"fecha_ingreso":"2006-06-12","estado":"activo"},{"id":5,"nombre":"Hector Alfredo Chamba Guashpa","cedula":"1204554495","telefono":"0992096843","placa":"PAA 9292","codigo_radio":"5099","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5099","estado_civil":"soltero","tipo":"socio","unidad":5,"bloqueado":false,"fecha_ingreso":"2007-08-15","estado":"activo"},{"id":6,"nombre":"Lupe Cecilia Diaz Manosalvas","cedula":"1714634902","telefono":"0985871706","placa":"PDH2928","codigo_radio":"5135","marca":"Chevrolrt","modelo":"Aveo Family","color":"amarillo","registro_municipal":"5135","estado_civil":"soltero","tipo":"socio","unidad":6,"bloqueado":false,"fecha_ingreso":"2012-10-31","estado":"activo"},{"id":7,"nombre":"Wilson Patricio Poveda Paguay","cedula":"1714302435","telefono":"0994528010","placa":"PAA 4633","codigo_radio":"5117","marca":"Chevrolrt","modelo":"Chevytaxi","color":"amarillo","registro_municipal":"5117","estado_civil":"soltero","tipo":"socio","unidad":7,"bloqueado":false,"fecha_ingreso":"2015-07-16","estado":"activo"},{"id":8,"nombre":"Mauro Antonio Salazar Toscano","cedula":"1709350043","telefono":"0988089273","placa":"PAA 4319","codigo_radio":"5143","marca":"Chevrolrt","modelo":"Chevytaxi","color":"amarillo","registro_municipal":"5143","estado_civil":"soltero","tipo":"socio","unidad":8,"bloqueado":false,"fecha_ingreso":"2014-08-05","estado":"activo"},{"id":9,"nombre":"Edwin Ricardo Vega Paucar","cedula":"1719984088","telefono":"0995452246","placa":"PBV 9363","codigo_radio":"5144","marca":"Nissan","modelo":"Tiida Entry","color":"amarillo","registro_municipal":"5144","estado_civil":"soltero","tipo":"socio","unidad":9,"bloqueado":false,"fecha_ingreso":"2014-07-08","estado":"activo"},{"id":10,"nombre":"Angel Gabriel Muñoz Paz","cedula":"1716081748","telefono":"0992914464","placa":"TBI9577","codigo_radio":"5122","marca":"Nissan","modelo":"Versa","color":"amarillo","registro_municipal":"5122","estado_civil":"soltero","tipo":"socio","unidad":10,"bloqueado":false,"fecha_ingreso":"2016-06-28","estado":"activo"},{"id":11,"nombre":"Irma Eufracia Cruz Encalada","cedula":"1723878334","telefono":"0998888646","placa":"PAA 4497","codigo_radio":"5124","marca":"Hyundai","modelo":"Elantra","color":"amarillo","registro_municipal":"5124","estado_civil":"soltero","tipo":"socio","unidad":11,"bloqueado":false,"fecha_ingreso":"2025-06-21","estado":"activo"},{"id":12,"nombre":"Silvio Danilo Almache Molineros","cedula":"1714000773","telefono":"0995909770","placa":"PCD7136","codigo_radio":"5139","marca":"Kia","modelo":"Cerato","color":"amarillo","registro_municipal":"5139","estado_civil":"soltero","tipo":"socio","unidad":12,"bloqueado":false,"fecha_ingreso":"2003-06-10","estado":"activo"},{"id":13,"nombre":"Gladys Rubiela Guaman Guevara","cedula":"1715970784","telefono":"0997627734","placa":"PUJ 0751","codigo_radio":"5101","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"5101","estado_civil":"soltero","tipo":"socio","unidad":13,"bloqueado":false,"fecha_ingreso":"2015-02-19","estado":"activo"},{"id":14,"nombre":"Santiago Vinicio Puetate Herrera","cedula":"1002176111","telefono":"0987439542","placa":"PUJ 0745","codigo_radio":"5125","marca":"Nissan","modelo":"","color":"amarillo","registro_municipal":"5125","estado_civil":"soltero","tipo":"socio","unidad":14,"bloqueado":false,"fecha_ingreso":"2014-10-16","estado":"activo"},{"id":15,"nombre":"Luis Carlos Mera Quillupangui","cedula":"1708888548","telefono":"097469400","placa":"PDD5306","codigo_radio":"5127","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5127","estado_civil":"soltero","tipo":"socio","unidad":15,"bloqueado":false,"fecha_ingreso":"1994-01-11","estado":"activo"},{"id":16,"nombre":"Franklin Guaman Colcha","cedula":"1715363410","telefono":"","placa":"PAA 4139","codigo_radio":"5107","marca":"Nissan","modelo":"Sentra","color":"amarillo","registro_municipal":"5107","estado_civil":"soltero","tipo":"socio","unidad":16,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":17,"nombre":"Hernan Enrique  Zumba Maldonado","cedula":"502007693","telefono":"0968134647","placa":"PDT1492","codigo_radio":"5137","marca":"Kia","modelo":"","color":"amarillo","registro_municipal":"5137","estado_civil":"soltero","tipo":"socio","unidad":17,"bloqueado":false,"fecha_ingreso":"2007-07-11","estado":"activo"},{"id":18,"nombre":"Franklin Giovanni Vascones Brito","cedula":"1717219131","telefono":"0990505182","placa":"PAB 2137","codigo_radio":"5112","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5112","estado_civil":"soltero","tipo":"socio","unidad":18,"bloqueado":false,"fecha_ingreso":"2020-03-14","estado":"activo"},{"id":19,"nombre":"Siro Francisco Carpio Cueva","cedula":"1718178658","telefono":"0987282874","placa":"PBY 9349","codigo_radio":"5114","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5114","estado_civil":"soltero","tipo":"socio","unidad":19,"bloqueado":false,"fecha_ingreso":"2012-06-13","estado":"activo"},{"id":20,"nombre":"Luis Olmedo Ushiña Paucar","cedula":"1708456726","telefono":"0969012340","placa":"PAA 4846","codigo_radio":"5149","marca":"","modelo":"","color":"amarillo","registro_municipal":"5149","estado_civil":"soltero","tipo":"socio","unidad":20,"bloqueado":false,"fecha_ingreso":"2013-11-05","estado":"activo"},{"id":21,"nombre":"Efrain Rovinson Rengel Pacheco","cedula":"501182711","telefono":"0979399157","placa":"PFD6180","codigo_radio":"5138","marca":"Kia","modelo":"","color":"amarillo","registro_municipal":"5138","estado_civil":"soltero","tipo":"socio","unidad":21,"bloqueado":false,"fecha_ingreso":"1994-01-01","estado":"activo"},{"id":22,"nombre":"Milton Absalon Valle Carranza","cedula":"1707081392","telefono":"0993239346","placa":"PAA7802","codigo_radio":"5154","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5154","estado_civil":"soltero","tipo":"socio","unidad":22,"bloqueado":false,"fecha_ingreso":"2001-02-20","estado":"activo"},{"id":23,"nombre":"Segundo Marcelo Urquizo Valdez","cedula":"1723535389","telefono":"0967354308","placa":"PCH 5341","codigo_radio":"5119","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5119","estado_civil":"soltero","tipo":"socio","unidad":23,"bloqueado":false,"fecha_ingreso":"2014-07-08","estado":"activo"},{"id":24,"nombre":"Jose Ricardo Tapia Villarreal","cedula":"1704442381","telefono":"0995113703","placa":"PAC 7987","codigo_radio":"5150","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5150","estado_civil":"soltero","tipo":"socio","unidad":24,"bloqueado":false,"fecha_ingreso":"1998-06-29","estado":"activo"},{"id":25,"nombre":"Samuel Eduardo Acuña Paredes","cedula":"500634092","telefono":"0987274643","placa":"PBL 9614","codigo_radio":"5094","marca":"Toyota","modelo":"Yaris","color":"amarillo","registro_municipal":"5094","estado_civil":"soltero","tipo":"socio","unidad":25,"bloqueado":false,"fecha_ingreso":"1985-04-01","estado":"activo"},{"id":26,"nombre":"Segundo Gerardo Gualoto Quisilema","cedula":"1714351499","telefono":"0993049360","placa":"PAA 6805","codigo_radio":"5096","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5096","estado_civil":"soltero","tipo":"socio","unidad":26,"bloqueado":false,"fecha_ingreso":"2013-06-19","estado":"activo"},{"id":27,"nombre":"Juan Carlos Silva Portilla","cedula":"1704497500","telefono":"0959291123","placa":"PUH 0963","codigo_radio":"5148","marca":"","modelo":"","color":"amarillo","registro_municipal":"5148","estado_civil":"soltero","tipo":"socio","unidad":27,"bloqueado":false,"fecha_ingreso":"1999-03-24","estado":"activo"},{"id":28,"nombre":"Nestor Raul Chicaiza Jami","cedula":"1715848881","telefono":"0995615456","placa":"PAE2018","codigo_radio":"5118","marca":"Kia","modelo":"Rio","color":"amarillo","registro_municipal":"5118","estado_civil":"soltero","tipo":"socio","unidad":28,"bloqueado":false,"fecha_ingreso":"2015-04-16","estado":"activo"},{"id":29,"nombre":"Oswaldo Molina","cedula":"1800573980","telefono":"0995464389","placa":"PCT4567","codigo_radio":"5131","marca":"Chevrolet","modelo":"Sail","color":"amarillo","registro_municipal":"5131","estado_civil":"soltero","tipo":"socio","unidad":29,"bloqueado":false,"fecha_ingreso":"1986-09-18","estado":"activo"},{"id":30,"nombre":"Segundo Manuel Parra Obando","cedula":"1706450242","telefono":"0987110312","placa":"PCI 2126","codigo_radio":"5106","marca":"Nissan","modelo":"","color":"amarillo","registro_municipal":"5106","estado_civil":"soltero","tipo":"socio","unidad":30,"bloqueado":false,"fecha_ingreso":"2013-12-03","estado":"activo"},{"id":31,"nombre":"Jose Hernan Espinosa","cedula":"1705056073","telefono":"0992765724","placa":"PAC 5053","codigo_radio":"5120","marca":"Nissan","modelo":"Versa","color":"amarillo","registro_municipal":"5120","estado_civil":"soltero","tipo":"socio","unidad":31,"bloqueado":false,"fecha_ingreso":"1995-07-04","estado":"activo"},{"id":32,"nombre":"Angel Norberto Lainaguan Garcia","cedula":"201519527","telefono":"0988310223","placa":"PAC 3096","codigo_radio":"5136","marca":"","modelo":"","color":"amarillo","registro_municipal":"5136","estado_civil":"soltero","tipo":"socio","unidad":32,"bloqueado":false,"fecha_ingreso":"2013-08-27","estado":"activo"},{"id":33,"nombre":"Segundo Pedro Valdez Tasanbay","cedula":"602110983","telefono":"0969056793","placa":"PCT 6875","codigo_radio":"5155","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5155","estado_civil":"soltero","tipo":"socio","unidad":33,"bloqueado":false,"fecha_ingreso":"2023-12-09","estado":"activo"},{"id":34,"nombre":"Dario Patricio Vaca Guevara","cedula":"1004031405","telefono":"0995556480","placa":"PDF7787","codigo_radio":"5108","marca":"Chevrolet","modelo":"Sail","color":"amarillo","registro_municipal":"5108","estado_civil":"soltero","tipo":"socio","unidad":34,"bloqueado":false,"fecha_ingreso":"2025-05-13","estado":"activo"},{"id":35,"nombre":"Washington Ulpiano Sanchez Pico","cedula":"1709897993","telefono":"0995291131","placa":"PCM5690","codigo_radio":"5146","marca":"","modelo":"","color":"amarillo","registro_municipal":"5146","estado_civil":"soltero","tipo":"socio","unidad":35,"bloqueado":false,"fecha_ingreso":"1997-07-08","estado":"activo"},{"id":36,"nombre":"Marco Anibal Morillo Carapaz","cedula":"1711048882","telefono":"0981974988","placa":"PAA 6889","codigo_radio":"5097","marca":"","modelo":"","color":"amarillo","registro_municipal":"5097","estado_civil":"soltero","tipo":"socio","unidad":36,"bloqueado":false,"fecha_ingreso":"2022-12-15","estado":"activo"},{"id":37,"nombre":"Quiroz Bolaño Jorge Fernando","cedula":"1721602892","telefono":"0989598844","placa":"PBV 3202","codigo_radio":"5151","marca":"","modelo":"","color":"amarillo","registro_municipal":"5151","estado_civil":"soltero","tipo":"socio","unidad":37,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":38,"nombre":"Oswaldo Salas Paredes","cedula":"1707274526","telefono":"0968321313","placa":"PUJ 0784","codigo_radio":"5142","marca":"","modelo":"","color":"amarillo","registro_municipal":"5142","estado_civil":"soltero","tipo":"socio","unidad":38,"bloqueado":false,"fecha_ingreso":"1993-08-03","estado":"activo"},{"id":39,"nombre":"Jose Miguel Lasso Vargas","cedula":"503659674","telefono":"0979397980","placa":"PBV 2940","codigo_radio":"5102","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5102","estado_civil":"soltero","tipo":"socio","unidad":39,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":40,"nombre":"Luis Ermenejildo Recalde Vaca","cedula":"1709859845","telefono":"0999700659","placa":"PAA 9419","codigo_radio":"5103","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5103","estado_civil":"soltero","tipo":"socio","unidad":40,"bloqueado":false,"fecha_ingreso":"2006-12-19","estado":"activo"},{"id":41,"nombre":"Jose Alfonso Villa Camani","cedula":"201050465","telefono":"0985682591","placa":"PAA 6743","codigo_radio":"5095","marca":"","modelo":"","color":"amarillo","registro_municipal":"5095","estado_civil":"soltero","tipo":"socio","unidad":41,"bloqueado":false,"fecha_ingreso":"2009-02-12","estado":"activo"},{"id":42,"nombre":"Luis Alberto Chicaiza Pichucho","cedula":"1701618314","telefono":"0992613110","placa":"PAC3592","codigo_radio":"5098","marca":"","modelo":"","color":"amarillo","registro_municipal":"5098","estado_civil":"soltero","tipo":"socio","unidad":42,"bloqueado":false,"fecha_ingreso":"2008-04-15","estado":"activo"},{"id":43,"nombre":"Washington Umajinga Cunuhay","cedula":"0503588113","telefono":"0991744421","placa":"TBI6770","codigo_radio":"5147","marca":"Nissan","modelo":"Versa","color":"amarillo","registro_municipal":"5147","estado_civil":"soltero","tipo":"socio","unidad":43,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":44,"nombre":"Maria Magdalena Faz Zumba","cedula":"502474471","telefono":"0991981513","placa":"PAC 8212","codigo_radio":"5116","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5116","estado_civil":"soltero","tipo":"socio","unidad":44,"bloqueado":false,"fecha_ingreso":"2023-02-11","estado":"activo"},{"id":45,"nombre":"Luis Enrrique Casillas Vargas","cedula":"","telefono":"0991015637","placa":"PUK 0319","codigo_radio":"5109","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5109","estado_civil":"soltero","tipo":"socio","unidad":45,"bloqueado":false,"fecha_ingreso":"2022-11-19","estado":"activo"},{"id":46,"nombre":"Jose Francisco Yungan Obando","cedula":"1705945424","telefono":"0995765473","placa":"PBZ 1018","codigo_radio":"5133","marca":"","modelo":"","color":"amarillo","registro_municipal":"5133","estado_civil":"soltero","tipo":"socio","unidad":46,"bloqueado":false,"fecha_ingreso":"2013-02-28","estado":"activo"},{"id":47,"nombre":"Ruber Herrera Paredes","cedula":"1103087829","telefono":"0967940563","placa":"PCA 4989","codigo_radio":"5110","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5110","estado_civil":"soltero","tipo":"socio","unidad":47,"bloqueado":false,"fecha_ingreso":"2016-02-04","estado":"activo"},{"id":48,"nombre":"Hugo Victor Maila Tivan","cedula":"1726353301","telefono":"","placa":"PAC 1805","codigo_radio":"5128","marca":"","modelo":"","color":"amarillo","registro_municipal":"5128","estado_civil":"soltero","tipo":"socio","unidad":48,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":49,"nombre":"Cristian Edmundo Lema Hidalgo","cedula":"1717794208","telefono":"0988193523","placa":"PCP7473","codigo_radio":"5100","marca":"Hyundai","modelo":"Accent","color":"amarillo","registro_municipal":"5100","estado_civil":"soltero","tipo":"socio","unidad":49,"bloqueado":false,"fecha_ingreso":"2013-06-19","estado":"activo"},{"id":50,"nombre":"Baño Vega Jaime Fausto","cedula":"503586885","telefono":"0983756975","placa":"PUD 0926","codigo_radio":"5134","marca":"Nissan","modelo":"Sentra","color":"amarillo","registro_municipal":"5134","estado_civil":"soltero","tipo":"socio","unidad":50,"bloqueado":false,"fecha_ingreso":"2021-03-17","estado":"activo"},{"id":51,"nombre":"Anibal Ivan Mantilla Sosa","cedula":"1714850748","telefono":"0987253824","placa":"PBT1661","codigo_radio":"5141","marca":"","modelo":"","color":"amarillo","registro_municipal":"5141","estado_civil":"soltero","tipo":"socio","unidad":51,"bloqueado":false,"fecha_ingreso":"2015-06-09","estado":"activo"},{"id":52,"nombre":"Hernan Rodrigo Espinoza Zurita","cedula":"1711774156","telefono":"0995044566","placa":"PUK 0794","codigo_radio":"5121","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5121","estado_civil":"soltero","tipo":"socio","unidad":52,"bloqueado":false,"fecha_ingreso":"2006-05-03","estado":"activo"},{"id":53,"nombre":"Luis Octavio Pacalla Vilema","cedula":"1713039764","telefono":"0994725380","placa":"PCJ 8485","codigo_radio":"5104","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"5104","estado_civil":"soltero","tipo":"socio","unidad":53,"bloqueado":false,"fecha_ingreso":"2023-02-11","estado":"activo"},{"id":54,"nombre":"Nelson Marcelo Hernandez Narvaez","cedula":"1709217994","telefono":"0983729459","placa":"PAB 2357","codigo_radio":"5129","marca":"","modelo":"","color":"amarillo","registro_municipal":"5129","estado_civil":"soltero","tipo":"socio","unidad":54,"bloqueado":false,"fecha_ingreso":"2003-07-30","estado":"activo"},{"id":55,"nombre":"Luis Edwin Tatayo Rondal","cedula":"1707990816","telefono":"0939192169","placa":"PAB 4132","codigo_radio":"5152","marca":"Aveo","modelo":"Family","color":"amarillo","registro_municipal":"5152","estado_civil":"soltero","tipo":"socio","unidad":55,"bloqueado":false,"fecha_ingreso":"1991-08-03","estado":"activo"},{"id":56,"nombre":"Humberto Bolivar Castro Hidalgo","cedula":"701822843","telefono":"0987818010","placa":"PAC 3465","codigo_radio":"5111","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"5111","estado_civil":"soltero","tipo":"socio","unidad":56,"bloqueado":false,"fecha_ingreso":"1994-10-18","estado":"activo"},{"id":57,"nombre":"Edison Rolando Almache Arrieta","cedula":"1714134812","telefono":"0988707803","placa":"PAC 7845","codigo_radio":"5132","marca":"","modelo":"","color":"amarillo","registro_municipal":"5132","estado_civil":"soltero","tipo":"socio","unidad":57,"bloqueado":false,"fecha_ingreso":"2006-12-19","estado":"activo"},{"id":58,"nombre":"Ricardo Joselito Coronado Perez","cedula":"1707147649","telefono":"0995729293","placa":"PAA 6943","codigo_radio":"5115","marca":"","modelo":"","color":"amarillo","registro_municipal":"5115","estado_civil":"soltero","tipo":"socio","unidad":58,"bloqueado":false,"fecha_ingreso":"1993-09-16","estado":"activo"},{"id":59,"nombre":"Alex Dario Chicaiza Toapanta","cedula":"1750012252","telefono":"0999902476","placa":"PDC8443","codigo_radio":"5126","marca":"Kia","modelo":"","color":"amarillo","registro_municipal":"5126","estado_civil":"soltero","tipo":"socio","unidad":59,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":60,"nombre":"Hugo Patricio Chauca Gualoto","cedula":"1708183361","telefono":"0995059594","placa":"PAC9887","codigo_radio":"5123","marca":"Kia","modelo":"","color":"amarillo","registro_municipal":"5123","estado_civil":"soltero","tipo":"socio","unidad":60,"bloqueado":false,"fecha_ingreso":"2011-01-18","estado":"activo"},{"id":61,"nombre":"Jose Elias Nicolalde","cedula":"1704726031","telefono":"0984733507","placa":"PBV 4403","codigo_radio":"5105","marca":"Nissan","modelo":"","color":"amarillo","registro_municipal":"5105","estado_civil":"soltero","tipo":"socio","unidad":61,"bloqueado":false,"fecha_ingreso":"2013-10-15","estado":"activo"},{"id":62,"nombre":"Miguel Angel Achig Cabezas","cedula":"1707981807","telefono":"0995854848","placa":"PAB 1647","codigo_radio":"5093","marca":"","modelo":"","color":"amarillo","registro_municipal":"5093","estado_civil":"soltero","tipo":"socio","unidad":62,"bloqueado":false,"fecha_ingreso":"1996-04-23","estado":"activo"},{"id":63,"nombre":"Gina Elizabeth Sanchez Pico","cedula":"1712260031","telefono":"0999815236","placa":"PAC 6626","codigo_radio":"5145","marca":"","modelo":"","color":"amarillo","registro_municipal":"5145","estado_civil":"soltero","tipo":"socio","unidad":63,"bloqueado":false,"fecha_ingreso":"2018-06-12","estado":"activo"},{"id":64,"nombre":"Sergio Andres Macias Zumba","cedula":"1724771785","telefono":"0980536656","placa":"PCL 6375","codigo_radio":"20080","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"20080","estado_civil":"soltero","tipo":"concesionario","unidad":64,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":65,"nombre":"Bolivar Armando Ramos Vinueza","cedula":"1713318051","telefono":"0998551139","placa":"PCW 5562","codigo_radio":"19892","marca":"Great","modelo":"Wall","color":"amarillo","registro_municipal":"19892","estado_civil":"soltero","tipo":"concesionario","unidad":65,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":66,"nombre":"Jose Alexis Astudillo Arpi","cedula":"1718682147","telefono":"0981428716","placa":"PCW 5150","codigo_radio":"19882","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19882","estado_civil":"soltero","tipo":"concesionario","unidad":66,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":67,"nombre":"Wilmer Stalin Bustamante Paucar","cedula":"1719960179","telefono":"0982851072","placa":"PBO 9193","codigo_radio":"19884","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19884","estado_civil":"soltero","tipo":"concesionario","unidad":67,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":68,"nombre":"Edwin Israel Quishpe Guaman","cedula":"1720206851","telefono":"0988010648","placa":"PCW 3127","codigo_radio":"19891","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19891","estado_civil":"soltero","tipo":"concesionario","unidad":68,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":69,"nombre":"Darwin  Edinson Coral Astudillo","cedula":"1717126211","telefono":"0988529045","placa":"PCH 5698","codigo_radio":"19885","marca":"Kia","modelo":"","color":"amarillo","registro_municipal":"19885","estado_civil":"soltero","tipo":"concesionario","unidad":69,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":70,"nombre":"Marco Vinicio Diaz Manosalvas","cedula":"1717078032","telefono":"0979090993","placa":"IBA 7072","codigo_radio":"19888","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19888","estado_civil":"soltero","tipo":"concesionario","unidad":70,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":71,"nombre":"Enrique Salomon Beltran","cedula":"1710059161","telefono":"0995429330","placa":"IBA 2016","codigo_radio":"19883","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19883","estado_civil":"soltero","tipo":"concesionario","unidad":71,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":72,"nombre":"Pedro Vicente Toasa Cabay","cedula":"604732248","telefono":"0997436725","placa":"TBC 6944","codigo_radio":"19894","marca":"Hyundai","modelo":"","color":"amarillo","registro_municipal":"19894","estado_civil":"soltero","tipo":"concesionario","unidad":72,"bloqueado":false,"fecha_ingreso":"2022-10-08","estado":"activo"},{"id":73,"nombre":"Ruben Armando Cordova Diaz","cedula":"1715676365","telefono":"0954959608","placa":"PBW 3577","codigo_radio":"19886","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19886","estado_civil":"soltero","tipo":"concesionario","unidad":73,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":74,"nombre":"Dennis Xavier Paucar Herrera","cedula":"1729189827","telefono":"","placa":"PCT 1321","codigo_radio":"19890","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19890","estado_civil":"soltero","tipo":"concesionario","unidad":74,"bloqueado":false,"fecha_ingreso":"2000-01-01","estado":"activo"},{"id":75,"nombre":"Andres Giovanny Coro Paguay","cedula":"1725434193","telefono":"0984999817","placa":"PCQ 4350","codigo_radio":"19887","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19887","estado_civil":"soltero","tipo":"concesionario","unidad":75,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":76,"nombre":"Aparicio Guatemal Novoa","cedula":"1707505259","telefono":"0993327190","placa":"PCD 5832","codigo_radio":"19889","marca":"Chevrolet","modelo":"","color":"amarillo","registro_municipal":"19889","estado_civil":"soltero","tipo":"concesionario","unidad":76,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":77,"nombre":"Franklin Alejandro Tamayo Venegas","cedula":"1719254177","telefono":"0983291067","placa":"PDC7755","codigo_radio":"19893","marca":"Kia","modelo":"Rio","color":"amarillo","registro_municipal":"19893","estado_civil":"soltero","tipo":"concesionario","unidad":77,"bloqueado":false,"fecha_ingreso":"2018-01-16","estado":"activo"},{"id":78,"nombre":"Pedro Fernando Umajinga Umajinga","cedula":"502962541","telefono":"0984917175","placa":"PBY 6116","codigo_radio":"19895","marca":"Hyundai","modelo":"Elantra","color":"amarillo","registro_municipal":"19895","estado_civil":"soltero","tipo":"concesionario","unidad":78,"bloqueado":false,"fecha_ingreso":"2022-11-12","estado":"activo"},{"id":79,"nombre":"Alexis Gerardo Gualoto Simbaña","cedula":"1719186536","telefono":"0979147317","placa":"PBM 2784","codigo_radio":"19881","marca":"Aveo","modelo":"Activo","color":"amarillo","registro_municipal":"19881","estado_civil":"soltero","tipo":"concesionario","unidad":79,"bloqueado":false,"fecha_ingreso":"2022-06-20","estado":"activo"}]')

@app.route('/api/conductor/login',methods=['POST'])
def conductor_login():
    d=request.get_json(force=True) or {}
    unidad_raw = d.get('unidad', 0)
    try: unidad = int(unidad_raw)
    except: unidad = 0
    tel_sufijo=str(d.get('tel_sufijo','')).strip()
    codigo_colaborador = str(d.get('codigo_colaborador', '')).strip().upper()

    # ─── Login de colaborador con código (ej: "49D" — unidad + letra D) ────
    if codigo_colaborador:
        conn=get_db()
        # Buscar en conductores tipo='colaborador' por unidad_codigo (texto 49D)
        try:
            codigo_int=int(codigo_colaborador.rstrip('D'))
        except:
            codigo_int=0
        colab=conn.execute(
            "SELECT * FROM conductores WHERE tipo='colaborador' AND estado='activo' AND (LOWER(unidad_codigo)=LOWER(?) OR unidad_codigo=? OR unidad_base=?)",
            (codigo_colaborador, codigo_int, codigo_int)).fetchone()
        if not colab:
            conn.close()
            return jsonify({'ok':False,'error':f'Codigo {codigo_colaborador} no valido. Verifique con la cooperativa.'}),401
        colab=dict(colab)
        unit_real=colab['unidad_base']
        conductores=_get_conductores(conn)
        conn.close()
        socio=next((c for c in conductores if c.get('unidad')==unit_real and c.get('tipo')=='socio'),{})
        session['conductor_unidad']=unit_real
        session['conductor_tipo']='colaborador'
        session['conductor_codigo']=str(colab['unidad_codigo'])
        return jsonify({'ok':True,'conductor':{
            'id':colab['id'],'unidad':unit_real,'nombre':colab['nombre'],
            'placa':socio.get('placa',''),'marca':socio.get('marca',''),
            'modelo':socio.get('modelo',''),'tipo':'colaborador',
            'codigo':str(colab['unidad_codigo'])}})

    # ─── Login normal socio/concesionario ─────────────────────────────────
    if not unidad:
        return jsonify({'ok':False,'error':'Número de unidad requerido'}),400
    conn=get_db()
    conductores = _get_conductores(conn)
    cond=next((c for c in conductores if c.get('unidad')==unidad),None)
    if not cond: conn.close(); return jsonify({'ok':False,'error':f'Unidad {unidad} no registrada'}),404
    if cond.get('estado')!='activo': conn.close(); return jsonify({'ok':False,'error':'Unidad inactiva'}),403
    conn.close()
    # Validar últimos 4 dígitos del teléfono
    tel=str(cond.get('telefono','')).replace(' ','')
    if tel_sufijo and len(tel)>=4 and not tel.endswith(tel_sufijo):
        return jsonify({'ok':False,'error':'Teléfono no coincide con el registrado'}),401
    session['conductor_unidad']=unidad
    session['conductor_tipo'] = cond.get('tipo', 'socio')
    session['conductor_codigo'] = ''
    return jsonify({'ok':True,'conductor':{'id':cond['id'],'unidad':unidad,'nombre':cond['nombre'],
        'placa':cond.get('placa',''),'marca':cond.get('marca',''),'modelo':cond.get('modelo',''),
        'tipo': cond.get('tipo','socio'), 'codigo': ''}})

@app.route('/api/conductor/sesion')
def conductor_sesion():
    u=session.get('conductor_unidad')
    tipo=session.get('conductor_tipo','socio')
    codigo=session.get('conductor_codigo','')
    if not u: return jsonify({'ok':False})
    conn=get_db()
    if tipo=='colaborador' and codigo:
        try: codigo_int=int(codigo)
        except: codigo_int=0
        cond=conn.execute("SELECT * FROM conductores WHERE tipo='colaborador' AND unidad_base=? AND estado='activo'",(u,)).fetchone()
        if cond:
            cond=dict(cond); cond['unidad']=cond['unidad_base']
        socio=conn.execute("SELECT * FROM conductores WHERE tipo='socio' AND unidad_base=? AND estado='activo'",(u,)).fetchone()
        conn.close()
        if cond and socio:
            return jsonify({'ok':True,'conductor':{
                'id':cond['id'],'unidad':u,'nombre':cond['nombre'],
                'placa':dict(socio).get('placa',''),'marca':dict(socio).get('marca',''),
                'modelo':dict(socio).get('modelo',''),'tipo':'colaborador','codigo':str(cond['unidad_codigo'])}})
    conductores=_get_conductores(conn)
    conn.close()
    cond=next((c for c in conductores if c.get('unidad')==u and c.get('tipo')==tipo),None)
    if not cond:
        cond=next((c for c in conductores if c.get('unidad')==u),None)
    if not cond: return jsonify({'ok':False})
    return jsonify({'ok':True,'conductor':{'id':cond['id'],'unidad':u,'nombre':cond['nombre'],
        'placa':cond.get('placa',''),'marca':cond.get('marca',''),'modelo':cond.get('modelo',''),
        'tipo':cond.get('tipo','socio'),'codigo':codigo}})

@app.route('/api/conductor/estado/<int:unidad>')
def conductor_estado(unidad):
    hoy=date.today().isoformat()
    conn=get_db()
    # QAP activo?
    actividad_row=conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    actividad=json.loads(actividad_row['value'] or '[]') if actividad_row and actividad_row['value'] else []
    by_u=[a for a in actividad if a['numero_unidad']==unidad]
    by_u.sort(key=lambda a:a['fecha_hora'],reverse=True)
    qap_activo=bool(by_u and by_u[0]['accion']=='qap')
    min_qap=0
    if qap_activo:
        from datetime import datetime as dt2
        try: min_qap=int((dt2.now()-dt2.fromisoformat(by_u[0]['fecha_hora'])).total_seconds()//60)
        except: pass
    # Viajes hoy
    viajes=conn.execute("SELECT * FROM viajes WHERE numero_unidad=? AND date(fecha_asignacion)=? ORDER BY fecha_asignacion DESC",(unidad,hoy)).fetchall()
    viajes_list=rows_to_list(viajes)
    viajes_hoy=len([v for v in viajes_list if v['estado'] in ('asignado','completado')])
    # Viaje activo (últimos 5 min)
    from datetime import datetime as dt3
    viaje_activo=None
    for v in viajes_list:
        if v['estado']=='asignado':
            try:
                t=dt3.fromisoformat(v['fecha_asignacion'] or v['fecha_solicitud'])
                if (dt3.now()-t).total_seconds()<1800: viaje_activo=v; break
            except: pass
    # Historial hoy
    historial=viajes_list[:15]
    # Ranking (posición por viajes)
    all_viajes=conn.execute("SELECT numero_unidad,COUNT(*) as n FROM viajes WHERE date(fecha_asignacion)=? AND estado IN ('asignado','completado') GROUP BY numero_unidad ORDER BY n DESC",(hoy,)).fetchall()
    ranking=1
    for i,(r2) in enumerate(all_viajes):
        if r2['numero_unidad']==unidad: ranking=i+1; break
    conn.close()
    return jsonify({'qap_activo':qap_activo,'minutos_qap':min_qap,'viajes_hoy':viajes_hoy,
        'viaje_activo':viaje_activo,'historial':historial,'posicion_ranking':ranking})

@app.route('/api/conductor/qap',methods=['POST'])
def conductor_qap():
    d=request.get_json(force=True) or {}
    unidad=int(d.get('unidad',0))
    if not unidad: return jsonify({'ok':False,'error':'Unidad requerida'}),400
    conn=get_db()
    conductores = _get_conductores(conn)
    cond=next((c for c in conductores if c.get('unidad')==unidad),None)
    if not cond: conn.close(); return jsonify({'ok':False,'error':'Unidad no encontrada'}),404
    if cond.get('bloqueado'): conn.close(); return jsonify({'ok':False,'error':'Unidad bloqueada por adeudos'}),403
    # ✅ VALIDACIÓN: socio y colaborador NO pueden estar QAP al mismo tiempo
    es_colab = session.get('conductor_tipo') == 'colaborador'
    act_row_check=conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    if act_row_check and act_row_check['value']:
        try:
            act_check = json.loads(act_row_check['value'])
            qap_unidad = [a for a in act_check if str(a.get('numero_unidad',''))==str(unidad) and a.get('accion')=='qap']
            if qap_unidad:
                qap_unidad.sort(key=lambda x: x.get('fecha_hora',''), reverse=True)
                obs = str(qap_unidad[0].get('obs','')).lower()
                actual_es_colab = 'colaborador' in obs
                if es_colab and not actual_es_colab:
                    conn.close(); return jsonify({'ok':False,'error':'Unidad ya tiene al socio en QAP. Solo puede haber una persona activa por unidad.'}),409
                elif not es_colab and actual_es_colab:
                    conn.close(); return jsonify({'ok':False,'error':'Unidad ya tiene al colaborador en QAP. Solo puede haber una persona activa por unidad.'}),409
        except Exception:
            pass
    act_row=conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    actividad=json.loads(act_row['value']) if act_row else []
    nuevo_id=max((a['id'] for a in actividad),default=0)+1
    obs_val = f"Colaborador {session.get('conductor_codigo','')}" if es_colab else 'App conductor'
    actividad.append({'id':nuevo_id,'numero_unidad':unidad,'accion':'qap','fecha_hora':datetime.now().isoformat(),'obs':obs_val})
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",(json.dumps(actividad,ensure_ascii=False),))
    conn.execute("INSERT INTO unidades_actividad(numero_unidad,accion,obs) VALUES(?,?,?)",(unidad,'qap',obs_val))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'unidades_actividad', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/conductor/salida',methods=['POST'])
def conductor_salida():
    d=request.get_json(force=True) or {}
    unidad=int(d.get('unidad',0))
    if not unidad: return jsonify({'ok':False,'error':'Unidad requerida'}),400
    conn=get_db()
    act_row=conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    actividad=json.loads(act_row['value']) if act_row else []
    nuevo_id=max((a['id'] for a in actividad),default=0)+1
    actividad.append({'id':nuevo_id,'numero_unidad':unidad,'accion':'salida','fecha_hora':datetime.now().isoformat(),'obs':'App conductor'})
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",(json.dumps(actividad,ensure_ascii=False),))
    conn.execute("INSERT INTO unidades_actividad(numero_unidad,accion,obs) VALUES(?,?,?)",(unidad,'salida','App conductor'))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'unidades_actividad', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/unidades/qrt',methods=['POST'])
def unidad_qrt():
    """CERRAR TURNO POR UNIDAD (QRT): marca SALIDA de UNA unidad específica.
    El resto del sistema (viajes, reportes, conficciones socio/colaborador) no cambia."""
    d=request.get_json(force=True) or {}
    try:
        unidad=int(d.get('unidad',0))
    except Exception:
        unidad=0
    if not unidad or unidad<1 or unidad>100:
        return jsonify({'ok':False,'error':'Unidad inválida (1-100)'}),400
    obs=str(d.get('obs','') or 'QRT').strip() or 'QRT'
    conn=get_db()
    try:
        kv = conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
        actividad = json.loads(kv['value']) if kv else []
        nuevo_id = max((a.get('id',0) for a in actividad), default=0) + 1
        ahora = datetime.now().isoformat()
        actividad.append({'id':nuevo_id,'numero_unidad':unidad,'accion':'salida','fecha_hora':ahora,'obs':obs})
        conn.execute(
            "INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('unidades_actividad',?,datetime('now','localtime'))",
            (json.dumps(actividad, ensure_ascii=False),))
        conn.execute("INSERT INTO unidades_actividad(numero_unidad,accion,obs) VALUES(?,?,?)",(unidad,'salida',obs))
        try:
            sse_broadcast('update', {'key': 'unidades_actividad', 'ts': ahora})
        except Exception:
            pass
        log_action('QRT_UNIDAD', f"Unidad {unidad} fuera de servicio: {obs}", 'operacion')
        conn.commit()
        return jsonify({'ok':True,'unidad':unidad})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}),500
    finally:
        conn.close()

@app.route('/api/conductor/completar-viaje',methods=['POST'])
def completar_viaje():
    d=request.get_json(force=True) or {}
    id_viaje=d.get('id_viaje'); unidad=d.get('unidad')
    conn=get_db()
    conn.execute("UPDATE viajes SET estado='completado' WHERE id=? AND numero_unidad=?",(id_viaje,unidad))
    # Sync kv
    viajes=rows_to_list(conn.execute("SELECT * FROM viajes ORDER BY id").fetchall())
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('viajes',?,datetime('now','localtime'))",(json.dumps(viajes,ensure_ascii=False),))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'viajes', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

# ── GPS REAL DE CONDUCTORES ────────────────────────────────────────────────
@app.route('/api/conductor/ubicacion',methods=['POST'])
def conductor_ubicacion():
    """El conductor envía su posición GPS. Se guarda la última + historial."""
    d=request.get_json(force=True) or {}
    try:
        unidad=int(d.get('unidad',0))
        lat=float(d.get('lat')); lng=float(d.get('lng'))
        velocidad=float(d.get('velocidad', 0))
    except:
        return jsonify({'ok':False,'error':'Datos incompletos'}),400
    if not unidad or lat is None or lng is None:
        return jsonify({'ok':False,'error':'Datos incompletos'}),400
    conn=get_db()
    # Última posición (kv_store para display rápido)
    row=conn.execute("SELECT value FROM kv_store WHERE key='ubicaciones'").fetchone()
    ubicaciones=json.loads(row['value']) if row else []
    ubicaciones=[u for u in ubicaciones if u.get('unidad')!=unidad]
    ubicaciones.append({'unidad':unidad,'lat':lat,'lng':lng,'velocidad':velocidad,'ts':datetime.now().isoformat()})
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES('ubicaciones',?,datetime('now','localtime'))",
                 (json.dumps(ubicaciones,ensure_ascii=False),))
    # Historial GPS (para playback y auditoria)
    viaje_activo = conn.execute("SELECT id FROM viajes WHERE numero_unidad=? AND estado='asignado' ORDER BY id DESC LIMIT 1",(unidad,)).fetchone()
    id_viaje = viaje_activo['id'] if viaje_activo else None
    conn.execute("INSERT INTO gps_historial(numero_unidad,lat,lng,velocidad,id_viaje) VALUES(?,?,?,?,?)",
                 (unidad, lat, lng, velocidad, id_viaje))
    conn.commit(); conn.close()
    sse_broadcast('ubicacion', {'unidad': unidad, 'lat': lat, 'lng': lng, 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/ubicaciones')
def get_ubicaciones():
    """Última posición GPS reportada por cada unidad."""
    conn=get_db()
    row=conn.execute("SELECT value FROM kv_store WHERE key='ubicaciones'").fetchone()
    conn.close()
    if row:
        try:
            return jsonify(json.loads(row['value']))
        except:
            pass
    return jsonify([])


# ═══════════════════════════════════════════════════════════════════════════
# MÓDULOS DE PRODUCCIÓN — Excel, PDF, USB backup, SSE multi-operadora, KPIs
# ═══════════════════════════════════════════════════════════════════════════
import threading, queue, io, string
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from flask import Response, stream_with_context, send_file

# ── SSE — sincronización multi-operadora en tiempo real ────────────────────
_sse_clients = []
_sse_lock = threading.Lock()

def sse_broadcast(evento, datos):
    msg = "event: " + evento + "\ndata: " + json.dumps(datos, ensure_ascii=False) + "\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try: q.put_nowait(msg)
            except: dead.append(q)
        for q in dead:
            try: _sse_clients.remove(q)
            except: pass

@app.route('/api/eventos')
def sse_stream():
    def gen():
        q = queue.Queue(maxsize=30)
        with _sse_lock: _sse_clients.append(q)
        yield "retry: 3000\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with _sse_lock:
                try: _sse_clients.remove(q)
                except: pass
    return Response(stream_with_context(gen()), content_type='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/api/broadcast/<key>', methods=['POST'])
def api_broadcast(key):
    data = request.get_json(force=True)
    if data is None: return jsonify({'ok':False}),400
    conn = get_db()
    if key=='viajes':
        data = _normalizar_viajes(data, conn)
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 (key, json.dumps(data,ensure_ascii=False)))
    _sync_rel(conn, key, data)
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': key, 'ts': datetime.now().isoformat()})
    return jsonify({'ok': True})

# ── CONDUCTORES — registro socio / colaborador / concesionario ───────────────
def _calcular_codigo_unidad(unidad_base, tipo):
    if tipo == 'colaborador':
        return f'{unidad_base}D'
    return int(unidad_base)

def _get_conductores(conn=None, solo_activos=True):
    close=False
    if conn is None: conn=get_db(); close=True
    where="WHERE estado='activo'" if solo_activos else ""
    rows=conn.execute(f"SELECT * FROM conductores {where} ORDER BY unidad_base, CASE tipo WHEN 'socio' THEN 0 WHEN 'colaborador' THEN 1 ELSE 2 END").fetchall()
    if close: conn.close()
    result=[]
    for r in rows:
        d=dict(r)
        d['unidad']=d['unidad_base']
        d['tipo']=d.get('tipo','socio')
        d['unidad_codigo']=d.get('unidad_codigo',d['unidad_base'])
        result.append(d)
    return result

def _get_conductor_por_unidad(conn, unidad, tipo=None):
    if tipo:
        row=conn.execute("SELECT * FROM conductores WHERE unidad_base=? AND tipo=? AND estado='activo'",(unidad,tipo)).fetchone()
    else:
        row=conn.execute("SELECT * FROM conductores WHERE unidad_base=? AND estado='activo' ORDER BY CASE tipo WHEN 'socio' THEN 0 ELSE 1 END",(unidad,)).fetchone()
    if not row: return None
    d=dict(row)
    d['unidad']=d['unidad_base']
    d['unidad_codigo']=d.get('unidad_codigo',d['unidad_base'])
    return d

def _find_active_conductor_server(conn, conductores, unidad):
    """Devuelve el conductor activo (socio o colaborador) según quién esté QAP en unidades_actividad."""
    socio = next((x for x in conductores if x.get('unidad_base')==unidad and x.get('tipo')=='socio' and x.get('estado')=='activo'), None)
    colab = next((x for x in conductores if x.get('unidad_base')==unidad and x.get('tipo')=='colaborador' and x.get('estado')=='activo'), None)
    # Consultar última actividad QAP de esta unidad
    act_row = conn.execute("SELECT value FROM kv_store WHERE key='unidades_actividad'").fetchone()
    import json as _json
    if act_row and act_row['value']:
        try:
            act_list = _json.loads(act_row['value'])
            # Filtrar por unidad y ordenar por fecha descendente
            act_unidad = [a for a in act_list if str(a.get('numero_unidad',''))==str(unidad) and a.get('accion')=='qap']
            if act_unidad:
                act_unidad.sort(key=lambda x: x.get('fecha_hora',''), reverse=True)
                obs = str(act_unidad[0].get('obs','')).lower()
                if 'colaborador' in obs and colab:
                    return colab
                elif socio:
                    return socio
        except Exception:
            pass
    # Sin actividad QAP registrada: devolver socio primero, luego colaborador
    if socio: return socio
    if colab: return colab
    return None

@app.route('/api/conductores', methods=['GET'])
def listar_conductores():
    conn=get_db()
    rows=conn.execute("SELECT * FROM conductores ORDER BY unidad_base, tipo").fetchall()
    conn.close()
    result=[]
    for r in rows:
        d=dict(r)
        d['unidad']=d['unidad_base']
        d['unidad_codigo']=d.get('unidad_codigo',d['unidad_base'])
        result.append(d)
    return jsonify(result)

@app.route('/api/conductores', methods=['POST'])
def crear_conductor():
    d=request.get_json(force=True) or {}
    tipo=d.get('tipo','socio').strip().lower()
    if tipo not in ('socio','colaborador','concesionario'):
        return jsonify({'ok':False,'error':'Tipo inválido. Use: socio, colaborador, concesionario'}),400
    nombre=d.get('nombre','').strip()
    if not nombre:
        return jsonify({'ok':False,'error':'Nombre requerido'}),400
    try: unidad_base=int(d.get('unidad_base',0))
    except: return jsonify({'ok':False,'error':'Unidad inválida'}),400
    if tipo in ('socio','colaborador') and unidad_base<=0:
        return jsonify({'ok':False,'error':'Unidad requerida para socios y colaboradores'}),400
    if tipo=='colaborador':
        conn=get_db()
        socio=conn.execute("SELECT * FROM conductores WHERE tipo='socio' AND unidad_base=? AND estado='activo'",(unidad_base,)).fetchone()
        conn.close()
        if not socio:
            return jsonify({'ok':False,'error':f'No hay socio activo en unidad {unidad_base}'}),400
    if tipo=='concesionario':
        if unidad_base<=0:
            max_row=None
            conn=get_db()
            max_row=conn.execute("SELECT MAX(unidad_base) as mx FROM conductores WHERE tipo='concesionario'").fetchone()
            conn.close()
            unidad_base=(max_row['mx'] or 199)+1 if max_row else 200
    uc=_calcular_codigo_unidad(unidad_base, tipo)
    conn=get_db()
    existing=conn.execute("SELECT id FROM conductores WHERE cedula=? AND tipo=?",(d.get('cedula',''),tipo)).fetchone()
    if existing:
        conn.close()
        return jsonify({'ok':False,'error':f'Ya existe un conductor con cédula {d.get("cedula","")} tipo {tipo}'}),400
    if tipo=='colaborador':
        dup=conn.execute("SELECT id FROM conductores WHERE unidad_base=? AND tipo='colaborador' AND estado='activo'",(unidad_base,)).fetchone()
        if dup:
            conn.close()
            return jsonify({'ok':False,'error':f'Ya hay un colaborador activo en unidad {unidad_base}'}),400
    conn.execute("""INSERT INTO conductores(
        tipo,unidad_base,unidad_codigo,nombre,cedula,telefono,placa,
        codigo_radio,marca,modelo,color,registro_municipal,
        estado_civil,fecha_ingreso,estado,bloqueado)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tipo,unidad_base,uc,nombre,d.get('cedula',''),d.get('telefono',''),
         d.get('placa',''),d.get('codigo_radio',''),d.get('marca',''),
         d.get('modelo',''),d.get('color','amarillo'),d.get('registro_municipal',''),
         d.get('estado_civil',''),d.get('fecha_ingreso',datetime.now().strftime('%Y-%m-%d')),
         'activo',0))
    conn.commit()
    row=conn.execute("SELECT * FROM conductores WHERE cedula=? AND tipo=?",(d.get('cedula',''),tipo)).fetchone()
    conn.close()
    log_action('crear_conductor',f"{tipo} unidad {unidad_base} código {uc} - {nombre}",'conductores')
    sse_broadcast('conductores', {'action':'created','tipo':tipo,'unidad_codigo':uc})
    if row:
        rd=dict(row); rd['unidad']=rd['unidad_base']
        return jsonify({'ok':True,'conductor':rd})
    return jsonify({'ok':True})

@app.route('/api/conductores/<int:cid>', methods=['PUT'])
def actualizar_conductor(cid):
    d=request.get_json(force=True) or {}
    conn=get_db()
    cond=conn.execute("SELECT * FROM conductores WHERE id=?",(cid,)).fetchone()
    if not cond: conn.close(); return jsonify({'ok':False,'error':'Conductor no encontrado'}),404
    updates=[]
    vals=[]
    for k in ('nombre','cedula','telefono','placa','codigo_radio','marca','modelo','color','registro_municipal','estado_civil','fecha_ingreso','estado','bloqueado'):
        if k in d:
            updates.append(f"{k}=?")
            vals.append(d[k])
    if 'tipo' in d and d['tipo']!=cond['tipo']:
        conn.close(); return jsonify({'ok':False,'error':'No se puede cambiar el tipo. Cree uno nuevo.'}),400
    if 'unidad_base' in d and int(d['unidad_base'])!=cond['unidad_base']:
        new_ub=int(d['unidad_base'])
        updates.append("unidad_base=?")
        vals.append(new_ub)
        new_uc=_calcular_codigo_unidad(new_ub, cond['tipo'])
        updates.append("unidad_codigo=?")
        vals.append(new_uc)
    if updates:
        vals.append(cid)
        conn.execute(f"UPDATE conductores SET {','.join(updates)} WHERE id=?",vals)
        conn.commit()
    row=conn.execute("SELECT * FROM conductores WHERE id=?",(cid,)).fetchone()
    conn.close()
    log_action('actualizar_conductor',f"id={cid}",'conductores')
    if row:
        d=dict(row); d['unidad']=d['unidad_base']
        return jsonify({'ok':True,'conductor':d})
    return jsonify({'ok':True,'conductor':None})

@app.route('/api/conductores/<int:cid>', methods=['DELETE'])
def eliminar_conductor(cid):
    conn=get_db()
    cond=conn.execute("SELECT * FROM conductores WHERE id=?",(cid,)).fetchone()
    if not cond: conn.close(); return jsonify({'ok':False,'error':'Conductor no encontrado'}),404
    if cond['tipo']=='socio':
        colab=conn.execute("SELECT id FROM conductores WHERE unidad_base=? AND tipo='colaborador' AND estado='activo'",(cond['unidad_base'],)).fetchone()
        if colab:
            conn.close()
            return jsonify({'ok':False,'error':f'No puede eliminar socio. Hay un colaborador activo en unidad {cond["unidad_base"]}'}),400
    conn.execute("DELETE FROM conductores WHERE id=?",(cid,))
    conn.commit(); conn.close()
    log_action('eliminar_conductor',f"id={cid} {cond['nombre']}",'conductores')
    return jsonify({'ok':True})

@app.route('/api/conductores/unidad/<int:unidad>', methods=['GET'])
def conductor_por_unidad(unidad):
    conn=get_db()
    rows=conn.execute("SELECT * FROM conductores WHERE unidad_base=? AND estado='activo' ORDER BY tipo",(unidad,)).fetchall()
    conn.close()
    result=[]
    for r in rows:
        d=dict(r)
        d['unidad']=d['unidad_base']
        d['tipo']=d.get('tipo','socio')
        d['unidad_codigo']=d.get('unidad_codigo',d['unidad_base'])
        result.append(d)
    return jsonify(result)

@app.route('/api/conductores/qap-conflict-check', methods=['POST'])
def qap_conflict_check():
    d=request.get_json(force=True) or {}
    unidad_base=int(d.get('unidad_base',0))
    exclude_tipo=d.get('exclude_tipo','')
    conn=get_db()
    if exclude_tipo=='socio':
        conflict=conn.execute("SELECT * FROM conductores WHERE unidad_base=? AND tipo='colaborador' AND estado='activo' AND bloqueado=0",(unidad_base,)).fetchone()
    else:
        conflict=conn.execute("SELECT * FROM conductores WHERE unidad_base=? AND tipo='socio' AND estado='activo' AND bloqueado=0",(unidad_base,)).fetchone()
    conn.close()
    if conflict:
        return jsonify({'ok':False,'conflict':True,'mensaje':f'Unidad {unidad_base}: ya tiene un {conflict["tipo"]} activo ({conflict["nombre"]})'})
    return jsonify({'ok':True,'conflict':False})

# ── PRE-DESPACHO — cola de viajes pendientes de asignar unidad ──────────────
def _get_pre_despachos(conn=None):
    close=False
    if conn is None: conn=get_db(); close=True
    row=conn.execute("SELECT value FROM kv_store WHERE key='pre_despachos'").fetchone()
    data=json.loads(row['value']) if row and row['value'] else []
    if close: conn.close()
    return data

def _save_pre_despachos(data, conn=None):
    close=False
    if conn is None: conn=get_db(); close=True
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 ('pre_despachos', json.dumps(data, ensure_ascii=False)))
    if close: conn.commit(); conn.close()

@app.route('/api/viajes/pre-despacho', methods=['GET'])
def listar_pre_despachos():
    return jsonify(_get_pre_despachos())

@app.route('/api/viajes/pre-despacho', methods=['POST'])
def crear_pre_despacho():
    d=request.get_json(force=True) or {}
    if not d.get('cliente_nombre') or not d.get('direccion_recogida'):
        return jsonify({'ok':False,'error':'Cliente y dirección requeridos'}),400
    conn=get_db()
    pre=_get_pre_despachos(conn)
    max_id=max((p.get('id',0) for p in pre), default=0)
    viaje={
        'id': max_id+1,
        'fecha_solicitud': d.get('fecha_solicitud', datetime.now().isoformat()),
        'id_cliente': d.get('id_cliente',0),
        'cliente_nombre': d.get('cliente_nombre',''),
        'cliente_tel': d.get('cliente_tel',''),
        'direccion_recogida': d.get('direccion_recogida',''),
        'referencia_recogida': d.get('referencia_recogida',''),
        'destino': d.get('destino',''),
        'observaciones': d.get('observaciones',''),
        'usuario_id': d.get('usuario_id'),
        'usuario_nombre': d.get('usuario_nombre',''),
        'estado': 'pre_despacho'
    }
    pre.append(viaje)
    _save_pre_despachos(pre, conn)
    conn.commit(); conn.close()
    sse_broadcast('pre_despacho', {'action':'created','viaje':viaje})
    return jsonify({'ok':True,'viaje':viaje})

@app.route('/api/viajes/pre-despacho/<int:pid>', methods=['DELETE'])
def eliminar_pre_despacho(pid):
    conn=get_db()
    pre=_get_pre_despachos(conn)
    pre=[p for p in pre if p.get('id')!=pid]
    _save_pre_despachos(pre, conn)
    conn.commit(); conn.close()
    sse_broadcast('pre_despacho', {'action':'deleted','id':pid})
    return jsonify({'ok':True})

@app.route('/api/viajes/pre-despacho/<int:pid>/despachar', methods=['POST'])
def despachar_pre_despacho(pid):
    d=request.get_json(force=True) or {}
    n=d.get('numero_unidad')
    if not n: return jsonify({'ok':False,'error':'Número de unidad requerido'}),400
    n=int(n)
    conn=get_db()
    conductores = _get_conductores(conn)
    # Buscar conductor activo: socio o colaborador según quién esté QAP
    c = _find_active_conductor_server(conn, conductores, n)
    if not c: conn.close(); return jsonify({'ok':False,'error':f'Unidad {n} no encontrada'}),400
    if c.get('bloqueado'): conn.close(); return jsonify({'ok':False,'error':f'Unidad {n} bloqueada'}),400
    pre=_get_pre_despachos(conn)
    target=next((p for p in pre if p.get('id')==pid),None)
    if not target: conn.close(); return jsonify({'ok':False,'error':'Pre-despacho no encontrado'}),400
    # Observaciones adicionales del frontend (queja, instrucción, etc.)
    obs_extra = d.get('observaciones','').strip()
    obs_final = target.get('observaciones','')
    if obs_extra:
        obs_final = (obs_final + ' | ' + obs_extra) if obs_final else obs_extra
    viaje={
        'id': target['id'],
        'fecha_solicitud': target.get('fecha_solicitud', datetime.now().isoformat()),
        'id_cliente': target.get('id_cliente',0),
        'cliente_nombre': target.get('cliente_nombre',''),
        'cliente_tel': target.get('cliente_tel',''),
        'direccion_recogida': target.get('direccion_recogida',''),
        'referencia_recogida': target.get('referencia_recogida',''),
        'destino': target.get('destino',''),
        'observaciones': obs_final,
        'numero_unidad': n,
        'id_conductor': c.get('id'),
        'conductor_nombre': c.get('nombre',''),
        'conductor_tel': c.get('telefono',''),
        'usuario_id': target.get('usuario_id'),
        'usuario_nombre': target.get('usuario_nombre',''),
        'fecha_asignacion': datetime.now().isoformat(),
        'estado': 'asignado'
    }
    viajes_row=conn.execute("SELECT value FROM kv_store WHERE key='viajes'").fetchone()
    viajes=json.loads(viajes_row['value']) if viajes_row and viajes_row['value'] else []
    viajes.append(viaje)
    _save_pre_despachos([p for p in pre if p.get('id')!=pid], conn)
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 ('viajes', json.dumps(viajes, ensure_ascii=False)))
    _sync_rel(conn, 'viajes', viajes)
    conn.commit(); conn.close()
    sse_broadcast('pre_despacho', {'action':'dispatched','id':pid,'numero_unidad':n})
    sse_broadcast('update', {'key':'viajes','ts': datetime.now().isoformat()})
    return jsonify({'ok':True,'viaje':viaje})

@app.route('/api/viajes/<int:vid>/cancelar', methods=['POST'])
def cancelar_viaje(vid):
    d=request.get_json(force=True) or {}
    motivo=d.get('motivo','cancelado_operador')
    obs=d.get('observaciones','')
    conn=get_db()
    viajes_row=conn.execute("SELECT value FROM kv_store WHERE key='viajes'").fetchone()
    if not viajes_row or not viajes_row['value']:
        conn.close(); return jsonify({'ok':False,'error':'Sin viajes registrados'}),404
    viajes=json.loads(viajes_row['value'])
    v=next((x for x in viajes if x.get('id')==vid),None)
    if not v: conn.close(); return jsonify({'ok':False,'error':'Viaje no encontrado'}),404
    v['estado']=motivo
    obs_anterior=v.get('observaciones','')
    v['observaciones']=(obs_anterior+' | ' if obs_anterior else '')+'['+motivo.replace('_',' ')+'] '+(obs or 'Sin detalle')
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 ('viajes', json.dumps(viajes, ensure_ascii=False)))
    _sync_rel(conn, 'viajes', viajes)
    conn.commit(); conn.close()
    sse_broadcast('update', {'key':'viajes', 'ts': datetime.now().isoformat()})
    log_action('CANCEL', f"Viaje #{vid} — {motivo} — {obs}", 'operaciones')
    return jsonify({'ok':True})

@app.route('/api/viajes/<int:vid>/completado', methods=['POST'])
def completar_viaje_operadora(vid):
    """Marca un viaje como COMPLETADO por la operadora (después de la ventana de
    reasignación de 2 minutos, el botón Reasignar cambia a Completada)."""
    conn=get_db()
    viajes_row=conn.execute("SELECT value FROM kv_store WHERE key='viajes'").fetchone()
    if not viajes_row or not viajes_row['value']:
        conn.close(); return jsonify({'ok':False,'error':'Sin viajes registrados'}),404
    viajes=json.loads(viajes_row['value'])
    v=next((x for x in viajes if x.get('id')==vid),None)
    if not v: conn.close(); return jsonify({'ok':False,'error':'Viaje no encontrado'}),404
    if v.get('estado') not in ('asignado','en_curso','en_destino'):
        conn.close(); return jsonify({'ok':True,'sin_cambio':True})
    v['estado']='completado'
    v['fecha_completado']=datetime.now().isoformat()
    conn.execute("INSERT OR REPLACE INTO kv_store(key,value,updated_at) VALUES(?,?,datetime('now','localtime'))",
                 ('viajes', json.dumps(viajes, ensure_ascii=False)))
    _sync_rel(conn, 'viajes', viajes)
    conn.commit(); conn.close()
    sse_broadcast('update', {'key':'viajes', 'ts': datetime.now().isoformat()})
    log_action('COMPLETADO', f"Viaje #{vid} marcado completado por operadora", 'operaciones')
    return jsonify({'ok':True})

@app.route('/ping')
def ping():
    return jsonify({'ok': True, 'ts': datetime.now().isoformat()})

# ── EXCEL ────────────────────────────────────────────────────────────────
NAVY='0F1320'; GOLD='F5A623'; WHITE='FFFFFF'; GRAY='F5F5F5'

def _hdr(ws,row,cols):
    fill=PatternFill("solid",fgColor=NAVY); font=Font(bold=True,color=WHITE,size=10)
    for col,title in enumerate(cols,1):
        c=ws.cell(row=row,column=col,value=title); c.fill=fill; c.font=font
        c.alignment=Alignment(horizontal='center',vertical='center')

def _gold(ws,row):
    fill=PatternFill("solid",fgColor=GOLD)
    for col in range(1,ws.max_column+1): ws.cell(row=row,column=col).fill=fill

def _autow(ws):
    widths = {}
    for row in ws.iter_rows():
        for c in row:
            if c.__class__.__name__ == 'MergedCell':
                continue
            L = c.column_letter
            widths[L] = max(widths.get(L, 10), len(str(c.value or '')))
    for L, w in widths.items():
        ws.column_dimensions[L].width = min(w+4, 45)

@app.route('/api/exportar/viajes-dia')
def exportar_viajes_dia():
    fecha=request.args.get('fecha', date.today().isoformat())
    conn=get_db()
    viajes=rows_to_list(conn.execute("SELECT * FROM viajes WHERE date(fecha_solicitud)=? ORDER BY fecha_solicitud",(fecha,)).fetchall())
    conn.close()
    wb=Workbook(); ws=wb.active; ws.title="Viajes"
    ws.merge_cells('A1:H1')
    t=ws['A1']; t.value="CTO Occidental 119 - Viajes del "+fecha
    t.font=Font(bold=True,size=13,color=NAVY); t.alignment=Alignment(horizontal='center')
    _gold(ws,1)
    _hdr(ws,2,['#','Hora','Cliente','Telefono','Direccion','Unidad','Conductor','Estado'])
    for i,v in enumerate(viajes,1):
        fill=PatternFill("solid",fgColor='FFFFFF' if i%2 else GRAY)
        row=[i,(v.get('fecha_solicitud') or '')[:16],v.get('cliente_nombre',''),v.get('cliente_tel',''),
             v.get('direccion_recogida',''),v.get('numero_unidad',''),v.get('conductor_nombre',''),v.get('estado','')]
        for col,val in enumerate(row,1): ws.cell(row=i+2,column=col,value=val).fill=fill
    last=len(viajes)+3
    ws.cell(row=last,column=1,value="Total: "+str(len(viajes))+" viajes").font=Font(bold=True,color=NAVY)
    _autow(ws)
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf,download_name='viajes_'+fecha+'.xlsx',as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/exportar/cobros-mes')
def exportar_cobros_mes():
    anio=int(request.args.get('anio',datetime.now().year)); mes=int(request.args.get('mes',datetime.now().month))
    meses=['','Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
    conn=get_db()
    pagos=rows_to_list(conn.execute("SELECT * FROM pagos_mensuales WHERE anio=? AND mes=? ORDER BY id",(anio,mes)).fetchall())
    multas=rows_to_list(conn.execute("SELECT * FROM multas WHERE anio=? AND mes=? ORDER BY id",(anio,mes)).fetchall())
    conductores=_get_conductores(conn, solo_activos=False)
    conn.close()
    wb=Workbook(); ws1=wb.active; ws1.title="Cobros"
    ws1.merge_cells('A1:G1')
    t=ws1['A1']; t.value="CTO Occidental 119 - Cobros "+meses[mes]+" "+str(anio)
    t.font=Font(bold=True,size=13,color=NAVY); t.alignment=Alignment(horizontal='center')
    _gold(ws1,1)
    _hdr(ws1,2,['Unidad','Conductor','Tipo','Base ($)','Multas ($)','Total ($)','Estado'])
    total_rec=total_pend=0
    for i,p in enumerate(pagos,1):
        c=next((x for x in conductores if x.get('id')==p.get('id_conductor')),{})
        fill=PatternFill("solid",fgColor=('E8F5E9' if p.get('estado')=='pagado' else 'FFF9E6') if i%2 else GRAY)
        row=[c.get('unidad',''),' '.join(c.get('nombre','').split()[:3]),c.get('tipo',''),
             p.get('monto_base',0),p.get('multas',0),p.get('total',0),p.get('estado','')]
        for col,val in enumerate(row,1): ws1.cell(row=i+2,column=col,value=val).fill=fill
        if p.get('estado')=='pagado': total_rec+=p.get('total',0)
        else: total_pend+=p.get('total',0)
    last=len(pagos)+3
    ws1.cell(row=last,column=5,value='Recaudado:').font=Font(bold=True)
    ws1.cell(row=last,column=6,value=round(total_rec,2)).font=Font(bold=True,color='2E7D32')
    ws1.cell(row=last+1,column=5,value='Pendiente:').font=Font(bold=True)
    ws1.cell(row=last+1,column=6,value=round(total_pend,2)).font=Font(bold=True,color='C62828')
    _autow(ws1)
    ws2=wb.create_sheet("Morosos")
    morosos=[p for p in pagos if p.get('estado')!='pagado']
    ws2.merge_cells('A1:F1')
    t2=ws2['A1']; t2.value="Unidades con Adeudos - "+meses[mes]+" "+str(anio)
    t2.font=Font(bold=True,size=12,color='C62828'); t2.alignment=Alignment(horizontal='center')
    _hdr(ws2,2,['Unidad','Conductor','Tipo','Total Adeudado ($)','Estado','Telefono'])
    for i,p in enumerate(morosos,1):
        c=next((x for x in conductores if x.get('id')==p.get('id_conductor')),{})
        fill=PatternFill("solid",fgColor='FFEBEE' if i%2 else 'FFCDD2')
        row=[c.get('unidad',''),' '.join(c.get('nombre','').split()[:3]),c.get('tipo',''),
             round(p.get('total',0),2),p.get('estado',''),c.get('telefono','')]
        for col,val in enumerate(row,1): ws2.cell(row=i+2,column=col,value=val).fill=fill
    _autow(ws2)
    if multas:
        ws3=wb.create_sheet("Multas")
        _hdr(ws3,1,['Unidad','Conductor','Concepto','Monto ($)','Fecha','Pagada'])
        for i,m in enumerate(multas,1):
            c=next((x for x in conductores if x.get('id')==m.get('id_conductor')),{})
            fill=PatternFill("solid",fgColor='FFFFFF' if i%2 else GRAY)
            row=[c.get('unidad',''),' '.join(c.get('nombre','').split()[:3]),m.get('concepto',''),
                 m.get('monto',0),(m.get('fecha') or '')[:10],'Si' if m.get('pagada') else 'No']
            for col,val in enumerate(row,1): ws3.cell(row=i+1,column=col,value=val).fill=fill
        _autow(ws3)
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf,download_name='cobros_'+meses[mes]+'_'+str(anio)+'.xlsx',as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/exportar/jornada-pdf')
def exportar_jornada_pdf():
    fecha=request.args.get('fecha', date.today().isoformat())
    conn=get_db()
    viajes=rows_to_list(conn.execute("SELECT * FROM viajes WHERE date(fecha_solicitud)=? ORDER BY fecha_solicitud",(fecha,)).fetchall())
    qap=rows_to_list(conn.execute("SELECT numero_unidad,accion,fecha_hora FROM unidades_actividad WHERE date(fecha_hora)=? ORDER BY fecha_hora",(fecha,)).fetchall())
    conn.close()
    total_ok=sum(1 for v in viajes if v['estado']=='asignado')
    total_perd=sum(1 for v in viajes if v['estado']=='perdido')
    unids_act=len(set(a['numero_unidad'] for a in qap if a['accion']=='qap'))
    filas=''
    for v in viajes:
        badge='badge-ok' if v['estado']=='asignado' else 'badge-no'
        filas += "<tr><td>"+(v.get('fecha_solicitud') or '')[:16]+"</td><td>"+str(v.get('cliente_nombre',''))+"</td><td>"+str(v.get('cliente_tel',''))+"</td><td>"+str((v.get('direccion_recogida') or '')[:45])+"</td><td><strong>"+str(v.get('numero_unidad',''))+"</strong></td><td><span class='"+badge+"'>"+str(v['estado'])+"</span></td></tr>"
    html = "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Reporte CTO "+fecha+"</title><style>"
    html += "body{font-family:Arial,sans-serif;font-size:12px;color:#222;margin:20px}"
    html += "h1{color:#0f1320;border-bottom:3px solid #f5a623;padding-bottom:6px;font-size:18px}"
    html += "table{width:100%;border-collapse:collapse;margin-top:8px}"
    html += "th{background:#0f1320;color:#fff;padding:6px 8px;text-align:left;font-size:11px}"
    html += "td{padding:5px 8px;border-bottom:1px solid #eee;font-size:11px}"
    html += "tr:nth-child(even) td{background:#f9f9f9}"
    html += ".stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}"
    html += ".stat{background:#f5f5f5;border:1px solid #ddd;border-radius:6px;padding:12px;text-align:center}"
    html += ".stat .val{font-size:28px;font-weight:bold;color:#f5a623}"
    html += ".stat .lbl{font-size:11px;color:#666}"
    html += ".badge-ok{background:#e8f5e9;color:#2e7d32;padding:2px 8px;border-radius:10px}"
    html += ".badge-no{background:#fce4ec;color:#c62828;padding:2px 8px;border-radius:10px}"
    html += "@media print{button{display:none}}</style></head><body>"
    html += "<h1>CTO Cooperativa de Taxis Occidental 119 - Reporte de Jornada</h1>"
    html += "<p><strong>Fecha:</strong> "+fecha+" | <strong>Generado:</strong> "+datetime.now().strftime('%H:%M:%S')+"</p>"
    html += "<div class='stats'>"
    html += "<div class='stat'><div class='val'>"+str(total_ok)+"</div><div class='lbl'>Carreras realizadas</div></div>"
    html += "<div class='stat'><div class='val'>"+str(total_perd)+"</div><div class='lbl'>Carreras perdidas</div></div>"
    html += "<div class='stat'><div class='val'>"+str(unids_act)+"</div><div class='lbl'>Unidades activas</div></div>"
    html += "<div class='stat'><div class='val'>"+str(len(viajes))+"</div><div class='lbl'>Total solicitudes</div></div></div>"
    html += "<button onclick='window.print()' style='background:#0f1320;color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px'>Imprimir / Guardar PDF</button>"
    html += "<h2>Detalle de Viajes</h2><table><tr><th>Hora</th><th>Cliente</th><th>Telefono</th><th>Direccion</th><th>Unidad</th><th>Estado</th></tr>"
    html += filas + "</table></body></html>"
    return Response(html, content_type='text/html; charset=utf-8')

# ── REPORTE DE CIERRE DIARIO ───────────────────────────────────────────────
@app.route('/api/reportes/cierre/listar')
def listar_reportes_cierre():
    """Lista todos los reportes de cierre disponibles."""
    if not session.get('uid'):
        return jsonify({'ok': False, 'error': 'Sesión requerida'}), 401
    reportes_dir = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos', 'reportes_cierre')
    if not os.path.exists(reportes_dir):
        return jsonify([])
    archivos = sorted([f for f in os.listdir(reportes_dir) if f.startswith('reporte_') and f.endswith('.json')], reverse=True)
    resultados = []
    for archivo in archivos[:50]:
        try:
            filepath = os.path.join(reportes_dir, archivo)
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            resultados.append({
                'fecha': data.get('fecha'),
                'generado': data.get('generado'),
                'resumen': data.get('resumen'),
            })
        except Exception:
            continue
    return jsonify(resultados)


@app.route('/api/reportes/cierre/<fecha>')
def obtener_reporte_cierre(fecha):
    """Obtiene el reporte de cierre completo de una fecha específica."""
    if not session.get('uid'):
        return jsonify({'ok': False, 'error': 'Sesión requerida'}), 401
    reportes_dir = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos', 'reportes_cierre')
    filepath = os.path.join(reportes_dir, f'reporte_{fecha}.json')
    if not os.path.exists(filepath):
        return jsonify({'error': 'Reporte no encontrado'}), 404
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/reportes/cierre/generar', methods=['POST'])
def generar_reporte_cierre_manual():
    """Genera reporte de cierre manualmente para una fecha."""
    if not session.get('uid'):
        return jsonify({'ok': False, 'error': 'Sesión requerida'}), 401
    d = request.get_json(force=True) or {}
    fecha = d.get('fecha', date.today().isoformat())
    conn = None
    try:
        conn = get_db()
        reporte = _generar_reporte_cierre(conn, fecha)
        return jsonify({'ok': True, 'reporte': reporte})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


# ── RESPALDO USB ─────────────────────────────────────────────────────────
@app.route('/api/backup/usb', methods=['POST'])
def backup_usb():
    ts='backup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.db'
    copiados=[]
    for letra in string.ascii_uppercase[3:]:
        ruta=letra+':\\'
        if os.path.exists(ruta):
            try:
                destino=os.path.join(ruta, ts)
                shutil.copy2(DB_PATH, destino)
                copiados.append(destino)
            except Exception as e: logger.warning(f"USB backup copy failed {ruta}: {e}")
    local=os.path.join(BACKUP_DIR, ts)
    shutil.copy2(DB_PATH, local)
    log_action('BACKUP_USB', "USB:"+str(copiados)+" Local:"+local, 'sistema')
    return jsonify({'ok':True,'usb':copiados,'local':local,
                    'mensaje':"Respaldo guardado en "+str(len(copiados))+" USB + carpeta local"})

# ── SECTORES DE QUITO ───────────────────────────────────────────────────
SECTORES_QUITO = {
    'norte': ['condado','cotocollao','carcelen','comite del pueblo','ponceano','cochapamba',
              'kennedy','america','colon','santa clara','jipijapa','iñaquito','la carolina',
              'bellavista','quito tennis','naciones unidas','shyris','republica'],
    'centro': ['centro','la merced','san blas','sagrario','gonzalez suarez','la floresta',
               'la vicentina','itchimbia','san juan','belisario quevedo'],
    'sur': ['solanda','chillogallo','la ecuatoriana','quitumbe','guamani','turubamba',
            'la magdalena','chimbacalle','villaflora','guajalo','la argelia','mena'],
    'valle_tumbaco': ['cumbaya','tumbaco','puembo','pifo','el quinche','yaruqui','checa'],
    'valle_los_chillos': ['sangolqui','conocoto','alangasi','pintag','amaguaña',
                          'la armenia','san rafael','fajardo'],
    'calderon_norte': ['calderon','llano chico','pomasqui','san antonio','mitad del mundo',
                       'nayon','zambiza','guayllabamba'],
}

def _quitar_tildes(txt):
    import unicodedata
    nfkd = unicodedata.normalize('NFKD', txt)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))

@app.route('/api/utils/sector')
def detectar_sector():
    addr_raw = (request.args.get('direccion') or '')
    addr = _quitar_tildes(addr_raw.lower())
    if not addr: return jsonify({'sector':None})
    for sector,kws in SECTORES_QUITO.items():
        if any(_quitar_tildes(kw) in addr for kw in kws):
            nombre = sector.replace('_',' ').title()
            return jsonify({'sector':nombre,'raw':sector})
    return jsonify({'sector':'Centro','raw':'centro'})

# ── KPIs GERENCIALES ────────────────────────────────────────────────────
@app.route('/api/estadisticas/kpis')
def estadisticas_kpis():
    conn=get_db(); hoy=date.today().isoformat(); mes=date.today().month; anio=date.today().year
    vhoy=dict(conn.execute("SELECT COUNT(*) as t,SUM(CASE WHEN estado='asignado' THEN 1 ELSE 0 END) as ok,SUM(CASE WHEN estado='perdido' THEN 1 ELSE 0 END) as perd FROM viajes WHERE date(fecha_solicitud)=?",(hoy,)).fetchone() or {})
    vmes=dict(conn.execute("SELECT COUNT(*) as t FROM viajes WHERE strftime('%Y-%m',fecha_solicitud)=?",(str(anio).zfill(4)+'-'+str(mes).zfill(2),)).fetchone() or {})
    uact=conn.execute("SELECT COUNT(DISTINCT numero_unidad) as n FROM unidades_actividad WHERE date(fecha_hora)=? AND accion='qap'",(hoy,)).fetchone()
    rec=conn.execute("SELECT COALESCE(SUM(total),0) as s FROM pagos_mensuales WHERE anio=? AND mes=? AND estado='pagado'",(anio,mes)).fetchone()
    pend=conn.execute("SELECT COUNT(*) as n FROM pagos_mensuales WHERE anio=? AND mes=? AND estado!='pagado'",(anio,mes)).fetchone()
    hp=conn.execute("SELECT strftime('%H',fecha_solicitud) as h,COUNT(*) as n FROM viajes WHERE date(fecha_solicitud)=? GROUP BY h ORDER BY n DESC LIMIT 1",(hoy,)).fetchone()
    conn.close()
    t = vhoy.get('t') or 0
    ok = vhoy.get('ok') or 0
    return jsonify({
        'viajes_hoy': ok, 'perdidos_hoy': vhoy.get('perd') or 0,
        'viajes_mes': vmes.get('t') or 0,
        'unidades_activas': uact['n'] if uact else 0,
        'recaudacion_mes': round(rec['s'] if rec else 0,2),
        'unidades_deben': pend['n'] if pend else 0,
        'hora_pico': (hp['h']+'h00') if hp else '-',
        'efectividad': round((ok/t)*100) if t else 0,
    })


# ═════════════════════════════════════════════════════════════════════════════
# NUEVOS MÓDULOS v11.0 — Tarifa, SOS, Calificación, GPS Playback,
# Documentos, Soporte, Limpieza, Auditoría Nocturna, Rate Limit Conductores
# ═════════════════════════════════════════════════════════════════════════════

# ── #6: CÁLCULO DE TARIFA ───────────────────────────────────────────────
@app.route('/api/tarifas')
def listar_tarifas():
    conn=get_db(); rows=rows_to_list(conn.execute("SELECT * FROM tarifas WHERE activa=1 ORDER BY zona").fetchall()); conn.close()
    return jsonify(rows)

@app.route('/api/tarifas', methods=['POST'])
def crear_tarifa():
    d=request.get_json(force=True) or {}
    zona=d.get('zona','').strip()
    if not zona: return jsonify({'ok':False,'error':'Zona requerida'}),400
    conn=get_db()
    conn.execute("INSERT INTO tarifas(zona,tarifa_base,precio_por_km,minimo,recargo_pico,recargo_nocturno) VALUES(?,?,?,?,?,?)",
                 (zona, d.get('tarifa_base',0), d.get('precio_por_km',0), d.get('minimo',0),
                  d.get('recargo_pico',0), d.get('recargo_nocturno',0)))
    conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/tarifas/<int:id>', methods=['PUT'])
def editar_tarifa(id):
    d=request.get_json(force=True) or {}
    conn=get_db()
    conn.execute("UPDATE tarifas SET zona=?,tarifa_base=?,precio_por_km=?,minimo=?,recargo_pico=?,recargo_nocturno=? WHERE id=?",
                 (d.get('zona',''),d.get('tarifa_base',0),d.get('precio_por_km',0),d.get('minimo',0),
                  d.get('recargo_pico',0),d.get('recargo_nocturno',0),id))
    conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/tarifas/<int:id>', methods=['DELETE'])
def eliminar_tarifa(id):
    conn=get_db(); conn.execute("DELETE FROM tarifas WHERE id=?", (id,)); conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/tarifas/calcular')
def calcular_tarifa():
    zona = request.args.get('zona','')
    hora = request.args.get('hora', datetime.now().strftime('%H:%M'))
    km = float(request.args.get('km', 0))
    conn=get_db()
    t = conn.execute("SELECT * FROM tarifas WHERE zona=? AND activa=1", (zona,)).fetchone()
    feriado = conn.execute("SELECT * FROM feriados WHERE fecha=?", (date.today().isoformat(),)).fetchone()
    conn.close()
    if not t:
        return jsonify({'ok':False,'error':'Zona no encontrada','tarifa_sugerida': 2.50})
    base = t['tarifa_base'] or 2.50
    por_km = t['precio_por_km'] or 0.50
    recargo = 0
    hh = int(hora[:2]) if len(hora) >= 2 else datetime.now().hour
    if (hh >= 21 or hh < 6):
        recargo = t['recargo_nocturno'] or 0.30
    elif (hh >= 7 and hh <= 9) or (hh >= 17 and hh <= 19):
        recargo = t['recargo_pico'] or 0.20
    if feriado:
        recargo += feriado['recargo'] or 0.15
    subtotal = base + (por_km * km)
    total = round(subtotal * (1 + recargo), 2)
    total = max(total, t['minimo'] or base)
    return jsonify({'ok':True, 'base': base, 'por_km': por_km, 'km': km,
                    'recargo': round(recargo * 100), 'total': total, 'zona': zona})

@app.route('/api/feriados')
def listar_feriados():
    conn=get_db(); rows=rows_to_list(conn.execute("SELECT * FROM feriados ORDER BY fecha").fetchall()); conn.close()
    return jsonify(rows)

@app.route('/api/feriados', methods=['POST'])
def crear_feriado():
    d=request.get_json(force=True) or {}
    conn=get_db()
    try:
        conn.execute("INSERT INTO feriados(fecha,nombre,recargo) VALUES(?,?,?)",
                     (d.get('fecha',''), d.get('nombre',''), d.get('recargo',0.15)))
        conn.commit()
    except: pass
    conn.close()
    return jsonify({'ok':True})

@app.route('/api/feriados/<int:id>', methods=['DELETE'])
def eliminar_feriado(id):
    conn=get_db(); conn.execute("DELETE FROM feriados WHERE id=?", (id,)); conn.commit(); conn.close()
    return jsonify({'ok':True})

# ── #7/#17: NOTIFICACIÓN WHATSAPP AL CLIENTE ────────────────────────────
@app.route('/api/whatsapp/notificar-cliente', methods=['POST'])
def whatsapp_notificar_cliente():
    d=request.get_json(force=True) or {}
    telefono = d.get('telefono','')
    mensaje = d.get('mensaje','')
    unidad = d.get('unidad','')
    if not telefono or not mensaje:
        return jsonify({'ok':False,'error':'Teléfono y mensaje requeridos'}), 400
    conn=get_db()
    conn.execute("INSERT INTO mensajes_whatsapp(numero,contenido,estado) VALUES(?,?,'enviado')",
                 (telefono, mensaje))
    conn.commit(); conn.close()
    import webbrowser
    numero_limpio = telefono.replace('-','').replace(' ','')
    if numero_limpio.startswith('0'):
        numero_limpio = '593' + numero_limpio[1:]
    url = f"https://wa.me/{numero_limpio}?text={mensaje}"
    try: webbrowser.open(url)
    except: pass
    log_action('WHATSAPP_NOTIF', f"Cliente {telefono} - Unidad {unidad}", 'operacion')
    return jsonify({'ok':True, 'url': url})

# ── #8: BOTÓN DE PÁNICO / SOS ───────────────────────────────────────────
@app.route('/api/conductor/sos', methods=['POST'])
def conductor_sos():
    d=request.get_json(force=True) or {}
    unidad = int(d.get('unidad', 0))
    lat = d.get('lat')
    lng = d.get('lng')
    if not unidad: return jsonify({'ok':False,'error':'Unidad requerida'}), 400
    conn=get_db()
    conductores = _get_conductores(conn)
    conductor = _find_active_conductor_server(conn, conductores, unidad)
    conn.execute("""INSERT INTO alertas_sos(numero_unidad,id_conductor,conductor_nombre,lat,lng)
        VALUES(?,?,?,?,?)""",
        (unidad, conductor.get('id',0) if conductor else 0,
         conductor.get('nombre','') if conductor else '', lat, lng))
    conn.commit(); conn.close()
    sse_broadcast('alerta_sos', {'unidad': unidad, 'conductor': conductor.get('nombre','') if conductor else '',
                                  'lat': lat, 'lng': lng, 'ts': datetime.now().isoformat()})
    log_action('SOS', f"Unidad {unidad} - {conductor.get('nombre','') if conductor else ''}", 'seguridad')
    return jsonify({'ok':True})

@app.route('/api/sos/activas')
def sos_activas():
    conn=get_db()
    rows=rows_to_list(conn.execute("SELECT * FROM alertas_sos WHERE estado='activa' ORDER BY fecha DESC").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/sos/atender/<int:id>', methods=['POST'])
def atender_sos(id):
    d=request.get_json(force=True) or {}
    conn=get_db()
    conn.execute("UPDATE alertas_sos SET estado='atendida', atendida_fecha=datetime('now','localtime'), atendida_usuario=? WHERE id=?",
                 (d.get('usuario',''), id))
    conn.commit(); conn.close()
    sse_broadcast('sos_atendida', {'id': id})
    return jsonify({'ok':True})

# ── #9: TRACKING DE VIAJE EN CURSO ──────────────────────────────────────
@app.route('/api/conductor/iniciar-viaje', methods=['POST'])
def iniciar_viaje_conductor():
    d=request.get_json(force=True) or {}
    id_viaje = d.get('id_viaje')
    if not id_viaje: return jsonify({'ok':False,'error':'id_viaje requerido'}), 400
    conn=get_db()
    conn.execute("UPDATE viajes SET estado='en_curso' WHERE id=? AND estado='asignado'", (id_viaje,))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'viajes', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/conductor/llego-destino', methods=['POST'])
def llego_destino_conductor():
    d=request.get_json(force=True) or {}
    id_viaje = d.get('id_viaje')
    if not id_viaje: return jsonify({'ok':False,'error':'id_viaje requerido'}), 400
    conn=get_db()
    conn.execute("UPDATE viajes SET estado='en_destino' WHERE id=? AND estado='en_curso'", (id_viaje,))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'viajes', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

# ── #10: AUDITORÍA NOCTURNA AUTOMÁTICA ─────────────────────────────────
@app.route('/api/turno/auditoria')
def auditoria_nocturna():
    fecha = request.args.get('fecha', date.today().isoformat())
    conn=get_db()
    viajes = rows_to_list(conn.execute("SELECT * FROM viajes WHERE date(fecha_solicitud)=?", (fecha,)).fetchall())
    qap_list = rows_to_list(conn.execute("SELECT * FROM unidades_actividad WHERE date(fecha_hora)=? AND accion='qap'", (fecha,)).fetchall())
    conductores_qap = set(a['numero_unidad'] for a in qap_list)
    total = len(viajes)
    completados = sum(1 for v in viajes if v['estado'] == 'asignado')
    perdidos = sum(1 for v in viajes if v['estado'] == 'perdido')
    cancelados = sum(1 for v in viajes if v['estado'] in ('cancelado_cliente', 'cancelado_operador'))
    efectividad = round((completados / total * 100)) if total else 0
    unidades_despachadas = len(set(v['numero_unidad'] for v in viajes if v.get('numero_unidad')))
    topo = {}
    for v in viajes:
        n = v.get('numero_unidad')
        if n: topo[n] = topo.get(n, 0) + 1
    top_unidades = sorted(topo.items(), key=lambda x: -x[1])[:10]
    SOS = rows_to_list(conn.execute("SELECT * FROM alertas_sos WHERE date(fecha)=? AND estado='activa'", (fecha,)).fetchall())
    conn.close()
    return jsonify({
        'fecha': fecha, 'total_viajes': total, 'completados': completados,
        'perdidos': perdidos, 'cancelados': cancelados, 'efectividad': efectividad,
        'unidades_qap': len(conductores_qap), 'unidades_despachadas': unidades_despachadas,
        'top_unidades': [{'unidad': u, 'viajes': c} for u, c in top_unidades],
        'alertas_sos': len(SOS),
    })

# ── #11: CALIFICACIÓN DE SERVICIO ───────────────────────────────────────
@app.route('/api/calificaciones', methods=['POST'])
def crear_calificacion():
    d=request.get_json(force=True) or {}
    conn=get_db()
    conn.execute("""INSERT INTO calificaciones(id_viaje,id_cliente,cliente_nombre,numero_unidad,
        id_conductor,conductor_nombre,puntuacion,comentario) VALUES(?,?,?,?,?,?,?,?)""",
        (d.get('id_viaje'), d.get('id_cliente'), d.get('cliente_nombre',''),
         d.get('numero_unidad'), d.get('id_conductor'), d.get('conductor_nombre',''),
         d.get('puntuacion', 5), d.get('comentario','')))
    conn.commit(); conn.close()
    sse_broadcast('update', {'key': 'calificaciones', 'ts': datetime.now().isoformat()})
    return jsonify({'ok':True})

@app.route('/api/calificaciones')
def listar_calificaciones():
    conn=get_db()
    rows=rows_to_list(conn.execute("SELECT * FROM calificaciones ORDER BY fecha DESC LIMIT 200").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/calificaciones/unidad/<int:unidad>')
def calificaciones_unidad(unidad):
    conn=get_db()
    rows=rows_to_list(conn.execute("SELECT * FROM calificaciones WHERE numero_unidad=? ORDER BY fecha DESC LIMIT 50", (unidad,)).fetchall())
    avg = conn.execute("SELECT AVG(puntuacion) as promedio, COUNT(*) as total FROM calificaciones WHERE numero_unidad=?", (unidad,)).fetchone()
    conn.close()
    return jsonify({'calificaciones': rows, 'promedio': round(avg['promedio'], 1) if avg['promedio'] else 5, 'total': avg['total']})

@app.route('/api/calificaciones/dashboard')
def calificaciones_dashboard():
    conn=get_db()
    top = rows_to_list(conn.execute("""SELECT numero_unidad, AVG(puntuacion) as promedio, COUNT(*) as total
        FROM calificaciones GROUP BY numero_unidad HAVING total >= 3 ORDER BY promedio DESC LIMIT 10""").fetchall())
    recent = rows_to_list(conn.execute("SELECT * FROM calificaciones ORDER BY fecha DESC LIMIT 10").fetchall())
    conn.close()
    return jsonify({'top_unidades': top, 'recientes': recent})

# ── #13: DOCUMENTOS DE CONDUCTORES ──────────────────────────────────────
@app.route('/api/documentos-conductor')
def listar_documentos():
    id_cond = request.args.get('id_conductor')
    conn=get_db()
    if id_cond:
        rows=rows_to_list(conn.execute("SELECT * FROM documentos_conductor WHERE id_conductor=? ORDER BY fecha_vencimiento", (id_cond,)).fetchall())
    else:
        rows=rows_to_list(conn.execute("SELECT * FROM documentos_conductor ORDER BY fecha_vencimiento").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/documentos-conductor/vencidos')
def documentos_vencidos():
    hoy = date.today().isoformat()
    conn=get_db()
    rows=rows_to_list(conn.execute("SELECT * FROM documentos_conductor WHERE fecha_vencimiento <= ? ORDER BY fecha_vencimiento", (hoy,)).fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/documentos-conductor', methods=['POST'])
def crear_documento():
    d=request.get_json(force=True) or {}
    conn=get_db()
    conn.execute("""INSERT INTO documentos_conductor(id_conductor,tipo,numero,fecha_emision,fecha_vencimiento,observaciones)
        VALUES(?,?,?,?,?,?)""",
        (d.get('id_conductor'), d.get('tipo',''), d.get('numero',''),
         d.get('fecha_emision',''), d.get('fecha_vencimiento',''), d.get('observaciones','')))
    conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/documentos-conductor/<int:id>', methods=['DELETE'])
def eliminar_documento(id):
    conn=get_db(); conn.execute("DELETE FROM documentos_conductor WHERE id=?", (id,)); conn.commit(); conn.close()
    return jsonify({'ok':True})

# ── #15: RATE LIMITING CONDUCTORES ──────────────────────────────────────
_conductor_rate = collections.defaultdict(list)

@app.before_request
def check_conductor_rate():
    if request.endpoint and 'conductor' in request.endpoint:
        ip = request.remote_addr or '127.0.0.1'
        now = datetime.now().timestamp()
        key = f"cond_{ip}"
        _conductor_rate[key] = [t for t in _conductor_rate[key] if now - t < 60]
        if len(_conductor_rate[key]) >= 60:
            return jsonify({'ok':False,'error':'Demasiadas peticiones del conductor'}), 429
        _conductor_rate[key].append(now)

# ── #16: LIMPIEZA DE DATOS ANTIGUOS ─────────────────────────────────────
@app.route('/api/admin/limpiar-datos', methods=['POST'])
def limpiar_datos_antiguos():
    d = request.get_json(force=True) or {}
    dias = int(d.get('dias', 90))
    conn = get_db()
    fecha_corte = (datetime.now() - timedelta(days=dias)).isoformat()
    conn.execute("DELETE FROM gps_historial WHERE fecha < ?", (fecha_corte,))
    conn.execute("DELETE FROM unidades_actividad WHERE fecha_hora < ?", (fecha_corte,))
    conn.execute("DELETE FROM bitacora WHERE fecha < ?", (fecha_corte,))
    conn.commit()
    eliminado = conn.total_changes
    conn.close()
    log_action('LIMPIEZA', f"Datos anteriores a {dias} días eliminados", 'admin')
    return jsonify({'ok': True, 'eliminados': eliminado})

@app.route('/api/admin/estadisticas-db')
def estadisticas_db():
    conn = get_db()
    stats = {}
    for tabla in ['viajes','clientes','unidades_actividad','bitacora','gps_historial','calificaciones','alertas_sos']:
        r = conn.execute(f"SELECT COUNT(*) as n FROM {tabla}").fetchone()
        stats[tabla] = r['n'] if r else 0
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    conn.close()
    return jsonify({'tablas': stats, 'tamano_mb': round(size / 1024 / 1024, 2)})

# ── #23: SOPORTE / TICKETS ──────────────────────────────────────────────
@app.route('/api/soporte/tickets')
def listar_tickets():
    conn=get_db()
    rows=rows_to_list(conn.execute("SELECT * FROM soporte_tickets ORDER BY fecha DESC LIMIT 100").fetchall())
    conn.close()
    return jsonify(rows)

@app.route('/api/soporte/tickets', methods=['POST'])
def crear_ticket():
    d=request.get_json(force=True) or {}
    conn=get_db()
    conn.execute("""INSERT INTO soporte_tickets(usuario_id,usuario_nombre,modulo,titulo,descripcion,prioridad)
        VALUES(?,?,?,?,?,?)""",
        (session.get('uid'), session.get('nombre',''), d.get('modulo',''), d.get('titulo',''),
         d.get('descripcion',''), d.get('prioridad','normal')))
    conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/soporte/tickets/<int:id>', methods=['PUT'])
def actualizar_ticket(id):
    d=request.get_json(force=True) or {}
    conn=get_db()
    campos = []
    vals = []
    for k in ('estado','prioridad','resuelto_usuario'):
        if k in d:
            campos.append(f"{k}=?"); vals.append(d[k])
    if d.get('estado') == 'resuelto':
        campos.append("resuelto_fecha=datetime('now','localtime')")
    vals.append(id)
    if campos:
        conn.execute(f"UPDATE soporte_tickets SET {','.join(campos)} WHERE id=?", vals)
        conn.commit()
    conn.close()
    return jsonify({'ok':True})

# ── GPS PLAYBACK (#18) ──────────────────────────────────────────────────
@app.route('/api/gps/playback')
def gps_playback():
    unidad = request.args.get('unidad')
    fecha = request.args.get('fecha', date.today().isoformat())
    id_viaje = request.args.get('id_viaje')
    conn=get_db()
    if id_viaje:
        rows=rows_to_list(conn.execute("SELECT lat,lng,velocidad,fecha FROM gps_historial WHERE id_viaje=? ORDER BY fecha", (id_viaje,)).fetchall())
    elif unidad:
        rows=rows_to_list(conn.execute("SELECT lat,lng,velocidad,fecha FROM gps_historial WHERE numero_unidad=? AND date(fecha)=? ORDER BY fecha",
                                        (unidad, fecha)).fetchall())
    else:
        rows = []
    conn.close()
    return jsonify(rows)

# ── RUTAS DE DESCARGA DE APP NATIVA Y PWA DE CONDUCTORES ─────────────────────
@app.route('/descargar-apk')
def descargar_apk():
    """Entrega el archivo APK nativo de la app para Android."""
    # Buscar primero en static/
    apk_path = os.path.join(STATIC_DIR, 'CTO_Conductor.apk')
    if os.path.exists(apk_path):
        return send_from_directory(STATIC_DIR, 'CTO_Conductor.apk', as_attachment=True)
    
    # Buscar en la raíz del proyecto
    alt_path = os.path.join(os.path.dirname(BASE_DIR), 'CTO_Conductor.apk')
    if os.path.exists(alt_path):
        return send_from_directory(os.path.dirname(alt_path), 'CTO_Conductor.apk', as_attachment=True)
        
    return "El binario APK nativo está en proceso de construcción. Mientras tanto, utilice la versión Web PWA en /conductor", 404

@app.route('/descargar-app')
def pagina_descargar_app():
    """Servir la página de ayuda e instalación con QR para conductores."""
    return send_from_directory(STATIC_DIR, 'install_conductores.html')


# ── BACKUP AUTOMÁTICO ───────────────────────────────────────────────────
def _ejecutar_backup_auto():
    """Ejecuta UNA copia de seguridad programada (snapshot consistente con WAL)."""
    try:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        dest = os.path.join(BACKUP_DIR, f'auto_{ts}.db')
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        src.backup(dst)   # snapshot consistente (incluye WAL)
        dst.close(); src.close()
        bks = sorted(f for f in os.listdir(BACKUP_DIR)
                     if f.startswith('auto_') and f.endswith('.db'))
        for old in bks[:-30]:
            try: os.remove(os.path.join(BACKUP_DIR, old))
            except Exception as e: logger.warning(f"Old backup remove failed: {e}")
        print(f"[CTO] Backup automatico -> {dest}")
        try: log_action('BACKUP_AUTO', dest, 'sistema')
        except Exception as e: logger.error(f"Backup log failed: {e}")
        return dest
    except Exception as e:
        print(f"[CTO] Error en backup automatico: {e}")
        return None

def _backup_automatico():
    """Backup programado: snapshot consistente de la BD cada N horas.
    Intervalo configurable con la variable de entorno CTO_BACKUP_HORAS (horas)."""
    intervalo = max(0.5, float(os.environ.get('CTO_BACKUP_HORAS', '6')))

    def ciclo():
        _ejecutar_backup_auto()
        threading.Timer(intervalo * 3600, ciclo).start()

    threading.Timer(60, ciclo).start()   # primera copia 1 min tras el arranque
    print(f"[CTO] Backup automatico activo - cada {intervalo} horas -> {BACKUP_DIR}")


if __name__=='__main__':
    init_db()
    _recuperar_qap_dia_anterior()
    _backup_automatico()
    threading.Thread(target=_cierre_automatico_medianoche, daemon=True).start()
    threading.Thread(target=_cleanup_rate_limits, daemon=True).start()
    port = int(os.environ.get('PORT', 5199))
    print("="*55)
    print("  CTO CallCenter v11.0 — Web Cloud Ready")
    print(f"  BD: {DB_PATH}")
    print(f"  Puerto: {port}")
    print("="*55)
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
