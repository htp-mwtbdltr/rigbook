"""SQLite storage and default settings."""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("RIGBOOK_DB", "/data/rigbook.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    asset_type   TEXT,
    status       TEXT,
    location     TEXT,
    hostname     TEXT,
    address      TEXT,
    os           TEXT,
    manufacturer TEXT,
    model        TEXT,
    model_number TEXT,
    serial       TEXT,
    notes        TEXT,
    scan_user    TEXT,
    last_scan    TEXT,
    raw_scan     TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    category     TEXT NOT NULL,
    name         TEXT NOT NULL,
    manufacturer TEXT,
    model_number TEXT,
    specs        TEXT NOT NULL DEFAULT '{}',
    spare_qty    INTEGER NOT NULL DEFAULT 0,
    location     TEXT,
    notes        TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS installs (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES items(id),
    qty        INTEGER NOT NULL DEFAULT 1,
    slot       TEXT,
    serial     TEXT,
    notes      TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS installs_asset ON installs(asset_id);
CREATE INDEX IF NOT EXISTS installs_item ON installs(item_id);
CREATE TABLE IF NOT EXISTS scans (
    id         INTEGER PRIMARY KEY,
    created_at TEXT,
    source     TEXT,
    asset_id   INTEGER,
    raw        TEXT NOT NULL,
    parsed     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    ts       TEXT,
    asset_id INTEGER,
    item_id  INTEGER,
    message  TEXT
);
CREATE TABLE IF NOT EXISTS ports (
    id         INTEGER PRIMARY KEY,
    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    ifindex    INTEGER,
    name       TEXT,
    descr      TEXT,
    alias      TEXT,
    kind       TEXT,
    speed      INTEGER,
    admin      TEXT,
    oper       TEXT,
    mac        TEXT,
    vlan       TEXT,
    learned    TEXT NOT NULL DEFAULT '[]',
    neighbor   TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ports_asset ON ports(asset_id);
CREATE TABLE IF NOT EXISTS racks (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    width      INTEGER NOT NULL DEFAULT 19,
    height_u   INTEGER NOT NULL DEFAULT 42,
    location   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
-- What's in a rack: an asset, or something Rigbook doesn't track (patch panel, shelf, monitor, UPS...).
-- u_pos is the bottom U; x and w are the horizontal slot as fractions of the rack's width.
-- An asset's height is its rack_u, so it's the same in every rack. Wall plates have no rack, just a location.
CREATE TABLE IF NOT EXISTS rack_items (
    id          INTEGER PRIMARY KEY,
    rack_id     INTEGER REFERENCES racks(id) ON DELETE CASCADE,
    asset_id    INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'other',
    label       TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    u_pos       REAL NOT NULL DEFAULT 0,
    u_height    REAL NOT NULL DEFAULT 1,
    x           REAL NOT NULL DEFAULT 0,
    w           REAL NOT NULL DEFAULT 1,
    mounted     INTEGER NOT NULL DEFAULT 1,
    ports       INTEGER NOT NULL DEFAULT 0,
    port_names  TEXT NOT NULL DEFAULT '',
    port_labels TEXT NOT NULL DEFAULT '{}',
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS rack_items_rack ON rack_items(rack_id);
-- Cables. Each end is an asset's port, or one side (front/back) of a patch panel or wall plate port.
CREATE TABLE IF NOT EXISTS patch_links (
    id      INTEGER PRIMARY KEY,
    a_item  INTEGER REFERENCES rack_items(id) ON DELETE CASCADE,
    a_asset INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    a_port  TEXT NOT NULL DEFAULT '',
    a_side  TEXT NOT NULL DEFAULT '',
    b_item  INTEGER REFERENCES rack_items(id) ON DELETE CASCADE,
    b_asset INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    b_port  TEXT NOT NULL DEFAULT '',
    b_side  TEXT NOT NULL DEFAULT '',
    label   TEXT NOT NULL DEFAULT ''
);
"""


def _sel(*opts):
    return list(opts)


DEFAULT_CATEGORIES = [
    {
        "key": "cpu", "label": "CPU", "name_template": "", "match": ["name"],
        "fields": [
            {"key": "cores", "label": "Cores", "type": "number"},
            {"key": "threads", "label": "Threads", "type": "number"},
            {"key": "base_clock", "label": "Base clock (GHz)", "type": "text"},
            {"key": "boost_clock", "label": "Boost clock (GHz)", "type": "text"},
            {"key": "socket", "label": "Socket", "type": "text"},
        ],
    },
    {
        "key": "ram", "label": "RAM", "name_template": "{capacity} {type}",
        "match": ["capacity", "type", "form_factor"],
        "fields": [
            {"key": "capacity", "label": "Capacity", "type": "text"},
            {"key": "type", "label": "Type", "type": "select",
             "options": _sel("DDR3", "DDR3L", "DDR4", "DDR5", "LPDDR3", "LPDDR4", "LPDDR4X", "LPDDR5", "LPDDR5X")},
            {"key": "form_factor", "label": "Form factor", "type": "select",
             "options": _sel("DIMM", "SODIMM", "Soldered", "CAMM2")},
            {"key": "speed", "label": "Speed (MT/s)", "type": "text"},
        ],
    },
    {
        "key": "storage", "label": "Storage", "name_template": "{capacity} {interface} {media}",
        "match": ["capacity", "interface", "media", "form_factor"],
        "fields": [
            {"key": "capacity", "label": "Capacity", "type": "text"},
            {"key": "media", "label": "Media", "type": "select", "options": _sel("SSD", "HDD")},
            {"key": "interface", "label": "Interface", "type": "select", "options": _sel("NVMe", "SATA", "SAS", "USB")},
            {"key": "form_factor", "label": "Form factor", "type": "select",
             "options": _sel("M.2", "2.5\"", "3.5\"", "mSATA", "U.2")},
        ],
    },
    {
        "key": "gpu", "label": "GPU", "name_template": "", "match": ["name"],
        "fields": [
            {"key": "vram", "label": "VRAM", "type": "text"},
        ],
    },
    {
        "key": "motherboard", "label": "Motherboard", "name_template": "", "match": ["name"],
        "fields": [
            {"key": "socket", "label": "Socket", "type": "text"},
            {"key": "chipset", "label": "Chipset", "type": "text"},
            {"key": "form_factor", "label": "Form factor", "type": "select",
             "options": _sel("ATX", "Micro-ATX", "Mini-ITX", "E-ATX", "Proprietary")},
        ],
    },
    {
        "key": "psu", "label": "PSU", "name_template": "", "match": ["name"],
        "fields": [
            {"key": "wattage", "label": "Wattage", "type": "text"},
            {"key": "rating", "label": "80+ rating", "type": "select",
             "options": _sel("None", "White", "Bronze", "Silver", "Gold", "Platinum", "Titanium")},
            {"key": "modular", "label": "Modular", "type": "select", "options": _sel("No", "Semi", "Full")},
        ],
    },
    {
        "key": "nic", "label": "NIC", "name_template": "", "match": ["name"],
        "fields": [
            {"key": "speed", "label": "Speed", "type": "text"},
            {"key": "ports", "label": "Ports", "type": "number"},
        ],
    },
    {"key": "other", "label": "Other", "name_template": "", "match": ["name"], "fields": []},
]

KEYCAP_PROFILES = _sel("Cherry", "OEM", "SA", "DSA", "XDA", "XVX", "KAT", "KAM", "MT3", "MDA", "ASA", "OSA", "DCS",
                       "Low profile", "Other")

# Parts that go into keyboards rather than computers; listed under Keyboards, not Inventory
KEYBOARD_CATEGORIES = [
    {
        "key": "switch", "label": "Switches", "group": "keyboard", "name_template": "{manufacturer} {model_number}",
        "match": ["name"],
        "fields": [
            {"key": "type", "label": "Type", "type": "select",
             "options": _sel("Linear", "Tactile", "Clicky", "Silent linear", "Silent tactile")},
            {"key": "actuation", "label": "Actuation force (gf)", "type": "number"},
            {"key": "bottom_out", "label": "Bottom-out force (gf)", "type": "number"},
        ],
    },
    {
        "key": "keycaps", "label": "Keycaps", "group": "keyboard", "name_template": "{manufacturer} {profile} {model_number}",
        "match": ["name"],
        "fields": [
            {"key": "profile", "label": "Profile", "type": "select", "options": KEYCAP_PROFILES},
            {"key": "link", "label": "Product link", "type": "url"},
        ],
    },
]
DEFAULT_CATEGORIES += KEYBOARD_CATEGORIES
KEYBOARD_LAYOUTS = ["40%", "60%", "65%", "75%", "TKL (80%)", "96%", "Full size (100%)", "Split", "Other"]
KEYBOARD_CONNECTIONS = ["Wired", "2.4GHz", "Bluetooth"]

DEFAULT_STATUSES = ["In use", "Spare", "Wiped", "For sale", "Broken", "Retired"]
NETWORK_TYPES = ["Switch", "Access point", "Router", "Firewall", "Modem"]
DEFAULT_ASSET_TYPES = ["Desktop", "SFF PC", "Mini PC", "Custom PC", "Laptop", "Server", *NETWORK_TYPES, "Other"]
# Columns added after the first release: (name, type)
ASSET_COLUMNS = [("macs", "TEXT"), ("snmp", "TEXT"), ("arp", "TEXT"), ("kind", "TEXT"), ("specs", "TEXT"),
                 ("uplink_id", "INTEGER"), ("uplink_port", "TEXT"), ("rack_u", "REAL")]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "field"


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        defaults = {
            "categories": DEFAULT_CATEGORIES,
            "statuses": DEFAULT_STATUSES,
            "asset_types": DEFAULT_ASSET_TYPES,
            "keyboard_layouts": KEYBOARD_LAYOUTS,
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        _upgrade_rack_items(conn)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(assets)")}
        for name, kind in ASSET_COLUMNS:
            if name not in have:
                conn.execute(f"ALTER TABLE assets ADD COLUMN {name} {kind}")
        # Offer the network device types once to databases created before they existed
        if get_setting(conn, "network_types_added") is None:
            types = get_setting(conn, "asset_types")
            missing = [t for t in NETWORK_TYPES if t not in types]
            if missing:
                at = types.index("Other") if "Other" in types else len(types)
                set_setting(conn, "asset_types", types[:at] + missing + types[at:])
            set_setting(conn, "network_types_added", True)
        if get_setting(conn, "modem_type_added") is None:
            types = get_setting(conn, "asset_types")
            if "Modem" not in types:
                at = types.index("Other") if "Other" in types else len(types)
                set_setting(conn, "asset_types", types[:at] + ["Modem"] + types[at:])
            set_setting(conn, "modem_type_added", True)
        # Keyboard part categories, once, for databases created before them
        if get_setting(conn, "keyboard_categories_added") is None:
            cats = get_setting(conn, "categories")
            have_keys = {c["key"] for c in cats}
            set_setting(conn, "categories", cats + [c for c in KEYBOARD_CATEGORIES if c["key"] not in have_keys])
            set_setting(conn, "keyboard_categories_added", True)
        # Bottom-out force on switches, once, for databases that got the switch category before it existed
        if get_setting(conn, "switch_bottom_out_added") is None:
            cats = get_setting(conn, "categories")
            for c in cats:
                if c["key"] == "switch" and not any(f["key"] == "bottom_out" for f in c["fields"]):
                    at = next((i + 1 for i, f in enumerate(c["fields"]) if f["key"] == "actuation"), len(c["fields"]))
                    c["fields"].insert(at, {"key": "bottom_out", "label": "Bottom-out force (gf)", "type": "number"})
            set_setting(conn, "categories", cats)
            set_setting(conn, "switch_bottom_out_added", True)
        if get_setting(conn, "keycaps_profile_link_added") is None:
            _add_keycap_profile_and_link(conn)
            set_setting(conn, "keycaps_profile_link_added", True)


def _upgrade_rack_items(conn):
    """The first racks release kept rack items in a rack (rack_id NOT NULL) and cables in rack_links. Wall plates
    need items without a rack, so rebuild the table, and move any rack_links into patch_links."""
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(rack_items)")}
    if cols["rack_id"]["notnull"]:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            ddl = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS rack_items"):]
            ddl = ddl[:ddl.index(");") + 2].replace("IF NOT EXISTS rack_items", "rack_items_new")
            conn.execute(ddl)
            keep = ", ".join(c for c in cols if c in {r["name"] for r in conn.execute("PRAGMA table_info(rack_items_new)")})
            conn.execute(f"INSERT INTO rack_items_new ({keep}) SELECT {keep} FROM rack_items")
            conn.execute("DROP TABLE rack_items")
            conn.execute("ALTER TABLE rack_items_new RENAME TO rack_items")
            conn.execute("CREATE INDEX IF NOT EXISTS rack_items_rack ON rack_items(rack_id)")
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'rack_links'").fetchone():
        assets = {r["id"]: r["asset_id"] for r in conn.execute("SELECT id, asset_id FROM rack_items")}
        for r in conn.execute("SELECT * FROM rack_links").fetchall():
            a_asset, b_asset = assets.get(r["a_item"]), r["b_asset"] or assets.get(r["b_item"])
            conn.execute("INSERT INTO patch_links (a_item, a_asset, a_port, a_side, b_item, b_asset, b_port, b_side, label) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (None if a_asset else r["a_item"], a_asset, r["a_port"], "" if a_asset else "front",
                          None if b_asset else r["b_item"], b_asset, r["b_port"], "" if b_asset else "front", r["label"]))
        conn.execute("DROP TABLE rack_links")


def _add_keycap_profile_and_link(conn):
    """Give keycaps a profile and a product link, once. Links typed into the per-keyboard notes move onto the set."""
    cats = get_setting(conn, "categories")
    new = next(c for c in KEYBOARD_CATEGORIES if c["key"] == "keycaps")
    for c in cats:
        if c["key"] != "keycaps":
            continue
        have = {f["key"] for f in c["fields"]}
        c["fields"] += [f for f in new["fields"] if f["key"] not in have]
        if c.get("name_template") in ("", "{manufacturer} {model_number}"):
            c["name_template"] = new["name_template"]
    set_setting(conn, "categories", cats)
    rows = conn.execute(
        """SELECT i.id, i.notes, it.id AS item_id, it.specs FROM installs i JOIN items it ON it.id = i.item_id
           WHERE it.category = 'keycaps' AND i.notes LIKE 'http%'""").fetchall()
    for r in rows:
        specs = json.loads(r["specs"] or "{}")
        if not specs.get("link") and re.fullmatch(r"https?://\S+", r["notes"].strip()):
            specs["link"] = r["notes"].strip()
            conn.execute("UPDATE items SET specs = ?, updated_at = ? WHERE id = ?", (json.dumps(specs), now(), r["item_id"]))
            conn.execute("UPDATE installs SET notes = '' WHERE id = ?", (r["id"],))


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def categories(conn):
    return {c["key"]: c for c in get_setting(conn, "categories")}


def log(conn, message, asset_id=None, item_id=None):
    conn.execute(
        "INSERT INTO events (ts, asset_id, item_id, message) VALUES (?, ?, ?, ?)",
        (now(), asset_id, item_id, message),
    )


def item_row(row):
    d = dict(row)
    d["specs"] = json.loads(d.get("specs") or "{}")
    return d
