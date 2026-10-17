#!/bin/sh
# Rigbook hardware scan for FreeBSD (OPNsense, pfSense, TrueNAS CORE). Prints raw hardware info in "### section" blocks.
# Run as root so dmidecode and kenv can read serial numbers.
#
#   fetch -qo - http://<rigbook>/scan-freebsd.sh | sh
#
# When downloaded from Rigbook, the result is also sent back to it for review.

SUBMIT_URL="__SUBMIT_URL__"

report() {
    echo "### rigbook-freebsd 1"
    echo "### meta"
    echo "uid=$(id -u)"
    echo "hostname=$(hostname -s 2>/dev/null)"
    echo "fqdn=$(hostname 2>/dev/null)"

    echo "### os"
    command -v opnsense-version >/dev/null 2>&1 && echo "opnsense=$(opnsense-version 2>/dev/null)"
    [ -r /etc/version ] && echo "pfsense=$(cat /etc/version)"
    echo "freebsd=$(freebsd-version 2>/dev/null || uname -r)"

    # SMBIOS strings the loader puts in the kernel environment; readable without dmidecode
    echo "### kenv"
    kenv 2>/dev/null | grep '^smbios\.'

    echo "### dmidecode"
    command -v dmidecode >/dev/null 2>&1 && dmidecode -t system -t baseboard -t chassis -t processor -t memory 2>&1

    echo "### sysctl"
    sysctl hw.model hw.ncpu hw.physmem kern.smp.cpus kern.smp.cores kern.smp.threads_per_core \
        dev.cpu.0.freq_levels 2>/dev/null

    echo "### disks"
    geom disk list 2>/dev/null

    # Every PCI function with its driver, name and capabilities. "slot" on a bridge marks a physical slot,
    # which is how cards are told apart from onboard chips.
    echo "### pciconf"
    pciconf -lvc 2>/dev/null

    # The parent of each device, to walk from a NIC up to the bridge (slot) it sits behind
    echo "### devtree"
    sysctl dev 2>/dev/null | grep -E '^dev\.[a-z0-9_]+\.[0-9]+\.%(parent|location):'

    echo "### ifconfig"
    ifconfig -a -m 2>/dev/null

    echo "### route"
    route -n get default 2>/dev/null
    echo "### end"
}

if [ "$(id -u)" != "0" ] && [ -z "$RIGBOOK_NO_ROOT_WARNING" ]; then
    echo "warning: not running as root, memory and serial details will be missing" >&2
fi

case "$SUBMIT_URL" in
    http*)
        out=$(report)
        printf '%s\n' "$out"
        # OPNsense and pfSense ship curl; base FreeBSD's fetch(1) can't POST
        if command -v curl >/dev/null 2>&1; then
            printf '%s\n' "$out" | curl -fsS -X POST --data-binary @- "$SUBMIT_URL" >/dev/null \
                && echo "Sent to Rigbook, open it to review this scan." >&2 \
                || echo "Could not send to $SUBMIT_URL" >&2
        else
            echo "No curl found. Save the output above and upload it in Rigbook." >&2
        fi
        ;;
    *)
        report
        ;;
esac
