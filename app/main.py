"""Rigbook: homelab asset and parts inventory."""
import base64
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db as store
from . import scan as scanner
from . import snmp
from .db import db, item_row, log, now

STATIC = Path(__file__).parent / "static"
APP_PASSWORD = os.environ.get("RIGBOOK_PASSWORD", "")
ASSET_FIELDS = ["kind", "name", "asset_type", "status", "location", "hostname", "address", "os", "manufacturer",
                "model", "model_number", "serial", "notes", "scan_user", "macs"]
SNMP_KEYS = ["version", "user", "level", "auth_proto", "priv_proto"]
SNMP_SECRETS = ["community", "auth_pass", "priv_pass"]
ITEM_FIELDS = ["category", "name", "manufacturer", "model_number", "spare_qty", "location", "notes"]

app = FastAPI(title="Rigbook", docs_url=None, redoc_url=None)
store.init()


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optional single-password protection. Scan scripts and scan uploads stay open so live USBs can post."""
    path = request.url.path
    open_paths = path in ("/scan.sh", "/scan-freebsd.sh", "/scan.ps1") or (path == "/api/scans" and request.method == "POST")
    if APP_PASSWORD and not open_paths:
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Rigbook"'})
    return await call_next(request)


@app.exception_handler(scanner.ScanError)
async def scan_error(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------------------------------------------------------- helpers

def clean(value):
    return value.strip() if isinstance(value, str) else value


def norm(value):
    return re.sub(r"[\s\-_]+", "", str(value or "")).lower()


def render_name(cat, specs, fallback=""):
    """Fill the category's name template; it can use spec keys plus {manufacturer} and {model_number}."""
    template = (cat or {}).get("name_template") or ""
    if not template:
        return fallback
    name = re.sub(r"\{(\w+)\}", lambda m: str(specs.get(m.group(1), "")), template)
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"(?i)(^|\s)(\S+)(?:\s\2)+(?=\s|$)", r"\1\2", name) or fallback


def capacity_gb(text):
    m = re.match(r"([\d.]+)\s*(GB|TB|MB)", str(text or ""), re.I)
    if not m:
        return 0
    n = float(m.group(1))
    return {"gb": n, "tb": n * 1024, "mb": n / 1024}[m.group(2).lower()]


def fetch_asset(conn, asset_id):
    row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Asset not found")
    return dict(row)


def fetch_item(conn, item_id):
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    return item_row(row)


def asset_components(conn, asset_id):
    rows = conn.execute(
        """SELECT i.id AS install_id, i.qty, i.slot, i.serial, i.notes AS install_notes, it.*
           FROM installs i JOIN items it ON it.id = i.item_id
           WHERE i.asset_id = ? ORDER BY i.id""", (asset_id,)).fetchall()
    return [item_row(r) for r in rows]


def summarize(components, cats):
    """Short per-category text shown in the asset list."""
    out = {}
    order = list(cats)
    by_cat = {}
    for c in components:
        by_cat.setdefault(c["category"], []).append(c)
    for key in sorted(by_cat, key=lambda k: order.index(k) if k in order else 99):
        group = by_cat[key]
        if key == "ram":
            total = sum(capacity_gb(c["specs"].get("capacity")) * c["qty"] for c in group)
            types = sorted({c["specs"].get("type", "") for c in group} - {""})
            label = (f"{total:g}GB" if total < 1024 else f"{total / 1024:g}TB") if total else ""
            out[key] = " ".join(x for x in [label] + types if x) or ", ".join(c["name"] for c in group)
        else:
            counts = {}
            for c in group:
                counts[c["name"]] = counts.get(c["name"], 0) + c["qty"]
            out[key] = ", ".join(f"{q}× {n}" if q > 1 else n for n, q in counts.items())
    return out


def mac_list(value):
    """MACs from a list or a comma/space separated string, normalised and de-duplicated."""
    parts = value if isinstance(value, list) else re.split(r"[\s,;]+", value or "")
    out = []
    for p in parts:
        m = scanner.norm_mac(p)
        if m and m not in out:
            out.append(m)
    return out


def port_row(row):
    d = dict(row)
    d["learned"] = json.loads(d.get("learned") or "[]")
    d["neighbor"] = json.loads(d.get("neighbor") or "{}")
    return d


def asset_ref(a):
    return {"id": a["id"], "name": a["name"], "asset_type": a["asset_type"]} if a else None


