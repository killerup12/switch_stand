---
name: Mihomo TUN на MikroTik требует init-обёртку
description: Неочевидный фикс — auto-route mihomo'ы внутри MikroTik-контейнера не настраивает маршруты в kernel netns; нужен скрипт-wrapper вокруг /mihomo
type: project
originSessionId: f010a139-0c8b-44c4-b781-27b73a3df0c8
---
В MikroTik-контейнерах (RouterOS 7.x) `auto-route: true` у mihomo НЕ работает корректно: добавляется только маршрут на саму TUN-подсеть (`28.0.0.0/30 dev utun0`), но не catch-all `0.0.0.0/1` и `128.0.0.0/1`. Без них пакеты, маршрутизированные роутером в контейнер, не попадают в utun0 — kernel отправляет их обратно через default gateway (router) → петля.

**Why:** MikroTik-контейнеры не дают полный набор capabilities или sysctl-доступа, который mihomo ожидает на обычном Linux. ip_forward по умолчанию = 0; rp_filter может отбрасывать ассиметричный трафик.

**How to apply:** перед запуском mihomo в контейнере должен крутиться init.sh, который:
1. `echo 1 > /proc/sys/net/ipv4/ip_forward`
2. `echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter` (и для default, utun0, MIHOMO)
3. После старта mihomo (sleep 5) добавить:
   - `ip route add 0.0.0.0/1 dev utun0`
   - `ip route add 128.0.0.0/1 dev utun0`
4. Маршруты-исключения чтобы ответы mihomo'ы клиентам в LAN не зацикливались обратно в utun0:
   - `ip route add 192.168.0.0/16 via <docker-bridge-router-ip> dev <veth>`
   - `ip route add 10.0.0.0/8 via <docker-bridge-router-ip> dev <veth>`
   - `ip route add 172.16.0.0/12 via <docker-bridge-router-ip> dev <veth>`
5. `exec /mihomo -d /etc/mihomo`  ← обязательно! без флага mihomo стартует без конфига

Контейнер запускается с `entrypoint=/bin/sh cmd=/path/to/init.sh` (override через `/container/set ... entrypoint=`, не `default-entrypoint=` — последний RouterOS не даёт менять у созданного контейнера).

Также в mihomo config обязательно `sniffer.enable: true` — без него правила `DOMAIN-SUFFIX` не матчатся (трафик приходит как сырой IP-пакет без DNS-контекста).

Файл со скриптом: `/tmp/mihomo-init.sh` локально, `/usb1/docker/mihomo/etc/init.sh` на роутере.
