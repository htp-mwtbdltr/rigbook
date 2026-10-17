"""Run hardware scans over SSH and turn their raw output into an asset + component list."""
import base64
import gzip
import json
import re
import shlex
from pathlib import Path

SCRIPTS = Path(__file__).parent / "scripts"


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def norm_mac(value):
    h = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    if len(h) != 12 or h in ("000000000000", "ffffffffffff"):
        return ""
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def human_size(num_bytes):
    """Round a byte count to the marketing size (256GB, 1TB, 1.5TB)."""
    gb = float(num_bytes) / 1e9
    if gb >= 1000:
        tb = round(gb / 1000, 1)
        return f"{tb:g}TB"
    return f"{round(gb)}GB"


def vram_label(num_bytes):
    gb = float(num_bytes or 0) / 2**30
    return gb_label(round(gb)) if gb >= 0.75 else ""


def gb_label(gb):
    gb = float(gb)
    return f"{gb:g}GB" if gb < 1024 else f"{gb / 1024:g}TB"


def clean_cpu_name(name):
    """'12th Gen Intel(R) Core(TM) i7-1260P' -> 'i7-1260P', 'AMD Ryzen 5 5600X 6-Core Processor' -> 'Ryzen 5 5600X'."""
    n = name or ""
    n = re.sub(r"\(R\)|\(TM\)|\(tm\)|®|™", "", n)
    n = re.sub(r"\d+(st|nd|rd|th) Gen ", "", n)
    n = re.sub(r"\s*@.*$", "", n)
    n = re.sub(r"\b(CPU|Processor)\b", "", n)
    n = re.sub(r"\b\d+-Core\b", "", n)
    n = re.sub(r"^\s*(Intel|AMD)\s+", "", n)
    n = re.sub(r"^Core\s+(?=[im]\d)", "", n)      # "Core i5-8500T" -> "i5-8500T", keeps "Core Ultra 7"
    n = re.sub(r"\s+with Radeon.*$", "", n)
    return re.sub(r"\s+", " ", n).strip() or (name or "").strip()


def cpu_vendor(text):
    t = (text or "").lower()
    if "intel" in t:
        return "Intel"
    if "amd" in t or "authenticamd" in t:
        return "AMD"
    return (text or "").strip()


def short_vendor(text):
    t = (text or "").lower()
    for key, name in (("realtek", "Realtek"), ("broadcom", "Broadcom"), ("mellanox", "Mellanox"),
                      ("aquantia", "Aquantia"), ("marvell", "Marvell"), ("chelsio", "Chelsio"),
                      ("solarflare", "Solarflare"), ("nvidia", "NVIDIA"), ("intel", "Intel"), ("advanced micro", "AMD"), ("amd", "AMD"),
                      ("ati", "AMD"), ("matrox", "Matrox"), ("aspeed", "ASPEED")):
        if key in t:
            return name
    return (text or "").strip()


JUNK_VENDOR = re.compile(r"to be filled|system manufacturer|system product|default string|not specified|o\.e\.m|^none$|^$", re.I)


def real(value):
    """Blank out placeholder strings that firmware likes to report."""
    v = (value or "").strip()
    if JUNK_VENDOR.search(v) or re.fullmatch(r"[0x\s]*|unknown|n/a|0123456789|serial ?number", v, re.I):
        return ""
    return v


CHASSIS_TYPES = {
    3: "Desktop", 4: "SFF PC", 5: "Desktop", 6: "SFF PC", 7: "Desktop", 13: "Desktop", 15: "SFF PC", 16: "SFF PC",
    8: "Laptop", 9: "Laptop", 10: "Laptop", 14: "Laptop", 30: "Laptop", 31: "Laptop", 32: "Laptop",
    17: "Server", 23: "Server", 28: "Server", 29: "Server", 35: "Mini PC", 36: "Mini PC",
}

RAM_TYPES_SMBIOS = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3",
                    30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}


def comp(category, name, qty=1, include=True, specs=None, slot="", serial="", notes="", manufacturer="", model_number=""):
    return {
        "category": category, "name": name, "qty": qty, "include": include,
        "specs": {k: v for k, v in (specs or {}).items() if v not in (None, "")},
        "slot": slot or "", "serial": real(serial), "notes": notes or "",
        "manufacturer": manufacturer or "", "model_number": model_number or "",
    }


