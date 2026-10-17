"""SNMP discovery for switches, access points and other network gear.

Uses net-snmp's command line tools (snmpbulkwalk) with numeric OIDs, so no MIB files are needed. Every walk's raw
output is kept, so a scan can be re-parsed or debugged later. Credentials never end up in that output.
"""
import os
import re
import subprocess
import tempfile
from collections import Counter

from .scan import ScanError

# (name, OID). Everything is walked with -Ox so octet strings arrive as hex and MACs can't be mangled.
# Big tables only have the columns that are used walked (COLUMNS), which matters a lot on slow switches.
WALKS = [
    ("system", "1.3.6.1.2.1.1"),
    ("ifDescr", "1.3.6.1.2.1.2.2.1.2"),
    ("ifType", "1.3.6.1.2.1.2.2.1.3"),
    ("ifSpeed", "1.3.6.1.2.1.2.2.1.5"),
    ("ifPhysAddress", "1.3.6.1.2.1.2.2.1.6"),
    ("ifAdminStatus", "1.3.6.1.2.1.2.2.1.7"),
    ("ifOperStatus", "1.3.6.1.2.1.2.2.1.8"),
    ("ifName", "1.3.6.1.2.1.31.1.1.1.1"),
    ("ifHighSpeed", "1.3.6.1.2.1.31.1.1.1.15"),
    ("ifAlias", "1.3.6.1.2.1.31.1.1.1.18"),
    ("entPhysical", "1.3.6.1.2.1.47.1.1.1.1"),
    ("ipForwarding", "1.3.6.1.2.1.4.1"),
    ("arp", "1.3.6.1.2.1.4.22.1.2"),
    ("bridgePort", "1.3.6.1.2.1.17.1.4.1.2"),
    ("fdbPort", "1.3.6.1.2.1.17.4.3.1.2"),
    ("fdbStatus", "1.3.6.1.2.1.17.4.3.1.3"),
    ("qfdbPort", "1.3.6.1.2.1.17.7.1.2.2.1.2"),
    ("qfdbStatus", "1.3.6.1.2.1.17.7.1.2.2.1.3"),
    ("pvid", "1.3.6.1.2.1.17.7.1.4.5.1.1"),
    ("vlanName", "1.3.6.1.2.1.17.7.1.4.3.1.1"),
    ("lldpLocPort", "1.0.8802.1.1.2.1.3.7.1"),
    ("lldpRem", "1.0.8802.1.1.2.1.4.1.1"),
]
COLUMNS = {
    "entPhysical": [5, 9, 10, 11, 12, 13],
    "lldpLocPort": [3, 4],
    "lldpRem": [5, 7, 8, 9, 10],
}
WALK_LABELS = {"system": "system info", "entPhysical": "model and serial", "ipForwarding": "routing", "arp": "ARP table",
               "bridgePort": "MAC address table", "fdbPort": "MAC address table", "fdbStatus": "MAC address table",
               "qfdbPort": "MAC address table", "qfdbStatus": "MAC address table", "pvid": "VLANs",
               "vlanName": "VLANs", "lldpLocPort": "LLDP neighbours", "lldpRem": "LLDP neighbours"}

AUTH_PROTOS = ["MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"]
PRIV_PROTOS = ["DES", "AES", "AES192", "AES256"]
LEVELS = ["noAuthNoPriv", "authNoPriv", "authPriv"]

# IANA enterprise numbers (from sysObjectID) for vendors likely to show up in a homelab
ENTERPRISES = {
    9: "Cisco", 11: "HP", 171: "D-Link", 674: "Dell", 1916: "Extreme Networks", 2011: "Huawei", 2636: "Juniper",
    4526: "Netgear", 6486: "Alcatel-Lucent", 11863: "TP-Link", 12356: "Fortinet", 14988: "MikroTik",
    25461: "Palo Alto Networks", 30065: "Arista", 41112: "Ubiquiti", 47196: "HPE Aruba",
}
NET_SNMP = 8072  # net-snmp's own agent, i.e. a Linux/BSD box running snmpd

IF_KIND = {6: "ethernet", 117: "ethernet", 62: "ethernet", 71: "wifi", 161: "lag", 53: "virtual", 135: "vlan",
           136: "vlan", 24: "loopback", 131: "tunnel", 209: "bridge"}
OPER = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}


# ---------------------------------------------------------------- running the walks