def network_index(conn):
    """Lookups for matching what switches see (MACs, LLDP names) back to assets."""
    assets, by_mac, by_name, arp = {}, {}, {}, {}
    for r in conn.execute("SELECT id, name, hostname, asset_type, macs, arp FROM assets"):
        a = dict(r)
        assets[a["id"]] = a
        for m in mac_list(a["macs"]):
            by_mac.setdefault(m, a["id"])
        host = (a["hostname"] or "").lower()
        for n in (a["name"].lower(), host, host.split(".")[0]):
            if n:
                by_name.setdefault(n, a["id"])
        if a["arp"]:
            arp.update(json.loads(a["arp"]))
    return assets, by_mac, by_name, arp


def neighbor_asset(nb, by_mac, by_name):
    if not nb:
        return None
    # Some hosts (Windows, lldpd setups) only put their name in the port description
    names = [(nb.get("name") or "").lower(), (nb.get("port_desc") or "").lower()]
    for n in [x for name in names for x in (name, name.split(".")[0])]:
        if n and n in by_name:
            return by_name[n]
    return by_mac.get(scanner.norm_mac(nb.get("chassis")))


def ports_with_links(conn, asset_id):
    """This device's ports, each with the assets seen on it (LLDP first, then the MAC table)."""
    rows = conn.execute("SELECT * FROM ports WHERE asset_id = ? ORDER BY id", (asset_id,)).fetchall()
    if not rows:
        return []
    assets, by_mac, by_name, arp = network_index(conn)
    out = []
    for r in rows:
        p = port_row(r)
        devices, seen, unknown = [], set(), []
        nb = neighbor_asset(p["neighbor"], by_mac, by_name)
        if nb and nb != asset_id:
            devices.append(dict(asset_ref(assets[nb]), via="lldp"))
            seen.add(nb)
        nb_mac = scanner.norm_mac(p["neighbor"].get("chassis")) if nb else ""
        for m in p["learned"]:
            aid = by_mac.get(m)
            if aid == asset_id or m == nb_mac:
                continue
            if aid:
                if aid not in seen:
                    devices.append(dict(asset_ref(assets[aid]), via="mac"))
                    seen.add(aid)
            else:
                unknown.append({"mac": m, "ip": arp.get(m, "")})
        p["devices"], p["unknown"] = devices, unknown
        out.append(p)
    return out


def asset_links(conn, asset):
    """Where this asset plugs in: per network device, the port it shows up on.

    A MAC is seen on its own port and on every uplink, so the port with the fewest MACs is the real one.
    """
    macs = set(mac_list(asset.get("macs")))
    assets, by_mac, by_name, _ = network_index(conn)
    best = {}
    for r in conn.execute("SELECT * FROM ports WHERE asset_id != ?", (asset["id"],)):
        p = port_row(r)
        lldp = neighbor_asset(p["neighbor"], by_mac, by_name) == asset["id"]
        if not lldp and not (macs & set(p["learned"])):
            continue
        score = (0 if lldp else 1, len(p["learned"]))
        if r["asset_id"] not in best or score < best[r["asset_id"]][0]:
            best[r["asset_id"]] = (score, p)
    links = []
    for dev_id, (score, p) in sorted(best.items(), key=lambda kv: kv[1][0]):
        via_id = neighbor_asset(p["neighbor"], by_mac, by_name)
        via = assets.get(via_id) if via_id not in (None, asset["id"], dev_id) else None
        links.append({"device": asset_ref(assets.get(dev_id)), "port": p["name"], "alias": p["alias"],
                      "speed": p["speed"], "oper": p["oper"], "vlan": p["vlan"], "via": asset_ref(via),
                      "lldp": score[0] == 0})
    return links


