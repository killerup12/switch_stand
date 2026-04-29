#!/usr/bin/env bash
set -euo pipefail

ROUTER="admin@192.168.88.1"
REMOTE_ETC="usb1/docker/vpn-ui/etc"
LOCAL_ETC="$(cd "$(dirname "$0")/etc" && pwd)"
CONTAINER_COMMENT="mihomo-vpn-ui"
API_URL="http://vpn.lan:8080/api/state"

echo "==> Загружаем файлы на роутер..."
sftp "$ROUTER" <<EOF
put "${LOCAL_ETC}/app.py"           ${REMOTE_ETC}/app.py
put "${LOCAL_ETC}/start.sh"         ${REMOTE_ETC}/start.sh
put "${LOCAL_ETC}/dnstest.py"       ${REMOTE_ETC}/dnstest.py
put "${LOCAL_ETC}/nettest.py"       ${REMOTE_ETC}/nettest.py
put "${LOCAL_ETC}/static/app.js"    ${REMOTE_ETC}/static/app.js
put "${LOCAL_ETC}/static/index.html" ${REMOTE_ETC}/static/index.html
put "${LOCAL_ETC}/static/style.css" ${REMOTE_ETC}/static/style.css
exit
EOF

echo "==> Перезапускаем контейнер..."
ssh "$ROUTER" "/container/stop [find comment=\"${CONTAINER_COMMENT}\"]"
sleep 4
ssh "$ROUTER" "/container/start [find comment=\"${CONTAINER_COMMENT}\"]"

echo "==> Ждём запуска (до 60 сек)..."
for i in $(seq 1 12); do
  sleep 5
  if curl -sf "$API_URL" > /dev/null 2>&1; then
    echo "==> Готово. Сервис отвечает: $API_URL"
    exit 0
  fi
  echo "    ожидание... ($((i * 5))s)"
done

echo "ОШИБКА: сервис не ответил за 60 секунд. Проверь логи:"
echo "  ssh $ROUTER '/log/print where topics~\"container\"'"
exit 1
