#!/bin/sh
echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null
echo 0 > /proc/sys/net/ipv4/conf/all/rp_filter 2>/dev/null
echo 0 > /proc/sys/net/ipv4/conf/default/rp_filter 2>/dev/null

(sleep 5
 ip route add 0.0.0.0/1 dev utun0 2>/dev/null
 ip route add 128.0.0.0/1 dev utun0 2>/dev/null
 ip route add 192.168.0.0/16 via 192.168.254.1 dev MIHOMO 2>/dev/null
 ip route add 10.0.0.0/8 via 192.168.254.1 dev MIHOMO 2>/dev/null
 ip route add 172.16.0.0/12 via 192.168.254.1 dev MIHOMO 2>/dev/null
 echo 0 > /proc/sys/net/ipv4/conf/utun0/rp_filter 2>/dev/null
 echo 0 > /proc/sys/net/ipv4/conf/MIHOMO/rp_filter 2>/dev/null
) &

exec /mihomo -d /etc/mihomo