def replace_ports(conn, asset_id, ports):
    conn.execute("DELETE FROM ports WHERE asset_id = ?", (asset_id,))
    for p in ports:
        conn.execute(
            """INSERT INTO ports (asset_id, ifindex, name, descr, alias, kind, speed, admin, oper, mac, vlan, learned,
                                  neighbor, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, p.get("ifindex"), p.get("name"), p.get("descr"), p.get("alias"), p.get("kind"),
             p.get("speed") or 0, p.get("admin"), p.get("oper"), p.get("mac"), p.get("vlan"),
             json.dumps(p.get("learned") or []), json.dumps(p.get("neighbor") or {}), now()))


def match_item(conn, cats, component):
    """Find the inventory item a scanned component most likely is."""
    cat = cats.get(component["category"])
    keys = (cat or {}).get("match") or ["name"]
    best, best_score = None, 0
    for row in conn.execute("SELECT * FROM items WHERE category = ?", (component["category"],)):
        item = item_row(row)
        score = 0
        for k in keys:
            a = norm(component["name"] if k == "name" else component["specs"].get(k))
            b = norm(item["name"] if k == "name" else item["specs"].get(k))
            if a and b:
                if a != b:
                    score = -1
                    break
                score += 1
        if score > best_score:
            best, best_score = item, score
    return best


def prepare_scan(conn, parsed, asset_id=None):
    """Attach item matches and generated names to a parsed scan."""
    cats = store.categories(conn)
    comps = []
    for c in parsed["components"]:
        c = dict(c)
        if not c["name"]:
            c["name"] = render_name(cats.get(c["category"]), c["specs"], c.get("notes") or c["category"])
        match = match_item(conn, cats, c)
        c["item_id"] = match["id"] if match else None
        c["item_name"] = match["name"] if match else None
        comps.append(c)
    result = dict(parsed, components=comps)
    if asset_id:
        result["current"] = {"asset": fetch_asset(conn, asset_id), "components": asset_components(conn, asset_id)}
    return result


def store_scan(conn, raw, source, asset_id=None):
    parsed = scanner.parse(raw)
    cur = conn.execute("INSERT INTO scans (created_at, source, asset_id, raw, parsed) VALUES (?, ?, ?, ?, ?)",
                       (now(), source, asset_id, raw, json.dumps(parsed)))
    return cur.lastrowid


def describe(qty, name):
    return f"{qty}× {name}" if qty > 1 else name


def remove_install(conn, install_id, to_spare, quiet=False):
    row = conn.execute(
        """SELECT i.*, it.name AS item_name, a.name AS asset_name FROM installs i
           JOIN items it ON it.id = i.item_id JOIN assets a ON a.id = i.asset_id WHERE i.id = ?""",
        (install_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Component not found")
    conn.execute("DELETE FROM installs WHERE id = ?", (install_id,))
    if to_spare:
        conn.execute("UPDATE items SET spare_qty = spare_qty + ?, updated_at = ? WHERE id = ?",
                     (row["qty"], now(), row["item_id"]))
    if quiet:
        return
    where = " (returned to spares)" if to_spare else ""
    log(conn, f"Removed {describe(row['qty'], row['item_name'])} from {row['asset_name']}{where}",
        asset_id=row["asset_id"], item_id=row["item_id"])


def add_install(conn, asset_id, item_id, qty=1, slot="", serial="", notes="", take_from_spare=False, quiet=False):
    asset = fetch_asset(conn, asset_id)
    item = fetch_item(conn, item_id)
    qty = max(1, int(qty or 1))
    conn.execute(
        "INSERT INTO installs (asset_id, item_id, qty, slot, serial, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (asset_id, item_id, qty, clean(slot) or "", clean(serial) or "", clean(notes) or "", now()))
    if take_from_spare:
        conn.execute("UPDATE items SET spare_qty = MAX(0, spare_qty - ?), updated_at = ? WHERE id = ?",
                     (qty, now(), item_id))
    if quiet:
        return
    src = " from spares" if take_from_spare else ""
    log(conn, f"Installed {describe(qty, item['name'])} in {asset['name']}{src}", asset_id=asset_id, item_id=item_id)


def create_item(conn, data):
    cats = store.categories(conn)
    category = data.get("category")
    if category not in cats:
        raise HTTPException(400, "Unknown category")
    specs = {k: clean(v) for k, v in (data.get("specs") or {}).items() if v not in (None, "")}
    name = clean(data.get("name")) or render_name(cats[category], {
        **specs, "manufacturer": clean(data.get("manufacturer")) or "", "model_number": clean(data.get("model_number")) or ""})
    if not name:
        raise HTTPException(400, "Name is required")
    cur = conn.execute(
        """INSERT INTO items (category, name, manufacturer, model_number, specs, spare_qty, location, notes,
                              created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, name, clean(data.get("manufacturer")) or "", clean(data.get("model_number")) or "",
         json.dumps(specs), max(0, int(data.get("spare_qty") or 0)), clean(data.get("location")) or "",
         clean(data.get("notes")) or "", now(), now()))
    log(conn, f"Created {name}", item_id=cur.lastrowid)
    return cur.lastrowid


