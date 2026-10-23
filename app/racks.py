"""Racks: what's mounted where. Cables are in cabling.py; the rack view draws the ones between its own items, plus
the network map's links between two of its assets that no cable already covers.
"""
import json
import math

from fastapi import APIRouter, Body, HTTPException

from .db import db

router = APIRouter()

WIDTHS = (19, 10)
KINDS = ("asset", "patch_panel", "wall_plate", "shelf", "blank", "monitor", "kvm", "cable_mgmt", "power", "other")
EPS = 1e-6


def num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clean(value):
    return value.strip() if isinstance(value, str) else value


def fetch_rack(conn, rack_id):
    row = conn.execute("SELECT * FROM racks WHERE id = ?", (rack_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Rack not found")
    return dict(row)


def item_rows(conn, rack_id):
    """A rack's items, or with rack_id None, the wall plates."""
    rows = conn.execute(
        f"""SELECT i.*, a.name AS asset_name, a.asset_type, a.status, a.address, a.hostname, a.manufacturer, a.model,
                  a.rack_u FROM rack_items i LEFT JOIN assets a ON a.id = i.asset_id
           WHERE {"i.rack_id = ?" if rack_id is not None else "i.rack_id IS NULL"}
           ORDER BY i.u_pos DESC, i.x, i.location, i.label""", (rack_id,) if rack_id is not None else ()).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["port_labels"] = json.loads(d["port_labels"] or "{}")
        if d["asset_id"]:
            d["u_height"] = d["rack_u"] or 1
        d["mounted"] = bool(d["mounted"])
        out.append(d)
    return out


def port_names(item):
    """Ports of something Rigbook can't scan: 1 to its port count, with custom names for the first ones.

    "Coax" with 6 ports is Coax, 2, 3, 4, 5, 6. With no count, there's one port per name.
    """
    names = [n.strip() for n in (item.get("port_names") or "").split(",") if n.strip()]
    n = item.get("ports") or len(names)
    return (names + [str(i) for i in range(len(names) + 1, n + 1)])[:n]


def with_ports(conn, items):
    """Give each item its port list: an asset's SNMP ports (state, and what's seen on them) or its numbered ports."""
    from .main import ports_with_links
    for i in items:
        snmp = [p for p in ports_with_links(conn, i["asset_id"]) if p["kind"] == "ethernet"] if i["asset_id"] else []
        i["snmp"] = bool(snmp)
        i["port_list"] = [{"name": p["name"], "alias": p["alias"], "oper": p["oper"], "admin": p["admin"],
                           "speed": p["speed"], "vlan": p["vlan"], "unknown": len(p["unknown"]),
                           "devices": [{"id": d["id"], "name": d["name"]} for d in p["devices"]]} for p in snmp] \
            or [{"name": n, "alias": "", "oper": "", "admin": "", "speed": 0, "vlan": "", "unknown": 0, "devices": []}
                for n in port_names(i)]
    return items


def check_fit(rack, items, item):
    """Raise if the item is outside the rack or overlaps another item."""
    lo, hi = item["u_pos"], item["u_pos"] + item["u_height"]
    if lo < 1 - EPS or hi > rack["height_u"] + 1 + EPS:
        raise HTTPException(400, f"That doesn't fit: the rack is {rack['height_u']}U and this is {item['u_height']:g}U")
    if item["x"] < -EPS or item["x"] + item["w"] > 1 + EPS:
        raise HTTPException(400, "That's wider than the rack")
    for o in items:
        if o["id"] == item.get("id"):
            continue
        if lo < o["u_pos"] + o["u_height"] - EPS and o["u_pos"] < hi - EPS and \
                item["x"] < o["x"] + o["w"] - EPS and o["x"] < item["x"] + item["w"] - EPS:
            name = o["asset_name"] or o["label"] or o["kind"]
            raise HTTPException(400, f"That overlaps {name} at U{o['u_pos']:g}")


def free_spot(rack, items, h, w):
    """The highest free position for something h U tall and w wide."""
    for top in range(rack["height_u"], 0, -1):
        u = top - math.ceil(h - EPS) + 1
        if u < 1:
            break
        for x in [i / 4 for i in range(0, 4)]:
            if x + w > 1 + EPS:
                continue
            cand = {"u_pos": u, "u_height": h, "x": x, "w": w}
            try:
                check_fit(rack, items, cand)
                return u, x
            except HTTPException:
                pass
    raise HTTPException(400, f"There's no free {h:g}U space left in {rack['name']}")


def rack_fields(data, rack=None):
    rack = dict(rack or {"name": "", "width": 19, "height_u": 42, "location": "", "notes": ""})
    if "name" in data:
        rack["name"] = clean(data["name"]) or ""
    if "width" in data:
        rack["width"] = int(num(data["width"], 19))
    if "height_u" in data:
        rack["height_u"] = int(num(data["height_u"], 0))
    for k in ("location", "notes"):
        if k in data:
            rack[k] = clean(data[k]) or ""
    if not rack["name"]:
        raise HTTPException(400, "Give the rack a name")
    if rack["width"] not in WIDTHS:
        raise HTTPException(400, "Racks are 19 or 10 inches wide")
    if not 1 <= rack["height_u"] <= 60:
        raise HTTPException(400, "Rack height must be between 1 and 60U")
    return rack


@router.get("/api/racks")
def list_racks():
    with db() as conn:
        out = []
        for r in conn.execute("SELECT * FROM racks ORDER BY name COLLATE NOCASE"):
            rack = dict(r)
            items = item_rows(conn, rack["id"])
            rack["items"] = len(items)
            rack["used_u"] = sum(i["u_height"] * i["w"] for i in items)
            out.append(rack)
        return out


@router.post("/api/racks")
def create_rack(data: dict = Body(...)):
    with db() as conn:
        r = rack_fields(data)
        cur = conn.execute("INSERT INTO racks (name, width, height_u, location, notes, created_at) VALUES "
                           "(?, ?, ?, ?, ?, datetime('now'))", (r["name"], r["width"], r["height_u"], r["location"], r["notes"]))
        return {"id": cur.lastrowid}


@router.put("/api/racks/{rack_id}")
def update_rack(rack_id: int, data: dict = Body(...)):
    with db() as conn:
        r = rack_fields(data, fetch_rack(conn, rack_id))
        items = item_rows(conn, rack_id)
        top = max((i["u_pos"] + i["u_height"] - 1 for i in items), default=0)
        if top > r["height_u"] + EPS:
            raise HTTPException(400, f"Something is mounted up to U{top:g}; move it before making the rack {r['height_u']}U")
        conn.execute("UPDATE racks SET name = ?, width = ?, height_u = ?, location = ?, notes = ? WHERE id = ?",
                     (r["name"], r["width"], r["height_u"], r["location"], r["notes"], rack_id))
        return {"ok": True}


@router.delete("/api/racks/{rack_id}")
def delete_rack(rack_id: int):
    with db() as conn:
        fetch_rack(conn, rack_id)
        conn.execute("DELETE FROM racks WHERE id = ?", (rack_id,))
        return {"ok": True}


@router.get("/api/racks/{rack_id}")
def get_rack(rack_id: int):
    from .cabling import annotate, chains, load_links
    from .main import build_map
    with db() as conn:
        rack = fetch_rack(conn, rack_id)
        items = with_ports(conn, item_rows(conn, rack_id))
        by_asset = {i["asset_id"]: i for i in items if i["asset_id"]}
        mine = {i["id"] for i in items}
        nodes = build_map(conn)["nodes"]
        # Cables with both ends in this rack, with asset ends turned into their rack item
        links, linked, touching_links = [], set(), []
        for l in load_links(conn):
            if any(l[f"{e}_asset"] in by_asset or l[f"{e}_item"] in mine for e in ("a", "b")):
                touching_links.append(l)
            ends = []
            for e in ("a", "b"):
                it = by_asset.get(l[f"{e}_asset"]) if l[f"{e}_asset"] else (l[f"{e}_item"] in mine and {"id": l[f"{e}_item"]})
                ends.append(it["id"] if it else None)
            if all(ends):
                links.append({"id": l["id"], "a_item": ends[0], "a_port": l["a_port"], "b_item": ends[1],
                              "b_port": l["b_port"], "label": l["label"]})
        all_chains = annotate(conn, chains(conn), nodes)
        for c in all_chains:
            hs = c["hops"]
            if hs[0]["kind"] == "asset" and hs[-1]["kind"] == "asset":
                linked.add(frozenset((hs[0]["id"], hs[-1]["id"])))
        # The network map's links between two assets in this rack, unless a cable already joins them
        cables = []
        for n in nodes:
            if n["type"] != "asset" or n["id"] not in by_asset or n.get("parent") not in by_asset:
                continue
            link = n.get("link") or {}
            if link.get("wireless") or link.get("source") == "patch" or frozenset((n["id"], n["parent"])) in linked:
                continue
            up, down = by_asset[n["parent"]], by_asset[n["id"]]
            # The map's tree runs outward from the firewall, so the port can be on either end
            port = (link.get("port") or "").lower()
            has = lambda i: next((p["name"] for p in i["port_list"] if p["name"].lower() == port), "") if port else ""
            cables.append({"a_item": up["id"], "a_port": has(up), "b_item": down["id"], "b_port": has(down),
                           "source": link.get("source", "")})
        # Chains touching this rack, so a port can show its whole path
        touching = [c for c in all_chains if any((h["kind"] == "item" and h["id"] in mine) or
                                                 (h["kind"] == "asset" and h["id"] in by_asset) for h in c["hops"])]
        return {"rack": rack, "items": items, "cables": cables, "links": links, "chains": touching,
                "all_links": touching_links}


@router.post("/api/racks/{rack_id}/items")
def add_item(rack_id: int, data: dict = Body(...)):
    with db() as conn:
        rack = fetch_rack(conn, rack_id)
        items = item_rows(conn, rack_id)
        item = item_fields(conn, data, {"kind": "other", "label": "", "u_height": 1, "x": 0, "w": 1, "mounted": True,
                                        "ports": 0, "notes": "", "asset_id": None, "location": "", "port_names": ""})
        if item["asset_id"]:
            other = conn.execute("SELECT r.name FROM rack_items i JOIN racks r ON r.id = i.rack_id WHERE i.asset_id = ?",
                                 (item["asset_id"],)).fetchone()
            if other:
                raise HTTPException(400, f"That's already in {other['name']}")
        if num(data.get("u_pos")) is None:
            item["u_pos"], item["x"] = free_spot(rack, items, item["u_height"], item["w"])
        check_fit(rack, items, item)
        save_height(conn, item)
        cur = conn.execute(
            """INSERT INTO rack_items (rack_id, asset_id, kind, label, u_pos, u_height, x, w, mounted, ports, port_names, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rack_id, item["asset_id"], item["kind"], item["label"], item["u_pos"], item["u_height"], item["x"],
             item["w"], int(item["mounted"]), item["ports"], item["port_names"], item["notes"]))
        return {"id": cur.lastrowid}


def item_fields(conn, data, item):
    item = dict(item)
    if "asset_id" in data:
        item["asset_id"] = int(data["asset_id"]) if data["asset_id"] else None
        if item["asset_id"]:
            a = conn.execute("SELECT kind, rack_u FROM assets WHERE id = ?", (item["asset_id"],)).fetchone()
            if not a or a["kind"] == "keyboard":
                raise HTTPException(400, "Unknown asset")
            item["kind"] = "asset"
            if "u_height" not in data:
                item["u_height"] = a["rack_u"] or 1
    if "kind" in data and not item.get("asset_id"):
        item["kind"] = data["kind"] if data["kind"] in KINDS and data["kind"] != "asset" else "other"
    for k in ("label", "notes", "location"):
        if k in data:
            item[k] = clean(data[k]) or ""
    if "port_names" in data:
        item["port_names"] = ", ".join(n.strip() for n in (clean(data["port_names"]) or "").split(",") if n.strip())
    for k in ("u_pos", "u_height", "x", "w"):
        if k in data and num(data[k]) is not None:
            item[k] = num(data[k])
    if "mounted" in data:
        item["mounted"] = bool(data["mounted"])
    if "ports" in data:
        item["ports"] = max(0, min(96, int(num(data["ports"], 0))))
    if item.get("port_names") and not item["ports"]:
        item["ports"] = len(port_names(item))
    if not 0 < item["u_height"] <= 60:
        raise HTTPException(400, "Height must be between 0 and 60U")
    if not 0 < item["w"] <= 1:
        raise HTTPException(400, "Width must be between a sliver and the full rack")
    if item["mounted"]:
        item["u_pos"] = round(item.get("u_pos", 1) or 1)
    if not item.get("asset_id") and not item["label"]:
        item["label"] = {"patch_panel": f"Patch panel ({item['ports']}-port)" if item["ports"] else "Patch panel",
                         "wall_plate": "Wall plate",
                         "shelf": "Shelf", "blank": "Blank panel", "monitor": "Monitor", "kvm": "Keyboard drawer",
                         "cable_mgmt": "Cable management", "power": "Power"}.get(item["kind"], "Other")
    return item


def save_height(conn, item):
    """An asset's height lives on the asset, so it's the same wherever it's mounted."""
    if item.get("asset_id"):
        conn.execute("UPDATE assets SET rack_u = ? WHERE id = ?", (item["u_height"], item["asset_id"]))


@router.put("/api/rack-items/{item_id}")
def update_item(item_id: int, data: dict = Body(...)):
    with db() as conn:
        row = conn.execute("SELECT rack_id FROM rack_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not in a rack")
        old_rack = row["rack_id"]
        current = next(i for i in item_rows(conn, old_rack) if i["id"] == item_id)
        data = {k: v for k, v in data.items() if k != "asset_id"}
        item = item_fields(conn, data, current)
        rack_id = old_rack
        if old_rack is not None:  # wall plates aren't in a rack, so there's nothing to fit
            rack_id = int(data.get("rack_id") or old_rack)
            rack = fetch_rack(conn, rack_id)
            items = item_rows(conn, rack_id)
            if rack_id != old_rack and num(data.get("u_pos")) is None:
                item["u_pos"], item["x"] = free_spot(rack, items, item["u_height"], item["w"])
            check_fit(rack, items, item)
        if "port_labels" in data and isinstance(data["port_labels"], dict):
            item["port_labels"] = {str(k): clean(v) for k, v in data["port_labels"].items() if clean(v)}
        save_height(conn, item)
        conn.execute(
            """UPDATE rack_items SET rack_id = ?, kind = ?, label = ?, location = ?, u_pos = ?, u_height = ?, x = ?, w = ?,
               mounted = ?, ports = ?, port_names = ?, port_labels = ?, notes = ? WHERE id = ?""",
            (rack_id, item["kind"], item["label"], item["location"], item["u_pos"], item["u_height"], item["x"], item["w"],
             int(item["mounted"]), item["ports"], item["port_names"], json.dumps(item["port_labels"]), item["notes"], item_id))
        return {"ok": True}


@router.delete("/api/rack-items/{item_id}")
def remove_item(item_id: int):
    with db() as conn:
        conn.execute("DELETE FROM rack_items WHERE id = ?", (item_id,))
        return {"ok": True}
