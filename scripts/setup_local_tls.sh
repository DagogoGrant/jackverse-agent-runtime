#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# JackVerse Local TLS Certificate Setup (Phase 5.3.3.1)
# ==============================================================================
# Generates browser-trusted local TLS certificates with proper Subject Alternative
# Names (SAN) for localhost, 127.0.0.1, and ::1 using mkcert.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CERTS_DIR="${REPO_ROOT}/certs"

mkdir -p "${CERTS_DIR}"

if ! command -v mkcert >/dev/null 2>&1; then
    echo "ERROR: 'mkcert' is not installed."
    echo "Install it via Homebrew: brew install mkcert"
    exit 1
fi

echo "==> Generating local TLS certificates for localhost, 127.0.0.1, and ::1..."
mkcert \
    -key-file "${CERTS_DIR}/localhost.key" \
    -cert-file "${CERTS_DIR}/localhost.crt" \
    localhost 127.0.0.1 ::1

echo ""
echo "==> TLS certificates successfully written to:"
echo "    Certificate: ${CERTS_DIR}/localhost.crt"
echo "    Private Key: ${CERTS_DIR}/localhost.key"
echo ""
echo "==> Browser Trust Instructions:"
echo "    To ensure your browser (Chrome/Safari/Firefox) trusts https://localhost:3443,"
echo "    run the following command in your terminal (prompts for local admin authorization once):"
echo "        mkcert -install"
echo ""
echo "    These certificates are for local development only and are ignored by Git."