def save_asset(conn, data, asset_id=None):
    fields = {k: clean(data[k]) or "" for k in ASSET_FIELDS if k in data and k != "macs"}
    if "macs" in data:
        fields["macs"] = ", ".join(mac_list(data["macs"]))
    if fields.get("kind") not in (None, "", "keyboard"):
        raise HTTPException(400, "Unknown asset kind")
    if isinstance(data.get("specs"), dict):
        fields["specs"] = json.dumps({k: clean(v) for k, v in data["specs"].items() if v not in (None, "", [])})
    if asset_id is None:
        if not fields.get("name"):
            raise HTTPException(400, "Name is required")
        fields.update(created_at=now(), updated_at=now())
        cols = ", ".join(fields)
        cur = conn.execute(f"INSERT INTO assets ({cols}) VALUES ({', '.join('?' * len(fields))})",
                           list(fields.values()))
        log(conn, f"Created asset {fields['name']}", asset_id=cur.lastrowid)
        return cur.lastrowid
    fetch_asset(conn, asset_id)
    if "name" in fields and not fields["name"]:
        raise HTTPException(400, "Name is required")
    fields["updated_at"] = now()
    conn.execute(f"UPDATE assets SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                 [*fields.values(), asset_id])
    return asset_id


# ---------------------------------------------------------------- settings

@app.get("/api/meta")
def meta():
    with db() as conn:
        locations = sorted({r[0] for r in conn.execute(
            "SELECT location FROM assets WHERE location != '' UNION SELECT location FROM items WHERE location != ''")})
        return {
            "categories": store.get_setting(conn, "categories"),
            "statuses": store.get_setting(conn, "statuses"),
            "asset_types": store.get_setting(conn, "asset_types"),
            "keyboard_layouts": store.get_setting(conn, "keyboard_layouts") or store.KEYBOARD_LAYOUTS,
            "keyboard_connections": store.KEYBOARD_CONNECTIONS,
            "locations": locations,
            "pending_scans": conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0],
        }


@app.put("/api/settings")
def save_settings(data: dict = Body(...)):
    with db() as conn:
        if "categories" in data:
            seen = set()
            cats = []
            for c in data["categories"]:
                label = clean(c.get("label"))
                if not label:
                    continue
                key = c.get("key") or store.slug(label)
                while key in seen:
                    key += "_"
                seen.add(key)
                fields, fkeys = [], set()
                for f in c.get("fields") or []:
                    flabel = clean(f.get("label"))
                    if not flabel:
                        continue
                    fkey = f.get("key") or store.slug(flabel)
                    while fkey in fkeys:
                        fkey += "_"
                    fkeys.add(fkey)
                    field = {"key": fkey, "label": flabel, "type": f.get("type") or "text"}
                    if field["type"] == "select":
                        field["options"] = [o.strip() for o in f.get("options") or [] if o.strip()]
                    fields.append(field)
                match = [m for m in c.get("match") or [] if m == "name" or m in fkeys] or ["name"]
                cats.append({"key": key, "label": label, "fields": fields, "match": match,
                             "name_template": clean(c.get("name_template")) or "",
                             "group": "keyboard" if c.get("group") == "keyboard" else ""})
            used = {r[0] for r in conn.execute("SELECT DISTINCT category FROM items")}
            missing = used - seen
            if missing:
                raise HTTPException(400, f"Categories still in use can't be removed: {', '.join(sorted(missing))}")
            store.set_setting(conn, "categories", cats)
        for key in ("statuses", "asset_types", "keyboard_layouts"):
            if key in data:
                store.set_setting(conn, key, [s.strip() for s in data[key] if s.strip()])
    return meta()


# ---------------------------------------------------------------- assets

@app.get("/api/assets")
def list_assets():
    with db() as conn:
        cats = store.categories(conn)
        assets = [dict(r) for r in conn.execute(
            "SELECT id, name, asset_type, status, location, hostname, address, os, manufacturer, model, serial, last_scan, "
            "kind, specs FROM assets ORDER BY name COLLATE NOCASE")]
        ports = {r["asset_id"]: {"total": r["total"], "up": r["up"]} for r in conn.execute(
            "SELECT asset_id, SUM(kind = 'ethernet') AS total, SUM(kind = 'ethernet' AND oper = 'up') AS up "
            "FROM ports GROUP BY asset_id")}
        for a in assets:
            comps = asset_components(conn, a["id"])
            a["summary"] = summarize(comps, cats)
            a["counts"] = {}
            for c in comps:
                a["counts"][c["category"]] = a["counts"].get(c["category"], 0) + c["qty"]
            a["kind"] = a["kind"] or ""
            a["specs"] = json.loads(a["specs"] or "{}")
            a["ports"] = ports.get(a["id"])
        return assets


@app.post("/api/assets")
def create_asset(data: dict = Body(...)):
    with db() as conn:
        return {"id": save_asset(conn, data)}


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: int):
    with db() as conn:
        asset = fetch_asset(conn, asset_id)
        asset.pop("raw_scan", None)
        asset.pop("arp", None)
        asset["kind"] = asset["kind"] or ""
        asset["specs"] = json.loads(asset["specs"] or "{}")
        profile = json.loads(asset.pop("snmp", None) or "null")
        asset["snmp"] = {k: profile.get(k) or "" for k in SNMP_KEYS} if profile else None
        asset["snmp_saved"] = bool(profile and profile.get("remember"))
        asset["ports"] = ports_with_links(conn, asset_id)
        asset["links"] = asset_links(conn, asset)
        asset["components"] = asset_components(conn, asset_id)
        asset["summary"] = summarize(asset["components"], store.categories(conn))
        asset["events"] = [dict(r) for r in conn.execute(
            "SELECT ts, message FROM events WHERE asset_id = ? ORDER BY id DESC LIMIT 50", (asset_id,))]
        return asset


