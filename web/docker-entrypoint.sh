#!/bin/sh
set -e

# Validate presence of local TLS certificate and private key in /etc/nginx/ssl (mounted read-only)
if [ ! -s /etc/nginx/ssl/localhost.crt ] || [ ! -s /etc/nginx/ssl/localhost.key ]; then
    echo "================================================================================" >&2
    echo "ERROR: Local TLS certificate or private key missing in /etc/nginx/ssl." >&2
    echo "JackVerse Caseworker HTTPS requires a locally trusted certificate with SANs." >&2
    echo "" >&2
    echo "Please generate the certificate on the host machine before starting the service:" >&2
    echo "    ./scripts/setup_local_tls.sh" >&2
    echo "" >&2
    echo "This writes certs/localhost.crt and certs/localhost.key (mounted read-only)." >&2
    echo "================================================================================" >&2
    exit 1
fi

exec "$@"
