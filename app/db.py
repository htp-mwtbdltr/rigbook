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
CREATE INDEX IF NOT EXISTS events_asset ON events(asset_id);
CREATE INDEX IF NOT EXISTS events_item ON events(item_id);
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

# Parts that go into keyboards rather than computers; listed under Keyboards, not Inventory
KEYBOARD_CATEGORIES = [
    {
        "key": "switch", "label": "Switches", "group": "keyboard", "name_template": "{manufacturer} {model_number}",
        "match": ["name"],
        "fields": [
            {"key": "type", "label": "Type", "type": "select",
             "options": _sel("Linear", "Tactile", "Clicky", "Silent linear", "Silent tactile")},
            {"key": "actuation", "label": "Actuation force (gf)", "type": "number"},
        ],
    },
    {
        "key": "keycaps", "label": "Keycaps", "group": "keyboard", "name_template": "{manufacturer} {model_number}",
        "match": ["name"], "fields": [],
    },
]
DEFAULT_CATEGORIES += KEYBOARD_CATEGORIES
KEYBOARD_LAYOUTS = ["40%", "60%", "65%", "75%", "TKL (80%)", "96%", "Full size (100%)", "Split", "Other"]
KEYBOARD_CONNECTIONS = ["Wired", "2.4GHz", "Bluetooth"]

DEFAULT_STATUSES = ["In use", "Spare", "Wiped", "For sale", "Broken", "Retired"]
NETWORK_TYPES = ["Switch", "Access point", "Router", "Firewall"]
DEFAULT_ASSET_TYPES = ["Desktop", "SFF PC", "Mini PC", "Custom PC", "Laptop", "Server", *NETWORK_TYPES, "Other"]
# Columns added after the first release: (name, type)
ASSET_COLUMNS = [("macs", "TEXT"), ("snmp", "TEXT"), ("arp", "TEXT"), ("kind", "TEXT"), ("specs", "TEXT")]


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
        # Keyboard part categories, once, for databases created before them
        if get_setting(conn, "keyboard_categories_added") is None:
            cats = get_setting(conn, "categories")
            have_keys = {c["key"] for c in cats}
            set_setting(conn, "categories", cats + [c for c in KEYBOARD_CATEGORIES if c["key"] not in have_keys])
            set_setting(conn, "keyboard_categories_added", True)


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
