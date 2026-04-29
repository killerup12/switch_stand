# mikrotik-vpn

Selective VPN на MikroTik hAP ax3: контейнер mihomo гонит выбранный трафик через Hysteria2 на VPS, а vpn-ui — веб-морда для управления списком доменов/IP, идущих через VPN.

## Топология

```
LAN client
   │
   ▼
MikroTik (192.168.88.1)
   ├─ mangle prerouting: dst-address-list=vpn-route → routing-mark=to_vpn
   ├─ /ip route table=to_vpn  →  gw=192.168.254.4 (mihomo)  check-gateway=ping
   │
   └─ Docker bridge (192.168.254.0/24)
        ├─ b4:latest      192.168.254.2   DPI-обход (НЕ ТРОГАТЬ)
        ├─ PiHole         192.168.254.3   DNS / блокировка
        ├─ mihomo         192.168.254.4   VPN-клиент Hysteria2 + web UI :9090
        └─ vpn-ui         192.168.254.5   Flask UI :8080
                                            └── ssh→ роутер для правки
                                                /ip firewall address-list vpn-route
```

Внутри mihomo: `sniffer.enable: true` определяет домен из SNI/Host, дальше правила `DOMAIN-SUFFIX,...,VPN` или `MATCH,DIRECT`. VPN-исходящий — Hysteria2 на VPS; DIRECT — назад в общий канал (фактически через b4).

## Структура репозитория

```
mikrotik-vpn/
├── README.md                              ← этот файл
├── docs/
│   ├── 01-router-mihomo-overview.md       контейнеры, IP, web UI, бэкапы
│   ├── 02-mihomo-tun-fix.md               почему нужен init.sh (TUN auto-route не работает)
│   ├── 03-vpnui-deployment.md             RouterOS quirks, найденные при деплое vpn-ui
│   └── 04-RECOVERY.md                     4 сценария rollback mihomo
├── mihomo/
│   ├── config-initial.yaml                первый рабочий конфиг (2026-04-26)
│   ├── config-current.yaml                актуальный конфиг (2026-04-27)
│   ├── init.sh                            entrypoint-обёртка вокруг /mihomo
│   └── dnsmasq-mihomo.conf                запись для PiHole: mihomo.lan → 192.168.254.4
├── vpn-ui/
│   ├── DEPLOY.md                          инструкция по деплою контейнера
│   └── etc/                               код приложения
│       ├── app.py                         Flask backend
│       ├── start.sh                       entrypoint (apk add, chmod ключа, run)
│       ├── nettest.py / dnstest.py        диагностика
│       └── static/                        index.html, app.js, style.css
└── backups/
    ├── before-mihomo-20260425-212909.rsc  конфиг роутера до внедрения mihomo
    ├── backup-20260426-205334.backup      бинарный бэкап RouterOS (с vpn-ui)
    └── config-20260426-205334.rsc         текстовый export того же состояния
```

## Где это всё живёт на роутере

| Артефакт                    | Путь на MikroTik                              |
|-----------------------------|-----------------------------------------------|
| mihomo конфиг               | `/usb1/docker/mihomo/etc/config.yaml`         |
| mihomo init wrapper         | `/usb1/docker/mihomo/etc/init.sh`             |
| vpn-ui код                  | `/usb1/docker/vpn-ui/etc/`                    |
| SSH-ключ vpn-ui→router      | `/usb1/docker/vpn-ui/etc/id_ed25519` (FAT, копируется в /tmp+chmod 600 при старте) |
| Бэкап до mihomo             | `/file before-mihomo-20260425.backup`         |

## Точки доступа

- mihomo web UI (MetaCubeXD): `http://clash.lan:9090/ui`
- mihomo mixed proxy (HTTP+SOCKS): `clash.lan:7890`
- vpn-ui: `http://vpn.lan:8080`
- SSH на роутер: `ssh admin@192.168.88.1` (по ключу, без пароля)

## Главные грабли (см. docs/)

1. **TUN auto-route в RouterOS-контейнере не настраивает catch-all маршруты** — нужен `init.sh`, который вручную добавляет `0.0.0.0/1` и `128.0.0.0/1` через `utun0`, ставит `ip_forward=1`, `rp_filter=0` и исключает приватные подсети. Без этого петля.
2. **mihomo без `-d /etc/mihomo`** стартует с пустым конфигом (default-entrypoint в образе `metacubex/mihomo:latest` не подхватывает `/etc/mihomo`).
3. **`sniffer.enable: true` обязателен** — без него `DOMAIN-SUFFIX` правила не матчатся.
4. **SCP upload в этой версии RouterOS не работает** — заливать файлы через `/file/set ... contents=` или `/file/add`.
5. **`/container/add` использует `mountlists=`**, а `/container/mounts/add` — `list=`. Никаких `comment=` для маунтов, никаких `workdir=` для FAT-USB.

## Rollback

См. `docs/04-RECOVERY.md` — 4 сценария от «откатить только mihomo-правила» до «полный restore из бэкапа».
