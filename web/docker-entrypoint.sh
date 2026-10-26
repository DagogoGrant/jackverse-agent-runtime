#!/bin/sh
set -e

# If localhost.crt or localhost.key are missing in /etc/nginx/ssl, generate fallback self-signed
if [ ! -s /etc/nginx/ssl/localhost.crt ] || [ ! -s /etc/nginx/ssl/localhost.key ]; then
    echo "[nginx-entrypoint] Missing TLS certificate or key in /etc/nginx/ssl. Generating fallback self-signed cert..."
    mkdir -p /etc/nginx/ssl
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout /etc/nginx/ssl/localhost.key \
        -out /etc/nginx/ssl/localhost.crt \
        -subj "/CN=localhost" 2>/dev/null
fi

exec "$@"
