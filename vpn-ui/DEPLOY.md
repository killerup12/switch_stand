# Деплой vpn-ui

> **Находки от 2026-04-27**: SCP upload (Mac→Router) не работает в данной версии RouterOS.
> **SFTP работает** — `deploy.sh` использует его для обновлений кода.
> veth, bridge ports и mounts **переживают** роллбек RouterOS-бэкапа — проверь `print` перед созданием.

---

## Обновление кода (контейнер уже запущен)

```bash
cd ~/Developer/switch_stand/vpn-ui
bash deploy.sh   # SFTP-загрузка + перезапуск контейнера
```

Шаги ниже — только для **первичной установки**.

---

## Шаг 1 — Скопировать файлы на роутер (первичная установка)

SFTP работает только после авторизации SSH-ключа контейнера (Шаг 2).
При самом первом деплое файлы передавать через SMB:

```bash
# Примонтировать SMB-шару роутера (macOS)
open smb://192.168.88.1/usb1

# Или через Finder: Go → Connect to Server → smb://192.168.88.1/usb1
# Скопировать vpn-ui/etc/ → usb1/docker/vpn-ui/etc/
```

Либо записать текстовые файлы через RouterOS `/file/set`:

```bash
# Пример записи файла через SSH
ssh admin@192.168.88.1 '/file/set [find name="usb1/docker/vpn-ui/etc/app.py"] contents="..."'
# Для бинарных файлов (id_ed25519) — только через SMB
```

## Шаг 2 — SSH-ключ для контейнера

### Первичная генерация (новая установка)

```bash
# Генерим ключ
ssh-keygen -t ed25519 -N "" -C "vpn-ui-container" -f /tmp/vpn-ui-key

# Приватный ключ — через SMB на /usb1/docker/vpn-ui/etc/id_ed25519

# Публичный ключ — записать через RouterOS file/add, потом импортировать
PUBKEY=$(cat /tmp/vpn-ui-key.pub)
ssh admin@192.168.88.1 "/file/add name=\"usb1/docker/vpn-ui/etc/vpn-ui-key.pub\" contents=\"${PUBKEY}\""
ssh admin@192.168.88.1 '/user/ssh-keys/import public-key-file=usb1/docker/vpn-ui/etc/vpn-ui-key.pub user=admin'

rm /tmp/vpn-ui-key /tmp/vpn-ui-key.pub
```

### Восстановление ключа (после роллбека — ключ на USB жив, публичный не авторизован)

```bash
# Достать приватный ключ с роутера
scp admin@192.168.88.1:/usb1/docker/vpn-ui/etc/id_ed25519 /tmp/vpn-ui-key-recover
chmod 600 /tmp/vpn-ui-key-recover

# Восстановить публичный ключ
ssh-keygen -y -f /tmp/vpn-ui-key-recover > /tmp/vpn-ui-key-recover.pub

# Если vpn-ui-key.pub уже есть на USB — сразу импортировать:
ssh admin@192.168.88.1 '/user/ssh-keys/import public-key-file=usb1/docker/vpn-ui/etc/vpn-ui-key.pub user=admin'
# Если файла нет — создать через /file/add (см. выше), потом импортировать

rm /tmp/vpn-ui-key-recover /tmp/vpn-ui-key-recover.pub
```

## Шаг 3 — Mihomo (если не поднят)

Перед тем как ставить vpn-ui, mihomo должен быть запущен.
Проверить: `curl http://192.168.254.4:9090/version`

Если mihomo отсутствует — поднять:

```routeros
# Проверить что уже существует перед созданием!
# veth MIHOMO и bridge port/mounts могут пережить роллбек.

/interface/veth/print where name=MIHOMO
/interface/bridge/port/print where interface=MIHOMO
/container/mounts/print where list=mihomo_mounts

# Создавать только то, чего нет:
/interface/veth/add name=MIHOMO address=192.168.254.4/24 gateway=192.168.254.1 comment="mihomo-vpn"
/interface/bridge/port/add interface=MIHOMO bridge=Docker comment="mihomo-vpn"
/container/mounts/add list=mihomo_mounts src=/usb1/docker/mihomo/etc dst=/etc/mihomo mode=rw

# Образ: metacubex/mihomo:latest (Docker Hub).
# ВАЖНО: entrypoint=/bin/sh cmd=/etc/mihomo/init.sh
# init.sh должен запускать: exec /mihomo -d /etc/mihomo
# (без -d mihomo стартует без конфига и не слушает порты)
/container/add \
    remote-image=metacubex/mihomo:latest \
    interface=MIHOMO \
    root-dir=/usb1/docker/mihomo-root \
    mountlists=mihomo_mounts \
    entrypoint=/bin/sh \
    cmd=/etc/mihomo/init.sh \
    dns=192.168.254.1 \
    start-on-boot=yes \
    logging=yes \
    comment="mihomo-vpn"

# Routing rules
/routing/table/add disabled=no fib name=to_vpn comment="mihomo-vpn"
/ip/route/add routing-table=to_vpn gateway=192.168.254.4 check-gateway=ping comment="mihomo-vpn"
/ip/firewall/mangle/add chain=prerouting action=mark-routing \
    new-routing-mark=to_vpn passthrough=no \
    dst-address-list=vpn-route in-interface-list=LAN \
    place-before=0 comment="mihomo-vpn"

/container/start [find comment="mihomo-vpn"]
```

