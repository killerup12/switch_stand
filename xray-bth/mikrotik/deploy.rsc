# ============================================================
# xray-bth container deploy — Back-To-Home VLESS+Reality server
# Run AFTER: scp config.json admin@192.168.88.1:/usb1/docker/xray-bth/etc/config.json
# ============================================================

# 1. veth interface
/interface veth
add name=XRAYBTH address=192.168.254.7/24 gateway=192.168.254.1 gateway6="" \
    mac-address=02:AA:BB:CC:DD:07 container-mac-address=02:AA:BB:CC:DD:08 \
    dhcp=no comment=xray-bth

# 2. NAT: forward external :2053 to container
/ip firewall nat
add chain=dstnat protocol=tcp dst-port=2053 action=dst-nat \
    to-addresses=192.168.254.7 to-ports=2053 comment=xray-bth

# 3. Firewall: allow forwarded traffic to container
/ip firewall filter
add chain=forward dst-address=192.168.254.7 action=accept comment=xray-bth

# 4. Add veth to LAN interface list (for b4/mihomo mangle rules)
/interface/list/member
add interface=XRAYBTH list=LAN comment=xray-bth

# 5. Add veth to Docker bridge
/interface/bridge/port
add interface=XRAYBTH bridge=Docker comment=xray-bth

# 6. mount
/container mounts
add list=xray_bth_etc dst=/etc/xray-bth src=/usb1/docker/xray-bth/etc

# 7. container
/container
add name=xray-bth \
    remote-image=ghcr.io/xtls/xray-core:latest \
    interface=XRAYBTH \
    root-dir=/usb1/docker/xray-bth-root \
    mountlists=xray_bth_etc \
    cmd="run -c /etc/xray-bth/config.json" \
    start-on-boot=yes \
    logging=yes \
    comment=xray-bth