@app.get("/api/assets/{asset_id}/raw", response_class=PlainTextResponse)
def asset_raw(asset_id: int):
    with db() as conn:
        return fetch_asset(conn, asset_id).get("raw_scan") or "No scan stored for this asset."


@app.put("/api/assets/{asset_id}")
def update_asset(asset_id: int, data: dict = Body(...)):
    with db() as conn:
        save_asset(conn, data, asset_id)
        return {"id": asset_id}


@app.delete("/api/assets/{asset_id}")
def delete_asset(asset_id: int, return_to_spare: bool = True):
    with db() as conn:
        asset = fetch_asset(conn, asset_id)
        for row in conn.execute("SELECT id FROM installs WHERE asset_id = ?", (asset_id,)).fetchall():
            remove_install(conn, row["id"], return_to_spare)
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        log(conn, f"Deleted asset {asset['name']}")
        return {"ok": True}


@app.post("/api/assets/{asset_id}/components")
def install_component(asset_id: int, data: dict = Body(...)):
    with db() as conn:
        item_id = data.get("item_id")
        if not item_id:
            item_id = create_item(conn, data.get("new_item") or {})
        add_install(conn, asset_id, item_id, data.get("qty"), data.get("slot"), data.get("serial"),
                    data.get("notes"), bool(data.get("take_from_spare")))
        return {"ok": True, "item_id": item_id}


