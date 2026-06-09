#!/bin/bash

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Status indicators
SUCCESS="[${GREEN}\xe2\x9c\x93${NC}]"
FAILURE="[${RED}\xe2\x9c\x97${NC}]"
WARNING="[${YELLOW}!${NC}]"

print_section_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
    echo -e "${BLUE}$(printf '=%.0s' {1..40})${NC}"
}

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------
# Globals populated by detect_platform()
PLATFORM="UNKNOWN"      # MILAN | GENOA | TURIN | UNKNOWN
PLATFORM_ZEN=""         # Zen3 | Zen4 | Zen5
EXPECTED_ASIDS=""       # informational: documented ASID count for the platform

detect_platform() {
    print_section_header "AMD Platform Detection"

    local family model vendor
    vendor=$(grep -m1 "vendor_id" /proc/cpuinfo | awk -F: '{print $2}' | tr -d ' ')
    family=$(grep -m1 "cpu family" /proc/cpuinfo | awk -F: '{print $2}' | tr -d ' ')
    # Match the bare "model" line, not "model name"
    model=$(grep -m1 -E "^model[[:space:]]*:" /proc/cpuinfo | awk -F: '{print $2}' | tr -d ' ')

    echo "Vendor: ${vendor}"
    echo "CPU family: ${family}  model: ${model}"

    if [ "$vendor" != "AuthenticAMD" ]; then
        echo -e "${FAILURE} Non-AMD CPU detected (${vendor}). SEV-SNP requires an AMD EPYC platform.${NC}"
        exit 1
    fi

    # Family 25 (0x19) = Zen3/Zen4 ; Family 26 (0x1A) = Zen5
    # Zen3  (Milan,  EPYC 7003)      : family 0x19, model 0x00-0x0F  -> SEV 3.0, 509 ASIDs
    # Zen4  (Genoa/Bergamo/Siena,    : family 0x19, model 0x10-0x1F  -> SEV 3.1, 1006 ASIDs
    #        EPYC 9004/8004)           and 0xA0-0xAF (Bergamo/Siena)
    # Zen5  (Turin,  EPYC 9005)      : family 0x1A
    if [ "$family" = "25" ]; then
        if [ "$model" -le 15 ]; then
            PLATFORM="MILAN"; PLATFORM_ZEN="Zen3"; EXPECTED_ASIDS="509 (SEV 3.0)"
        else
            PLATFORM="GENOA"; PLATFORM_ZEN="Zen4"; EXPECTED_ASIDS="1006 (SEV 3.1)"
        fi
    elif [ "$family" = "26" ]; then
        PLATFORM="TURIN";     PLATFORM_ZEN="Zen5"; EXPECTED_ASIDS="1006+ (SEV 3.1+)"
    else
        PLATFORM="UNKNOWN";   PLATFORM_ZEN="?"
    fi

    if [ "$PLATFORM" = "UNKNOWN" ]; then
        echo -e "${WARNING} Unrecognized AMD family/model. SEV-SNP requires EPYC 7003 (Zen 3) or newer.${NC}"
        echo -e "${WARNING} Proceeding with generic checks only.${NC}"
    else
        echo -e "${SUCCESS} Detected platform: ${PLATFORM} (${PLATFORM_ZEN}), expected ASIDs: ${EXPECTED_ASIDS}${NC}"
    fi
}