Дождаться `curl http://192.168.254.4:9090/version` → `{"meta":true,...}`.

## Шаг 4 — vpn-ui RouterOS-команды

```routeros
# Проверить что уже существует перед созданием:
/interface/veth/print where name=VPNUI
/interface/bridge/port/print where interface=VPNUI
/container/mounts/print where list=vpnui_mounts

# 1. veth-интерфейс (только если нет)
/interface/veth/add name=VPNUI address=192.168.254.5/24 gateway=192.168.254.1 comment="mihomo-vpn-ui"

# 2. Маунты (list= обязателен; comment= не поддерживается для mounts)
/container/mounts/add list=vpnui_mounts src=/usb1/docker/vpn-ui/etc dst=/app mode=rw
/container/mounts/add list=vpnui_mounts src=/usb1/docker/mihomo/etc dst=/mihomo-cfg mode=rw

# 3. Контейнер (mountlists=, не mounts=; workdir НЕ указывать — FAT-маунт)
# Если root-dir уже существует с другим контейнером — удалить через WebFig Files
/container/add \
    remote-image=python:3-alpine \
    interface=VPNUI \
    root-dir=/usb1/vpn-ui-root \
    mountlists=vpnui_mounts \
    entrypoint=/bin/sh \
    cmd=/app/start.sh \
    dns=192.168.254.1 \
    start-on-boot=yes \
    logging=yes \
    comment="mihomo-vpn-ui"

# 4. Добавить VPNUI в Docker bridge (обязательно — иначе LAN не видит контейнер)
/interface/bridge/port/add interface=VPNUI bridge=Docker comment="mihomo-vpn-ui"

# 5. Firewall: SSH от Docker + блок WAN→8080
# place-before=N: найди номер правила "drop all not from LAN" через:
#   /ip/firewall/filter/print where chain=input action=drop
# Поставь SSH-правило перед ним.
/ip/firewall/filter/add chain=input action=accept protocol=tcp \
    in-interface=Docker dst-port=22 place-before=7 \
    comment="mihomo-vpn-ui-ssh-from-docker"
# После добавления ПРОВЕРЬ порядок:
#   /ip/firewall/filter/print where chain=input
# SSH accept должен стоять ПЕРЕД drop-правилом.
# Если нет — переместить: /ip/firewall/filter/move [find comment="mihomo-vpn-ui-ssh-from-docker"] destination=<номер_drop>

/ip/firewall/filter/add chain=input action=drop \
    in-interface-list=WAN dst-port=8080 protocol=tcp \
    comment="mihomo-vpn-ui-no-wan"
```

## Шаг 5 — PiHole DNS

Записи уже есть в `/usb1/docker/pihole/etc/pihole.toml`:
```
"192.168.254.4 clash.lan"
"192.168.254.5 switch_stand.lan"
```
Если нет — добавить вручную в массив `hosts` и перезапустить PiHole.

## Шаг 6 — Запустить и проверить

```routeros
/container/start [find comment="mihomo-vpn-ui"]
```

Подождать ~60 секунд (apk + pip при первом старте).

```bash
# Проверить API
curl http://switch_stand.lan:8080/api/state
# или
curl http://192.168.254.5:8080/api/state
```

Ожидаем `200 OK` с JSON `{draft, live, dirty, mihomo_status}`.

Если `draft.json` существует (после роллбека данные живы):
- `dirty: true` — address-list пуст, нажать Apply в UI для восстановления правил
- `dirty: false` — всё синхронизировано

## Откат (полный снос vpn-ui)

```routeros
/container/stop [find comment="mihomo-vpn-ui"]
/container/remove [find comment="mihomo-vpn-ui"]
/interface/veth/remove [find name=VPNUI]
/ip/firewall/filter/remove [find comment~"mihomo-vpn-ui"]
# mounts не имеют comment= — удалить вручную:
/container/mounts/remove [find list=vpnui_mounts]
```

Файлы в WebFig Files: удалить `/usb1/docker/vpn-ui/` и `/usb1/vpn-ui-root/`.

## Откат (полный снос mihomo + routing)

```routeros
/container/stop [find comment="mihomo-vpn"]
/container/remove [find comment="mihomo-vpn"]
/interface/veth/remove [find name=MIHOMO]
/interface/bridge/port/remove [find comment="mihomo-vpn"]
/ip/firewall/mangle/remove [find comment="mihomo-vpn"]
/ip/route/remove [find comment="mihomo-vpn"]
/routing/table/remove [find comment="mihomo-vpn"]
/container/mounts/remove [find list=mihomo_mounts]
```

Файлы: удалить `/usb1/docker/mihomo/` и `/usb1/docker/mihomo-root/` через WebFig Files.