def ram_components(sticks, warnings):
    """sticks: dicts with gb, type, form, speed, slot, serial, maker, part.  Collapses soldered memory into one row."""
    soldered = [s for s in sticks if s["form"] == "Soldered" or s["type"].startswith("LP")]
    socketed = [s for s in sticks if s not in soldered]
    out = []
    for s in socketed:
        out.append(comp("ram", "", specs={"capacity": gb_label(s["gb"]), "type": s["type"], "form_factor": s["form"],
                                          "speed": s["speed"]},
                        slot=s["slot"], serial=s["serial"],
                        notes=" ".join(x for x in (s["maker"], s["part"]) if x)))
    if soldered:
        total = sum(s["gb"] for s in soldered)
        mtype = soldered[0]["type"] or ""
        reported = f"{len(soldered)} × {gb_label(soldered[0]['gb'])} {mtype}".strip()
        if mtype and not mtype.startswith("LP") and len(soldered) >= 4:
            mtype = "LP" + mtype
            warnings.append(f"Soldered memory reported as {reported}; combined into {gb_label(total)} {mtype}. "
                            "LPDDR is a guess, check it.")
        elif len(soldered) > 1:
            warnings.append(f"Soldered memory reported as {reported}; combined into one {gb_label(total)} entry.")
        out.append(comp("ram", "", specs={"capacity": gb_label(total), "type": mtype, "form_factor": "Soldered",
                                          "speed": soldered[0]["speed"]},
                        slot="Onboard", notes=" ".join(x for x in (soldered[0]["maker"], soldered[0]["part"]) if x)))
    return out


def disk_component(size, media, interface, model, serial, slot):
    virtual = bool(re.search(r"qemu|vmware|virtual|vbox", model or "", re.I))
    specs = {"capacity": human_size(size), "media": media, "interface": interface}
    if interface == "NVMe":
        specs["form_factor"] = "M.2"
    return comp("storage", "", include=not virtual, specs=specs, slot=slot, serial=serial,
                notes=(model or "").strip())


# ---------------------------------------------------------------- linux

def split_sections(text):
    sections, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^### (\S+)", line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def parse_kv(text, sep="="):
    out = {}
    for line in text.splitlines():
        if sep in line:
            k, v = line.split(sep, 1)
            out[k.strip()] = v.strip()
    return out


def parse_dmidecode(text):
    """Returns list of (type_number, {key: value}) blocks."""
    blocks = []
    for chunk in re.split(r"\n\s*\n", text):
        m = re.search(r"DMI type (\d+)", chunk)
        if not m:
            continue
        fields = {}
        for line in chunk.splitlines():
            mm = re.match(r"^\t([^\t:][^:]*):\s*(.*)$", line)
            if mm:
                fields[mm.group(1).strip()] = mm.group(2).strip()
        blocks.append((int(m.group(1)), fields))
    return blocks


def parse_size_gb(text):
    m = re.match(r"(\d+(?:\.\d+)?)\s*(MB|GB|TB|kB)", text or "")
    if not m:
        return 0
    n, unit = float(m.group(1)), m.group(2)
    return {"kB": n / 1024 / 1024, "MB": n / 1024, "GB": n, "TB": n * 1024}[unit]


def mhz_to_ghz(mhz):
    try:
        v = float(re.sub(r"[^\d.]", "", str(mhz)))
    except ValueError:
        return ""
    return f"{v / 1000:.2f}".rstrip("0").rstrip(".") if v > 0 else ""


def base_clock(model_name):
    """Older CPU names carry the base clock ('... @ 2.40GHz'); newer ones don't, so leave it blank."""
    m = re.search(r"@\s*([\d.]+)\s*GHz", model_name or "")
    return m.group(1).rstrip("0").rstrip(".") if m else ""


JEDEC = {"80ce": "Samsung", "00ce": "Samsung", "80ad": "SK Hynix", "00ad": "SK Hynix", "802c": "Micron",
         "002c": "Micron", "859b": "Crucial", "059b": "Crucial", "0198": "Kingston", "8198": "Kingston",
         "04cd": "G.Skill", "84cd": "G.Skill", "029e": "Corsair", "829e": "Corsair", "04f1": "TeamGroup",
         "84f1": "TeamGroup", "0443": "Ramaxel", "8443": "Ramaxel", "051a": "ADATA", "851a": "ADATA",
         "8551": "Qimonda", "80b0": "Nanya", "830b": "Nanya", "0b03": "Nanya", "8983": "Hynix"}


def ram_maker(text):
    t = real(text)
    if re.fullmatch(r"[0-9A-Fa-f]{4,}", t):
        return JEDEC.get(t[:4].lower(), t)
    return t


def gpu_name_from_lspci(vendor, device):
    v = short_vendor(vendor)
    m = re.search(r"\[(.+)\]", device)
    d = m.group(1) if m else device
    if not d.lower().startswith(v.lower()):
        d = f"{v} {d}"
    return v, d


AMD_IGPU = re.compile(r"Radeon (\(TM\) )?(Vega|Graphics)|Renoir|Cezanne|Lucienne|Barcelo|Rembrandt|Phoenix|"
                      r"Raphael|Picasso|Raven|Hawk Point|Strix|Granite Ridge|Mendocino|Van Gogh|Rembrandt", re.I)


def is_integrated(vendor, name, bus):
    if vendor == "Intel":
        return not re.search(r"Arc (A|B)\d", name)
    if vendor == "AMD":
        return bool(AMD_IGPU.search(name))
    return vendor in ("ASPEED", "Matrox")


