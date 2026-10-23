# Rigbook

A self-hosted homelab asset and parts inventory. **Assets** are machines and network gear. **Inventory** is every part, whether it's installed or on the shelf.

- **Asset pages** start with the asset's components (CPU, RAM, storage, NICs…). Network devices also get a port map.
- **Inventory pages** show where each part is installed and how many spares you have.
- **Hardware discovery:**
  - over SSH (Linux, FreeBSD/OPNsense, or Windows with OpenSSH Server);
  - by running a script on the machine, which posts the result back (this also works from a live USB);
  - over SNMP for switches and access points.
- **Every scan is reviewed** before it's saved, and you can edit anything first, for example correcting soldered RAM to LPDDR5. Rescans never overwrite what you recorded unless you tick the box.
- **Keyboards** have their own section: each board has a brand, model, size (60%, 75%, TKL…), key count and
  connection types. **Switches** are counted individually and **keycaps** are tracked by set. Putting 70 switches on a
  board takes them out of the on-hand count, and a board can mix several switch kinds and keycap sets.
- **Every list can be sorted** by clicking a column header. Text sorts A→Z then Z→A, and numbers and sizes sort
  smallest first then largest first.
- **Categories, fields and asset types** can be edited under Settings. Each category is listed under either
  Inventory (computer parts) or Keyboards.

Everything runs in one small container: FastAPI, plain JS with no build step, and SQLite.

---

## ⚠️ Secrets and state NOT in this repo

This folder holds everything needed to rebuild the container. These things were **left out on purpose** and have to be supplied again when you recreate it:

| What | Where it goes | Notes |
|---|---|---|
| **The `data/` directory** | `./data` (bind-mounted to `/data`) | `rigbook.db` holds every asset, part, install, scan, port map and setting. It's gitignored. **Back it up**, since it's the only state. To restore, copy it in before the first start. |
| **UI password** (optional) | `.env` → `RIGBOOK_PASSWORD=` | Empty means no login. The original deployment had none (LAN only). |
| **Public URL for scan scripts** (optional) | `.env` → `RIGBOOK_URL=` | Empty means the address you opened the UI on. Original: `http://192.168.1.216:8290`. |
| **Reverse proxy** | Nginx Proxy Manager, not this repo | The original was proxied as `http://inventory.jarvis/` → `192.168.1.216:8290`. Re-create the proxy host if you want that name back. No special settings are needed, because discovery runs as a background job so it can't hit a proxy timeout. |
| **SNMP credentials** | Entered in the UI per device | These are the community string (v1/v2c) or v3 user/auth/priv passwords. They're stored only if you tick **Remember** (in plaintext in `rigbook.db`) and the API never returns them. The original DB had none saved: the switch, `192.168.1.112`, uses v2c read-only, and the Omada APs use SNMPv3 pushed from the controller. |
| **Omada Open API credentials** | Entered under Settings → Omada controller | The client ID and secret, stored in plaintext in `rigbook.db`. The API never returns the secret. The original controller is `https://192.168.1.99:8043`. |
| **Proxmox API token** | Entered under Settings → Proxmox VE | The token ID and secret, stored in plaintext in `rigbook.db`. The API never returns the secret. The original host is `https://192.168.1.88:8006`, token `root@pam!rigbook` with PVEAuditor. |
| **SSH passwords** | Typed per scan | Never stored. |
| **Snipe-IT API key** | Command-line argument only | Only needed for the one-time importer (see below). It was never in the code. |

`.env` is gitignored, and `.env.example` is the template. No secrets were removed from the source code itself, because none were ever in it. The only site-specific values are example IPs used as input placeholders in `app/static/app.js`.

---

## Quick start (recreating from scratch)

```bash
git clone http://git.proxub/billy/rigbook.git
cd rigbook

cp .env.example .env            # optional: set RIGBOOK_PASSWORD / RIGBOOK_URL
mkdir -p data                   # or copy an old data/ folder in to restore
docker compose up -d --build
```

Open **http://&lt;host&gt;:8290**. On a fresh database, the default categories and asset types are created automatically.

The container runs as `1000:1000`. If your user has a different UID, change `user:` in `compose.yaml` or `chown` the `data/` folder.

