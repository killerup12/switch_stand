#!/usr/bin/env bash
set -euo pipefail

ROUTER="admin@192.168.88.1"
CONTAINER_COMMENT="mihomo-vpn-ui"

echo "ВНИМАНИЕ: Это полностью удалит vpn-ui с роутера."
echo "  - Контейнер и его root-dir (/usb1/vpn-ui-root)"
echo "  - veth-интерфейс VPNUI"
echo "  - Firewall-правила vpn-ui"
echo "  - Container mounts (vpnui_mounts)"
echo ""
read -r -p "Продолжить? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Отменено."; exit 0; }

echo "==> Останавливаем контейнер..."
ssh "$ROUTER" "/container/stop [find comment=\"${CONTAINER_COMMENT}\"]" || true

sleep 3

echo "==> Удаляем контейнер..."
ssh "$ROUTER" "/container/remove [find comment=\"${CONTAINER_COMMENT}\"]" || true

echo "==> Удаляем veth и bridge port..."
ssh "$ROUTER" "/interface/bridge/port/remove [find comment=\"${CONTAINER_COMMENT}\"]" || true
ssh "$ROUTER" "/interface/veth/remove [find name=VPNUI]" || true

echo "==> Удаляем firewall-правила..."
ssh "$ROUTER" "/ip/firewall/filter/remove [find comment~\"${CONTAINER_COMMENT}\"]" || true

echo "==> Удаляем container mounts..."
ssh "$ROUTER" "/container/mounts/remove [find list=vpnui_mounts]" || true

echo ""
echo "==> RouterOS-часть удалена."
echo ""
echo "Осталось вручную (через WebFig → Files или Winbox):"
echo "  /usb1/docker/vpn-ui/   — исходники и draft.json"
echo "  /usb1/vpn-ui-root/     — root-dir контейнера"