def dmi_sticks(dmi):
    """Memory devices (DMI type 17) in the shape ram_components takes."""
    sticks = []
    for n, f in dmi:
        if n != 17:
            continue
        gb = parse_size_gb(f.get("Size", ""))
        if not gb:
            continue
        form = {"SODIMM": "SODIMM", "DIMM": "DIMM", "Row Of Chips": "Soldered"}.get(f.get("Form Factor", ""), "")
        mtype = f.get("Type", "")
        mtype = "" if mtype in ("Unknown", "Other") else mtype
        speed = re.sub(r"[^\d]", "", f.get("Speed", "")) or re.sub(r"[^\d]", "", f.get("Configured Memory Speed", ""))
        sticks.append({"gb": gb, "type": mtype, "form": form, "speed": speed,
                       "slot": f.get("Locator", ""), "serial": f.get("Serial Number", ""),
                       "maker": ram_maker(f.get("Manufacturer", "")), "part": real(f.get("Part Number", ""))})
    return sticks


# Interfaces that don't belong to the machine itself (containers, VMs, VPNs)
VIRTUAL_IF = r"(lo$|docker|br-|veth|virbr|cni|flannel|tailscale|zt|lxc|wg|tap|fwbr|fwpr|fwln|vnet|kube|cali|vxlan|bond\d+\.)"


