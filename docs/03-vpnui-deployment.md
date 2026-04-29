---
name: vpn-ui deployment facts
description: Deployed vpn-ui container on MikroTik; key quirks found during deployment
type: reference
originSessionId: a52eb128-5954-4e71-ad7d-de076d2abb9e
---
## vpn-ui контейнер

- IP: 192.168.254.5, порт 8080
- Доступен как http://switch_stand.lan:8080 (PiHole DNS)
- Файлы: /usb1/docker/vpn-ui/etc/ (app.py, start.sh, static/, draft.json, id_ed25519)
- Исходники на Mac: ~/Developer/switch_stand/vpn-ui/

## RouterOS quirks (найдены при деплое 2026-04-26, дополнено 2026-04-27)

- `/container/add` использует `mountlists=<name>` (не `mounts=`)
- `/container/mounts/add` использует `list=<name>` (не `name=`), `comment=` не поддерживается
- `workdir=` НЕ указывать для маунтов на FAT/USB — директория может не существовать до маунта
- `print as-value` НЕ работает для `/ip/firewall/address-list` в этой версии RouterOS
- DNS в контейнере: использовать `dns=192.168.254.1` (router), правило firewall уже разрешает UDP/53 от Docker
- Контейнер нужно добавить в Docker bridge: `/interface/bridge/port/add interface=VPNUI bridge=Docker`
- SSH из контейнера на роутер: нужно правило `chain=input accept tcp in-interface=Docker dst-port=22`
- SSH ключ на FAT (0666) — копировать в /tmp и chmod 600 при старте (start.sh делает это)
- `apk add` при старте требует sleep 3 (race condition: сеть поднимается чуть позже)
- 8.8.8.8 недоступен из контейнера (маршрут через VPN?), 1.1.1.1 работает, 192.168.254.1 работает
- `SCP upload` (Mac→Router) не работает в этой версии RouterOS; `SCP download` работает; для записи файлов на USB используй `/file/set [find name=...] contents=` или `/file/add name=... contents=`
- SSH-ключ vpn-ui контейнера: восстановить через `scp router:/usb1/docker/vpn-ui/etc/id_ed25519 /tmp/` + `ssh-keygen -y` + `/user/ssh-keys/import`
- `metacubex/mihomo:latest` (Docker Hub) имеет `/mihomo` как default-entrypoint; **требует `-d /etc/mihomo`** для нахождения конфига; без флага стартует с пустым конфигом
- init.sh для mihomo должен использовать `exec /mihomo -d /etc/mihomo` (не просто `exec /mihomo`)
- `place-before=N` для mangle/filter: RouterOS может показывать нумерацию не последовательно — доверяй порядку в `print`, не номерам
- Для повторного деплоя после роллбека: veth MIHOMO уже существовал, bridge port MIHOMO уже был, mounts mihomo_mounts уже были — RouterOS сохраняет часть конфига при роллбеке (veth/mounts/bridge ports НЕ затрагиваются бэкапом)
- ifconfig.me должен быть в vpn-route rules (группа Test) иначе Test VPN endpoint возвращает ISP IP вместо VPS IP