# ---------------------------------------------------------------------------
# OS / kernel prerequisites
#   SEV-SNP host support is upstream as of Linux 6.11; Ubuntu 25.04+ ships it.
# ---------------------------------------------------------------------------
check_os_prereqs() {
    local results=()
    local all_passed=true

    print_section_header "OS / Kernel Prerequisites"

    results+=("Distribution:")
    if [ -r /etc/os-release ]; then
        . /etc/os-release
        results+=("  ${ID} ${VERSION_ID}")
        local rel_num
        rel_num=$(echo "${VERSION_ID:-0}" | awk -F. '{printf "%d%02d", $1, $2}')
        if [ "${ID:-}" = "ubuntu" ] && [ "$rel_num" -ge 2504 ]; then
            results+=("${SUCCESS} Ubuntu ${VERSION_ID} provides in-tree SEV-SNP host support${NC}")
        else
            results+=("${WARNING} ${ID} ${VERSION_ID}: verify kernel >= 6.11 for SNP host${NC}")
        fi
    else
        results+=("${WARNING} /etc/os-release not readable; cannot verify distribution${NC}")
    fi

    results+=("Kernel (host, min 6.11 for SEV-SNP):")
    local kver kmaj kmin
    kver=$(uname -r)
    kmaj=$(uname -r | cut -d. -f1)
    kmin=$(uname -r | cut -d. -f2 | sed 's/[^0-9].*//')
    results+=("  Running kernel: ${kver}")
    if [ "$kmaj" -gt 6 ] || { [ "$kmaj" -eq 6 ] && [ "$kmin" -ge 11 ]; }; then
        results+=("${SUCCESS} Kernel supports SEV-SNP host (>= 6.11)${NC}")
    else
        results+=("${FAILURE} Kernel ${kver} likely lacks SEV-SNP host support${NC}")
        results+=("  Required: Linux >= 6.11 with CONFIG_KVM_AMD_SEV")
        all_passed=false
    fi

    print_section_header "Prerequisite Status"
    for result in "${results[@]}"; do
        echo -e "$result"
    done

    [ "$all_passed" = true ] && return 0 || return 1
}

# ---------------------------------------------------------------------------
# AMD-provided readiness check: `snphost ok`
#   Documented as the comprehensive CPU/BIOS/FW check. Use it if available.
# ---------------------------------------------------------------------------
run_snphost_ok() {
    print_section_header "AMD snphost readiness check"
    if command -v snphost >/dev/null 2>&1; then
        echo -e "${SUCCESS} snphost found; running 'snphost ok'...${NC}"
        echo "-----------------------------------------------------------"
        # snphost returns non-zero if any check fails; capture but don't abort,
        # the manual checks below provide the actionable BIOS guidance.
        if snphost ok; then
            echo "-----------------------------------------------------------"
            echo -e "${SUCCESS} snphost reports the host is ready${NC}"
            return 0
        else
            echo "-----------------------------------------------------------"
            echo -e "${WARNING} snphost reported one or more failures (see above)${NC}"
            return 1
        fi
    else
        echo -e "${WARNING} snphost not available (see install note above); using manual checks${NC}"
        return 2
    fi
}

# ---------------------------------------------------------------------------
# SMEE verification via MSR 0xC0010010 bit 23 (authoritative, per AMD FAQ)
#   SEV_INIT error 0x13 (HWERROR_PLATFORM) == SMEE not enabled in BIOS.
# ---------------------------------------------------------------------------
check_smee_msr() {
    # returns 0 if SMEE bit set, 1 if not, 2 if unable to read
    if ! command -v rdmsr >/dev/null 2>&1; then
        return 2
    fi
    modprobe msr 2>/dev/null || true
    local msr_val
    msr_val=$(rdmsr 0xc0010010 2>/dev/null || echo "")
    if [ -z "$msr_val" ]; then
        return 2
    fi
    # Bit 23 = SMEE. Convert hex MSR to decimal and test bit 23.
    local dec bit23
    dec=$(printf "%d" "0x${msr_val}" 2>/dev/null || echo 0)
    bit23=$(( (dec >> 23) & 1 ))
    SMEE_MSR_RAW="$msr_val"
    [ "$bit23" -eq 1 ] && return 0 || return 1
}