def parse_linux(raw):
    s = split_sections(raw)
    warnings = []
    meta = parse_kv(s.get("meta", ""))
    sysfs = parse_kv(s.get("dmi-sysfs", ""))
    osr = parse_kv(s.get("os-release", ""))
    dmi = parse_dmidecode(s.get("dmidecode", ""))
    if meta.get("uid") != "0":
        warnings.append("Scan did not run as root: memory details and serial numbers may be missing.")
    elif not dmi:
        warnings.append("dmidecode is not installed, so memory and serial details are missing.")

    def first(t):
        return next((f for n, f in dmi if n == t), {})

    system, board, chassis = first(1), first(2), first(3)

    # OS
    os_name = osr.get("PRETTY_NAME", "").strip('"')
    prox = s.get("proxmox", "").strip()
    m = re.search(r"pve-manager/(\d+(?:\.\d+)?)", prox)
    if m:
        os_name = f"Proxmox VE {m.group(1)}"
    elif prox.startswith("pbs"):
        m = re.search(r"(\d+\.\d+)", prox)
        os_name = "Proxmox Backup Server" + (f" {m.group(1)}" if m else "")

    vendor = real(system.get("Manufacturer") or sysfs.get("sys_vendor"))
    product = real(system.get("Product Name") or sysfs.get("product_name"))
    try:
        ctype = int(sysfs.get("chassis_type") or 0)
    except ValueError:
        ctype = 0
    asset_type = CHASSIS_TYPES.get(ctype, "")
    if not vendor:
        asset_type = "Custom PC"

    ip_text = s.get("ip", "")
    m = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", ip_text)
    ips = [m.group(1)] if m else []
    for line in ip_text.splitlines():
        mm = re.match(r"\d+:\s+(\S+)\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if mm and not re.match(VIRTUAL_IF, mm.group(1)):
            ips.append(mm.group(2))
    macs = []
    for line in s.get("ip-link", "").splitlines():
        mm = re.match(r"\d+:\s+([^:@\s]+)[^\n]*link/ether ([0-9a-f:]{17})", line)
        if mm and not re.match(VIRTUAL_IF, mm.group(1)) and mm.group(2) not in macs:
            macs.append(mm.group(2))
    fqdn = meta.get("fqdn") or ""
    hostname = fqdn if "." in fqdn and fqdn != "localhost" else meta.get("hostname", "")
    asset = {
        "name": meta.get("hostname", "").split(".")[0],
        "hostname": hostname,
        "os": os_name,
        "manufacturer": vendor,
        "model": product,
        "model_number": real(system.get("SKU Number") or sysfs.get("product_sku")),
        "serial": real(system.get("Serial Number") or sysfs.get("product_serial")),
        "asset_type": asset_type,
        "ip": ips[0] if ips else "",
        "macs": macs,
    }

    components = []

    # CPU
    lscpu = parse_kv(s.get("lscpu", ""), ":")
    cpu_lines = [l.split(",") for l in s.get("lscpu-p", "").splitlines() if l and not l.startswith("#")]
    max_mhz = max((float(l[3]) for l in cpu_lines if len(l) > 3 and l[3]), default=0)
    procs = [f for n, f in dmi if n == 4 and "Populated" in f.get("Status", "Populated")
             and f.get("Version") and not f.get("Version", "").startswith("Not")]
    model_name = lscpu.get("Model name") or re.sub(r"^model name\s*:\s*", "", s.get("cpuinfo", "").strip())
    if procs:
        for p in procs:
            raw_name = p.get("Version") or model_name
            if model_name and len(model_name) > len(raw_name):
                raw_name = model_name
            socket = p.get("Upgrade", "")
            socket = re.sub(r"^Socket\s+", "", socket) if re.search(r"LGA|AM\d|BGA|FC|TR|SP\d|sTR", socket) else ""
            components.append(comp("cpu", clean_cpu_name(raw_name), manufacturer=cpu_vendor(p.get("Manufacturer") or raw_name),
                                   model_number=raw_name.strip(), slot=p.get("Socket Designation", ""), specs={
                                       "cores": p.get("Core Count") or "",
                                       "threads": p.get("Thread Count") or "",
                                       "base_clock": base_clock(raw_name),
                                       "boost_clock": mhz_to_ghz(max_mhz) if max_mhz else "",
                                       "socket": socket}))
    elif model_name:
        sockets = len({l[2] for l in cpu_lines if len(l) > 2}) or 1
        cores = len({(l[1], l[2]) for l in cpu_lines if len(l) > 2})
        components.append(comp("cpu", clean_cpu_name(model_name), qty=sockets, manufacturer=cpu_vendor(model_name),
                               model_number=model_name.strip(), specs={
                                   "cores": str(cores // sockets) if cores else "",
                                   "threads": str(len(cpu_lines) // sockets) if cpu_lines else "",
                                   "boost_clock": mhz_to_ghz(max_mhz) if max_mhz else ""}))

    # Memory
    components += ram_components(dmi_sticks(dmi), warnings)

    # Storage
    try:
        devices = json.loads(s.get("lsblk", "") or "{}").get("blockdevices", [])
    except ValueError:
        devices = []
        warnings.append("Could not read the disk list (lsblk).")
    for d in devices:
        name = d.get("name", "")
        if d.get("type") != "disk" or str(d.get("rm")).lower() in ("1", "true") or d.get("tran") == "usb":
            continue
        if re.match(r"(loop|zram|ram|sr|md|dm-|nbd|rbd|zd)", name):
            continue
        size = int(d.get("size") or 0)
        if not size:
            continue
        interface = {"nvme": "NVMe", "sata": "SATA", "sas": "SAS", "ata": "SATA"}.get(d.get("tran") or "", "")
        if not interface and name.startswith("nvme"):
            interface = "NVMe"
        rota = str(d.get("rota")).lower() in ("1", "true")
        components.append(disk_component(size, "HDD" if rota else "SSD", interface, d.get("model"), d.get("serial"), name))

    # GPU
    nv = {}
    for line in s.get("nvidia", "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            nv[parts[0].lower()[-7:]] = (parts[1], parts[2])
    vram = {}
    for line in s.get("drm-vram", "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            vram[parts[0][-7:]] = int(parts[1])
    for line in s.get("lspci", "").splitlines():
        try:
            f = shlex.split(line)
        except ValueError:
            continue
        if len(f) < 4 or not re.search(r"VGA|3D|Display", f[1]):
            continue
        bus = f[0][-7:]
        vendor, name = gpu_name_from_lspci(f[2], f[3])
        mem = ""
        if bus.lower() in nv:
            name, mem = nv[bus.lower()]
            m = re.match(r"(\d+)", mem)
            mem = gb_label(round(int(m.group(1)) / 1024)) if m else ""
        elif bus in vram:
            mem = vram_label(vram[bus])
        integrated = is_integrated(vendor, name, bus)
        components.append(comp("gpu", name, include=not integrated, manufacturer=vendor, slot=f[0],
                               specs={"vram": mem}, notes="Integrated" if integrated else ""))

    # Motherboard: only interesting on self-built machines
    board_name = real(board.get("Product Name") or sysfs.get("board_name"))
    if board_name:
        components.append(comp("motherboard", board_name, include=not vendor,
                               manufacturer=real(board.get("Manufacturer") or sysfs.get("board_vendor")),
                               serial=board.get("Serial Number") or sysfs.get("board_serial")))
    return {"asset": asset, "components": components, "warnings": warnings}


# ---------------------------------------------------------------- freebsd

# NICs built into Intel chipsets; always onboard whatever the bridge says
PCH_NIC = re.compile(r"Ethernet Connection|\bI21[789]|\b8257[79]|\b8256[67]", re.I)


def parse_pciconf(text):
    """pciconf -lvc -> list of dicts: name, domain, bus, slot, func, cls, vid, did, vendor, device, caps."""
    devs, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(\S+?)@pci(\d+):(\d+):(\d+):(\d+):\s+class=0x([0-9a-f]{6})(.*)$", line)
        if m:
            ids = dict(re.findall(r"\b(vendor|device)=0x([0-9a-f]{4})", m.group(7)))
            cur = {"name": m.group(1), "domain": int(m.group(2)), "bus": int(m.group(3)), "slot": int(m.group(4)),
                   "func": int(m.group(5)), "cls": m.group(6), "vid": ids.get("vendor", ""), "did": ids.get("device", ""),
                   "vendor": "", "device": "", "caps": ""}
            devs.append(cur)
            continue
        if not cur:
            continue
        mm = re.match(r"^\s+(vendor|device)\s+=\s+'(.*)'\s*$", line)
        if mm:
            cur[mm.group(1)] = mm.group(2)
        else:
            cur["caps"] += line + "\n"
    return devs


def parse_ifconfig(text):
    """ifconfig -a -m -> {ifname: {mac, inet, media}}; mac prefers the burned-in hwaddr."""
    ifs, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^(\S+): flags=", line)
        if m:
            cur = ifs.setdefault(m.group(1), {"ether": "", "hwaddr": "", "inet": [], "media": []})
            continue
        if not cur:
            continue
        line = line.strip()
        mm = re.match(r"(ether|hwaddr) ([0-9a-f:]{17})", line)
        if mm:
            cur[mm.group(1)] = mm.group(2)
        mm = re.match(r"inet (\d+\.\d+\.\d+\.\d+)", line)
        if mm:
            cur["inet"].append(mm.group(1))
        mm = re.match(r"media (\d+)(G?)base", line, re.I)
        if mm:
            cur["media"].append(int(mm.group(1)) * (1000 if mm.group(2) else 1))
    for v in ifs.values():
        v["mac"] = norm_mac(v["hwaddr"] or v["ether"])
    return ifs


def speed_label(mbps):
    if not mbps:
        return ""
    return f"{mbps / 1000:g}G" if mbps >= 1000 else f"{mbps}M"


def speed_from_name(name):
    m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*-?\s*G(?:igabit|bE|b/s|bps)\b", name, re.I)
    if m:
        return f"{float(m.group(1)):g}G"
    if re.search(r"Gigabit", name, re.I):
        return "1G"
    return "100M" if re.search(r"Fast Ethernet", name, re.I) else ""


def freebsd_nics(pci, parents, ifs, warnings):
    """One component per physical NIC: each card in a slot, and each onboard model (ports counted)."""
    by_name = {d["name"]: d for d in pci}

    def slot_above(dev):
        """Walk device -> pciN -> pcibM -> ... and return (bridge, slot number) for the first slotted bridge.
        Returns None when the tree was walked without finding one, False when the tree is unknown."""
        cur, seen, walked = dev, set(), False
        while cur and cur not in seen:
            seen.add(cur)
            bus = parents.get(cur, "")
            if not bus.startswith("pci") or bus.startswith("pcib"):
                break
            bridge = parents.get(bus, "")
            b = by_name.get(bridge)
            if not b:
                break
            walked = True
            m = re.search(r"^\s+slot (\d+)", b["caps"], re.M)
            if m:
                return bridge, int(m.group(1))
            cur = bridge
        return None if walked else False

    groups = {}
    for d in pci:
        if not d["cls"].startswith("02"):
            continue
        name = d["device"] or f"PCI device {d['vid']}:{d['did']}"
        vendor = short_vendor(d["vendor"])
        if vendor and not name.lower().startswith(vendor.lower()):
            name = f"{vendor} {name}"
        found = slot_above(d["name"]) if not d["name"].startswith("none") else False
        if found is False and d["bus"] > 0 and not PCH_NIC.search(name):
            # No driver attached or no device tree: fall back to the bridge whose child bus number matches
            bridge = parents.get(f"pci{d['bus']}", "")
            b = by_name.get(bridge)
            m = b and re.search(r"^\s+slot (\d+)", b["caps"], re.M)
            found = (bridge, int(m.group(1))) if m else (None if b else False)
        if PCH_NIC.search(name):
            found = None
        if found:
            key, slot = ("card", found[0]), f"PCIe slot {found[1]}"
        elif found is None or d["bus"] == 0:
            key, slot = ("onboard", d["vid"], d["did"]), "Onboard"
        else:
            key, slot = ("bus", d["domain"], d["bus"], d["slot"]), f"PCI {d['domain']}:{d['bus']}:{d['slot']}"
            warnings.append(f"Could not tell whether {name} ({d['name']}) is onboard or on a card. Check it.")
        g = groups.setdefault(key, {"name": name, "vendor": vendor, "slot": slot, "ports": [], "ids": set()})
        g["ports"].append(d)
        g["ids"].add(f"{d['vid']}:{d['did']}")

    out = []
    for g in groups.values():
        ports, mbps = [], 0
        for d in sorted(g["ports"], key=lambda d: (d["bus"], d["slot"], d["func"])):
            i = ifs.get(d["name"], {})
            mbps = max([mbps, *i.get("media", [])])
            if d["name"].startswith("none"):
                ports.append(f"{d['name']} (no driver)")
            else:
                ports.append(" ".join(x for x in (d["name"], i.get("mac", "")) if x))
        notes = ", ".join(ports) + " · PCI " + ", ".join(sorted(g["ids"]))
        out.append(comp("nic", g["name"], manufacturer=g["vendor"], slot=g["slot"], notes=notes,
                        specs={"speed": speed_label(mbps) or speed_from_name(g["name"]), "ports": str(len(g["ports"]))}))
    out.sort(key=lambda c: (c["slot"] != "Onboard", c["slot"]))
    return out


def parse_freebsd(raw):
    s = split_sections(raw)
    warnings = []
    meta = parse_kv(s.get("meta", ""))
    osv = parse_kv(s.get("os", ""))
    kenv = {k: v.strip('"') for k, v in parse_kv(s.get("kenv", "")).items()}
    sysctl = parse_kv(s.get("sysctl", ""), ":")
    dmi = parse_dmidecode(s.get("dmidecode", ""))
    if meta.get("uid") != "0":
        warnings.append("Scan did not run as root: memory details and serial numbers may be missing.")
    elif not dmi:
        warnings.append("dmidecode is not installed (pkg install dmidecode), so memory details are missing.")

    def first(t):
        return next((f for n, f in dmi if n == t), {})

    system, board, chassis = first(1), first(2), first(3)
    appliance = ""
    if osv.get("opnsense"):
        appliance = osv["opnsense"]
        os_name = re.sub(r"\s*\(.*\)\s*$", "", appliance)
    elif osv.get("pfsense"):
        appliance = osv["pfsense"]
        os_name = f"pfSense {appliance}"
    else:
        os_name = f"FreeBSD {osv.get('freebsd', '')}".strip()

    vendor = real(system.get("Manufacturer") or kenv.get("smbios.system.maker"))
    product = real(system.get("Product Name") or kenv.get("smbios.system.product"))
    ctext = (chassis.get("Type") or kenv.get("smbios.chassis.type") or "").lower()
    asset_type = ("Firewall" if appliance else
                  "Laptop" if re.search(r"notebook|laptop|portable", ctext) else
                  "Server" if re.search(r"server|rack|blade", ctext) else
                  "Mini PC" if "mini" in ctext else "Desktop" if "desktop" in ctext or "tower" in ctext else "")

    # Interfaces and addresses
    pci = parse_pciconf(s.get("pciconf", ""))
    ifs = parse_ifconfig(s.get("ifconfig", ""))
    parents = {f"{m.group(1)}{m.group(2)}": m.group(3)
               for m in re.finditer(r"^dev\.([a-z0-9_]+)\.(\d+)\.%parent: (\S+)", s.get("devtree", ""), re.M)}
    physical = [d["name"] for d in pci if d["cls"].startswith("02") and d["name"] in ifs]
    m = re.search(r"interface: (\S+)", s.get("route", ""))
    wan = m.group(1) if m else ""
    # A firewall's default route is its WAN, so its LAN address is the one worth recording
    order = [n for n in ifs if n != wan] + [wan] if appliance else [wan] + [n for n in ifs if n != wan]
    ips = [ip for n in order for ip in ifs.get(n, {}).get("inet", []) if not ip.startswith("127.")]
    macs = []
    for n in physical:
        if ifs[n]["mac"] and ifs[n]["mac"] not in macs:
            macs.append(ifs[n]["mac"])

    fqdn = meta.get("fqdn", "")
    asset = {
        "name": meta.get("hostname", "").split(".")[0],
        "hostname": fqdn if "." in fqdn else meta.get("hostname", ""),
        "os": os_name,
        "manufacturer": vendor,
        "model": product,
        "model_number": real(system.get("SKU Number") or kenv.get("smbios.system.sku")),
        "serial": real(system.get("Serial Number") or kenv.get("smbios.system.serial")),
        "asset_type": asset_type,
        "ip": ips[0] if ips else "",
        "macs": macs,
    }

    components = []

    # CPU
    model_name = sysctl.get("hw.model", "")
    procs = [f for n, f in dmi if n == 4 and "Populated" in f.get("Status", "Populated")
             and f.get("Version") and not f.get("Version", "").startswith("Not")]
    for p in procs or [{}]:
        raw_name = model_name if len(model_name) >= len(p.get("Version", "")) else p["Version"]
        if not raw_name:
            continue
        socket = p.get("Upgrade", "")
        socket = re.sub(r"^Socket\s+", "", socket) if re.search(r"LGA|AM\d|BGA|FC|TR|SP\d|sTR", socket) else ""
        n = len(procs) or 1
        cores = p.get("Core Count") or (str(int(sysctl["kern.smp.cores"]) // n) if sysctl.get("kern.smp.cores", "").isdigit() else "")
        threads = p.get("Thread Count") or (str(int(sysctl["hw.ncpu"]) // n) if sysctl.get("hw.ncpu", "").isdigit() else "")
        components.append(comp("cpu", clean_cpu_name(raw_name), manufacturer=cpu_vendor(p.get("Manufacturer") or raw_name),
                               model_number=raw_name.strip(), slot=p.get("Socket Designation", ""), specs={
                                   "cores": cores, "threads": threads, "base_clock": base_clock(raw_name),
                                   "socket": socket}))

    # Memory
    sticks = dmi_sticks(dmi)
    if sticks:
        components += ram_components(sticks, warnings)
    elif sysctl.get("hw.physmem", "").isdigit():
        gb = int(sysctl["hw.physmem"]) / 2**30
        size = next((x for x in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512) if x >= gb * 0.97), round(gb))
        components.append(comp("ram", "", specs={"capacity": gb_label(size)}))
        if dmi:
            warnings.append("Memory type and slots are unknown; only the total was recorded.")

    # Storage
    for chunk in re.split(r"^Geom name: ", s.get("disks", ""), flags=re.M)[1:]:
        f = {k.strip(): v.strip() for k, v in re.findall(r"^\s*(?:\d+\. )?(\w+): (.*)$", chunk, re.M)}
        name = chunk.split("\n", 1)[0].strip()
        if re.match(r"(md|cd|zvol|mmcsd)\d", name):
            continue
        m = re.match(r"(\d+)", f.get("Mediasize", ""))
        size = int(m.group(1)) if m else 0
        if not size:
            continue
        descr = f.get("descr", "")
        if re.search(r"\bUSB\b|Flash Drive|Card Reader", descr, re.I):
            continue
        interface = "NVMe" if re.match(r"(nvd|nda)\d", name) else "SATA" if name.startswith("ada") else ""
        rot = f.get("rotationrate", "")
        media = "SSD" if rot == "0" or interface == "NVMe" or "SSD" in descr else "HDD" if rot.isdigit() else ""
        components.append(disk_component(size, media, interface, descr, f.get("ident"), name))

    # NICs: onboard and on cards
    components += freebsd_nics(pci, parents, ifs, warnings)

    board_name = real(board.get("Product Name") or kenv.get("smbios.planar.product"))
    if board_name:
        components.append(comp("motherboard", board_name, include=not vendor,
                               manufacturer=real(board.get("Manufacturer") or kenv.get("smbios.planar.maker")),
                               serial=board.get("Serial Number") or kenv.get("smbios.planar.serial")))
    return {"asset": asset, "components": components, "warnings": warnings}


# ---------------------------------------------------------------- windows

def parse_windows(data):
    warnings = []
    system = data.get("system") or {}
    board = data.get("board") or {}
    os_info = data.get("os") or {}
    vendor = real(system.get("Manufacturer"))
    chassis = data.get("chassis") or []
    if isinstance(chassis, int):
        chassis = [chassis]
    asset_type = next((CHASSIS_TYPES[c] for c in chassis if c in CHASSIS_TYPES), "")
    if not vendor:
        asset_type = "Custom PC"
    host = data.get("hostname", "")
    asset = {
        "name": host,
        "hostname": f"{host}.{data['domain']}".lower() if data.get("domain") else host,
        "os": re.sub(r"^Microsoft\s+", "", (os_info.get("Caption") or "")).strip(),
        "manufacturer": vendor,
        "model": real(system.get("Model")),
        "model_number": real(system.get("SystemSKUNumber")),
        "serial": real((data.get("bios") or {}).get("SerialNumber")),
        "asset_type": asset_type,
        "ip": (data.get("ips") or [""])[0],
        "macs": [m for m in (norm_mac(x) for x in (data.get("macs") if isinstance(data.get("macs"), list)
                                                   else [data.get("macs")])) if m],
    }

    def as_list(v):
        return v if isinstance(v, list) else ([v] if v else [])

    components = []
    for c in as_list(data.get("cpus")):
        name = (c.get("Name") or "").strip()
        socket = c.get("SocketDesignation") or ""
        components.append(comp("cpu", clean_cpu_name(name), manufacturer=cpu_vendor(c.get("Manufacturer") or name),
                               model_number=name, specs={
                                   "cores": str(c.get("NumberOfCores") or ""),
                                   "threads": str(c.get("NumberOfLogicalProcessors") or ""),
                                   "base_clock": mhz_to_ghz(c.get("MaxClockSpeed") or ""),
                                   "socket": socket if re.search(r"LGA|AM\d|BGA|FCLGA|sTR", socket) else ""}))

    mems = as_list(data.get("memory"))
    sticks = []
    for m in mems:
        gb = int(m.get("Capacity") or 0) / 2**30
        if not gb:
            continue
        mtype = RAM_TYPES_SMBIOS.get(int(m.get("SMBIOSMemoryType") or 0), "")
        ff = int(m.get("FormFactor") or 0)
        form = {8: "DIMM", 12: "SODIMM"}.get(ff, "")
        if not form and (mtype.startswith("LP") or len(mems) >= 4):
            form = "Soldered"
        sticks.append({"gb": gb, "type": mtype, "form": form, "speed": str(m.get("Speed") or ""),
                       "slot": m.get("DeviceLocator") or "", "serial": m.get("SerialNumber") or "",
                       "maker": ram_maker(m.get("Manufacturer")), "part": real((m.get("PartNumber") or "").strip())})
    components += ram_components(sticks, warnings)

    disks = as_list(data.get("disks"))
    if disks:
        for i, d in enumerate(disks):
            bus = d.get("BusType") or ""
            if re.search(r"USB|Virtual|Spaces|File Backed|iSCSI|SD|MMC", bus):
                continue
            interface = {"NVMe": "NVMe", "SATA": "SATA", "SAS": "SAS", "ATA": "SATA", "RAID": ""}.get(bus, "")
            media = "HDD" if d.get("MediaType") == "HDD" else "SSD" if d.get("MediaType") == "SSD" or interface == "NVMe" else ""
            components.append(disk_component(d.get("Size") or 0, media, interface, d.get("FriendlyName"),
                                             (d.get("SerialNumber") or "").strip(), f"Disk {i}"))
    else:
        for i, d in enumerate(as_list(data.get("diskdrive"))):
            if d.get("InterfaceType") == "USB":
                continue
            interface = "NVMe" if "nvme" in (d.get("Model") or "").lower() else ""
            components.append(disk_component(d.get("Size") or 0, "SSD" if interface else "", interface,
                                             d.get("Model"), (d.get("SerialNumber") or "").strip(), f"Disk {i}"))

    for g in as_list(data.get("gpus")):
        vendor = short_vendor(g.get("Vendor") or g.get("Name"))
        name = re.sub(r"\((R|TM)\)", "", g.get("Name") or "").strip()
        integrated = is_integrated(vendor, name, "")
        mem = g.get("Memory")
        components.append(comp("gpu", name, include=not integrated, manufacturer=vendor,
                               specs={"vram": vram_label(mem)},
                               notes="Integrated" if integrated else ""))

    board_name = real(board.get("Product"))
    if board_name:
        components.append(comp("motherboard", board_name, include=not vendor, manufacturer=real(board.get("Manufacturer")),
                               serial=board.get("SerialNumber")))
    return {"asset": asset, "components": components, "warnings": warnings}


# ---------------------------------------------------------------- entry points

def parse(raw):
    text = (raw or "").strip().lstrip("﻿")
    if text.startswith("{"):
        try:
            data = json.loads(text[:text.rfind("}") + 1])
        except ValueError as e:
            raise ScanError(f"Could not read the Windows scan output: {e}")
        if data.get("rigbook") != "windows":
            raise ScanError("This JSON does not look like a Rigbook scan.")
        return parse_windows(data)
    if text.startswith("### rigbook-snmp"):
        from .snmp import parse_snmp
        return parse_snmp(text)
    if "### rigbook-freebsd" in text:
        return parse_freebsd(text)
    if "### rigbook-linux" in text or "### meta" in text:
        return parse_linux(text)
    raise ScanError("This does not look like output from the Rigbook scan script.")


def script(name, submit_url=""):
    return (SCRIPTS / name).read_text().replace("__SUBMIT_URL__", submit_url)


def _powershell_command():
    body = "\n".join(l for l in script("scan.ps1").splitlines() if not l.lstrip().startswith("#"))
    packed = base64.b64encode(gzip.compress(body.encode("utf-8"))).decode()
    wrapper = ("$s=New-Object IO.Compression.GzipStream([IO.MemoryStream][Convert]::FromBase64String('%s'),"
               "[IO.Compression.CompressionMode]::Decompress);iex (New-Object IO.StreamReader($s)).ReadToEnd()" % packed)
    encoded = base64.b64encode(wrapper.encode("utf-16-le")).decode()
    return f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def ssh_scan(host, port, username, password):
    """Connect over SSH, run the right scan script, return the raw output."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=int(port or 22), username=username, password=password or None,
                       timeout=10, banner_timeout=15, auth_timeout=15,
                       look_for_keys=not password, allow_agent=False)
    except paramiko.AuthenticationException:
        raise ScanError(f"SSH login to {host} as {username} failed (wrong username or password).")
    except Exception as e:
        raise ScanError(f"Could not connect to {host}:{port}: {e}")

    def run(cmd, stdin_text=None, timeout=120):
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        if stdin_text is not None:
            stdin.write(stdin_text)
            stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out, err

    try:
        code, out, _ = run("uname -s", timeout=20)
        if code == 0 and out.strip() == "Linux":
            payload = base64.b64encode(script("scan.sh").encode()).decode()
            inner = f"echo {payload} | base64 -d | RIGBOOK_NO_ROOT_WARNING=1 sh"
            code, uid, _ = run("id -u", timeout=20)
            if uid.strip() == "0":
                code, out, err = run(f"sh -c {shlex.quote(inner)}")
            else:
                code, out, err = run(f"sudo -S -k -p '' sh -c {shlex.quote(inner)}",
                                     stdin_text=(password or "") + "\n")
                if "### meta" not in out:
                    # No sudo rights (or wrong sudo password): fall back to an unprivileged scan
                    code, out, err = run(f"sh -c {shlex.quote(inner)}")
            if "### meta" not in out:
                raise ScanError(f"The scan script failed on {host}: {err.strip()[:500]}")
            return out
        if code == 0 and out.strip() == "FreeBSD":
            # OPNsense/pfSense: usually root already; b64decode is in FreeBSD base
            payload = base64.b64encode(script("scan-freebsd.sh").encode()).decode()
            inner = f"echo {payload} | b64decode -r | RIGBOOK_NO_ROOT_WARNING=1 sh"
            code, uid, _ = run("id -u", timeout=20)
            if uid.strip() == "0":
                code, out, err = run(f"sh -c {shlex.quote(inner)}")
            else:
                code, out, err = run(f"sudo -S -k -p '' sh -c {shlex.quote(inner)}",
                                     stdin_text=(password or "") + "\n")
                if "### meta" not in out:
                    code, out, err = run(f"sh -c {shlex.quote(inner)}")
            if "### meta" not in out:
                raise ScanError(f"The scan script failed on {host}: {err.strip()[:500]}")
            return out
        if code == 0 and out.strip():
            raise ScanError(f"{host} runs {out.strip()}; only Linux, FreeBSD and Windows are supported.")
        code, out, err = run(_powershell_command(), timeout=180)
        start = out.find("{")
        if start < 0:
            raise ScanError(f"The Windows scan failed on {host}: {(err or out).strip()[:500]}")
        return out[start:].strip()
    finally:
        client.close()
