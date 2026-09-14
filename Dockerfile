FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directorio persistente para la base de datos sqlite
ENV DATA_DIR=/app/data
ENV PORT=5199
RUN mkdir -p /app/data

EXPOSE 5199

CMD ["gunicorn", "--bind", "0.0.0.0:5199", "--workers", "2", "--threads", "4", "--timeout", "120", "wsgi:app"]