# ---------------------------------------------------------------------------
# SEV firmware version from dmesg (ccp ...: SEV-SNP API:X.YY build:N)
#   Minimum for SNP: API 1.51 (0x33).
# ---------------------------------------------------------------------------
check_sev_fw_version() {
    # echoes status lines via the caller's results array is awkward; instead
    # set globals.
    SEV_FW_LINE=$(dmesg | grep -iE "ccp.*SEV-SNP API:" | head -1 || echo "")
    SEV_FW_OK="unknown"
    SEV_FW_VER=""
    if [ -n "$SEV_FW_LINE" ]; then
        # Extract e.g. "1.58" from "SEV-SNP API:1.58 build:5"
        SEV_FW_VER=$(echo "$SEV_FW_LINE" | grep -oiE "API:[0-9]+\.[0-9]+" | head -1 | cut -d: -f2)
        if [ -n "$SEV_FW_VER" ]; then
            local maj min
            maj=$(echo "$SEV_FW_VER" | cut -d. -f1)
            min=$(echo "$SEV_FW_VER" | cut -d. -f2)
            # Min 1.51
            if [ "$maj" -gt 1 ] || { [ "$maj" -eq 1 ] && [ "$min" -ge 51 ]; }; then
                SEV_FW_OK="yes"
            else
                SEV_FW_OK="no"
            fi
        fi
    fi
}

