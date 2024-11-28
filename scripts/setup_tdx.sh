#!/bin/bash

# Configuration variables
PCCS_API_KEY="aecd5ebb682346028d60c36131eb2d92"
PCCS_PORT="8081"
PCCS_PASSWORD="pccspassword123"
# Generate SHA512 hash of the password
USER_TOKEN=$(echo -n "${PCCS_PASSWORD}" | sha512sum | awk '{print $1}')

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

TMP_DIR=$1
# Download the setup-attestation-host.sh script
git clone -b noble-24.04 --single-branch --depth 1 --no-tags https://github.com/canonical/tdx.git "${TMP_DIR}/tdx-cannonical"
SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh
# Check for download errors
if [ $? -ne 0 ]; then
echo "Failed to download the setup-attestation-host.sh script."
exit 1
fi
# Make the script executable
chmod +x "${SCRIPT_PATH}"
# Run the script
echo "Running setup-attestation-host.sh..."
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

    # Get platform info from csv
    echo -e "${YELLOW}Platform information from CSV:${NC}"
    cat "$csv_file"

    # Register with PCCS using password
    echo -e "${GREEN}Registering with PCCS...${NC}"
    PCKIDRetrievalTool \
        -url "https://localhost:${PCCS_PORT}" \
        -use_secure_cert false \
        -user_token "${PCCS_PASSWORD}"
    
    return $?
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

# Set correct permissions
echo -e "${GREEN}Setting permissions...${NC}"
chown -R pccs:pccs /opt/intel/sgx-dcap-pccs/
chmod -R 750 /opt/intel/sgx-dcap-pccs/

# Enable and start services
echo -e "${GREEN}Enabling and starting services...${NC}"
systemctl enable pccs qgsd mpa_registration_tool
systemctl daemon-reload

# Start PCCS first
echo -e "${GREEN}Starting PCCS...${NC}"
systemctl start pccs
wait_for_service pccs
check_error "Failed to start PCCS"
sleep 5

# Get platform info and register
cd /opt/intel/sgx-dcap-pccs/
echo -e "${GREEN}Running PCKIDRetrievalTool...${NC}"
rm -f pckid_retrieval.csv
PCKIDRetrievalTool
check_error "PCKIDRetrievalTool failed"

echo -e "${GREEN}Registering platform with PCCS...${NC}"
register_platform
check_error "Failed to register platform"

# Start remaining services
echo -e "${GREEN}Starting remaining services...${NC}"
systemctl start qgsd
wait_for_service qgsd
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
