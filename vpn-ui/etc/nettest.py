import socket, sys

tests = [
    ("8.8.8.8", 53),
    ("1.1.1.1", 53),
    ("192.168.254.1", 80),
]

for host, port in tests:
    try:
        s = socket.create_connection((host, port), timeout=5)
        print(f"OK {host}:{port}")
        s.close()
    except Exception as e:
        print(f"FAIL {host}:{port} - {e}")

try:
    addr = socket.getaddrinfo("google.com", 80)
    print(f"DNS OK -> {addr[0][4][0]}")
except Exception as e:
    print(f"DNS FAIL - {e}")
