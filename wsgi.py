import os, sys, threading

# Asegurar que la carpeta 'app' esté en sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'app')
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app.server import app, init_db, _recuperar_qap_dia_anterior, _backup_automatico, _cierre_automatico_medianoche, _cleanup_rate_limits

# Inicialización al arrancar el proceso WSGI (Gunicorn / Waitress)
print("[CTO Cloud Web] Inicializando base de datos y tareas en segundo plano...")
init_db()
try:
    _recuperar_qap_dia_anterior()
except Exception as e:
    print(f"[CTO Cloud Web] Advertencia al recuperar QAP: {e}")

try:
    _backup_automatico()
except Exception as e:
    print(f"[CTO Cloud Web] Advertencia al iniciar backup: {e}")

# Hilos en segundo plano
threading.Thread(target=_cierre_automatico_medianoche, daemon=True).start()
threading.Thread(target=_cleanup_rate_limits, daemon=True).start()

print("[CTO Cloud Web] Aplicación lista para recibir tráfico HTTP/HTTPS.")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5199))
    app.run(host='0.0.0.0', port=port)
