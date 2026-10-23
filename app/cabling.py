"""Cabling: every cable, and the chains they make through patch panels and wall plates.

A cable joins two ends. An end is an asset's port (a switch port, a PC's NIC; the port can be blank), or one side of
a port on a pass-through panel: a patch panel or wall plate has a front and a back, and whatever's on one side of
port 16 carries on out of the other. Following that gives chains like
gi16 → patch panel 16 → wall plate behind the rack 1 → desk wall plate 1 → MAIN-RIG.

A chain with an asset at both ends is a connection the network map uses (unless the asset's "Connected to" is set by
hand). A chain that stops at an empty panel port still says which switch port it starts on, so the map's view of that
port (the MAC table) fills in what's probably at the far end.
"""
from fastapi import APIRouter, Body, HTTPException

from .db import db, get_setting, set_setting

router = APIRouter()

PANELS = ("patch_panel", "wall_plate")
SIDES = ("front", "back")


def clean(value):
    return value.strip() if isinstance(value, str) else ""


def items_by_id(conn):
    return {r["id"]: dict(r) for r in conn.execute(
        "SELECT i.id, i.rack_id, i.asset_id, i.kind, i.label, i.location, r.name AS rack_name FROM rack_items i "
        "LEFT JOIN racks r ON r.id = i.rack_id")}


def assets_by_id(conn):
    managed = {r[0] for r in conn.execute("SELECT DISTINCT asset_id FROM ports")}
    out = {}
    for r in conn.execute("SELECT id, name, asset_type, uplink_id FROM assets WHERE COALESCE(kind, '') != 'keyboard'"):
        a = dict(r)
        a["managed"] = a["id"] in managed
        out[a["id"]] = a
    return out


def node_of(row, end):
    """The graph node for one end of a link row: ('a', asset, port) or ('i', item, port, side)."""
    if row[f"{end}_asset"]:
        return ("a", row[f"{end}_asset"], row[f"{end}_port"])
    return ("i", row[f"{end}_item"], row[f"{end}_port"], row[f"{end}_side"])


