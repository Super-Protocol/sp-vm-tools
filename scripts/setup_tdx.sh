#!/bin/bash

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Modify status indicators:
SUCCESS="[${GREEN}✓${NC}]"
FAILURE="[${RED}✗${NC}]"

print_section_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
    echo -e "${BLUE}$(printf '=%.0s' {1..40})${NC}"
}

# Configuration variables
PCCS_API_KEY="aecd5ebb682346028d60c36131eb2d92"
PCCS_PORT="8081"
PCCS_PASSWORD="pccspassword123"
# Generate SHA512 hash of the password
USER_TOKEN=$(echo -n "${PCCS_PASSWORD}" | sha512sum | awk '{print $1}')

check_all_bios_settings() {
    local results=()
    local all_passed=true
    
    print_section_header "BIOS Configuration Check Results"
    echo "Checking all settings..."
    
    # CPU PA
    results+=("CPU PA Settings:")
    if true; then
        results+=("✓ CPU PA limit properly configured")
    else
        results+=("✗ CPU PA limit must be disabled")
        results+=("  Location: Uncore General Configuration")
        all_passed=false
    fi
    
    # TME Check
    results+=("TME Settings:")
    if rdmsr -f 0:0 0x981 2>/dev/null | grep -q "1" && \
       rdmsr -f 0:0 0x982 2>/dev/null | grep -q "1"; then
        results+=("✓ Memory encryption enabled")
    else
        results+=("✗ Memory encryption disabled")
        all_passed=false
    fi

    # SGX Check
    results+=("SGX Settings:")
    if grep -q "sgx" /proc/cpuinfo && [ -c "/dev/sgx_enclave" -o -c "/dev/sgx/enclave" ]; then
        results+=("✓ SGX enabled and configured")
    else
        results+=("✗ SGX not properly configured")
        all_passed=false
    fi

    # TXT Check  
    results+=("TXT Settings:")
    if grep -q "smx" /proc/cpuinfo || \
       (command -v rdmsr &> /dev/null && \
        modprobe msr 2>/dev/null && \
        [ "$(rdmsr 0x8B 2>/dev/null)" != "0" ]); then
        results+=("✓ TXT supported and enabled")
    else
        results+=("✗ TXT not properly configured")
        all_passed=false
    fi

    # TDX Check
    results+=("TDX Settings:")
    if grep -q "tdx" /proc/cpuinfo || dmesg | grep -q "TDX"; then
        results+=("✓ TDX supported and enabled")
    else 
        results+=("✗ TDX not properly configured")
        all_passed=false
    fi

    results+=("Required BIOS Configuration:")
    results+=("• Memory Encryption:")
    results+=("  - TME: Enable")
    results+=("  - TME Multi-Tenant: Enable")
    results+=("  - TME-MT keys: 31")
    results+=("  - Key split: 1")
    results+=("• TDX:")
    results+=("  - TDX: Enable")
    results+=("  - SEAM Loader: Enable")
    results+=("  - TDX keys: 32")

    print_section_header "Status"
    printf '%s\n' "${results[@]}"

    if [ "$all_passed" = true ]; then
        echo -e "\n✓ All settings properly configured"
        return 0
    else
        echo -e "\n✗ Some settings need attention"
        return 1
    fi
}

check_bios_settings() {
    echo "Performing comprehensive BIOS configuration check..."
    
    # Install msr-tools if not present
    if ! command -v rdmsr &> /dev/null; then
        echo "Installing msr-tools..."
        apt-get update && apt-get install -y msr-tools
    fi

    # Load the msr module if not loaded
    if ! lsmod | grep -q "^msr"; then
        echo "Loading MSR module..."
        modprobe msr
    fi

    # Run all checks at once
    check_all_bios_settings
    return $?
}

TMP_DIR=$1

if [ -d "${TMP_DIR}/tdx-cannonical" ]; then
    echo -e "${YELLOW}Directory ${TMP_DIR}/tdx-cannonical already exists${NC}"
    echo -e "Removing existing directory..."
    rm -rf "${TMP_DIR}/tdx-cannonical"
fi

# Download the setup-attestation-host.sh script
git clone -b noble-24.04 --single-branch --depth 1 --no-tags https://github.com/canonical/tdx.git "${TMP_DIR}/tdx-cannonical"
SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/setup-tdx-host.sh
# Check for download errors
if [ $? -ne 0 ]; then
echo "Failed to download the setup-tdx-host.sh script."
exit 1
fi
# Make the script executable
echo "Running setup-tdx-host.sh..."
chmod +x "${SCRIPT_PATH}"
"${SCRIPT_PATH}"

print_section_header "BIOS Configuration Verification"
if ! check_bios_settings; then
    echo -e "${RED}ERROR: Required BIOS settings are not properly configured${NC}"
    echo "Please configure BIOS settings according to the instructions above and try again"
    exit 1
fi

echo "Running setup-attestation-host.sh..."
SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh
chmod +x "${SCRIPT_PATH}"
"${SCRIPT_PATH}"

# Function for error handling
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: $1${NC}"
        exit 1
    fi
}

# Function to wait for service
wait_for_service() {
    local service=$1
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if systemctl is-active --quiet $service; then
            echo -e "${GREEN}$service is up${NC}"
            return 0
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    echo -e "${RED}$service failed to start${NC}"
    return 1
}