@app.put("/api/installs/{install_id}")
def update_install(install_id: int, data: dict = Body(...)):
    with db() as conn:
        row = conn.execute("SELECT * FROM installs WHERE id = ?", (install_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Component not found")
        qty = max(1, int(data.get("qty") or row["qty"]))
        item_id = int(data.get("item_id") or row["item_id"])
        fetch_item(conn, item_id)
        conn.execute("UPDATE installs SET qty = ?, item_id = ?, slot = ?, serial = ?, notes = ? WHERE id = ?",
                     (qty, item_id, clean(data.get("slot", row["slot"])) or "",
                      clean(data.get("serial", row["serial"])) or "", clean(data.get("notes", row["notes"])) or "",
                      install_id))
        return {"ok": True}


@app.delete("/api/installs/{install_id}")
def delete_install(install_id: int, return_to_spare: bool = True):
    with db() as conn:
        remove_install(conn, install_id, return_to_spare)
        return {"ok": True}


# ---------------------------------------------------------------- inventory

@app.get("/api/items")
def list_items():
    with db() as conn:
        rows = conn.execute(
            """SELECT it.*, COALESCE(SUM(i.qty), 0) AS installed_qty, COUNT(DISTINCT i.asset_id) AS asset_count
               FROM items it LEFT JOIN installs i ON i.item_id = it.id
               GROUP BY it.id ORDER BY it.category, it.name COLLATE NOCASE""").fetchall()
        return [item_row(r) for r in rows]


@app.post("/api/items")
def new_item(data: dict = Body(...)):
    with db() as conn:
        return {"id": create_item(conn, data)}


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    with db() as conn:
        item = fetch_item(conn, item_id)
        item["installs"] = [dict(r) for r in conn.execute(
            """SELECT i.id AS install_id, i.qty, i.slot, i.serial, i.notes, a.id AS asset_id, a.name AS asset_name,
                      a.status AS asset_status, a.asset_type, a.kind AS asset_kind, a.location AS asset_location
               FROM installs i JOIN assets a ON a.id = i.asset_id WHERE i.item_id = ?
               ORDER BY a.name COLLATE NOCASE, i.slot""", (item_id,))]
        item["installed_qty"] = sum(i["qty"] for i in item["installs"])
        item["events"] = [dict(r) for r in conn.execute(
            "SELECT ts, message FROM events WHERE item_id = ? ORDER BY id DESC LIMIT 50", (item_id,))]
        return item


@app.put("/api/items/{item_id}")
def update_item(item_id: int, data: dict = Body(...)):
    with db() as conn:
        item = fetch_item(conn, item_id)
        cats = store.categories(conn)
        fields = {k: clean(data[k]) for k in ITEM_FIELDS if k in data}
        if "category" in fields and fields["category"] not in cats:
            raise HTTPException(400, "Unknown category")
        if "name" in fields and not fields["name"]:
            raise HTTPException(400, "Name is required")
        if "spare_qty" in fields:
            fields["spare_qty"] = max(0, int(fields["spare_qty"] or 0))
            if fields["spare_qty"] != item["spare_qty"]:
                log(conn, f"Spare count {item['spare_qty']} → {fields['spare_qty']}", item_id=item_id)
        if "specs" in data:
            fields["specs"] = json.dumps({k: clean(v) for k, v in data["specs"].items() if v not in (None, "")})
        fields["updated_at"] = now()
        conn.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     [*fields.values(), item_id])
        return {"id": item_id}


@app.post("/api/items/{item_id}/merge")
def merge_item(item_id: int, data: dict = Body(...)):
    """Move installs and spare stock of this item into another one, then delete it."""
    target_id = int(data.get("target_id") or 0)
    if target_id == item_id:
        raise HTTPException(400, "Pick a different item")
    with db() as conn:
        src = fetch_item(conn, item_id)
        dst = fetch_item(conn, target_id)
        conn.execute("UPDATE installs SET item_id = ? WHERE item_id = ?", (target_id, item_id))
        conn.execute("UPDATE items SET spare_qty = spare_qty + ?, updated_at = ? WHERE id = ?",
                     (src["spare_qty"], now(), target_id))
        conn.execute("UPDATE events SET item_id = ? WHERE item_id = ?", (target_id, item_id))
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        log(conn, f"Merged {src['name']} into {dst['name']}", item_id=target_id)
        return {"id": target_id}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    with db() as conn:
        item = fetch_item(conn, item_id)
        if conn.execute("SELECT 1 FROM installs WHERE item_id = ?", (item_id,)).fetchone():
            raise HTTPException(400, "This item is still installed somewhere. Remove it from those assets first.")
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.execute("UPDATE events SET item_id = NULL WHERE item_id = ?", (item_id,))
        log(conn, f"Deleted item {item['name']}")
        return {"ok": True}


# ---------------------------------------------------------------- scanning

# Discovery can take minutes on slow devices, longer than a reverse proxy will hold a request open, so it runs in
# a background thread and the browser polls /api/jobs/{id}. Jobs live in memory (one worker process).
JOBS = {}
JOBS_LOCK = threading.Lock()


def start_job(work):
    """Run work(progress) in the background; it returns a scan id. Returns {"job_id": ...}."""
    job_id = secrets.token_hex(8)
    job = {"status": "running", "progress": "Starting…", "scan_id": None, "error": None, "started": time.time()}
    with JOBS_LOCK:
        for old in [k for k, j in JOBS.items() if time.time() - j["started"] > 3600]:
            del JOBS[old]
        JOBS[job_id] = job

    def run():
        try:
            job["scan_id"] = work(lambda text: job.update(progress=text))
            job["status"] = "done"
        except scanner.ScanError as e:
            job.update(status="error", error=str(e))
        except HTTPException as e:
            job.update(status="error", error=str(e.detail))
        except Exception as e:  # anything unexpected still has to reach the user
            job.update(status="error", error=f"Discovery failed: {e}")

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "That discovery job is gone (Rigbook may have restarted). Check Scans.")
    return {k: job[k] for k in ("status", "progress", "scan_id", "error")} | {"seconds": int(time.time() - job["started"])}


def load_parsed(row):
    """A scan's parsed data. SNMP scans are re-read from their raw output so parser fixes apply to them too."""
    parsed = json.loads(row["parsed"])
    if (row["raw"] or "").startswith("### rigbook-snmp"):
        parsed.update(scanner.parse(row["raw"]))
    return parsed


@app.post("/api/discover")
def discover(data: dict = Body(...)):
    host = clean(data.get("host"))
    user = clean(data.get("username"))
    if not host or not user:
        raise HTTPException(400, "Host and username are required")

    def work(progress):
        progress(f"Connecting to {host} and reading the hardware…")
        raw = scanner.ssh_scan(host, data.get("port") or 22, user, data.get("password") or "")
        with db() as conn:
            scan_id = store_scan(conn, raw, f"ssh {user}@{host}", data.get("asset_id"))
            conn.execute("UPDATE scans SET parsed = json_set(parsed, '$.address', ?, '$.scan_user', ?) WHERE id = ?",
                         (host, user, scan_id))
            return scan_id
    return start_job(work)