def _args(target, creds):
    version = str(creds.get("version") or "2c")
    if version in ("1", "2c"):
        community = creds.get("community") or ""
        if not community:
            raise ScanError("A community string is required for SNMP v1/v2c.")
        tool = "snmpwalk" if version == "1" else "snmpbulkwalk"
        return tool, [f"-v{version}", "-c", community]
    user = creds.get("user") or ""
    if not user:
        raise ScanError("A username is required for SNMPv3.")
    level = creds.get("level") or "authPriv"
    if level not in LEVELS:
        raise ScanError(f"Unknown security level {level}")
    args = ["-v3", "-u", user, "-l", level]
    if level in ("authNoPriv", "authPriv"):
        if (creds.get("auth_proto") or "SHA") not in AUTH_PROTOS:
            raise ScanError("Unknown auth protocol")
        if len(creds.get("auth_pass") or "") < 8:
            raise ScanError("SNMPv3 needs the auth password (at least 8 characters).")
        args += ["-a", creds.get("auth_proto") or "SHA", "-A", creds["auth_pass"]]
    if level == "authPriv":
        if (creds.get("priv_proto") or "AES") not in PRIV_PROTOS:
            raise ScanError("Unknown privacy protocol")
        if len(creds.get("priv_pass") or "") < 8:
            raise ScanError("SNMPv3 needs the privacy password (at least 8 characters).")
        args += ["-x", creds.get("priv_proto") or "AES", "-X", creds["priv_pass"]]
    return "snmpbulkwalk", args


def _explain(target, stderr):
    err = stderr.strip()
    low = err.lower()
    if "timeout" in low:
        return (f"No SNMP response from {target}. Check the address, that SNMP is enabled on the device, and the "
                "community string (with a wrong community, devices just stay silent).")
    if "unknown user" in low:
        return f"{target} doesn't know that SNMPv3 username."
    if "authentication failure" in low or "wrong digest" in low:
        return f"SNMPv3 authentication with {target} failed (wrong auth password or protocol)."
    if "decryption error" in low:
        return f"SNMPv3 decryption failed with {target} (wrong privacy password or protocol)."
    if "unknown host" in low:
        return f"Can't resolve {target}."
    lines = [l for l in err.splitlines() if l.strip() and "Cannot" not in l]
    return f"SNMP request to {target} failed: {(lines[-1] if lines else err)[:300]}"


def walk(target, creds, progress=None):
    """Walk every table in WALKS and return the raw text that parse_snmp() understands.

    progress(text) is called before each table, for showing what's happening on slow devices.
    """
    target = (target or "").strip()
    if not target:
        raise ScanError("Host is required")
    tool, auth = _args(target, creds)
    out = ["### rigbook-snmp 1", f"### target {target}", f"### version {creds.get('version') or '2c'}"]
    with tempfile.TemporaryDirectory() as tmp:
        # Keep net-snmp from looking for MIB files or writing state into a home directory it can't use
        env = dict(os.environ, HOME=tmp, SNMP_PERSISTENT_DIR=tmp, MIBS="", MIBDIRS=tmp, SNMPCONFPATH=tmp)
        for i, (name, base) in enumerate(WALKS):
            if progress:
                label = WALK_LABELS.get(name) or ("interfaces" if name.startswith("if") else name)
                progress(f"Reading {label} ({i + 1}/{len(WALKS)})")
            out.append(f"### walk {name}")
            for oid in [f"{base}.{c}" for c in COLUMNS[name]] if name in COLUMNS else [base]:
                cmd = [tool, *auth, "-On", "-Ox", "-Oe", "-t", "5", "-r", "1"]
                if tool == "snmpbulkwalk":
                    cmd.append("-Cr25")
                cmd += [target, oid]
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
                except FileNotFoundError:
                    raise ScanError("The SNMP tools are missing from this container (rebuild the image).")
                except subprocess.TimeoutExpired:
                    if i == 0:
                        raise ScanError(f"SNMP request to {target} timed out.")
                    out.append("# timed out")
                    continue
                if i == 0 and (res.returncode != 0 or ".1.3.6.1.2.1.1." not in res.stdout):
                    raise ScanError(_explain(target, res.stderr or res.stdout))
                out.append(res.stdout.rstrip())
                if res.returncode != 0 and res.stderr.strip():
                    out.append("# " + res.stderr.strip().replace("\n", "\n# ")[:500])
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- parsing walk output

