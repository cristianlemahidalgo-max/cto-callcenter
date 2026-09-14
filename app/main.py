import sys, os, time, threading, webbrowser, subprocess

if getattr(sys,'frozen',False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)
URL = 'http://127.0.0.1:5199'

def get_local_ip():
    """IP de esta máquina en la red local, para compartir con conductores/otras operadoras."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return '127.0.0.1'

def start_server():
    from server import app, init_db, _cierre_automatico_medianoche, _backup_automatico, _recuperar_qap_dia_anterior
    print("[CTO] Creando base de datos...")
    init_db()
    _recuperar_qap_dia_anterior()
    ip = get_local_ip()
    use_https = os.environ.get('CTO_HTTPS', '0') == '1'
    if use_https:
        import ssl
        DATA_DIR = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos')
        KEY = os.path.join(DATA_DIR, 'cto_key.pem')
        CERT = os.path.join(DATA_DIR, 'cto_cert.pem')
        if not os.path.exists(KEY) or not os.path.exists(CERT):
            print("[CTO] ERROR: Certificados SSL no encontrados. Ejecute: python generar_certificado.py")
            use_https = False
        else:
            global URL
            URL = 'https://127.0.0.1:5199'
            print("[CTO] HTTPS ACTIVADO")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT, KEY)
            app.config['SESSION_COOKIE_SECURE'] = True
    print("[CTO] Servidor listo.")
    print("[CTO] En esta PC:        " + URL)
    print("[CTO] Otras PC / celular: " + ("https" if use_https else "http") + "://" + ip + ":5199")
    print("[CTO]   App conductor:    " + ("https" if use_https else "http") + "://" + ip + ":5199/conductor")
    print("[CTO]   Panel unidades:   " + ("https" if use_https else "http") + "://" + ip + ":5199/admin/unidades")
    # Hilo de cierre automático a medianoche (QRT todos los días)
    import threading
    t_cierre = threading.Thread(target=_cierre_automatico_medianoche, daemon=True)
    t_cierre.start()
    # Backup automático cada 6 horas
    _backup_automatico()
    print("[CTO] Backup automatico activo (cada 6h)")
    if use_https:
        app.run(host='0.0.0.0', port=5199, debug=False,
                use_reloader=False, threaded=True, ssl_context=(CERT, KEY))
    else:
        app.run(host='0.0.0.0', port=5199, debug=False,
                use_reloader=False, threaded=True)

def wait_for_server(timeout=20):
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(URL + '/api/info', timeout=1)
            return True
        except:
            time.sleep(0.4)
    return False

def open_window():
    # Rutas de Chrome y Edge en Windows
    chrome_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe'),
    ]
    profile_dir = os.path.join(BASE_DIR, 'chrome_profile')
    for exe in chrome_paths:
        if os.path.exists(exe):
            print("[CTO] Abriendo en: " + exe)
            subprocess.Popen([
                exe,
                '--app=' + URL,
                '--window-size=1440,870',
                '--window-position=40,30',
                '--user-data-dir=' + profile_dir,
                '--no-first-run',
                '--disable-extensions',
            ])
            return
    # Fallback: navegador predeterminado
    print("[CTO] Chrome/Edge no encontrado. Abriendo en navegador predeterminado...")
    webbrowser.open(URL)

def main():
    print("[CTO] Iniciando servidor...")
    srv = threading.Thread(target=start_server, daemon=True)
    srv.start()

    if not wait_for_server():
        print("[CTO] ERROR: el servidor no respondio a tiempo.")
        input("Presiona Enter para salir...")
        sys.exit(1)

    print("[CTO] Abriendo ventana del sistema...")
    open_window()

    print("[CTO] Sistema corriendo. Cierra esta ventana para detener.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[CTO] Sistema detenido.")

if __name__ == '__main__':
    main()
