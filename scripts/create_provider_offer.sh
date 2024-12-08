#!/bin/bash

# Install petname if not present
if ! command -v petname &> /dev/null; then
    echo "Installing petname package..."
    sudo apt update && sudo apt install -y petname
fi

# Function to convert sizes to bytes
to_bytes() {
    local value=$1
    printf "%.0f" "$(echo "$value * 1024 * 1024 * 1024" | bc)"
}

# Function for safe floating point arithmetic
calc() {
    printf "%.0f" "$(echo "scale=2; $1" | bc)"
}

# Function to format name
format_name() {
    local petname=$1
    echo "$petname" | sed 's/[-_]/ /g' | awk '{for(i=1;i<=NF;i++)sub(/./,toupper(substr($i,1,1)),$i)}1'
}

# Function to extract GPU name
extract_gpu_name() {
    local gpu_info=$1
    echo "$gpu_info" | sed -n 's/.*NVIDIA Corporation \([^(]*\).*/\1/p' | sed 's/[[:space:]]*$//'
}

# Get name from user or generate using petname
echo -n "Enter name for the configuration (press Enter for random pet name): "
read custom_name
if [ -z "$custom_name" ]; then
    custom_name=$(petname)
fi
name="Super $(format_name "$custom_name")"

# Get CPU cores (including HyperThreading)
total_cores=$(nproc)
adjusted_cores=$((total_cores - 6))
[[ $adjusted_cores -lt 1 ]] && adjusted_cores=1

# Get total RAM in bytes
total_ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
total_ram_gb=$(calc "$total_ram_kb / 1024 / 1024")
adjusted_ram_gb=$(calc "$total_ram_gb - 20")
adjusted_ram_gb=$(calc "$adjusted_ram_gb * 0.9")
ram_bytes=$(to_bytes "$adjusted_ram_gb")

# Get disk size in bytes
disk_size_bytes=$(df / --output=size -B 1 | tail -n 1)
disk_size_gb=$(calc "$disk_size_bytes / 1024 / 1024 / 1024")
adjusted_disk_gb=$(calc "$disk_size_gb - 20")
adjusted_disk_gb=$(calc "$adjusted_disk_gb * 0.85")
disk_bytes=$(to_bytes "$adjusted_disk_gb")

# GPU detection
gpu_present=0
vram_bytes=0
gpu_cores=0
gpu_info=""
gpu_name=""

# First, ensure lspci is installed
if ! command -v lspci &> /dev/null; then
    echo "Installing pciutils..."
    sudo apt update && sudo apt install -y pciutils
fi

# Simple GPU detection using only lspci
gpu_info=$(lspci | grep -i "nvidia" | head -n 1)
if [ ! -z "$gpu_info" ]; then
    gpu_present=1
    gpu_cores=1000
    gpu_name=$(extract_gpu_name "$gpu_info")
    # Default VRAM for H100
    if echo "$gpu_info" | grep -q "H100"; then
        vram_bytes=$(to_bytes "80")  # 80GB for H100
    fi
    echo "Detected GPU: $gpu_info"
else
    echo "No NVIDIA GPU detected"
fi

# Get network bandwidth (assuming 1 Gbps as default)
bandwidth=$(calc "1024 * 1024 * 1024 * 1024 / 8")

# Get CPU model
cpu_model=$(grep "model name" /proc/cpuinfo | head -n 1 | cut -d':' -f2 | xargs)
if [ -z "$cpu_model" ]; then
    cpu_model="Unknown CPU"
fi

# Create description
if [ $gpu_present -eq 1 ]; then
    description="CPU: $cpu_model, $adjusted_cores cores, $adjusted_ram_gb GB RAM, GPU: $gpu_name, $adjusted_disk_gb GB disk, 1 Gbps network"
else
    description="CPU: $cpu_model, $adjusted_cores cores, $adjusted_ram_gb GB RAM, $adjusted_disk_gb GB disk, 1 Gbps network"
fi

# Function to floor a division of large numbers
floor_divide() {
    local dividend=$1
    local divisor=$2
    echo $(( dividend / divisor ))
}

# Generate JSON file
cat > offer.json << EOF
{
   "name": "$name",
   "description": "$description",
   "teeType": "0",
   "subType": "2",
   "properties": "0",
   "argsPublicKey": "{\"algo\":\"ECIES\",\"encoding\":\"base64\",\"key\":\"BJvkdBht6dQFOfkNYxBqtpUnvnresjyypuzOfmW0RUOi1qmWoEfXvHUnLxD9U1YrkukXJPxQH58atsPd2s8cEeo=\"}",
   "hardwareInfo": {
      "slotInfo": {
         "cpuCores": $adjusted_cores,
         "gpuCores": $gpu_cores,
         "ram": $ram_bytes,
         "vram": $vram_bytes,
         "diskUsage": $disk_bytes
      },
      "optionInfo": {
         "bandwidth": $bandwidth,
         "traffic": 0,
         "externalPort": 0
      }
   }
}
EOF

# Calculate values for slot1
slot1_gpu_cores=$(floor_divide $gpu_cores $adjusted_cores)
slot1_disk=$(floor_divide $disk_bytes $adjusted_cores)
slot1_ram=$(floor_divide $ram_bytes $adjusted_cores)
slot1_vram=$(floor_divide $vram_bytes $adjusted_cores)

# Generate slot1.json
cat > slot1.json << EOF
{
   "info":{
      "cpuCores":1,
      "gpuCores":$slot1_gpu_cores,
      "diskUsage":$slot1_disk,
      "ram":$slot1_ram,
      "vram":$slot1_vram
   },
   "usage":{
      "maxTimeMinutes":0,
      "minTimeMinutes":0,
      "price":"167000000000000000",
      "priceType":"0"
   }
}
EOF

# Calculate values for slot2
slot2_gpu_cores=$(floor_divide $((gpu_cores * 3)) $adjusted_cores)
slot2_disk=$(floor_divide $((disk_bytes * 3)) $adjusted_cores)
slot2_ram=$(floor_divide $((ram_bytes * 3)) $adjusted_cores)
slot2_vram=$(floor_divide $((vram_bytes * 3)) $adjusted_cores)

# Generate slot2.json
cat > slot2.json << EOF
{
   "info":{
      "cpuCores":3,
      "gpuCores":$slot2_gpu_cores,
      "diskUsage":$slot2_disk,
      "ram":$slot2_ram,
      "vram":$slot2_vram
   },
   "usage":{
      "maxTimeMinutes":0,
      "minTimeMinutes":0,
      "price":"167000000000000000",
      "priceType":"0"
   }
}
EOF

echo "offer.json has been generated successfully."
echo "slot1.json has been generated successfully."
echo "slot2.json has been generated successfully."