# ---------------------------------------------------------------------------
# BIOS / firmware configuration checks
# ---------------------------------------------------------------------------
check_all_bios_settings() {
    local results=()
    local all_passed=true

    print_section_header "BIOS Configuration Check Results"
    echo "Checking all settings for ${PLATFORM} (${PLATFORM_ZEN})..."

    # --- SMEE (SME) : authoritative MSR check, fall back to cpuinfo flag ---
    results+=("SMEE / SME (memory encryption master switch):")
    check_smee_msr
    local smee_rc=$?
    if [ "$smee_rc" -eq 0 ]; then
        results+=("${SUCCESS} SMEE enabled (MSR 0xC0010010 bit23 set; raw=0x${SMEE_MSR_RAW})${NC}")
    elif [ "$smee_rc" -eq 1 ]; then
        results+=("${FAILURE} SMEE disabled (MSR 0xC0010010 bit23 clear; raw=0x${SMEE_MSR_RAW})${NC}")
        results+=("  Location: Advanced -> AMD CBS -> CPU Common Options -> SMEE -> Enable")
        results+=("  Symptom if left off: SEV_INIT fails with error 0x13 (HWERROR_PLATFORM)")
        all_passed=false
    else
        # MSR unreadable; fall back to cpuinfo
        if grep -qw "sme" /proc/cpuinfo; then
            results+=("${WARNING} Could not read MSR; 'sme' flag present in cpuinfo${NC}")
        else
            results+=("${FAILURE} Could not read MSR and 'sme' flag absent${NC}")
            results+=("  Location: Advanced -> AMD CBS -> CPU Common Options -> SMEE -> Enable")
            all_passed=false
        fi
    fi

    # --- SEV CPU flags ----------------------------------------------------
    results+=("SEV CPU Flags:")
    for flag in sev sev_es sev_snp; do
        if grep -qw "$flag" /proc/cpuinfo; then
            results+=("${SUCCESS} ${flag} present${NC}")
        else
            results+=("${FAILURE} ${flag} absent in /proc/cpuinfo${NC}")
            all_passed=false
        fi
    done

    # --- KVM module parameters (sev / sev_es / sev_snp must read Y) --------
    results+=("KVM (kvm_amd) Parameters:")
    if [ ! -d /sys/module/kvm_amd ]; then
        results+=("${FAILURE} kvm_amd module not loaded${NC}")
        results+=("  Required: modprobe kvm_amd")
        all_passed=false
    else
        for param in sev sev_es sev_snp; do
            local pfile="/sys/module/kvm_amd/parameters/${param}"
            if [ -f "$pfile" ]; then
                local val
                val=$(cat "$pfile")
                if [ "$val" = "Y" ] || [ "$val" = "1" ]; then
                    results+=("${SUCCESS} kvm_amd.${param}=${val}${NC}")
                else
                    results+=("${FAILURE} kvm_amd.${param}=${val} (expected Y)${NC}")
                    results+=("  Likely resolved once BIOS settings below are applied")
                    all_passed=false
                fi
            else
                results+=("${WARNING} parameter ${param} not exposed by this kernel${NC}")
            fi
        done
    fi

    # --- SEV-SNP enablement + ASID range (kvm_amd: SEV-SNP enabled (ASIDs..))
    results+=("SEV-SNP Initialization:")
    local snp_enable_line
    snp_enable_line=$(dmesg | grep -iE "kvm_amd:.*SEV-SNP enabled" | head -1 || echo "")
    if [ -n "$snp_enable_line" ]; then
        results+=("${SUCCESS} SEV-SNP enabled${NC}")
        # e.g. "(ASIDs 1 - 98)"
        local asid_range
        asid_range=$(echo "$snp_enable_line" | grep -oiE "ASIDs[[:space:]]*[0-9]+[[:space:]]*-[[:space:]]*[0-9]+" || echo "")
        [ -n "$asid_range" ] && results+=("  ${asid_range}")
        [ -n "$EXPECTED_ASIDS" ] && results+=("  Platform documented total: ${EXPECTED_ASIDS}")
    else
        results+=("${FAILURE} 'SEV-SNP enabled' not found in dmesg${NC}")
        # Try to surface the actual reason rather than guessing BIOS.
        local snp_err
        snp_err=$(dmesg | grep -iE "SEV(-SNP)?:.*(fail|error|disabled)|ccp.*error" | head -3 || echo "")
        if [ -n "$snp_err" ]; then
            results+=("  Reported by kernel:")
            while IFS= read -r line; do
                [ -n "$line" ] && results+=("    $(echo "$line" | sed -E 's/^\[[^]]*\] //')")
            done <<< "$snp_err"
            results+=("  -> 'INIT error 0x1, rc -5' = PSP BootLoader too old; update system BIOS")
            results+=("  -> 'error 0x13' (HWERROR_PLATFORM) = SMEE disabled in BIOS")
        elif ! grep -qw "sev_snp" /proc/cpuinfo; then
            results+=("  Cause: BIOS - CPU does not expose 'sev_snp'")
            results+=("  Location: Advanced -> NBIO Common Options -> IOMMU/Security -> SEV-SNP Support -> Enable")
        else
            results+=("  CPU exposes sev_snp; check kvm_amd.sev_snp param and RMP/firmware lines above")
        fi
        all_passed=false
    fi

    # --- RMP table (SEV-SNP: ... RMP ...) ---------------------------------
    results+=("RMP Table:")
    local rmp_line
    rmp_line=$(dmesg | grep -iE "SEV-SNP:.*RMP" | head -1 || echo "")
    if [ -n "$rmp_line" ]; then
        results+=("${SUCCESS} RMP table present${NC}")
        results+=("  $(echo "$rmp_line" | sed -E 's/.*SEV-SNP: //')")
    else
        results+=("${FAILURE} RMP table not reported in dmesg${NC}")
        results+=("  Location: Advanced -> AMD CBS -> CPU Common Options -> SNP Memory (RMP Table) Coverage -> Enabled")
        all_passed=false
    fi

    # --- SEV firmware version (min 1.51 / 0x33) ---------------------------
    results+=("SEV Firmware (min API 1.51):")
    check_sev_fw_version
    if [ -n "$SEV_FW_VER" ]; then
        if [ "$SEV_FW_OK" = "yes" ]; then
            results+=("${SUCCESS} SEV-SNP API ${SEV_FW_VER} (>= 1.51)${NC}")
        elif [ "$SEV_FW_OK" = "no" ]; then
            results+=("${FAILURE} SEV-SNP API ${SEV_FW_VER} is below minimum 1.51${NC}")
            results+=("  Update firmware: developer.amd.com/sev or linux-firmware package")
            results+=("  Then: rmmod kvm_amd ccp; modprobe ccp; modprobe kvm_amd (or reboot)")
            all_passed=false
        fi
    else
        results+=("${WARNING} Could not read SEV-SNP API version from dmesg${NC}")
        results+=("  If you see 'SEV: failed to INIT error 0x1, rc -5' -> PSP BootLoader too old; update system BIOS")
    fi

    # --- IOMMU (mandatory for SNP) ----------------------------------------
    results+=("IOMMU Settings:")
    if dmesg | grep -qi "AMD-Vi: Interrupt remapping enabled" || \
       dmesg | grep -qiE "iommu: Default domain type:"; then
        results+=("${SUCCESS} IOMMU enabled and active${NC}")
    elif [ -d /sys/class/iommu ] && [ -n "$(ls -A /sys/class/iommu 2>/dev/null)" ]; then
        results+=("${SUCCESS} IOMMU groups present${NC}")
    else
        results+=("${FAILURE} IOMMU not enabled - SEV-SNP requires it${NC}")
        results+=("  Location: Advanced -> NBIO Common Options -> IOMMU -> Enable")
        results+=("  Also add to kernel cmdline: amd_iommu=on iommu=pt")
        all_passed=false
    fi

    # --- SMT (informational; SNP works with SMT on) -----------------------
    results+=("SMT Settings (informational):")
    if [ -r /sys/devices/system/cpu/smt/active ]; then
        if [ "$(cat /sys/devices/system/cpu/smt/active)" = "1" ]; then
            results+=("${SUCCESS} SMT active${NC}")
        else
            results+=("${WARNING} SMT inactive (allowed for SNP)${NC}")
        fi
    fi

    # --- Platform-specific notes ------------------------------------------
    case "$PLATFORM" in
        MILAN)
            results+=("${YELLOW}Note (Milan/Zen3, EPYC 7003): min PSP BootLoader 00.13.00.70 (AGESA PI 1.0.0.9+).${NC}")
            results+=("${YELLOW}  Updating FW with an older BootLoader fails: SEV: failed to INIT error 0x1, rc -5.${NC}")
            ;;
        GENOA)
            results+=("${YELLOW}Note (Genoa/Zen4, EPYC 9004/8004): SEV FW updates are delivered via system BIOS.${NC}")
            ;;
        TURIN)
            results+=("${YELLOW}Note (Turin/Zen5): newest AGESA; confirm SNP + CipherTextHiding settings.${NC}")
            ;;
    esac

    # --- Required BIOS configuration summary (verbatim AMD menu paths) -----
    results+=("${YELLOW}Required BIOS Configuration (AMD CBS menu):${NC}")
    results+=("Advanced -> AMD CBS -> CPU Common Options")
    results+=("    SMEE                              -> Enable")
    results+=("    SEV Control                       -> Enable")
    results+=("    SEV-ES ASID Space Limit           -> 99")
    results+=("    SNP Memory (RMP Table) Coverage   -> Enabled")
    results+=("Advanced -> NBIO Common Options -> IOMMU/Security")
    results+=("    SEV-SNP Support                   -> Enable")
    results+=("    IOMMU                             -> Enable")
    results+=("${YELLOW}Kernel cmdline:${NC}")
    results+=("    kvm_amd.sev=1 kvm_amd.sev_es=1 kvm_amd.sev_snp=1 amd_iommu=on iommu=pt")

    print_section_header "Status"
    for result in "${results[@]}"; do
        echo -e "$result"
    done

    if [ "$all_passed" = true ]; then
        echo -e "\n${SUCCESS} All settings properly configured${NC}"
        return 0
    else
        echo -e "\n${FAILURE} Some settings need attention${NC}"
        return 1
    fi
}