## Adding machines

**Add asset** gives you four options:

| Method | Use it for |
|---|---|
| **Discover over SSH** | Linux (the password is also used for `sudo` so dmidecode can read memory and serial numbers), FreeBSD/OPNsense/pfSense (log in as root), or Windows with OpenSSH Server. |
| **Network device (SNMP)** | Switches, APs, routers. Reads the model, serial and firmware, every port, the MAC table, LLDP neighbours and ARP. Machines are matched to switch ports by MAC address. For Omada, point it at each AP; the controller pushes its SNMP settings to them. |
| **Run a script** | Wiped machines on a live USB, or boxes with no SSH. See below. |
| **Manual** | Anything else. |

Each result appears under **Scans** for review before it's saved.

### Scan scripts

```sh
curl -fsSL http://<host>:8290/scan.sh | sudo sh            # Linux
irm http://<host>:8290/scan.ps1 | iex                      # Windows PowerShell
curl -fsSL http://<host>:8290/scan-freebsd.sh | sh         # FreeBSD / OPNsense / pfSense, as root
```

Rigbook bakes its own address into each script it serves, so the result posts back automatically. You can also run a script offline and paste its output into the **Scans** page.

### FreeBSD / OPNsense

On OPNsense, log in over SSH or the console as root, choose option **8 (Shell)**, and run the `scan-freebsd.sh` line above. Alternatively, use **Discover over SSH** with the root login; Rigbook detects FreeBSD by itself.

What it reads:

- **System:** the board, serial and so on, from `kenv` SMBIOS data, or from `dmidecode` when it's installed (`pkg install dmidecode` if it's missing).
- **Hardware:** the CPU and memory from `sysctl`, and the disks from `geom disk list`.
- **NICs:** every PCI network function from `pciconf -lvc`, so a NIC is listed even when no driver is attached (it shows as `noneN`). MACs and link speeds come from `ifconfig -a -m`.
- **Onboard or card:** Rigbook follows each NIC up the device tree (`sysctl dev.*.%parent`) to the PCIe bridge above it. If that bridge reports a physical **slot**, the NIC is a card, and every port on that card is grouped into one component (for example *Intel I350, 4 ports, PCIe slot 1*). Otherwise it's **Onboard**. Intel chipset NICs such as the I219 are always onboard.
- **Each NIC component's notes** list its interface names with their MACs, plus the PCI IDs.

Because the default route on a firewall is its WAN, the asset's IP is taken from the LAN side.

If the BIOS reports an onboard port as a slot, or doesn't report a card's slot, the review screen shows a warning. You can fix the slot field there before saving.

## Network map

**Network map** in the sidebar draws the network as a tree, from the Internet through the firewall and switches out
to every machine. It's built from what the switch scans saw:

- **Placing machines:** a machine goes on the most specific switch port its MAC address shows up on, or on the port
  where LLDP names it.
- **Unknown devices:** MACs that don't match an asset are grouped per port. Common ones are labelled, such as
  Proxmox or Hyper-V VMs, Docker, and phones' private MACs, and any of them can be added as a device from the map.
  Unknown MACs on a port with only one known machine are drawn under that machine, since they're usually its VMs.
- **What scans can't see:** unmanaged switches, Wi-Fi clients and docks are set by hand. Add the device, then set
  **Connected to** (and optionally the port) on it, from its edit form, its Network card or the map's side panel.
- **Taking over a port:** a device set by hand as plugged into a managed switch's port takes over everything else the
  switch sees on that port. For example, an unmanaged switch on `gi18` collects every machine seen on `gi18`.
- **Wi-Fi links:** a device connected to an access point is drawn with a dashed line.
- **Matching by name:** a MAC that no asset has is matched to an asset whose name or hostname equals the name Omada or
  reverse DNS gives it, ignoring case and punctuation (`Billy's S25 Ultra` matches `Billy-s-S25-Ultra`). That keeps
  phones with randomized MACs placed without recording a MAC at all.
