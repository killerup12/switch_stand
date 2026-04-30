#!/usr/bin/env python3
"""Switch Stand backend — MikroTik address-list + mihomo config manager."""

import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse

import requests
import yaml

DEFAULT_ROUTER_HOST = "192.168.254.1"
DEFAULT_ROUTER_USER = "admin"
DEFAULT_MIHOMO_DNS  = "192.168.254.3"
SSH_KEY = "/tmp/id_ed25519"  # copied from /app at startup with chmod 600
MIHOMO_API = "http://192.168.254.4:9090"
MIHOMO_CFG = "/mihomo-cfg/config.yaml"
DRAFT_FILE = "/app/draft.json"
SETTINGS_FILE = "/app/settings.json"
LISTEN_PORT = 8080
COMMENT_PREFIX = "mihomo-vpn"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def read_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}

def write_settings(data: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_router_host() -> str:
    return read_settings().get("router_host") or DEFAULT_ROUTER_HOST

def get_router_user() -> str:
    return read_settings().get("router_user") or DEFAULT_ROUTER_USER

def get_mihomo_dns() -> str:
    return read_settings().get("mihomo_dns") or DEFAULT_MIHOMO_DNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

def infer_type(value: str) -> str:
    if "/" in value:
        return "cidr"
    if _IP_RE.match(value):
        return "ip"
    return "domain"


def ssh_run(cmd: str, check: bool = True) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-i", SSH_KEY,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR",
            f"{get_router_user()}@{get_router_host()}",
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"SSH error (rc={result.returncode}): {result.stderr.strip()}")
    return result.stdout


def parse_ros_asvalue(text: str) -> list:
    """Parse RouterOS 'print as-value' output into list of dicts.

    Handles both single-line (key=val key=val) and multi-line (.id=*N / key=val)
    formats that RouterOS may produce.
    """
    entries = []
    current = {}

    def flush():
        if current:
            entries.append(dict(current))
            current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith(".id=") and current:
            flush()

        pos = 0
        while pos < len(line):
            # skip whitespace / semicolons between fields
            while pos < len(line) and line[pos] in " \t;":
                pos += 1
            if pos >= len(line):
                break
            eq = line.find("=", pos)
            if eq == -1:
                break
            key = line[pos:eq]
            pos = eq + 1
            # quoted value
            if pos < len(line) and line[pos] == '"':
                pos += 1
                val_start = pos
                while pos < len(line) and line[pos] != '"':
                    if line[pos] == "\\":
                        pos += 1
                    pos += 1
                val = line[val_start:pos]
                if pos < len(line):
                    pos += 1
            else:
                val_start = pos
                while pos < len(line) and line[pos] not in " \t;":
                    pos += 1
                val = line[val_start:pos]
            if key and not key.startswith("#"):
                current[key] = val

    flush()
    return entries


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------

def read_address_list() -> list:
    """Return list of {address, comment} dicts for non-dynamic vpn-route entries.

    Uses regular print (as-value is not supported for address-list in this ROS version).
    Parses the tabular output where comments appear as ';;; ...' lines before each entry.
    Skips dynamic (D-flagged) entries — those are auto-resolved IPs, not user domains.
    """
    out = ssh_run(
        f'/ip/firewall/address-list/print without-paging '
        f'where list=vpn-route comment~"{COMMENT_PREFIX}"'
    )
    entries = []
    pending_comment = ""
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Flags:") or stripped.startswith("Columns:") or stripped.startswith("#"):
            continue
        if stripped.startswith(";;;"):
            pending_comment = stripped[3:].strip()
            continue
        # Entry line: "  N   LIST   ADDRESS   DATE" or "  N D LIST   ADDRESS   DATE"
        m = re.match(r"^\s*(\d+)\s+(D\s+)?(\S+)\s+(\S+)", line)
        if m:
            is_dynamic = bool(m.group(2))
            if not is_dynamic and pending_comment:
                entries.append({"address": m.group(4), "comment": pending_comment})
            pending_comment = ""
    return entries