ensure_tools() {
    print_section_header "Ensuring diagnostic tools are present"

    # --- apt-provided tools needed for diagnosis -------------------------
    # (apt-get update is performed earlier in the pipeline; not repeated here)
    local apt_pkgs=()
    command -v cpuid  >/dev/null 2>&1 || apt_pkgs+=("cpuid")
    command -v rdmsr  >/dev/null 2>&1 || apt_pkgs+=("msr-tools")

    if [ "${#apt_pkgs[@]}" -gt 0 ]; then
        echo "Installing missing apt packages: ${apt_pkgs[*]}"
        apt-get install -y "${apt_pkgs[@]}"
    else
        echo -e "${SUCCESS} apt diagnostic tools already present (cpuid, msr-tools)${NC}"
    fi

    # Load msr module so rdmsr works
    if ! lsmod | grep -q "^msr"; then
        modprobe msr || true
    fi

    # --- snphost (host readiness checker, not in apt) --------------------
    if command -v snphost >/dev/null 2>&1; then
        echo -e "${SUCCESS} snphost present${NC}"
    else
        echo "snphost not installed; attempting install from latest GitHub release..."
        install_snphost_latest || \
            echo -e "${WARNING} snphost auto-install skipped/failed; manual checks below still run${NC}"
    fi
}

