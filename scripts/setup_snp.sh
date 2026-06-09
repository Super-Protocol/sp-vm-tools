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

# ---------------------------------------------------------------------------
# BIOS menu paths (observed on this platform: Aptio Setup - AMI).
# These locations vary by vendor and BIOS version; treat as a starting point.
# ---------------------------------------------------------------------------
BIOS_NOTE="(menu location may differ on other vendors / BIOS versions)"
PATH_SMEE="CPU -> CPU Configuration -> SMEE -> Enabled"
PATH_SNP_SUPPORT="Chipset -> AMD CBS -> NBIO Common Options -> SEV-SNP Support -> Enabled"
PATH_RMP="Chipset -> AMD CBS -> CPU Common Options -> SNP Memory (RMP Table) Coverage -> Enabled"
PATH_ASID="Chipset -> AMD CBS -> CPU Common Options -> SEV-ES ASID Space Limit"

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
        results+=("  Location: ${PATH_SMEE}  ${BIOS_NOTE}")
        results+=("  Symptom if left off: SEV_INIT fails with error 0x13 (HWERROR_PLATFORM)")
        all_passed=false
    else
        # MSR unreadable; fall back to cpuinfo
        if grep -qw "sme" /proc/cpuinfo; then
            results+=("${WARNING} Could not read MSR; 'sme' flag present in cpuinfo${NC}")
        else
            results+=("${FAILURE} Could not read MSR and 'sme' flag absent${NC}")
            results+=("  Location: ${PATH_SMEE}  ${BIOS_NOTE}")
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
            results+=("  Location: ${PATH_SNP_SUPPORT}  ${BIOS_NOTE}")
        else
            results+=("  CPU exposes sev_snp; check kvm_amd.sev_snp param and RMP/firmware lines above")
            results+=("  Also verify SEV-ES ASID Space Limit is non-trivial (a value of 1 leaves no SNP ASIDs)")
            results+=("  Location: ${PATH_ASID}  ${BIOS_NOTE}")
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
        results+=("  Location: ${PATH_RMP}  ${BIOS_NOTE}")
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

    # --- Required BIOS configuration summary (observed AMI layout) --------
    results+=("${YELLOW}Required BIOS Configuration ${BIOS_NOTE}:${NC}")
    results+=("CPU -> CPU Configuration")
    results+=("    SMEE                              -> Enabled")
    results+=("Chipset -> AMD CBS -> CPU Common Options")
    results+=("    SEV-ES ASID Space Limit Control   -> Manual")
    results+=("    SEV-ES ASID Space Limit           -> 99   (AMD doc recommends 99)")
    results+=("    SNP Memory (RMP Table) Coverage   -> Enabled")
    results+=("Chipset -> AMD CBS -> NBIO Common Options")
    results+=("    SEV-SNP Support                   -> Enabled")
    results+=("${YELLOW}Note: enabling SEV-SNP Support also gates SEV / SEV-ES on this platform${NC}")
    results+=("${YELLOW}      (no separate 'SEV Control' item present).${NC}")

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
}

check_bios_settings() {
    echo "Performing comprehensive SEV-SNP BIOS configuration check..."

    ensure_tools

    detect_platform
    check_os_prereqs || true          # informative; don't abort
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