def address_list_to_groups(entries: list) -> list:
    groups_dict: dict = {}
    order: list = []
    for e in entries:
        parts = e["comment"].split("|")
        group = parts[1] if len(parts) == 3 else "Misc"
        typ = infer_type(e["address"])
        if group not in groups_dict:
            groups_dict[group] = []
            order.append(group)
        groups_dict[group].append({"value": e["address"], "type": typ})
    return [{"name": g, "entries": groups_dict[g]} for g in order]


def read_live() -> dict:
    entries = read_address_list()
    return {"groups": address_list_to_groups(entries)}


# ---------------------------------------------------------------------------
# config.yaml (text-based manipulation)
# ---------------------------------------------------------------------------

def read_yaml_vpn_rules(text: str) -> list:
    data = yaml.safe_load(text)
    rules = (data or {}).get("rules", [])
    return [
        r for r in rules
        if isinstance(r, str) and ",VPN" in r and not r.startswith("MATCH,")
    ]


def build_rules_for_draft(groups: list) -> list:
    rules = []
    for group in groups:
        for entry in group["entries"]:
            v, t = entry["value"], entry["type"]
            if t == "cidr":
                rules.append(f"IP-CIDR,{v},VPN,no-resolve")
            elif t == "ip":
                rules.append(f"IP-CIDR,{v}/32,VPN,no-resolve")
            else:
                rules.append(f"DOMAIN-SUFFIX,{v},VPN")
    return rules


def replace_yaml_rules(text: str, new_rules: list) -> str:
    """Replace rules section preserving everything else in the file."""
    rule_lines = "\n".join(f"  - {r}" for r in new_rules)
    rule_lines += "\n  - MATCH,DIRECT"

    new_text, n = re.subn(
        r"^rules:\s*\n([ \t]+[^\n]*\n|\n)*",
        f"rules:\n{rule_lines}\n",
        text,
        flags=re.MULTILINE,
    )
    if n == 0:
        new_text = text + f"\nrules:\n{rule_lines}\n"
    return new_text


def update_yaml_dns_servers(text: str, dns: str) -> str:
    """Replace nameserver values under default-nameserver and nameserver keys."""
    for key in ("default-nameserver", "nameserver"):
        text = re.sub(
            rf'({re.escape(key)}:\s*\n\s+-\s+)\S+',
            rf'\g<1>{dns}',
            text,
        )
    return text


# ---------------------------------------------------------------------------
# Proxy management
# ---------------------------------------------------------------------------

def read_proxies() -> list:
    if not os.path.exists(MIHOMO_CFG):
        return []
    with open(MIHOMO_CFG) as f:
        data = yaml.safe_load(f)
    return (data or {}).get("proxies", []) or []


