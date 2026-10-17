"""One-time import from Snipe-IT.

    docker exec rigbook python -m app.import_snipeit --url http://<snipe-it host> --key <API key> [--wipe]

Assets keep their name, model, serial, hostname, OS, status and location. Components become inventory
items: specs are pulled out of names like "16GB DDR4 SODIMM", and each Snipe-IT checkout becomes an install.
"""
import argparse
import html
import json
import re
import sys
import urllib.request

from . import db as store
from .db import db, log, now

STATUS_MAP = {"installed": "In use", "ready to deploy": "Spare", "pending": "Spare", "archived": "Retired"}
TYPE_MAP = {"sff pc": "SFF PC", "custom pc": "Custom PC", "laptops": "Laptop", "laptop": "Laptop",
            "desktop": "Desktop", "desktops": "Desktop", "server": "Server", "servers": "Server"}
CATEGORY_MAP = {"ram": "ram", "memory": "ram", "cpu": "cpu", "processor": "cpu", "storage media": "storage",
                "storage": "storage", "gpu": "gpu", "graphics": "gpu", "motherboard": "motherboard", "psu": "psu",
                "power supply": "psu", "nic": "nic"}

# Specs for the CPUs and parts already in Snipe-IT, so spares on the shelf aren't blank.
# Installed parts get filled from real scans anyway.
KNOWN = {
    "i5-7500t": {"cores": "4", "threads": "4", "base_clock": "2.7", "boost_clock": "3.3", "socket": "LGA1151"},
    "i5-7600t": {"cores": "4", "threads": "4", "base_clock": "2.8", "boost_clock": "3.7", "socket": "LGA1151"},
    "i5-8500t": {"cores": "6", "threads": "6", "base_clock": "2.1", "boost_clock": "3.5", "socket": "LGA1151"},
    "i5-8365u": {"cores": "4", "threads": "8", "base_clock": "1.6", "boost_clock": "4.1", "socket": "BGA1528"},
    "i7-1260p": {"cores": "12", "threads": "16", "base_clock": "2.1", "boost_clock": "4.7", "socket": "BGA1744"},
    "i5-14500t": {"cores": "14", "threads": "20", "base_clock": "1.7", "boost_clock": "4.8", "socket": "LGA1700"},
    "ryzen 5 5600x": {"cores": "6", "threads": "12", "base_clock": "3.7", "boost_clock": "4.6", "socket": "AM4"},
    "rx 6800": {"vram": "16GB"},
    "b550-a pro": {"socket": "AM4", "chipset": "B550", "form_factor": "ATX"},
    "b250m pro-vd": {"socket": "LGA1151", "chipset": "B250", "form_factor": "Micro-ATX"},
    "cs650m": {"wattage": "650W", "rating": "Gold", "modular": "Semi"},
}


