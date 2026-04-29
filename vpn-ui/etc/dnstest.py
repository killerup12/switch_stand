import os, socket

try:
    with open("/etc/resolv.conf") as f:
        print("resolv.conf:", f.read().strip())
except Exception as e:
    print("resolv.conf error:", e)

for host in ["1.1.1.1", "192.168.254.1", "192.168.254.3"]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        # Send a minimal DNS query for "google.com" A record
        query = b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01'
        s.sendto(query, (host, 53))
        data, _ = s.recvfrom(512)
        print(f"UDP DNS {host}:53 OK ({len(data)} bytes)")
        s.close()
    except Exception as e:
        print(f"UDP DNS {host}:53 FAIL - {e}")
