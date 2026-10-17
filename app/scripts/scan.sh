#!/bin/sh
# Rigbook hardware scan for Linux. Prints raw hardware info in "### section" blocks.
# Run as root (or with sudo) so dmidecode can read memory, CPU and serial details.
#
#   curl -fsSL http://<rigbook>/scan.sh | sudo sh
#
# When downloaded from Rigbook, the result is also sent back to it for review.

SUBMIT_URL="__SUBMIT_URL__"

report() {
    echo "### rigbook-linux 1"
    echo "### meta"
    echo "uid=$(id -u)"
    echo "hostname=$(hostname 2>/dev/null || cat /etc/hostname)"
    echo "fqdn=$(hostname -f 2>/dev/null)"

    echo "### os-release"
    cat /etc/os-release 2>/dev/null

    echo "### proxmox"
    command -v pveversion >/dev/null 2>&1 && pveversion 2>/dev/null
    command -v proxmox-backup-manager >/dev/null 2>&1 && echo "pbs $(proxmox-backup-manager version 2>/dev/null)"

    echo "### dmi-sysfs"
    for f in sys_vendor product_name product_sku product_serial product_family \
             board_vendor board_name board_serial chassis_type; do
        printf '%s=%s\n' "$f" "$(cat /sys/class/dmi/id/$f 2>/dev/null)"
    done

    echo "### dmidecode"
    command -v dmidecode >/dev/null 2>&1 && dmidecode -t system -t baseboard -t chassis -t processor -t memory 2>&1

    echo "### lscpu"
    command -v lscpu >/dev/null 2>&1 && lscpu 2>/dev/null

    echo "### lscpu-p"
    command -v lscpu >/dev/null 2>&1 && lscpu -p=CPU,CORE,SOCKET,MAXMHZ 2>/dev/null

    echo "### cpuinfo"
    grep -m1 '^model name' /proc/cpuinfo 2>/dev/null

    echo "### lsblk"
    lsblk -J -b -d -o NAME,SIZE,ROTA,TRAN,MODEL,SERIAL,TYPE,RM 2>/dev/null

    echo "### lspci"
    command -v lspci >/dev/null 2>&1 && lspci -mm 2>/dev/null

    echo "### nvidia"
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=pci.bus_id,name,memory.total --format=csv,noheader 2>/dev/null

    echo "### drm-vram"
    for d in /sys/class/drm/card[0-9]*/device; do
        [ -r "$d/mem_info_vram_total" ] && echo "$(basename "$(readlink -f "$d")") $(cat "$d/mem_info_vram_total")"
    done

    echo "### ip"
    ip -o route get 1.1.1.1 2>/dev/null
    ip -o -4 addr show scope global 2>/dev/null
    echo "### ip-link"
    ip -o link show 2>/dev/null
    echo "### end"
}

if [ "$(id -u)" != "0" ] && [ -z "$RIGBOOK_NO_ROOT_WARNING" ]; then
    echo "warning: not running as root, memory and serial details will be missing" >&2
fi

case "$SUBMIT_URL" in
    http*)
        out=$(report)
        printf '%s\n' "$out"
        if command -v curl >/dev/null 2>&1; then
            printf '%s\n' "$out" | curl -fsS -X POST --data-binary @- "$SUBMIT_URL" >/dev/null \
                && echo "Sent to Rigbook, open it to review this scan." >&2 \
                || echo "Could not send to $SUBMIT_URL" >&2
        elif command -v wget >/dev/null 2>&1; then
            printf '%s\n' "$out" > /tmp/rigbook-scan.txt
            wget -q -O /dev/null --post-file=/tmp/rigbook-scan.txt "$SUBMIT_URL" \
                && echo "Sent to Rigbook, open it to review this scan." >&2 \
                || echo "Could not send to $SUBMIT_URL" >&2
            rm -f /tmp/rigbook-scan.txt
        else
            echo "No curl or wget found. Save the output above and upload it in Rigbook." >&2
        fi
        ;;
    *)
        report
        ;;
esac
