"""TP-Link Omada controller, through its Open API: which access point every Wi-Fi client is on, plus client names.

APs don't report their clients over SNMP, so this is the only way to place wireless devices. The controller needs an
Open API app in Client Credentials mode (Settings → Platform Integration → Open API); its access tokens last two hours.
"""
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import scan as scanner
from .db import db, get_setting, now, set_setting

SYNC_EVERY = 300
KEEP_OFFLINE = 7 * 86400
# Omada's "token expired / invalid" error codes
TOKEN_ERRORS = {-44106, -44111, -44112, -44113, -44116}


class OmadaError(Exception):
    pass


class Client:
    def __init__(self, url, client_id, client_secret, timeout=15):
        self.url = url.rstrip("/")
        self.client_id, self.client_secret, self.timeout = client_id, client_secret, timeout
        # Controllers ship a self-signed certificate
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self.cid = None
        self.token = None

    def _req(self, method, path, body=None, auth=True):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", f"AccessToken={self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OmadaError(f"Can't reach the controller at {self.url}: {getattr(e, 'reason', e)}")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise OmadaError(f"The controller at {self.url} didn't answer with JSON. Is this the right address?")

    def login(self):
        info = self._req("GET", "/api/info", auth=False)
        self.cid = (info.get("result") or {}).get("omadacId")
        if not self.cid:
            raise OmadaError("Couldn't read the controller ID from /api/info")
        res = self._req("POST", "/openapi/authorize/token?grant_type=client_credentials",
                        {"omadacId": self.cid, "client_id": self.client_id, "client_secret": self.client_secret},
                        auth=False)
        if res.get("errorCode") != 0:
            raise OmadaError(f"Login refused: {res.get('msg') or res.get('errorCode')}. Check the client ID and secret.")
        self.token = res["result"]["accessToken"]

    def get(self, path, **query):
        if not self.token:
            self.login()
        qs = "?" + urllib.parse.urlencode(query) if query else ""
        for attempt in (0, 1):
            res = self._req("GET", f"/openapi/v1/{self.cid}/{path}{qs}")
            if res.get("errorCode") in TOKEN_ERRORS and attempt == 0:
                self.login()
                continue
            if res.get("errorCode") != 0:
                raise OmadaError(f"{path}: {res.get('msg') or res.get('errorCode')}")
            return res.get("result")

    def pages(self, path):
        out, page = [], 1
        while True:
            res = self.get(path, page=page, pageSize=1000) or {}
            data = res.get("data") or []
            out += data
            if not data or len(out) >= (res.get("totalRows") or 0):
                return out
            page += 1


def collect(cfg):
    """Every site's devices (APs, switches, gateways) and connected clients."""
    c = Client(cfg["url"], cfg["client_id"], cfg["client_secret"])
    devices, clients = [], []
    for site in c.pages("sites"):
        sid, sname = site.get("siteId") or site.get("id"), site.get("name") or ""
        for d in c.pages(f"sites/{sid}/devices"):
            devices.append({"mac": scanner.norm_mac(d.get("mac")), "name": d.get("name") or "", "ip": d.get("ip") or "",
                            "type": (d.get("type") or "").lower(), "model": d.get("model") or "", "site": sname})
        for x in c.pages(f"sites/{sid}/clients"):
            mac = scanner.norm_mac(x.get("mac"))
            if not mac or x.get("active") is False:
                continue
            # A client nobody has named gets its MAC as its name
            name = x.get("name") or ""
            if scanner.norm_mac(name) == mac:
                name = ""
            clients.append({
                "mac": mac, "name": name, "hostname": x.get("hostName") or "", "ip": x.get("ip") or "",
                "wireless": bool(x.get("wireless")), "ap_mac": scanner.norm_mac(x.get("apMac")),
                "ap_name": x.get("apName") or "", "ssid": x.get("ssid") or "",
                "switch_mac": scanner.norm_mac(x.get("switchMac")), "port": x.get("port") or "",
                "vendor": x.get("vendor") or "", "site": sname})
    return {"devices": devices, "clients": clients}


def config(conn):
    return get_setting(conn, "omada") or {}


def sync():
    """Pull a fresh snapshot from the controller; the error is kept with the last good snapshot."""
    with db() as conn:
        cfg = config(conn)
    if not (cfg.get("url") and cfg.get("client_id") and cfg.get("client_secret")):
        return None
    try:
        snap = collect(cfg)
        status = {"ok": True, "at": now(), "error": "", "clients": len(snap["clients"]), "devices": len(snap["devices"])}
    except OmadaError as e:
        snap, status = None, {"ok": False, "at": now(), "error": str(e)}
    with db() as conn:
        if snap is not None:
            # Clients that left are kept for a week, so they stay on the map at the AP they were last on
            stamp = now()
            fresh = {c["mac"] for c in snap["clients"]}
            for c in snap["clients"]:
                c["last_seen"], c["offline"] = stamp, False
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - KEEP_OFFLINE))
            for c in snapshot(conn)["clients"]:
                if c["mac"] not in fresh and (c.get("last_seen") or stamp) >= cutoff:
                    snap["clients"].append({**c, "last_seen": c.get("last_seen") or stamp, "offline": True})
            set_setting(conn, "omada_snapshot", snap)
        set_setting(conn, "omada_status", {**(get_setting(conn, "omada_status") or {}), **status})
    return status


def snapshot(conn):
    return get_setting(conn, "omada_snapshot") or {"devices": [], "clients": []}


def start():
    def loop():
        while True:
            try:
                sync()
            except Exception as e:  # never let the loop die
                with db() as conn:
                    set_setting(conn, "omada_status", {"ok": False, "at": now(), "error": f"Sync failed: {e}"})
            time.sleep(SYNC_EVERY)
    threading.Thread(target=loop, daemon=True).start()
