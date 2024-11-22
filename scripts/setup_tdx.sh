#!/bin/bash

# Configuration variables
PCCS_API_KEY="aecd5ebb682346028d60c36131eb2d92"
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

echo -e "${GREEN}Starting clean PCCS installation and setup...${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Stop and disable all services first
echo -e "${GREEN}Stopping all services...${NC}"
systemctl stop pccs qgsd mpa_registration_tool
systemctl disable pccs qgsd mpa_registration_tool

# Remove existing packages
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

# Create PCCS config directory
mkdir -p /opt/intel/sgx-dcap-pccs/config/

# Create PCCS configuration
echo -e "${GREEN}Creating PCCS configuration...${NC}"
cat > /opt/intel/sgx-dcap-pccs/config/default.json << EOL
{
    "HTTPS_PORT" : ${PCCS_PORT},
    "hosts" : "127.0.0.1",
    "uri": "https://api.trustedservices.intel.com/sgx/certification/v4/",
    "ApiKey" : "${PCCS_API_KEY}",
    "proxy" : "",
    "RefreshSchedule": "0 0 1 * *",
    "UserTokenHash" : "2997dd7ea4d3f7db747f5550b37ccaabd80e7b66cb7599443112a4f343f2e91c06793a0aa8a6f1c92b1a213776be55d5475f4b4c363d708ef4f39f3a6ed634ee",
    "AdminTokenHash" : "2997dd7ea4d3f7db747f5550b37ccaabd80e7b66cb7599443112a4f343f2e91c06793a0aa8a6f1c92b1a213776be55d5475f4b4c363d708ef4f39f3a6ed634ee",
    "CachingFillMode" : "LAZY",
    "LogLevel" : "info",
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

echo -e "\n${GREEN}Installation and setup completed!${NC}"
echo -e "${YELLOW}To check logs use:${NC}"
echo "PCCS logs: journalctl -u pccs -f"
echo "QGSD logs: journalctl -u qgsd -f"
echo "MPA Registration logs: cat /var/log/mpa_registration.log"