_LINE = re.compile(r"^(\.[\d.]+) = (?:([\w-]+): ?)?(.*)$")


def _value(vtype, text):
    text = text.strip()
    if vtype == "Hex-STRING":
        return bytes(int(b, 16) for b in text.split())
    if vtype in ("INTEGER", "Gauge32", "Counter32", "Counter64", "Unsigned32"):
        m = re.match(r"-?\d+", text)
        return int(m.group()) if m else 0
    if vtype == "Timeticks":
        m = re.match(r"\((\d+)\)", text)
        return int(m.group(1)) if m else 0
    if vtype == "OID":
        return text
    if vtype == "IpAddress":
        return text
    if vtype == "STRING" or vtype is None:
        return text[1:-1].encode() if text.startswith('"') and text.endswith('"') else text.encode()
    return text


def parse_walk(text, base):
    """Return {suffix tuple: value} for one walk's output."""
    out = {}
    prefix = "." + base + "."
    cur = None
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            oid, vtype, val = m.groups()
            if "No Such" in line or "No more variables" in line:
                cur = None
                continue
            if not oid.startswith(prefix):
                cur = None
                continue
            suffix = tuple(int(x) for x in oid[len(prefix):].split("."))
            cur = (suffix, vtype, [val])
            out[suffix] = _value(vtype, val)
        elif cur and line.strip():
            # Long hex strings wrap onto continuation lines
            suffix, vtype, parts = cur
            parts.append(line)
            out[suffix] = _value(vtype, " ".join(parts))
    return out


def text(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").replace("\x00", "").strip()
    return str(v or "").strip()


def mac(v):
    """Format 6 bytes (or a MAC-looking string) as aa:bb:cc:dd:ee:ff; '' if it isn't one."""
    if isinstance(v, bytes):
        if len(v) != 6 or not any(v) or v == b"\xff" * 6:
            return ""
        return ":".join(f"{b:02x}" for b in v)
    return norm_mac(v)


def norm_mac(s):
    h = re.sub(r"[^0-9a-f]", "", str(s or "").lower())
    if len(h) != 12 or h in ("000000000000", "ffffffffffff"):
        return ""
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def printable(v):
    return isinstance(v, bytes) and v and all(32 <= b < 127 for b in v)


def split_sections(raw):
    walks, meta = {}, {}
    name = None
    for line in raw.splitlines():
        if line.startswith("### "):
            parts = line[4:].split(" ", 1)
            if parts[0] == "walk":
                name = parts[1].strip()
                walks[name] = []
            else:
                meta[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
                name = None
        elif name:
            walks[name].append(line)
    return meta, {k: "\n".join(v) for k, v in walks.items()}


def natural_key(s):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s or "")]