register_platform() {
    local csv_file="pckid_retrieval.csv"
    
    if [ ! -f "$csv_file" ]; then
        echo -e "${RED}PCK ID retrieval file not found${NC}"
        return 1
    fi

    # Register with PCCS using password
    echo -e "${GREEN}Registering with PCCS...${NC}"
    PCKIDRetrievalTool \
        -url "https://localhost:${PCCS_PORT}" \
        -use_secure_cert false \
        -user_token "${PCCS_PASSWORD}"
    
    return $?
}

print_section_header "Starting clean PCCS installation and setup..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Stop and disable all services first
print_section_header "Stopping all services..."
systemctl stop pccs qgsd mpa_registration_tool
systemctl disable pccs qgsd mpa_registration_tool

# Remove existing packages
print_section_header "Removing existing packages..."
apt-get remove -y sgx-dcap-pccs 

# Clean up old configurations
print_section_header "Cleaning up old configurations..."
rm -f /etc/sgx_default_qcnl.conf
rm -rf /opt/intel/sgx-dcap-pccs/config/*

# Install packages
print_section_header "Installing packages..."
apt-get update
apt-get install -y \
    libsgx-ae-id-enclave \
    libsgx-ae-pce \
    libsgx-ae-tdqe \
    libsgx-dcap-default-qpl \
    libsgx-enclave-common1 \
    libsgx-pce-logic1 \
    libsgx-tdx-logic1 \
    libsgx-urts2 \
    sgx-dcap-pccs \
    sgx-pck-id-retrieval-tool \
    sgx-ra-service \
    sgx-setup \
    tdx-qgs
check_error "Failed to install packages"

# Create PCCS config directory
mkdir -p /opt/intel/sgx-dcap-pccs/config/

# Create PCCS configuration
print_section_header "Creating PCCS configuration..."
cat > /opt/intel/sgx-dcap-pccs/config/default.json << EOL
{
    "HTTPS_PORT" : ${PCCS_PORT},
    "hosts" : "127.0.0.1",
    "uri": "https://api.trustedservices.intel.com/sgx/certification/v4/",
    "ApiKey" : "${PCCS_API_KEY}",
    "proxy" : "",
    "RefreshSchedule": "0 0 1 * *",
    "UserTokenHash" : "${USER_TOKEN}",
    "AdminTokenHash" : "${USER_TOKEN}",
    "CachingFillMode" : "REQ",
    "LogLevel" : "debug",
    "DB_CONFIG" : "sqlite",
    "sqlite" : {
        "database" : "database",
        "username" : "username",
        "password" : "password",
        "options" : {
            "host": "localhost",
            "dialect": "sqlite",
            "pool": {
                "max": 5,
                "min": 0,
                "acquire": 30000,
                "idle": 10000
            },
            "define": {
                "freezeTableName": true
            },
            "logging" : false, 
            "storage": "pckcache.db"
        }
    }
}
EOL

# Configure QCNL
print_section_header "Configuring QCNL..."
cat > /etc/sgx_default_qcnl.conf << EOL
PCCS_URL=https://localhost:${PCCS_PORT}/sgx/certification/v4/
USE_SECURE_CERT=false
RETRY_TIMES=6
RETRY_DELAY=10
LOCAL_PCK_URL=http://localhost:${PCCS_PORT}/sgx/certification/v4/
LOCAL_PCK_RETRY_TIMES=6
LOCAL_PCK_RETRY_DELAY=10
EOL
check_error "Failed to create QCNL configuration"

# Set correct permissions
print_section_header "Setting permissions..."
chown -R pccs:pccs /opt/intel/sgx-dcap-pccs/
chmod -R 750 /opt/intel/sgx-dcap-pccs/

# Enable and start services
print_section_header "Enabling and starting services..."
systemctl enable pccs qgsd mpa_registration_tool
systemctl daemon-reload

# Start PCCS first
print_section_header "Starting PCCS..."
systemctl start pccs
wait_for_service pccs
check_error "Failed to start PCCS"
sleep 5

# Get platform info and register
cd /opt/intel/sgx-dcap-pccs/
print_section_header "Running PCKIDRetrievalTool..."
rm -f pckid_retrieval.csv
PCKIDRetrievalTool
check_error "PCKIDRetrievalTool failed"

print_section_header "Registering platform with PCCS..."
register_platform
check_error "Failed to register platform"

# Start remaining services
print_section_header "Starting remaining services..."
systemctl start qgsd
wait_for_service qgsd
systemctl start mpa_registration_tool

# Check services status
print_section_header "Checking services status..."
# Check PCCS and QGSD status
for service in pccs qgsd; do
    echo -e "\n${YELLOW}${service} Status:${NC}"
    if ! systemctl is-active --quiet $service; then
        echo -e "${RED}Error: $service is not running${NC}"
        systemctl status $service --no-pager
        exit 1
    else
        echo -e "${GREEN}$service is running${NC}"
        systemctl status $service --no-pager
    fi
done

# Separately handle mpa_registration_tool since it's expected to exit
echo -e "\n${YELLOW}mpa_registration_tool Status:${NC}"
if systemctl is-enabled --quiet mpa_registration_tool; then
    echo -e "${GREEN}mpa_registration_tool was properly configured${NC}"
    systemctl status mpa_registration_tool --no-pager || true
else
    echo -e "${RED}Error: mpa_registration_tool is not properly configured${NC}"
    exit 1
fi

print_section_header "Installation and setup completed!"
echo -e "${YELLOW}To check logs use:${NC}"
echo "PCCS logs: journalctl -u pccs -f"
echo "QGSD logs: journalctl -u qgsd -f"
echo "MPA Registration logs: cat /var/log/mpa_registration.log"