@app.post("/api/discover/snmp")
def discover_snmp(data: dict = Body(...)):
    """Walk a network device over SNMP. Secrets are only kept if the user ticks "remember"."""
    host = clean(data.get("host"))
    if not host:
        raise HTTPException(400, "Host is required")
    asset_id = data.get("asset_id")
    creds = {k: data.get(k) or "" for k in SNMP_KEYS + SNMP_SECRETS}
    remember = data.get("remember")
    if asset_id:
        with db() as conn:
            saved = json.loads(fetch_asset(conn, asset_id).get("snmp") or "{}")
        if data.get("use_saved"):
            creds = {**{k: saved.get(k) or "" for k in SNMP_KEYS + SNMP_SECRETS},
                     **{k: v for k, v in creds.items() if v}}
        if remember is None:
            remember = saved.get("remember")
    snmp._args(host, creds)  # bad or missing credentials fail now, not in the background
    profile = {k: creds.get(k) or "" for k in SNMP_KEYS}
    if remember:
        profile.update({k: creds.get(k) or "" for k in SNMP_SECRETS}, remember=True)

    def work(progress):
        raw = snmp.walk(host, creds, progress)
        with db() as conn:
            scan_id = store_scan(conn, raw, f"snmp v{creds.get('version') or '2c'} {host}", asset_id)
            conn.execute("UPDATE scans SET parsed = json_set(parsed, '$.address', ?, '$.snmp_profile', json(?)) "
                         "WHERE id = ?", (host, json.dumps(profile), scan_id))
            return scan_id
    return start_job(work)


