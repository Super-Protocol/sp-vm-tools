#!/bin/bash

# Configuration variables
PCCS_API_KEY="322ed6bd9a802109e1e9692be0a825c6"
PCCS_PORT="8081"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Function for error handling
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: $1${NC}"
        exit 1
    fi
}

check_sgx_device() {
    echo "Checking SGX device..."
    if [ ! -c "/dev/sgx_enclave" ] && [ ! -c "/dev/sgx/enclave" ]; then
        echo -e "${RED}SGX device not found. Please verify kernel support for SGX${NC}"
        return 1
    fi
    return 0
}

check_tdx_support() {
    echo "Checking TDX support..."
    if ! grep -q "tdx" /proc/cpuinfo && ! dmesg | grep -q "TDX"; then
        echo -e "${RED}TDX support not detected in kernel${NC}"
        return 1
    fi
    return 0
}

echo -e "${GREEN}Starting SGX/TDX setup...${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Verify hardware support
if ! check_sgx_device; then
    echo -e "${RED}SGX device not available. Please check BIOS settings and kernel support.${NC}"
    exit 1
fi

if ! check_tdx_support; then
    echo -e "${RED}TDX not available. Please check BIOS settings and kernel support.${NC}"
    exit 1
fi

# Stop and disable all services first
echo -e "${GREEN}Stopping all services...${NC}"
systemctl stop pccs qgsd mpa_registration_tool
systemctl disable pccs qgsd mpa_registration_tool

# Remove existing pcss packages
echo -e "${GREEN}Removing existing packages...${NC}"
apt-get remove -y sgx-dcap-pccs 

# Clean up old configurations
echo -e "${GREEN}Cleaning up old configurations...${NC}"
rm -f /etc/sgx_default_qcnl.conf
rm -rf /opt/intel/sgx-dcap-pccs/config/*

# Install packages
echo -e "${GREEN}Installing packages...${NC}"
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

# Run PCKIDRetrievalTool
echo -e "${GREEN}Running PCKIDRetrievalTool...${NC}"
PCKIDRetrievalTool
check_error "PCKIDRetrievalTool failed"

# Configure QCNL
echo -e "${GREEN}Configuring QCNL...${NC}"
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

# Enable and start services
echo -e "${GREEN}Enabling and starting services...${NC}"
systemctl enable pccs qgsd mpa_registration_tool
systemctl daemon-reload

systemctl start pccs
sleep 5
systemctl start qgsd
systemctl start mpa_registration_tool

# Check services status
echo -e "${GREEN}Checking services status...${NC}"
for service in pccs qgsd mpa_registration_tool; do
    echo -e "\n${YELLOW}${service} Status:${NC}"
    systemctl status $service --no-pager
done

# Verify services are running correctly
echo -e "\n${GREEN}Verifying services...${NC}"
services_ok=true
for service in pccs qgsd mpa_registration_tool; do
    if ! systemctl is-active --quiet $service; then
        echo -e "${RED}$service is not running${NC}"
        services_ok=false
    else
        echo -e "${GREEN}✓ $service is running${NC}"
    fi
done

if [ "$services_ok" = true ]; then
    echo -e "\n${GREEN}SGX/TDX setup completed successfully!${NC}"
    echo -e "${YELLOW}To check logs use:${NC}"
    echo "PCCS logs: journalctl -u pccs -f"
    echo "QGSD logs: journalctl -u qgsd -f"
    echo "MPA Registration logs: cat /var/log/mpa_registration.log"
else
    echo -e "\n${RED}Setup completed with errors. Please check the logs above.${NC}"
    exit 1
fi
