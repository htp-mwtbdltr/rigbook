"""Proxmox VE, through its API with a read-only token: every VM and container, its MACs and which node it runs on.

Guests share their host's switch port, so on the map they'd otherwise be a pile of unknown MACs. Needs an API token
with the PVEAuditor role on / (Datacenter → Permissions → API Tokens).
"""
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import scan as scanner
from .db import db, get_setting, now, set_setting

SYNC_EVERY = 300
MAC_RE = re.compile(r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.I)


class ProxmoxError(Exception):
    pass


class Client:
    def __init__(self, url, token_id, secret, timeout=15):
        self.url = url.rstrip("/")
        self.auth = f"PVEAPIToken={token_id}={secret}"
        self.timeout = timeout
        # Proxmox ships a self-signed certificate
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def get(self, path):
        req = urllib.request.Request(f"{self.url}/api2/json/{path}", headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                return json.loads(resp.read() or b"{}").get("data")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ProxmoxError("Login refused. Check the token ID (user@realm!name) and secret.")
            raise ProxmoxError(f"{path}: HTTP {e.code} {e.reason}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProxmoxError(f"Can't reach Proxmox at {self.url}: {getattr(e, 'reason', e)}")
        except ValueError:
            raise ProxmoxError(f"Proxmox at {self.url} didn't answer with JSON. Is this the right address?")

    def maybe(self, path):
        """For extras the token may not be allowed to see, or a guest without its agent running."""
        try:
            return self.get(path)
        except ProxmoxError:
            return None


def guest_ips(c, node, kind, vmid, running):
    if not running:
        return []
    if kind == "lxc":
        ifaces = c.maybe(f"nodes/{node}/lxc/{vmid}/interfaces") or []
        addrs = [(i.get("inet") or "").split("/")[0] for i in ifaces if i.get("name") != "lo"]
    else:
        res = c.maybe(f"nodes/{node}/qemu/{vmid}/agent/network-get-interfaces") or {}
        addrs = [a.get("ip-address", "") for i in res.get("result") or [] if i.get("name") != "lo"
                 for a in i.get("ip-addresses") or [] if a.get("ip-address-type") == "ipv4"]
    return [a for a in addrs if a and not a.startswith("127.")]


def collect(cfg):
    c = Client(cfg["url"], cfg["token_id"], cfg["secret"])
    nodes = [{"name": n.get("name", ""), "ip": n.get("ip", "")}
             for n in c.get("cluster/status") or [] if n.get("type") == "node"]
    guests = []
    for r in c.get("cluster/resources?type=vm") or []:
        if r.get("template"):
            continue
        node, kind, vmid = r["node"], r["type"], r["vmid"]
        conf = c.maybe(f"nodes/{node}/{kind}/{vmid}/config") or {}
        macs = [scanner.norm_mac(m) for k, v in sorted(conf.items()) if re.fullmatch(r"net\d+", k)
                for m in MAC_RE.findall(str(v))]
        running = r.get("status") == "running"
        guests.append({"vmid": vmid, "name": r.get("name") or f"{kind} {vmid}", "type": kind, "node": node,
                       "status": r.get("status") or "", "macs": [m for m in macs if m],
                       "ips": guest_ips(c, node, kind, vmid, running),
                       "cpus": r.get("maxcpu") or 0, "mem": r.get("maxmem") or 0,
                       "tags": [t for t in re.split(r"[;, ]+", r.get("tags") or "") if t]})
    return {"nodes": nodes, "guests": sorted(guests, key=lambda g: g["vmid"])}


def config(conn):
    return get_setting(conn, "proxmox") or {}


def sync():
    with db() as conn:
        cfg = config(conn)
    if not (cfg.get("url") and cfg.get("token_id") and cfg.get("secret")):
        return None
    try:
        snap = collect(cfg)
        status = {"ok": True, "at": now(), "error": "", "guests": len(snap["guests"]), "nodes": len(snap["nodes"])}
    except ProxmoxError as e:
        snap, status = None, {"ok": False, "at": now(), "error": str(e)}
    with db() as conn:
        if snap is not None:
            set_setting(conn, "proxmox_snapshot", snap)
        set_setting(conn, "proxmox_status", {**(get_setting(conn, "proxmox_status") or {}), **status})
    return status


def snapshot(conn):
    return get_setting(conn, "proxmox_snapshot") or {"nodes": [], "guests": []}


def start():
    def loop():
        while True:
            try:
                sync()
            except Exception as e:  # never let the loop die
                with db() as conn:
                    set_setting(conn, "proxmox_status", {"ok": False, "at": now(), "error": f"Sync failed: {e}"})
            time.sleep(SYNC_EVERY)
    threading.Thread(target=loop, daemon=True).start()
