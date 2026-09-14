"""
CTO CallCenter — Generador de Certificado SSL Auto-Firmado
Ejecutar UNA VEZ para generar el certificado.
"""
import os, sys
from datetime import datetime, timedelta

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("[CTO] Instalando cryptography...")
    os.system(f"{sys.executable} -m pip install cryptography")
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

DATA_DIR = os.path.join(os.path.expanduser('~'), 'CTO_CallCenter_Datos')
os.makedirs(DATA_DIR, exist_ok=True)

KEY_PATH = os.path.join(DATA_DIR, 'cto_key.pem')
CERT_PATH = os.path.join(DATA_DIR, 'cto_cert.pem')

if os.path.exists(KEY_PATH) and os.path.exists(CERT_PATH):
    print(f"[CTO] Certificados ya existen en {DATA_DIR}")
    print(f"  Key:  {KEY_PATH}")
    print(f"  Cert: {CERT_PATH}")
    print("  Para regenerar, elimina estos archivos y vuelve a ejecutar.")
    sys.exit(0)

print("[CTO] Generando certificado SSL auto-firmado...")

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "EC"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Pichincha"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Quito"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cooperativa Occidental 119"),
    x509.NameAttribute(NameOID.COMMON_NAME, "CTO CallCenter"),
])

cert = (x509.CertificateBuilder()
    .subject_name(subject).issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.utcnow())
    .not_valid_after(datetime.utcnow() + timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(__import__('ipaddress').IPv4Address("127.0.0.1")),
    ]), critical=False)
    .sign(key, hashes.SHA256()))

with open(KEY_PATH, 'wb') as f:
    f.write(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))

with open(CERT_PATH, 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print(f"[CTO] Certificados generados:")
print(f"  Key:  {KEY_PATH}")
print(f"  Cert: {CERT_PATH}")
print(f"  Vigencia: 10 años")
print(f"\nPara usar HTTPS, reinicie el servidor con:")
print(f"  set CTO_HTTPS=1")
print(f"  python app\\main.py")