def parse_snmp(raw):
    meta, walks = split_sections(raw)
    bases = dict(WALKS)
    w = {name: parse_walk(walks.get(name, ""), oid) for name, oid in bases.items()}
    warnings = []

    sysw = w["system"]
    sys_descr = text(sysw.get((1, 0)))
    sys_oid = text(sysw.get((2, 0)))
    sys_name = text(sysw.get((5, 0)))
    sys_location = text(sysw.get((6, 0)))
    m = re.match(r"\.?1\.3\.6\.1\.4\.1\.(\d+)", sys_oid)
    enterprise = int(m.group(1)) if m else 0

    # ENTITY-MIB: prefer the chassis entry
    ents = {}
    for (col, idx), v in w["entPhysical"].items():
        ents.setdefault(idx, {})[col] = v
    chassis = None
    for idx in sorted(ents):
        if ents[idx].get(5) == 3:
            chassis = ents[idx]
            break
    if not chassis:
        chassis = next((ents[i] for i in sorted(ents) if text(ents[i].get(11))), None) or {}
    model = text(chassis.get(13)) or ""
    serial = text(chassis.get(11)) or ""
    firmware = text(chassis.get(10)) or text(chassis.get(9)) or ""
    maker = ENTERPRISES.get(enterprise) or text(chassis.get(12))

    first_line = sys_descr.splitlines()[0] if sys_descr else ""
    model_number = ""
    mm = re.search(r"\b(EAP\d+[\w-]*|S[GFX]\d+[\w-]*|C\d{3,4}[\w-]*|WS-C[\w-]+|CRS[\w-]+|RB[\w-]+|"
                   r"US-[\w-]+|USW-[\w-]+|U6[\w-]*|UAP[\w-]*|GS\d+[\w-]*|TL-[\w-]+|SG\d+[\w-]*)", first_line)
    if mm and model and mm.group(1).lower() != model.lower():
        # e.g. sysDescr says SG300-52 while ENTITY-MIB has the part number SRW2048-K9
        model, model_number = mm.group(1), model
    elif mm and not model:
        model = mm.group(1)
    if not firmware:
        fm = re.search(r"(?:Version|ver\.?|firmware|SW:?)\s*:?\s*v?([\w.()-]*\d[\w.()-]*)", sys_descr, re.I)
        if fm:
            firmware = fm.group(1).rstrip(",")
    os_text = firmware
    if enterprise == NET_SNMP or first_line.startswith(("Linux", "FreeBSD")):
        bits = first_line.split()
        os_text = f"{bits[0]} {bits[2]}" if len(bits) > 2 else first_line
        maker = maker if enterprise != NET_SNMP else ""

    # Interfaces
    ifs = {}
    for (idx,), v in w["ifDescr"].items():
        ifs[idx] = {"ifindex": idx, "descr": text(v)}
    for key, name in (("ifType", "type"), ("ifSpeed", "speed_bps"), ("ifAdminStatus", "admin"),
                      ("ifOperStatus", "oper"), ("ifName", "name"), ("ifHighSpeed", "speed_mbps"),
                      ("ifAlias", "alias"), ("ifPhysAddress", "mac")):
        for suffix, v in w[key].items():
            if len(suffix) == 1:
                ifs.setdefault(suffix[0], {"ifindex": suffix[0], "descr": ""})[name] = v

    # Bridge port numbers -> ifIndex, and the VLAN each port is untagged in
    bport_if = {k[0]: v for k, v in w["bridgePort"].items() if len(k) == 1}
    vlan_names = {k[0]: text(v) for k, v in w["vlanName"].items() if len(k) == 1}
    pvid = {}
    for (bp,), v in ((k, v) for k, v in w["pvid"].items() if len(k) == 1):
        ifidx = bport_if.get(bp, bp)
        pvid[ifidx] = v

    own_macs = {mac(i.get("mac")) for i in ifs.values()} - {""}

    # MAC address table: Q-BRIDGE (per VLAN) if the switch has it, else the plain bridge table
    learned = {}
    def add_fdb(mac_bytes, bport, status):
        if status in (4,):  # self
            return
        mm = ":".join(f"{b:02x}" for b in mac_bytes)
        if mm in own_macs or not bport:
            return
        ifidx = bport_if.get(bport, bport)
        learned.setdefault(ifidx, set()).add(mm)
    if w["qfdbPort"]:
        for suffix, port in w["qfdbPort"].items():
            if len(suffix) == 7:
                add_fdb(bytes(suffix[1:]), port, w["qfdbStatus"].get(suffix, 3))
    else:
        for suffix, port in w["fdbPort"].items():
            if len(suffix) == 6:
                add_fdb(bytes(suffix), port, w["fdbStatus"].get(suffix, 3))

    # LLDP neighbours, keyed by the local port they were seen on
    loc = {}
    for (col, port), v in ((k, v) for k, v in w["lldpLocPort"].items() if len(k) == 2):
        loc.setdefault(port, {})[col] = v
    rems = {}
    for suffix, v in w["lldpRem"].items():
        if len(suffix) == 4:
            col, _, lport, ridx = suffix
            rems.setdefault((lport, ridx), {})[col] = v
    by_name = {}
    for idx, i in ifs.items():
        for k in ("name", "descr", "alias"):
            t = text(i.get(k)).lower()
            if t:
                by_name.setdefault(t, idx)
    neighbors = {}
    for (lport, _), r in rems.items():
        ifidx = None
        for col in (3, 4):  # local port id, local port description
            t = text(loc.get(lport, {}).get(col)).lower()
            if t and t in by_name:
                ifidx = by_name[t]
                break
        if ifidx is None:
            ifidx = lport if lport in ifs else bport_if.get(lport)
        if ifidx is None:
            continue
        port_id = r.get(7)
        neighbors[ifidx] = {
            "name": text(r.get(9)),
            "port": text(port_id) if printable(port_id) else mac(port_id),
            "port_desc": text(r.get(8)),
            "chassis": mac(r.get(5)) if not printable(r.get(5)) else text(r.get(5)),
            "descr": text(r.get(10))[:200],
        }

    arp = {}
    for suffix, v in w["arp"].items():
        if len(suffix) == 5:
            mm = mac(v)
            if mm:
                arp[mm] = ".".join(str(x) for x in suffix[1:])

    ports = []
    for idx in sorted(ifs):
        i = ifs[idx]
        kind = IF_KIND.get(i.get("type"), "other")
        # Many APs (Omada, MediaTek/Atheros based) report their radios as plain Ethernet
        if re.match(r"(ra|rax|rai|apcli|apclix|wlan|ath|wifi|wl)\d", text(i.get("name")) or i["descr"]):
            kind = "wifi"
        elif re.match(r"(br|vmbr|bridge)\d", text(i.get("name")) or i["descr"]):
            kind = "bridge"
        if kind == "loopback":
            continue
        name = text(i.get("name")) or i["descr"]
        speed = i.get("speed_mbps") or (int(i.get("speed_bps") or 0) // 1_000_000)
        vlan = pvid.get(idx)
        ports.append({
            "ifindex": idx, "name": name, "descr": i["descr"] if i["descr"] != name else "",
            "alias": text(i.get("alias")), "kind": kind, "speed": int(speed or 0),
            "admin": "up" if i.get("admin") == 1 else "down",
            "oper": OPER.get(i.get("oper"), "unknown"), "mac": mac(i.get("mac")),
            "vlan": f"{vlan}" + (f" ({vlan_names[vlan]})" if vlan in vlan_names and vlan_names[vlan] and
                                 vlan_names[vlan] not in (str(vlan), f"VLAN{vlan}", f"VLAN {vlan}") else "") if vlan else "",
            "learned": sorted(learned.get(idx, [])),
            "neighbor": neighbors.get(idx) or {},
        })
    ports.sort(key=lambda p: (p["kind"] not in ("ethernet", "wifi"), natural_key(p["name"])))

    eth = [p for p in ports if p["kind"] == "ethernet"]
    is_ap = any(p["kind"] == "wifi" for p in ports) or re.search(r"\b(EAP\d*|UAP|U6|access point|wireless)\b|\bAP\d",
                                                                    f"{model} {first_line}", re.I)
    if is_ap:
        asset_type = "Access point"
    elif len(bport_if) >= 5 or len(eth) >= 8:
        asset_type = "Switch"
    elif w["ipForwarding"].get((0,)) == 1 and enterprise != NET_SNMP:
        asset_type = "Router"
    elif enterprise == NET_SNMP:
        asset_type = "Server"
    else:
        asset_type = "Other"

    # The device's own MACs: a busy switch has one per port, so keep the management/VLAN ones there
    all_macs = [p["mac"] for p in ports if p["mac"]]
    if len(set(all_macs)) > 8:
        keep = [p["mac"] for p in ports if p["mac"] and p["kind"] not in ("ethernet", "wifi")]
        common = Counter(all_macs).most_common(1)
        macs = sorted(set(keep + ([common[0][0]] if common else [])))
    else:
        macs = sorted(set(all_macs))

    if not sys_name:
        warnings.append("The device didn't report a name (sysName); fill one in below.")
    if asset_type in ("Switch",) and not learned:
        warnings.append("No MAC address table came back, so connected devices can't be shown. "
                        "Some switches only expose it to SNMP views that include the BRIDGE-MIB.")

    target = meta.get("target", "")
    asset = {
        "name": sys_name.split(".")[0] if sys_name and not re.match(r"^\d+\.\d+\.\d+\.\d+$", sys_name) else (model or target),
        "hostname": sys_name if "." in sys_name else "",
        "os": os_text,
        "manufacturer": maker or "",
        "model": model or ("" if enterprise == NET_SNMP or first_line.startswith(("Linux", "FreeBSD")) else first_line[:60]),
        "model_number": model_number,
        "serial": serial,
        "asset_type": asset_type,
        "location": "",
        "ip": target.split(":")[0] if re.match(r"^\d+\.\d+\.\d+\.\d+", target) else "",
        "macs": macs,
    }
    if sys_location:
        asset["snmp_location"] = sys_location
    return {
        "kind": "network",
        "asset": asset,
        "components": [],
        "ports": ports,
        "arp": arp,
        "warnings": warnings,
        "sys_descr": sys_descr[:500],
    }
