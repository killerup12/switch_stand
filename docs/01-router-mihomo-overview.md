---
name: MikroTik router with mihomo selective VPN
description: Mihomo container на MikroTik hAP ax3 для selective routing через Hysteria2 VPN — где что лежит, как зайти, ключевые особенности
type: reference
originSessionId: f010a139-0c8b-44c4-b781-27b73a3df0c8
---
Роутер MikroTik hAP ax3, IP `192.168.88.1`, SSH `admin@192.168.88.1` (без пароля, по ключу).

## Контейнеры на роутере

| Контейнер | IP            | Роль                              |
|-----------|---------------|-----------------------------------|
| b4:latest | 192.168.254.2 | DPI-обход (НЕ ТРОГАТЬ)            |
| PiHole    | 192.168.254.3 | DNS/блокировка (НЕ ТРОГАТЬ)       |
| mihomo    | 192.168.254.4 | VPN-клиент Hysteria2 + web UI     |

## Mihomo: где что лежит

- **Конфиг**: `/usb1/docker/mihomo/etc/config.yaml` (на роутере)
- **Init wrapper**: `/usb1/docker/mihomo/etc/init.sh` — критичный, см. ниже
- **Web UI (MetaCubeXD)**: `http://clash.lan:9090/ui` (без пароля, LAN-only)
- **Mixed proxy** (HTTP+SOCKS): `clash.lan:7890`
- **Локальная копия конфига на Mac**: `/tmp/mihomo-config.yaml`, `/tmp/mihomo-init.sh`

## Архитектура selective routing

1. На роутере address-list `vpn-route` содержит IP доменов, идущих через VPN
2. Mangle prerouting (position 0): `dst-address-list=vpn-route in-interface-list=LAN` → mark routing-mark `to_vpn`, `passthrough=no`
3. `/ip route` table=to_vpn → gateway=192.168.254.4 (mihomo) `check-gateway=ping` (fail-close)
4. Внутри mihomo: правила `DOMAIN-SUFFIX,...,VPN` + `MATCH,DIRECT`. Sniffer включён — определяет домен из SNI/Host header
5. Mihomo с правилом VPN отправляет в Hysteria2 outbound; DIRECT — обратно в общий канал (фактически через b4)

## Бэкап и rollback

- На роутере: `/file before-mihomo-20260425.backup`
- На Mac: `~/Documents/mikrotik-backups/before-mihomo-20260425-212909.rsc`
- Инструкция rollback: `~/Desktop/RECOVERY-mihomo.txt` (4 сценария)
- Все mihomo-изменения помечены comment-ом начинающимся на `mihomo-vpn`