def update_yaml_proxies_section(text: str, proxies: list) -> str:
    """Replace proxies + proxy-groups sections in config.yaml."""
    proxy_names = [p["name"] for p in proxies]

    new_proxies = yaml.dump(
        {"proxies": proxies},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    new_groups = yaml.dump(
        {"proxy-groups": [{"name": "VPN", "type": "select", "proxies": proxy_names}]},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    new_block = new_proxies.rstrip() + "\n\n" + new_groups

    new_text, n = re.subn(
        r"^proxies:.*?(?=\nrules:)",
        new_block,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if n == 0:
        raise RuntimeError("proxies section not found in config.yaml")
    return new_text


def build_proxy(data: dict) -> dict:
    """Build mihomo proxy dict from UI form data."""
    name = data.get("name", "").strip()
    ptype = data.get("type", "")
    if not name:
        raise ValueError("Имя обязательно")
    if not data.get("server", "").strip():
        raise ValueError("Сервер обязателен")
    if not str(data.get("port", "")).strip():
        raise ValueError("Порт обязателен")

    if ptype == "hysteria2":
        proxy = {
            "name": name,
            "type": "hysteria2",
            "server": data["server"].strip(),
            "port": int(data["port"]),
            "password": data.get("password", ""),
            "skip-cert-verify": bool(data.get("skip_cert_verify", True)),
            "udp": True,
        }
        if data.get("obfs_password", "").strip():
            proxy["obfs"] = "salamander"
            proxy["obfs-password"] = data["obfs_password"].strip()
        return proxy

    if ptype == "vless":
        if not data.get("uuid", "").strip():
            raise ValueError("UUID обязателен")
        if not data.get("public_key", "").strip():
            raise ValueError("Public key обязателен")
        proxy = {
            "name": name,
            "type": "vless",
            "server": data["server"].strip(),
            "port": int(data["port"]),
            "uuid": data["uuid"].strip(),
            "network": "tcp",
            "tls": True,
            "reality-opts": {
                "public-key": data["public_key"].strip(),
            },
            "servername": data.get("servername", "").strip(),
            "client-fingerprint": data.get("fingerprint", "chrome"),
        }
        if data.get("short_id", "").strip():
            proxy["reality-opts"]["short-id"] = data["short_id"].strip()
        return proxy

    raise ValueError(f"Неизвестный тип прокси: {ptype}")


def _reload_mihomo():
    try:
        requests.put(f"{MIHOMO_API}/configs?force=true", json={}, timeout=10).raise_for_status()
    except Exception as e:
        print(f"[proxy] WARNING: mihomo reload failed: {e}")


# ---------------------------------------------------------------------------
# Draft persistence
# ---------------------------------------------------------------------------

_HOST32_RE = re.compile(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/32$')

def _normalize_draft(data: dict) -> dict:
    for g in data.get("groups", []):
        normalized = []
        seen = set()
        for e in g.get("entries", []):
            # Normalize x.x.x.x/32 → x.x.x.x
            m = _HOST32_RE.match(e["value"])
            if m:
                e["value"] = m.group(1)
            e["type"] = infer_type(e["value"])
            if e["value"] not in seen:
                seen.add(e["value"])
                normalized.append(e)
        g["entries"] = normalized
    return data

def read_draft():
    if os.path.exists(DRAFT_FILE):
        with open(DRAFT_FILE) as f:
            raw = json.load(f)
        normalized = _normalize_draft(raw)
        if normalized != raw:
            write_draft(normalized)
        return normalized
    return None


def write_draft(data: dict):
    with open(DRAFT_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_all_ros_addresses() -> set:
    """All addresses in vpn-route list — static and dynamic."""
    out = ssh_run(
        '/ip/firewall/address-list/print without-paging where list=vpn-route',
        check=False,
    )
    result = set()
    for line in out.splitlines():
        m = re.match(r'^\s*\d+\s+(?:D\s+)?\S+\s+(\S+)', line)
        if m:
            result.add(m.group(1))
    return result


def is_dirty(draft: dict, live: dict, all_ros: set = None) -> bool:
    def norm(data):
        return sorted(
            [
                {
                    "name": g["name"],
                    "entries": sorted(g["entries"], key=lambda e: e["value"]),
                }
                for g in data.get("groups", [])
            ],
            key=lambda g: g["name"],
        )

    if norm(draft) == norm(live):
        return False

    # Static live matches draft — remaining diff is only dynamic entries
    if all_ros is not None:
        draft_values = {e["value"] for g in draft.get("groups", []) for e in g["entries"]}
        live_values = {e["value"] for g in live.get("groups", []) for e in g["entries"]}
        if not (draft_values - all_ros) and not (live_values - draft_values):
            return False

    return True


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_changes(draft: dict) -> dict:
    # Backup config.yaml before any modification
    if os.path.exists(MIHOMO_CFG):
        shutil.copy2(MIHOMO_CFG, MIHOMO_CFG + ".bak")
        print("[apply] config.yaml backed up")

    live_entries = read_address_list()
    live_set = {e["address"]: e for e in live_entries}

    draft_set: dict = {}
    for group in draft.get("groups", []):
        for entry in group["entries"]:
            draft_set[entry["value"]] = (group["name"], entry["type"])

    to_add = {k: v for k, v in draft_set.items() if k not in live_set}
    to_remove = [k for k in live_set if k not in draft_set]
    # Entries that exist but have stale/wrong comments need remove+re-add
    to_update = {
        k: v
        for k, v in draft_set.items()
        if k in live_set
        and live_set[k].get("comment") != f"{COMMENT_PREFIX}|{v[0]}|{v[1]}"
    }
    to_remove.extend(to_update.keys())
    to_add.update(to_update)

    added: list = []
    try:
        for addr in to_remove:
            ssh_run(
                f'/ip/firewall/address-list/remove '
                f'[find list=vpn-route address="{addr}" comment~"{COMMENT_PREFIX}"]'
            )
            print(f"[apply] removed {addr}")

        for addr, (group, typ) in to_add.items():
            comment = f"{COMMENT_PREFIX}|{group}|{typ}"
            try:
                ssh_run(
                    f'/ip/firewall/address-list/add '
                    f'list=vpn-route address="{addr}" comment="{comment}"'
                )
                added.append(addr)
                print(f"[apply] added {addr} ({group}/{typ})")
            except RuntimeError as add_err:
                # Skip if already present as a dynamic entry
                count = ssh_run(
                    f'/ip/firewall/address-list/print count-only where list=vpn-route address="{addr}"',
                    check=False,
                ).strip()
                if count and count != "0":
                    print(f"[apply] skipped {addr} — already in address-list (dynamic)")
                else:
                    raise add_err

    except Exception as e:
        for addr in added:
            try:
                ssh_run(
                    f'/ip/firewall/address-list/remove '
                    f'[find list=vpn-route address="{addr}"]',
                    check=False,
                )
            except Exception:
                pass
        raise RuntimeError(f"address-list update failed: {e}") from e

    # Rewrite config.yaml (text-based, preserves formatting)
    if os.path.exists(MIHOMO_CFG):
        with open(MIHOMO_CFG) as f:
            cfg_text = f.read()
        new_rules = build_rules_for_draft(draft.get("groups", []))
        new_text = replace_yaml_rules(cfg_text, new_rules)
        with open(MIHOMO_CFG, "w") as f:
            f.write(new_text)
        print(f"[apply] config.yaml updated ({len(new_rules)} VPN rules)")

    # Reload mihomo
    try:
        resp = requests.put(f"{MIHOMO_API}/configs?force=true", json={}, timeout=10)
        resp.raise_for_status()
        print("[apply] mihomo reloaded")
    except Exception as e:
        print(f"[apply] WARNING: mihomo reload failed: {e}")

    return {"success": True, "added": len(added), "removed": len(to_remove)}


# ---------------------------------------------------------------------------
# First-start migration
# ---------------------------------------------------------------------------

def migrate_draft():
    print("[migrate] draft.json absent — migrating from live...")
    try:
        live_entries = read_address_list()
    except Exception as e:
        print(f"[migrate] ERROR reading address-list: {e}")
        write_draft({"groups": []})
        return

    # Remove known typo
    for e in live_entries:
        if e["address"] == "pi.anthropic.com":
            try:
                ssh_run(
                    '/ip/firewall/address-list/remove '
                    '[find list=vpn-route address="pi.anthropic.com"]'
                )
                print("[migrate] deleted typo entry pi.anthropic.com")
            except Exception as ex:
                print(f"[migrate] WARNING: could not delete pi.anthropic.com: {ex}")
    live_entries = [e for e in live_entries if e["address"] != "pi.anthropic.com"]

    _telegram_domains = {
        "t.me", "telegram.me", "telegram.org", "tdesktop.com",
        "telegra.ph", "telesco.pe", "telegram.im",
    }

    def classify(addr: str, comment: str) -> str:
        parts = comment.split("|")
        if len(parts) == 3 and parts[0] == COMMENT_PREFIX:
            return parts[1]
        # Old comment format: "mihomo-vpn <domain>" — extract domain hint
        hint = ""
        if comment.startswith(COMMENT_PREFIX + " "):
            hint = comment[len(COMMENT_PREFIX) + 1:].strip().lower()
        a = addr.lower()
        check = hint or a
        if "anthropic" in check or check == "claude.ai":
            return "Anthropic"
        if (
            "telegram" in check
            or check in _telegram_domains
            or re.match(r"^(91\.108\.|149\.154\.)", addr)
        ):
            return "Telegram"
        if check == "ifconfig.me":
            return "Test"
        return "Misc"

    groups_dict: dict = {}
    order: list = []
    for e in live_entries:
        group = classify(e["address"], e.get("comment", ""))
        typ = infer_type(e["address"])
        if group not in groups_dict:
            groups_dict[group] = []
            order.append(group)
        groups_dict[group].append({"value": e["address"], "type": typ})

    draft = {"groups": [{"name": g, "entries": groups_dict[g]} for g in order]}
    write_draft(draft)
    print(f"[migrate] created draft.json ({len(order)} groups)")

    try:
        apply_changes(draft)
        print("[migrate] normalized comments applied")
    except Exception as e:
        print(f"[migrate] WARNING: post-migrate apply failed: {e}")


# ---------------------------------------------------------------------------
# Mihomo status
# ---------------------------------------------------------------------------

def get_mihomo_status() -> dict:
    try:
        resp = requests.get(f"{MIHOMO_API}/version", timeout=2)
        mihomo_ok = resp.status_code == 200
    except Exception:
        mihomo_ok = False

    delay_ms = None
    if mihomo_ok:
        try:
            resp = requests.get(
                f"{MIHOMO_API}/proxies/VPN/delay",
                params={"url": "http://www.gstatic.com/generate_204", "timeout": 3000},
                timeout=4,
            )
            if resp.status_code == 200:
                delay_ms = resp.json().get("delay")
        except Exception:
            pass

    return {"mihomo_ok": mihomo_ok, "delay_ms": delay_ms}


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802
        print(f"[http] {self.address_string()} {fmt % args}")

    def send_json(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg: str, status: int = 500):
        self.send_json({"error": msg}, status)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def serve_static(self, rel: str):
        safe = os.path.normpath("/" + rel).lstrip("/")
        full = os.path.join("/app/static", safe)
        if not os.path.abspath(full).startswith("/app/static"):
            self.send_error_json("forbidden", 403)
            return
        if not os.path.isfile(full):
            self.send_error_json("not found", 404)
            return
        ext = os.path.splitext(full)[1]
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    # GET routing
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.serve_static("index.html")
        elif path.startswith("/static/"):
            self.serve_static(path[len("/static/"):])
        elif path == "/api/state":
            self._api_state()
        elif path == "/api/proxies":
            self._api_proxies_get()
        elif path == "/api/proxy/active":
            self._api_proxy_active_get()
        elif path == "/api/settings":
            self._api_settings_get()
        elif path == "/api/test/vpn":
            self._api_test_vpn()
        elif path == "/api/status":
            self._api_status()
        else:
            self.send_error_json("not found", 404)

    # POST routing
    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        parts = [p for p in path.split("/") if p]

        if path == "/api/apply":
            self._api_apply()
        elif path == "/api/discard":
            self._api_discard()
        elif path == "/api/proxies":
            self._api_proxy_add()
        elif path == "/api/proxy/active":
            self._api_proxy_active_set()
        elif path == "/api/settings":
            self._api_settings_save()
        elif parts == ["api", "groups"]:
            self._api_create_group()
        elif (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "groups"
            and parts[3] == "entries"
        ):
            self._api_add_entry(urllib.parse.unquote(parts[2]))
        elif (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "groups"
            and parts[3] == "entries"
            and parts[4] == "batch"
        ):
            self._api_add_entries_batch(urllib.parse.unquote(parts[2]))
        else:
            self.send_error_json("not found", 404)

    # DELETE routing
    def do_DELETE(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        parts = [p for p in path.split("/") if p]

        if len(parts) == 2 and parts == ["api", "proxies"]:
            body = self.read_body()
            self._api_proxy_delete(body.get("name", ""))
        elif len(parts) == 3 and parts[:2] == ["api", "groups"]:
            self._api_delete_group(urllib.parse.unquote(parts[2]))
        elif (
            len(parts) == 5
            and parts[:2] == ["api", "groups"]
            and parts[3] == "entries"
        ):
            self._api_delete_entry(
                urllib.parse.unquote(parts[2]),
                urllib.parse.unquote(parts[4]),
            )
        else:
            self.send_error_json("not found", 404)

    # --- Handlers ---

    def _api_state(self):
        with _lock:
            try:
                live = read_live()
            except Exception as e:
                self.send_error_json(f"failed to read live state: {e}")
                return
            draft = read_draft()
            if draft is None:
                draft = live.copy()
                write_draft(draft)
            all_ros = _read_all_ros_addresses()
            self.send_json(
                {
                    "draft": draft,
                    "live": live,
                    "dirty": is_dirty(draft, live, all_ros),
                    "mihomo_status": get_mihomo_status(),
                }
            )

    def _api_apply(self):
        with _lock:
            draft = read_draft()
            if not draft:
                self.send_error_json("no draft", 400)
                return
            try:
                result = apply_changes(draft)
                self.send_json(result)
            except Exception as e:
                self.send_error_json(str(e))

    def _api_discard(self):
        with _lock:
            try:
                live = read_live()
                write_draft(live)
                self.send_json({"success": True})
            except Exception as e:
                self.send_error_json(str(e))

    def _api_create_group(self):
        body = self.read_body()
        name = (body.get("name") or "").strip()
        if not name:
            self.send_error_json("name required", 400)
            return
        with _lock:
            draft = read_draft() or {"groups": []}
            if any(g["name"] == name for g in draft["groups"]):
                self.send_error_json("group already exists", 409)
                return
            draft["groups"].append({"name": name, "entries": []})
            write_draft(draft)
        self.send_json({"success": True})

    def _api_delete_group(self, name: str):
        with _lock:
            draft = read_draft() or {"groups": []}
            draft["groups"] = [g for g in draft["groups"] if g["name"] != name]
            write_draft(draft)
        self.send_json({"success": True})

    def _api_add_entry(self, group_name: str):
        body = self.read_body()
        value = (body.get("value") or "").strip()
        if not value:
            self.send_error_json("value required", 400)
            return
        typ = infer_type(value)
        with _lock:
            draft = read_draft() or {"groups": []}
            group = next((g for g in draft["groups"] if g["name"] == group_name), None)
            if group is None:
                self.send_error_json("group not found", 404)
                return
            if any(e["value"] == value for e in group["entries"]):
                self.send_error_json("entry already exists", 409)
                return
            group["entries"].append({"value": value, "type": typ})
            write_draft(draft)
        self.send_json({"success": True, "type": typ})

    def _api_add_entries_batch(self, group_name: str):
        body = self.read_body()
        values = [v.strip().lower() for v in body.get("values", []) if v.strip()]
        if not values:
            self.send_error_json("values required", 400)
            return
        added, skipped_dup, invalid = [], [], []
        with _lock:
            draft = read_draft() or {"groups": []}
            group = next((g for g in draft["groups"] if g["name"] == group_name), None)
            if group is None:
                self.send_error_json("group not found", 404)
                return
            existing = {e["value"] for e in group["entries"]}
            for value in values:
                if value in existing:
                    skipped_dup.append(value)
                    continue
                typ = infer_type(value)
                group["entries"].append({"value": value, "type": typ})
                existing.add(value)
                added.append(value)
            write_draft(draft)
        self.send_json({"added": len(added), "skipped_duplicates": len(skipped_dup), "invalid": invalid})

    def _api_delete_entry(self, group_name: str, value: str):
        with _lock:
            draft = read_draft() or {"groups": []}
            group = next((g for g in draft["groups"] if g["name"] == group_name), None)
            if group is None:
                self.send_error_json("group not found", 404)
                return
            group["entries"] = [e for e in group["entries"] if e["value"] != value]
            write_draft(draft)
        self.send_json({"success": True})

    def _api_settings_get(self):
        s = read_settings()
        self.send_json({
            "router_host": s.get("router_host") or DEFAULT_ROUTER_HOST,
            "router_user": s.get("router_user") or DEFAULT_ROUTER_USER,
            "mihomo_dns":  s.get("mihomo_dns")  or DEFAULT_MIHOMO_DNS,
        })

    def _api_settings_save(self):
        with _lock:
            body = self.read_body()
            s = read_settings()
            if "router_host" in body:
                s["router_host"] = body["router_host"].strip() or DEFAULT_ROUTER_HOST
            if "router_user" in body:
                s["router_user"] = body["router_user"].strip() or DEFAULT_ROUTER_USER
            if "mihomo_dns" in body:
                new_dns = body["mihomo_dns"].strip() or DEFAULT_MIHOMO_DNS
                if new_dns != (s.get("mihomo_dns") or DEFAULT_MIHOMO_DNS):
                    if os.path.exists(MIHOMO_CFG):
                        with open(MIHOMO_CFG) as f:
                            cfg_text = f.read()
                        with open(MIHOMO_CFG, "w") as f:
                            f.write(update_yaml_dns_servers(cfg_text, new_dns))
                        _reload_mihomo()
                s["mihomo_dns"] = new_dns
            write_settings(s)
            self.send_json({"success": True})

    def _api_proxy_active_get(self):
        try:
            resp = requests.get(f"{MIHOMO_API}/proxies/VPN", timeout=5)
            resp.raise_for_status()
            self.send_json({"active": resp.json().get("now", "")})
        except Exception as e:
            self.send_error_json(str(e))

    def _api_proxy_active_set(self):
        body = self.read_body()
        name = body.get("name", "").strip()
        if not name:
            self.send_error_json("name required", 400)
            return
        try:
            resp = requests.put(
                f"{MIHOMO_API}/proxies/VPN",
                json={"name": name},
                timeout=5,
            )
            resp.raise_for_status()
            self.send_json({"success": True})
        except Exception as e:
            self.send_error_json(str(e))

    def _api_proxies_get(self):
        try:
            self.send_json({"proxies": read_proxies()})
        except Exception as e:
            self.send_error_json(str(e))

    def _api_proxy_add(self):
        with _lock:
            body = self.read_body()
            try:
                proxy = build_proxy(body)
            except ValueError as e:
                self.send_error_json(str(e), 400)
                return
            try:
                proxies = read_proxies()
                if any(p["name"] == proxy["name"] for p in proxies):
                    self.send_error_json(f"Прокси '{proxy['name']}' уже существует", 409)
                    return
                proxies.append(proxy)
                if os.path.exists(MIHOMO_CFG):
                    with open(MIHOMO_CFG) as f:
                        cfg_text = f.read()
                    with open(MIHOMO_CFG, "w") as f:
                        f.write(update_yaml_proxies_section(cfg_text, proxies))
                _reload_mihomo()
                self.send_json({"success": True})
            except Exception as e:
                self.send_error_json(str(e))

    def _api_proxy_delete(self, name: str):
        with _lock:
            if not name:
                self.send_error_json("name required", 400)
                return
            try:
                proxies = read_proxies()
                new_proxies = [p for p in proxies if p["name"] != name]
                if len(new_proxies) == len(proxies):
                    self.send_error_json(f"Прокси '{name}' не найден", 404)
                    return
                if os.path.exists(MIHOMO_CFG):
                    with open(MIHOMO_CFG) as f:
                        cfg_text = f.read()
                    with open(MIHOMO_CFG, "w") as f:
                        f.write(update_yaml_proxies_section(cfg_text, new_proxies))
                _reload_mihomo()
                self.send_json({"success": True})
            except Exception as e:
                self.send_error_json(str(e))

    def _api_test_vpn(self):
        proxy = "http://192.168.254.4:7890"
        proxies = {"http": proxy, "https": proxy}
        for url in ["https://ifconfig.me/ip", "https://api.ipify.org", "http://checkip.amazonaws.com"]:
            try:
                r = requests.get(
                    url,
                    proxies=proxies,
                    timeout=8,
                    headers={"User-Agent": "curl/8.0"},
                    verify=False,
                )
                ip = r.text.strip()
                if ip:
                    self.send_json({"ip": ip})
                    return
            except Exception:
                continue
        self.send_error_json("VPN test failed: all endpoints returned empty")

    def _api_status(self):
        self.send_json(get_mihomo_status())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(DRAFT_FILE):
        migrate_draft()

    server = http.server.ThreadingHTTPServer(("", LISTEN_PORT), Handler)
    print(f"[switch_stand] listening on :{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