- **Names for unknown devices:** they're named from the Omada controller when it knows them, otherwise from reverse DNS
  on their IP (with OPNsense's Unbound registering DHCP leases, that gives names like `homeassistant`).

### Wi-Fi clients from an Omada controller

Omada APs don't list their clients over SNMP, so Rigbook asks the controller instead, through its Open API:

1. In the Omada controller, open **Settings → Platform Integration → Open API** (global settings, not a site's) and
   choose **Add New App**.
2. Name it `rigbook`, set **Mode** to **Client Credentials**, and pick a **Role**. **Viewer** is enough, since Rigbook
   only reads. Give it access to your site (or all sites), then save.
3. Copy the app's **Client ID** and **Client Secret**.
4. In Rigbook, go to **Settings → Omada controller** and enter the controller address (the software controller is
   `https://<ip>:8043`, an OC200/OC300 is `https://<ip>`), the client ID and the secret. **Save and test** logs in
   and syncs straight away.

Rigbook then syncs every 5 minutes. Each wireless client goes under the AP it's associated with, grouped by SSID, and
takes the controller's name for it. Wi-Fi devices move with each sync as they roam between APs or networks, and what Omada
sees beats an access point set by hand. A known device that drops off Wi-Fi stays at the AP and SSID it was last on,
faded and marked with when it was last seen, for 7 days. The APs have to be assets in Rigbook; they're matched by MAC, IP or name.

Click a device for its details and to change its connection, double-click to open it, drag to pan, and scroll to
zoom. Machines that are in use but couldn't be placed are listed under the map, with the reason.

### VMs and containers from Proxmox VE

Guests share their host's switch port, so without this they show as a pile of unknown MACs. Rigbook reads every VM
and container from the Proxmox API, with a read-only token:

1. In Proxmox, go to **Datacenter → Permissions → API Tokens → Add**. Choose user `root@pam`, token ID `rigbook`,
   leave **Privilege Separation** ticked, and copy the secret it shows (it's only shown once).
2. Go to **Datacenter → Permissions → Add → API Token Permission**. Set path `/`, token `root@pam!rigbook`, role
   **PVEAuditor**, and keep **Propagate** ticked.
3. In Rigbook, go to **Settings → Proxmox VE** and enter `https://<host>:8006`, the token ID `root@pam!rigbook` and
   the secret.

From the shell, steps 1 and 2 are
`pveum user token add root@pam rigbook --privsep 1` and `pveum acl modify / --tokens 'root@pam!rigbook' --roles PVEAuditor`.

Rigbook syncs every 5 minutes. Each guest is drawn by name under the asset that is its node (matched by name, hostname
or IP), with its VMID, IPs and status; stopped guests are faded. A guest that's also an asset, matched by MAC or name,
is drawn as that asset under its host. **Add as asset** on a guest makes it one. Templates are skipped.

## Import from Snipe-IT (one-time)

```bash
docker exec rigbook python -m app.import_snipeit --url http://<snipe-it host> --key <API key> [--wipe]
```

The API key comes from Snipe-IT → your user → **Manage API keys**. It's used only for that run and isn't saved anywhere.

## Backups

```bash
docker exec rigbook python -c "import sqlite3; s=sqlite3.connect('/data/rigbook.db'); s.backup(sqlite3.connect('/data/rigbook-backup.db'))"
```

Or stop the container and copy `data/rigbook.db`.

## Layout

    Dockerfile              python:3.12-slim + net-snmp tools
    compose.yaml            port 8290, ./data volume, settings from .env
    app/main.py             API (FastAPI), background discovery jobs, port/MAC matching
    app/scan.py             SSH runner and the Linux / FreeBSD / Windows scan parsers
    app/snmp.py             SNMP walker and parser (ENTITY, IF, BRIDGE/Q-BRIDGE, LLDP, ARP)
    app/omada.py            Omada controller Open API client and the 5-minute Wi-Fi client sync
    app/proxmox.py          Proxmox VE API client and the 5-minute VM/container sync
    app/scripts/            scan.sh, scan-freebsd.sh, scan.ps1 (run on the target machine)
    app/db.py               SQLite schema, migrations, default categories
    app/import_snipeit.py   Snipe-IT importer
    app/static/             UI (index.html, app.js, style.css)
