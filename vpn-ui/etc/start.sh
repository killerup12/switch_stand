#!/bin/sh
set -e
sleep 3
# Copy SSH key to tmpfs so chmod works (USB/FAT doesn't support permissions)
cp /app/id_ed25519 /tmp/id_ed25519
chmod 600 /tmp/id_ed25519
apk add --no-cache openssh
pip install --no-cache-dir requests pyyaml
exec python3 /app/app.py