def load_links(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM patch_links ORDER BY id")]


def rank(node, assets):
    """Which end of a chain is upstream: modem, firewall, managed switch, other switch, access point, the rest.

    APs are scanned over SNMP too, so "has scanned ports" alone doesn't make something a switch.
    """
    if node[0] != "a" or node[1] not in assets:
        return 9
    a = assets[node[1]]
    t = (a["asset_type"] or "").lower()
    if t == "modem":
        return 0
    if t in ("firewall", "router"):
        return 1
    if t == "access point":
        return 4
    return 2 if a["managed"] else 3 if t == "switch" else 5


def chains(conn, links=None, items=None, assets=None):
    """Every connected run of cables, ordered upstream end first, as a list of hops."""
    links = load_links(conn) if links is None else links
    items = items_by_id(conn) if items is None else items
    assets = assets_by_id(conn) if assets is None else assets
    adj = {}
    for l in links:
        a, b = node_of(l, "a"), node_of(l, "b")
        adj.setdefault(a, []).append((b, l["id"]))
        adj.setdefault(b, []).append((a, l["id"]))

    def through(n):
        # A pass-through panel port joins its two sides
        if n[0] == "i" and items.get(n[1], {}).get("kind") in PANELS:
            return ("i", n[1], n[2], "back" if n[3] == "front" else "front")
        return None

    def neighbours(n):
        out = [m for m, _ in adj.get(n, [])]
        t = through(n)
        if t:
            out.append(t)
        return out

    seen, out = set(), []
    for start in list(adj):
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack += neighbours(n)
        seen |= comp
        # Walk it end to end; the ends are nodes with one neighbour (a cycle has none, so start anywhere)
        ends = [n for n in comp if len(neighbours(n)) <= 1]
        first = min(ends or comp, key=lambda n: (rank(n, assets), str(n)))
        order, prev, cur = [], None, first
        while cur is not None and cur not in order:
            order.append(cur)
            nxt = [m for m in neighbours(cur) if m != prev and m not in order]
            prev, cur = cur, (nxt[0] if nxt else None)
        hops = []
        for n in order:
            if n[0] == "i" and hops and hops[-1]["kind"] == "item" and hops[-1]["id"] == n[1] and hops[-1]["port"] == n[2]:
                continue  # the other side of the panel port just added
            hops.append(hop(n, items, assets))
        link_ids = sorted({lid for n in comp for _, lid in adj.get(n, [])})
        out.append({"hops": hops, "links": link_ids})
    return out


def hop(n, items, assets):
    if n[0] == "a":
        a = assets.get(n[1], {})
        return {"kind": "asset", "id": n[1], "port": n[2], "name": a.get("name", "?"), "asset_type": a.get("asset_type") or ""}
    it = items.get(n[1], {})
    where = it.get("rack_name") or it.get("location") or ""
    return {"kind": "item", "id": n[1], "port": n[2], "side": n[3], "panel": it.get("kind"),
            "name": it.get("label") or "Panel", "where": where}


def chain_text(hops):
    return " → ".join(f"{h['name']} {h['port']}".strip() for h in hops)


def patched_uplinks(conn):
    """Connections the map can use: chains with an asset at both ends. {child asset: (parent, parent port, text)}"""
    assets = assets_by_id(conn)
    out = {}
    for c in chains(conn, assets=assets):
        hs = c["hops"]
        if len(hs) < 2 or hs[0]["kind"] != "asset" or hs[-1]["kind"] != "asset" or hs[0]["id"] == hs[-1]["id"]:
            continue
        parent, child = hs[0], hs[-1]
        if child["id"] not in out:
            out[child["id"]] = (parent["id"], parent["port"], chain_text(hs[1:-1]))
    return out


def resolve_end(conn, end, items):
    """An end from the browser: {item|asset, port, side}. Assets in racks are stored as the asset, not the item."""
    port = clean(end.get("port"))
    item_id = int(end["item"]) if end.get("item") else None
    asset_id = int(end["asset"]) if end.get("asset") else None
    if item_id:
        it = items.get(item_id)
        if not it:
            raise HTTPException(400, "That isn't in a rack any more")
        if it["asset_id"]:
            asset_id, item_id = it["asset_id"], None
    if asset_id:
        if not conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone():
            raise HTTPException(400, "Unknown device")
        return {"asset": asset_id, "item": None, "port": port, "side": ""}
    if not item_id:
        raise HTTPException(400, "Say what it connects to")
    side = end.get("side") if end.get("side") in SIDES else ""
    if items[item_id]["kind"] in PANELS and not side:
        side = "front"
    return {"asset": None, "item": item_id, "port": port, "side": side}


def links_at(conn, e):
    if e["asset"]:
        return conn.execute("SELECT * FROM patch_links WHERE (a_asset = ? AND a_port = ?) OR (b_asset = ? AND b_port = ?)",
                            (e["asset"], e["port"], e["asset"], e["port"])).fetchall()
    return conn.execute("SELECT * FROM patch_links WHERE (a_item = ? AND a_port = ? AND a_side = ?) OR "
                        "(b_item = ? AND b_port = ? AND b_side = ?)",
                        (e["item"], e["port"], e["side"], e["item"], e["port"], e["side"])).fetchall()


def connect(conn, a, b, label="", items=None):
    """Cable a to b. Whatever was on either end before is unplugged first, as with a real cable."""
    items = items or items_by_id(conn)
    a, b = resolve_end(conn, a, items), resolve_end(conn, b, items)
    if a == b:
        raise HTTPException(400, "A port can't connect to itself")
    for e in (a, b):
        # A device with no port named can have several cables (a PC on two wall jacks); a named port has one
        if e["item"] or e["port"]:
            for r in links_at(conn, e):
                conn.execute("DELETE FROM patch_links WHERE id = ?", (r["id"],))
    cur = conn.execute("INSERT INTO patch_links (a_item, a_asset, a_port, a_side, b_item, b_asset, b_port, b_side, label) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (a["item"], a["asset"], a["port"], a["side"], b["item"], b["asset"], b["port"], b["side"],
                        clean(label)))
    return cur.lastrowid


def is_panel(items, end):
    it = items.get(int(end["item"])) if end.get("item") else None
    return bool(it and it["kind"] in PANELS and not it["asset_id"])


def far_side(items, a):
    """Panel to panel is a cable run, which lands on the back; a patch cord from a device lands on the front."""
    return "back" if is_panel(items, a) else "front"


def free_side(conn, items, end, prefer):
    """For a panel end without a side: the preferred side if nothing's on it, else the other one."""
    if not end.get("item") or end.get("side") in SIDES:
        return end
    it = items.get(int(end["item"]))
    if not it or it["kind"] not in PANELS or it["asset_id"]:
        return end
    for side in (prefer, "back" if prefer == "front" else "front"):
        probe = {"asset": None, "item": it["id"], "port": clean(end.get("port")), "side": side}
        if not links_at(conn, probe):
            return {**end, "side": side}
    return {**end, "side": prefer}


@router.post("/api/links")
def add_link(data: dict = Body(...)):
    """{a: {item|asset, port, side}, b: {...}, label}. b's side, if it's a panel, defaults to a free one."""
    with db() as conn:
        items = items_by_id(conn)
        a = data.get("a") or {}
        b = free_side(conn, items, data.get("b") or {}, far_side(items, a))
        return {"id": connect(conn, a, b, data.get("label"), items)}


