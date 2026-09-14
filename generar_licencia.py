#!/usr/bin/env python3
"""
CTO CallCenter — Generador de Licencias
========================================
Genera un archivo licencia.json firmado con HMAC-SHA256.
Sin la firma correcta, el servidor rechaza la licencia.

USO:
  python generar_licencia.py                           → Trial 30 días desde hoy
  python generar_licencia.py --expira 2026-12-15       → Licencia hasta 15/12/2026
  python generar_licencia.py --tipo definitiva --expira 2027-12-31
  python generar_licencia.py --verificar                → Verificar licencia existente
"""
import hmac, hashlib, json, os, sys, argparse
from datetime import datetime, timedelta

# ── Clave secreta embebida (debe coincidir con server.py) ─────────────────
LICENSE_KEY = b'CTO_CC_v11_LIC_2026_COOP119'

def firmar(tipo, expira, empresa):
    """Genera la firma HMAC-SHA256 para la licencia."""
    payload = f'{tipo}|{expira}|{empresa}'.encode()
    return hmac.new(LICENSE_KEY, payload, hashlib.sha256).hexdigest()

def generar(tipo='trial', dias=30, expira=None, empresa='Cooperativa Occidental 119', destino=None):
    """Genera el archivo de licencia."""
    if expira:
        fecha_exp = expira
    else:
        fecha_exp = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')
    
    firma = firmar(tipo, fecha_exp, empresa)
    
    licencia = {
        'tipo': tipo,
        'expira': fecha_exp,
        'empresa': empresa,
        'firma': firma,
        'generado': datetime.now().isoformat()
    }
    
    # Guardar en ~/CTO_CallCenter_Datos/licencia.json
    if not destino:
        DATA_DIR = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos')
        os.makedirs(DATA_DIR, exist_ok=True)
        destino = os.path.join(DATA_DIR, 'licencia.json')
    
    with open(destino, 'w', encoding='utf-8') as f:
        json.dump(licencia, f, indent=2, ensure_ascii=False)
    
    print(f'OK Licencia generada: {destino}')
    print(f'   Tipo:     {tipo}')
    print(f'   Expira:   {fecha_exp}')
    print(f'   Empresa:  {empresa}')
    print(f'   Firma:    {firma[:16]}...')
    
    return licencia

def verificar(destino=None):
    """Verifica si una licencia es válida."""
    if not destino:
        DATA_DIR = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos')
        destino = os.path.join(DATA_DIR, 'licencia.json')
    
    if not os.path.exists(destino):
        print(f'X Licencia no encontrada: {destino}')
        return False
    
    with open(destino, 'r', encoding='utf-8') as f:
        lic = json.load(f)
    
    tipo = lic.get('tipo', '')
    expira = lic.get('expira', '')
    empresa = lic.get('empresa', '')
    firma = lic.get('firma', '')
    
    firma_esperada = firmar(tipo, expira, empresa)
    
    if not hmac.compare_digest(firma, firma_esperada):
        print(f'X Firma INVALIDA - la licencia fue modificada')
        return False
    
    fecha_exp = datetime.strptime(expira, '%Y-%m-%d')
    hoy = datetime.now()
    
    if hoy > fecha_exp:
        dias = (hoy - fecha_exp).days
        print(f'! Licencia VENCIDA hace {dias} dias (expiro {expira})')
        return False
    
    dias_rest = (fecha_exp - hoy).days
    print(f'OK Licencia VALIDA - expira el {expira} ({dias_rest} dias restantes)')
    print(f'   Tipo:     {tipo}')
    print(f'   Empresa:  {empresa}')
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CTO CallCenter — Generador de Licencias')
    parser.add_argument('--tipo', default='trial', choices=['trial', 'definitiva'],
                       help='Tipo de licencia (default: trial)')
    parser.add_argument('--dias', type=int, default=30,
                       help='Días de validez (default: 30)')
    parser.add_argument('--expira', type=str, default=None,
                       help='Fecha de expiración YYYY-MM-DD (sobreescribe --dias)')
    parser.add_argument('--empresa', type=str, default='Cooperativa Occidental 119',
                       help='Nombre de la empresa')
    parser.add_argument('--verificar', action='store_true',
                       help='Verificar licencia existente')
    parser.add_argument('--destino', type=str, default=None,
                       help='Ruta del archivo licencia.json')
    
    args = parser.parse_args()
    
    if args.verificar:
        ok = verificar(args.destino)
        sys.exit(0 if ok else 1)
    else:
        generar(
            tipo=args.tipo,
            dias=args.dias,
            expira=args.expira,
            empresa=args.empresa,
            destino=args.destino
        )