@app.post("/api/scans")
async def upload_scan(request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    if not raw.strip():
        raise HTTPException(400, "Empty scan")
    host = request.client.host if request.client else ""
    with db() as conn:
        return {"scan_id": store_scan(conn, raw, f"upload from {host}")}


@app.get("/api/scans")
def list_scans():
    with db() as conn:
        out = []
        for r in conn.execute("SELECT id, created_at, source, asset_id, raw, parsed FROM scans ORDER BY id DESC"):
            p = load_parsed(r)
            out.append({"id": r["id"], "created_at": r["created_at"], "source": r["source"], "asset_id": r["asset_id"],
                        "name": p["asset"].get("name"), "model": p["asset"].get("model"),
                        "manufacturer": p["asset"].get("manufacturer")})
        return out


@app.get("/api/scans/{scan_id}")
def get_scan(scan_id: int, asset_id: int | None = None):
    with db() as conn:
        row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Scan not found (it may already have been saved)")
        parsed = load_parsed(row)
        target = asset_id or row["asset_id"]
        if not target:
            # Offer to update an existing asset with the same serial or hostname
            a = parsed["asset"]
            hit = None
            if a.get("serial"):
                hit = conn.execute("SELECT id FROM assets WHERE serial = ? AND serial != ''", (a["serial"],)).fetchone()
            if not hit and a.get("name"):
                hit = conn.execute("SELECT id FROM assets WHERE lower(name) = lower(?) OR lower(hostname) = lower(?)",
                                   (a["name"], a.get("hostname") or a["name"])).fetchone()
            if not hit and parsed.get("address"):
                hit = conn.execute("SELECT id FROM assets WHERE address = ? AND address != ''",
                                   (parsed["address"],)).fetchone()
            parsed["suggested_asset_id"] = hit["id"] if hit else None
        if parsed.get("snmp_profile"):
            parsed["snmp_profile"] = {k: parsed["snmp_profile"].get(k) for k in SNMP_KEYS + ["remember"]}
        result = prepare_scan(conn, parsed, target)
        result.update(id=row["id"], source=row["source"], created_at=row["created_at"], asset_id=target)
        return result


@app.get("/api/scans/{scan_id}/raw", response_class=PlainTextResponse)
def scan_raw(scan_id: int):
    with db() as conn:
        row = conn.execute("SELECT raw FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Scan not found")
        return row["raw"]


@app.delete("/api/scans/{scan_id}")
def delete_scan(scan_id: int):
    with db() as conn:
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        return {"ok": True}


@app.post("/api/scans/{scan_id}/apply")
def apply_scan(scan_id: int, data: dict = Body(...)):
    """Save a reviewed scan.

    data = {asset_id, asset: {...fields}, replace: [category keys], components: [{include, item_id | new item fields,
            category, name, specs, qty, slot, serial, notes}], removed_to_spare: bool}
    """
    with db() as conn:
        row = conn.execute("SELECT raw, parsed FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Scan not found (it may already have been saved)")
        parsed = load_parsed(row)
        cats = store.categories(conn)
        asset_id = data.get("asset_id")
        fields = dict(data.get("asset") or {})
        fields.pop("ip", None)
        fields.pop("macs", None)
        if asset_id:
            save_asset(conn, fields, asset_id)
        else:
            asset_id = save_asset(conn, fields)
        conn.execute("UPDATE assets SET last_scan = ?, raw_scan = ? WHERE id = ?", (now(), row["raw"], asset_id))
        # MACs are how switches find this machine; add new ones, keep any typed in by hand
        have = mac_list(fetch_asset(conn, asset_id).get("macs"))
        merged = have + [m for m in mac_list(parsed["asset"].get("macs") or []) if m not in have]
        if merged != have:
            conn.execute("UPDATE assets SET macs = ? WHERE id = ?", (", ".join(merged), asset_id))
        if parsed.get("kind") == "network":
            replace_ports(conn, asset_id, parsed.get("ports") or [])
            conn.execute("UPDATE assets SET arp = ? WHERE id = ?", (json.dumps(parsed.get("arp") or {}), asset_id))
            if parsed.get("snmp_profile"):
                conn.execute("UPDATE assets SET snmp = ? WHERE id = ?", (json.dumps(parsed["snmp_profile"]), asset_id))

        replace = set(data.get("replace") or [])
        wanted = [c for c in data.get("components") or [] if c.get("include") and c.get("category") in replace]

        def tally(pairs):
            out = {}
            for key, qty in pairs:
                out[key] = out.get(key, 0) + int(qty or 1)
            return out

        # Categories whose parts are the same as before only get serials/slots refreshed, without log noise
        unchanged = set()
        for cat in replace:
            before = tally((r["item_id"], r["qty"]) for r in conn.execute(
                "SELECT i.item_id, i.qty FROM installs i JOIN items it ON it.id = i.item_id "
                "WHERE i.asset_id = ? AND it.category = ?", (asset_id, cat)))
            after = tally((c.get("item_id") or f"new:{c.get('name')}", c.get("qty")) for c in wanted if c["category"] == cat)
            if before == after and not any(c.get("take_from_spare") for c in wanted if c["category"] == cat):
                unchanged.add(cat)
        for cat in replace:
            for r in conn.execute(
                    "SELECT i.id FROM installs i JOIN items it ON it.id = i.item_id "
                    "WHERE i.asset_id = ? AND it.category = ?", (asset_id, cat)).fetchall():
                remove_install(conn, r["id"], bool(data.get("removed_to_spare")) and cat not in unchanged,
                               quiet=cat in unchanged)

        for c in wanted:
            specs = {k: clean(v) for k, v in (c.get("specs") or {}).items() if v not in (None, "")}
            item_id = c.get("item_id")
            if item_id:
                item = fetch_item(conn, item_id)
                # Fill in specs the inventory item doesn't have yet; never overwrite what's there
                merged = dict(specs, **{k: v for k, v in item["specs"].items() if v})
                if merged != item["specs"]:
                    conn.execute("UPDATE items SET specs = ?, updated_at = ? WHERE id = ?",
                                 (json.dumps(merged), now(), item_id))
                if not item["manufacturer"] and c.get("manufacturer"):
                    conn.execute("UPDATE items SET manufacturer = ? WHERE id = ?", (c["manufacturer"], item_id))
            else:
                if c["category"] not in cats:
                    continue
                item_id = create_item(conn, {"category": c["category"], "name": c.get("name"), "specs": specs,
                                             "manufacturer": c.get("manufacturer"),
                                             "model_number": c.get("model_number")})
            add_install(conn, asset_id, item_id, c.get("qty") or 1, c.get("slot"), c.get("serial"), c.get("notes"),
                        bool(c.get("take_from_spare")), quiet=c["category"] in unchanged)
        if parsed.get("kind") == "network":
            n_up = sum(1 for p in parsed.get("ports") or [] if p.get("kind") == "ethernet" and p.get("oper") == "up")
            log(conn, f"Saved SNMP scan ({n_up} ports up)", asset_id=asset_id)
        else:
            log(conn, "Saved hardware scan", asset_id=asset_id)
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        return {"asset_id": asset_id}


# ---------------------------------------------------------------- scripts + frontend

def submit_url(request: Request):
    base = os.environ.get("RIGBOOK_URL") or str(request.base_url)
    return base.rstrip("/") + "/api/scans"


@app.get("/scan.sh", response_class=PlainTextResponse)
def scan_sh(request: Request):
    return scanner.script("scan.sh", submit_url(request))


@app.get("/scan-freebsd.sh", response_class=PlainTextResponse)
def scan_freebsd_sh(request: Request):
    return scanner.script("scan-freebsd.sh", submit_url(request))


@app.get("/scan.ps1", response_class=PlainTextResponse)
def scan_ps1(request: Request):
    return scanner.script("scan.ps1", submit_url(request))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