@router.post("/api/links/batch")
def batch_links(data: dict = Body(...)):
    """Several cables at once, as {links: [{a, b, label}]}; all or nothing."""
    with db() as conn:
        items = items_by_id(conn)
        ids = []
        for l in data.get("links") or []:
            a = l.get("a") or {}
            ids.append(connect(conn, a, free_side(conn, items, l.get("b") or {}, far_side(items, a)),
                               l.get("label"), items))
        return {"ids": ids}


@router.delete("/api/links/{link_id}")
def delete_link(link_id: int):
    with db() as conn:
        conn.execute("DELETE FROM patch_links WHERE id = ?", (link_id,))
        return {"ok": True}


@router.post("/api/plates")
def add_plate(data: dict = Body(...)):
    """A wall plate: a pass-through panel that isn't in a rack."""
    from .racks import item_fields
    with db() as conn:
        it = item_fields(conn, {**data, "kind": "wall_plate"}, {"kind": "wall_plate", "label": "", "u_height": 1, "x": 0,
                                                                 "w": 1, "mounted": True, "ports": 0, "notes": "",
                                                                 "asset_id": None, "location": "", "port_names": ""})
        cur = conn.execute("INSERT INTO rack_items (rack_id, kind, label, location, ports, port_names, notes) "
                           "VALUES (NULL, 'wall_plate', ?, ?, ?, ?, ?)",
                           (it["label"], it["location"], it["ports"], it["port_names"], it["notes"]))
        return {"id": cur.lastrowid}


def annotate(conn, all_chains, map_nodes):
    """Where a chain stops at an empty panel port, what the switch sees on its first port is probably at the end."""
    kids = {}
    for n in map_nodes:
        if n["type"] == "asset" and isinstance(n.get("parent"), int):
            l = n.get("link") or {}
            if l.get("port") and l.get("source") in ("mac", "lldp", "manual", "omada"):
                kids.setdefault((n["parent"], l["port"].lower()), []).append(n)
    for c in all_chains:
        hs = c["hops"]
        c["text"] = chain_text(hs)
        c["end"] = None
        if hs[0]["kind"] == "asset" and hs[-1]["kind"] == "item":
            seen = kids.get((hs[0]["id"], hs[0]["port"].lower()), [])
            pick = seen[0] if len(seen) == 1 else next((n for n in seen if n["link"]["source"] == "manual"), None)
            if pick:
                c["end"] = {"id": pick["id"], "name": pick["name"], "asset_type": pick["asset_type"]}
    return all_chains


@router.get("/api/cabling")
def cabling():
    """Everything for the cabling view and the patch editor: racks, wall plates, cables and chains."""
    from .main import build_map
    from .racks import fetch_rack, item_rows, with_ports
    with db() as conn:
        racks = []
        for r in conn.execute("SELECT id FROM racks ORDER BY name COLLATE NOCASE").fetchall():
            racks.append({"rack": fetch_rack(conn, r["id"]), "items": with_ports(conn, item_rows(conn, r["id"]))})
        plates = with_ports(conn, item_rows(conn, None))
        assets = assets_by_id(conn)
        links = load_links(conn)
        all_chains = annotate(conn, chains(conn, links, assets=assets), build_map(conn)["nodes"])
        # Ports of devices that aren't in a rack, for the port pickers
        from .main import ports_with_links
        racked = {i["asset_id"] for r in racks for i in r["items"] if i["asset_id"]}
        devices = []
        for a in sorted(assets.values(), key=lambda a: a["name"].lower()):
            ports = [p["name"] for p in ports_with_links(conn, a["id"]) if p["kind"] == "ethernet"] if a["managed"] else []
            devices.append({"id": a["id"], "name": a["name"], "asset_type": a["asset_type"] or "", "racked": a["id"] in racked,
                            "ports": ports})
        return {"racks": racks, "plates": plates, "links": links, "chains": all_chains, "devices": devices,
                "layout": get_setting(conn, "cabling_layout") or {}}


@router.put("/api/cabling/layout")
def save_layout(data: dict = Body(...)):
    """Where each rack, wall plate and device sits on the cabling page: {"r:1": [x, y], "i:19": [x, y], "a:13": ...}"""
    pos = {str(k): [round(float(v[0])), round(float(v[1]))] for k, v in (data.get("positions") or {}).items()
           if isinstance(v, (list, tuple)) and len(v) == 2}
    with db() as conn:
        set_setting(conn, "cabling_layout", pos)
    return {"ok": True}