# ---------------------------------------------------------------------------
# Install snphost from the latest virtee/snphost GitHub release (.deb, amd64)
#   Resolves the asset dynamically so it always tracks 'latest'.
#   Degrades gracefully (returns non-zero) if offline or no matching asset.
# ---------------------------------------------------------------------------
install_snphost_latest() {
    local api="https://api.github.com/repos/virtee/snphost/releases/latest"
    local tmp deb_url

    command -v curl >/dev/null 2>&1 || { echo "  curl not available"; return 1; }

    tmp=$(mktemp -d)
    # Fetch release metadata
    if ! curl -fsSL "$api" -o "${tmp}/release.json"; then
        echo "  Could not reach GitHub API (offline contour?)"
        rm -rf "$tmp"
        return 1
    fi

    # Pick an amd64/x86_64 .deb asset URL from browser_download_url lines.
    deb_url=$(grep -oE '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]+"' "${tmp}/release.json" \
                | sed -E 's/.*"(https[^"]+)"/\1/' \
                | grep -iE '\.deb$' \
                | grep -iE 'amd64|x86_64|x86-64' \
                | head -1)

    # Fall back to any .deb if no arch-tagged one is found
    if [ -z "$deb_url" ]; then
        deb_url=$(grep -oE '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]+"' "${tmp}/release.json" \
                    | sed -E 's/.*"(https[^"]+)"/\1/' \
                    | grep -iE '\.deb$' \
                    | head -1)
    fi

    if [ -z "$deb_url" ]; then
        echo "  No .deb asset found in latest release; see https://github.com/virtee/snphost/releases/latest"
        rm -rf "$tmp"
        return 1
    fi

    echo "  Downloading: ${deb_url}"
    if ! curl -fsSL "$deb_url" -o "${tmp}/snphost.deb"; then
        echo "  Download failed"
        rm -rf "$tmp"
        return 1
    fi

    echo "  Installing snphost.deb..."
    if dpkg -i "${tmp}/snphost.deb"; then
        :
    else
        # Resolve any missing dependencies (apt update already done earlier)
        apt-get -f install -y || true
    fi

    rm -rf "$tmp"
    command -v snphost >/dev/null 2>&1 \
        && { echo -e "${SUCCESS} snphost installed ($(snphost --version 2>/dev/null | head -1))${NC}"; return 0; } \
        || { echo -e "${WARNING} snphost still not on PATH after install${NC}"; return 1; }
}

check_bios_settings() {
    echo "Performing comprehensive SEV-SNP BIOS configuration check..."

    ensure_tools

    detect_platform
    check_os_prereqs || true          # informative; don't abort
    run_snphost_ok || true            # AMD's own check if present; don't abort
    check_all_bios_settings
    return $?
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

print_section_header "AMD SEV-SNP BIOS Configuration Verification"
if ! check_bios_settings; then
    echo -e "${RED}ERROR: Required BIOS settings are not properly configured${NC}"
    echo "Please configure BIOS settings according to the instructions above and try again"
    exit 1
fi

echo -e "\n${GREEN}SEV-SNP host BIOS verification passed.${NC}"