def fetch(base, key, path):
    req = urllib.request.Request(f"{base.rstrip('/')}/api/v1/{path}",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def rows(base, key, path):
    out, offset = [], 0
    while True:
        sep = "&" if "?" in path else "?"
        data = fetch(base, key, f"{path}{sep}limit=200&offset={offset}")
        out += data.get("rows", [])
        offset += 200
        if offset >= data.get("total", 0):
            return out


def name_of(value):
    return html.unescape(value.get("name") or "") if isinstance(value, dict) else html.unescape(value or "")


def parse_component(category, name):
    """Pull specs out of a Snipe-IT component name and return (clean name, specs)."""
    n = name.strip()
    specs = {}
    cap = re.search(r"(\d+(?:\.\d+)?)\s*(GB|TB)", n, re.I)
    if category == "ram":
        if cap:
            specs["capacity"] = f"{cap.group(1)}{cap.group(2).upper()}"
        t = re.search(r"\b(LP)?DDR(\d)(X|L)?\b", n, re.I)
        if t:
            specs["type"] = t.group(0).upper()
        if re.search(r"so-?dimm", n, re.I):
            specs["form_factor"] = "SODIMM"
        elif re.search(r"dimm", n, re.I):
            specs["form_factor"] = "DIMM"
        elif specs.get("type", "").startswith("LP"):
            specs["form_factor"] = "Soldered"
    elif category == "storage":
        if cap:
            specs["capacity"] = f"{cap.group(1)}{cap.group(2).upper()}"
        if re.search(r"nvme", n, re.I):
            specs.update(interface="NVMe", media="SSD", form_factor="M.2")
        elif re.search(r"hdd", n, re.I):
            specs.update(interface="SATA", media="HDD")
        elif re.search(r"ssd", n, re.I):
            specs.update(interface="SATA" if re.search(r"sata", n, re.I) else "", media="SSD")
            if re.search(r"m\.?2", n, re.I):
                specs["form_factor"] = "M.2"
            elif re.search(r"sata", n, re.I):
                specs["form_factor"] = '2.5"'
    specs.update(KNOWN.get(n.lower(), {}))
    return n, {k: v for k, v in specs.items() if v}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--wipe", action="store_true", help="delete everything in Rigbook first")
    args = ap.parse_args()

    store.init()
    with db() as conn:
        existing = conn.execute("SELECT (SELECT COUNT(*) FROM assets) + (SELECT COUNT(*) FROM items)").fetchone()[0]
        if existing and not args.wipe:
            sys.exit("Rigbook already has data. Re-run with --wipe to replace it.")
        if args.wipe:
            for table in ("installs", "items", "assets", "events", "scans"):
                conn.execute(f"DELETE FROM {table}")

        cats = store.categories(conn)
        hardware = rows(args.url, args.key, "hardware")
        components = rows(args.url, args.key, "components")

        asset_ids = {}
        for a in sorted(hardware, key=lambda r: r["id"]):
            cf = {k: (v or {}).get("value") for k, v in (a.get("custom_fields") or {}).items()}
            status = name_of(a.get("status_label"))
            cat = name_of(a.get("category"))
            model = name_of(a.get("model"))
            serial = a.get("serial") or ""
            cur = conn.execute(
                """INSERT INTO assets (name, asset_type, status, location, hostname, os, manufacturer, model,
                                       model_number, serial, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (html.unescape(a.get("name") or a.get("asset_tag") or f"Asset {a['id']}"),
                 TYPE_MAP.get(cat.lower(), cat), STATUS_MAP.get(status.lower(), status),
                 name_of(a.get("location")) or name_of(a.get("rtd_location")),
                 cf.get("Hostname") or "", cf.get("Operating System") or "",
                 name_of(a.get("manufacturer")), model, a.get("model_number") or "", serial,
                 html.unescape(a.get("notes") or ""), now(), now()))
            asset_ids[a["id"]] = cur.lastrowid
            log(conn, "Imported from Snipe-IT", asset_id=cur.lastrowid)

        for c in sorted(components, key=lambda r: r["id"]):
            snipe_cat = name_of(c.get("category")).lower()
            category = CATEGORY_MAP.get(snipe_cat, "other")
            if category not in cats:
                category = "other"
            raw_name = html.unescape(c.get("name") or "")
            name, specs = parse_component(category, raw_name)
            template = cats[category].get("name_template")
            if template:
                rendered = re.sub(r"\s+", " ", re.sub(r"\{(\w+)\}", lambda m: specs.get(m.group(1), ""), template)).strip()
                if rendered and len(rendered.split()) >= 2:
                    name = rendered
            model_number = c.get("model_number") or ""
            if category == "cpu" and model_number:
                model_number = ""  # Snipe-IT had the marketing name here, not a part number
            cur = conn.execute(
                """INSERT INTO items (category, name, manufacturer, model_number, specs, spare_qty, location, notes,
                                      created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (category, name, name_of(c.get("manufacturer")), model_number, json.dumps(specs),
                 int(c.get("remaining") or 0), name_of(c.get("location")), html.unescape(c.get("notes") or ""),
                 now(), now()))
            item_id = cur.lastrowid
            log(conn, f"Imported from Snipe-IT as “{raw_name}”", item_id=item_id)
            serial = c.get("serial") or ""
            for r in rows(args.url, args.key, f"components/{c['id']}/assets"):
                target = r.get("name") if isinstance(r.get("name"), dict) else r
                asset_id = asset_ids.get(target.get("id"))
                if not asset_id:
                    continue
                conn.execute(
                    "INSERT INTO installs (asset_id, item_id, qty, slot, serial, notes, created_at) VALUES (?, ?, ?, '', ?, ?, ?)",
                    (asset_id, item_id, int(r.get("assigned_qty") or r.get("qty") or 1), serial,
                     html.unescape(r.get("note") or ""), now()))
        n_inst = conn.execute("SELECT COUNT(*) FROM installs").fetchone()[0]
        print(f"Imported {len(asset_ids)} assets, {len(components)} inventory items, {n_inst} installs.")


if __name__ == "__main__":
    main()
