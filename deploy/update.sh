#!/bin/bash
# Обновить код и перезапустить демон
set -e

cd /opt/dcurl/repo
git pull origin main

/opt/dcurl/venv/bin/pip install -r requirements.txt -q

systemctl restart dcurl
echo "Обновлён и перезапущен."
