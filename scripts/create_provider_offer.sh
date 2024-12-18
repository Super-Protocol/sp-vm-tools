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

# Function to set system hostname and update hosts file
set_system_hostname() {
    local new_hostname=$1
    local old_hostname=$(hostname)
    
    echo "Setting system hostname to: $new_hostname"
    
    # Set the new hostname using hostnamectl
    sudo hostnamectl set-hostname "$new_hostname"
    
    # Update /etc/hosts file
    echo "Updating /etc/hosts..."
    # Create backup of hosts file
    sudo cp /etc/hosts /etc/hosts.backup
    
    # Replace old hostname with new hostname in /etc/hosts
    sudo sed -i "s/\b${old_hostname}\b/${new_hostname}/g" /etc/hosts
    
    # Ensure localhost entries exist with new hostname
    if ! grep -q "127.0.0.1.*${new_hostname}" /etc/hosts; then
        sudo sed -i "1i127.0.0.1\tlocalhost ${new_hostname}" /etc/hosts
    fi
    if ! grep -q "::1.*${new_hostname}" /etc/hosts; then
        sudo sed -i "2i::1\tlocalhost ${new_hostname}" /etc/hosts
    fi
    
    echo "Hostname has been updated. Changes will take full effect after reboot."
}
# Function to format hostname
format_hostname() {
    local name=$1
    # Convert to lowercase, replace spaces with hyphens, remove any special characters
    echo "$name" | tr '[:upper:]' '[:lower:]' | sed 's/ /-/g' | sed 's/[^a-z0-9-]//g'
}

# Function to extract GPU name
extract_gpu_name() {
    local gpu_info=$1
    echo "$gpu_info" | sed -n 's/.*NVIDIA Corporation \([^(]*\).*/\1/p' | sed 's/[[:space:]]*$//'
}

# Function to get largest disk size in GB
get_largest_disk_size() {
    local max_size=0
    local size
    
    # Read lsblk output and look for mounted volumes
    while IFS= read -r line; do
        # Skip empty lines and loop devices
        [[ -z "$line" ]] && continue
        [[ "$line" =~ ^loop ]] && continue
        
        # Extract size and unit
        if [[ "$line" =~ ([0-9]+(\.[0-9]+)?)(T|G) ]]; then
            size="${BASH_REMATCH[1]}"
            unit="${BASH_REMATCH[3]}"
            
            # Convert to GB if needed
            if [ "$unit" = "T" ]; then
                size=$(calc "$size * 1024")
            fi
            
            # Update max_size if current size is larger
            if (( $(echo "$size > $max_size" | bc -l) )); then
                max_size=$size
            fi
        fi
    done < <(lsblk -b -o SIZE,TYPE,MOUNTPOINTS | grep -vE '^$|^loop')
    
    # Ensure we have a positive number
    if (( $(echo "$max_size == 0" | bc -l) )); then
        # Fallback to df if lsblk didn't give us good results
        max_size=$(df -BG | grep -vE '^tmpfs|^udev|^/dev/loop' | awk '{print $2}' | sed 's/G//' | sort -nr | head -1)
    fi
    
    echo "$max_size"
}

# Function for safe disk size adjustment
adjust_disk_size() {
    local size=$1
    local adjusted
    
    # Ensure minimum size is 20GB
    if (( $(echo "$size < 20" | bc -l) )); then
        echo "20"
        return
    fi
    
    # Calculate adjusted size (size - 20) * 0.85
    adjusted=$(calc "($size - 20) * 0.85")
    
    # Ensure result is positive
    if (( $(echo "$adjusted <= 0" | bc -l) )); then
        echo "20"
        return
    fi
    
    echo "$adjusted"
}

# Function to get maximum network speed in Mbps
get_max_network_speed() {
    local max_speed=0
    local current_speed
    
    # Check all network interfaces except loopback and virtual interfaces
    for interface in /sys/class/net/*; do
        # Skip if not a physical or bond interface
        if [[ ! -d "$interface" ]] || [[ "$(basename $interface)" == "lo" ]] || 
           [[ "$(basename $interface)" == virbr* ]] || 
           [[ "$(basename $interface)" == bonding_masters ]]; then
            continue
        fi
        
        # Get interface name
        iface=$(basename $interface)
        
        # Get speed from interface
        if [[ -f "$interface/speed" ]]; then
            current_speed=$(cat "$interface/speed" 2>/dev/null || echo 0)
            
            # Convert invalid speeds to 0
            if [[ $current_speed -lt 0 ]]; then
                current_speed=0
            fi
            
            # Update max_speed if current_speed is higher
            if [[ $current_speed -gt $max_speed ]]; then
                max_speed=$current_speed
            fi
        fi
    done
    
    # If no speed was found, check ethtool output for active interfaces
    if [[ $max_speed -eq 0 ]]; then
        for iface in $(ip link show up | grep -v 'lo:' | awk -F: '{print $2}' | tr -d ' '); do
            if command -v ethtool >/dev/null 2>&1; then
                current_speed=$(ethtool $iface 2>/dev/null | grep 'Speed:' | awk '{print $2}' | sed 's/[^0-9]//g')
                if [[ ! -z "$current_speed" && $current_speed -gt $max_speed ]]; then
                    max_speed=$current_speed
                fi
            fi
        done
    fi
    
    # Default to 1000 if no speed could be determined
    if [[ $max_speed -eq 0 ]]; then
        max_speed=1000
    fi
    
    echo $max_speed
}

# Function to extract GPU VRAM size
extract_gpu_vram() {
    local gpu_info=$1
    if [[ $gpu_info =~ ([0-9]+)GB ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "0"  # Return 0 if no GB value found
    fi
}

# Function to get CPU frequency in GHz
get_cpu_freq() {
    local freq=""
    
    # Try to get max frequency from cpufreq
    if [ -d "/sys/devices/system/cpu/cpu0/cpufreq" ]; then
        freq=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null)
        if [ -n "$freq" ]; then
            # Convert kHz to GHz
            freq=$(echo "scale=1; $freq/1000000" | bc)
            echo "$freq"
            return
        fi
    fi
    
    # Try to get from model name
    freq=$(grep "model name" /proc/cpuinfo | head -n 1 | grep -o "@[[:space:]]*[0-9.]*[[:space:]]*GHz" | grep -o "[0-9.]*" || echo "")
    if [ -n "$freq" ]; then
        echo "$freq"
        return
    fi
    
    # Try to get from lscpu
    if command -v lscpu >/dev/null 2>&1; then
        freq=$(lscpu | grep "CPU max MHz" | awk '{printf "%.1f", $4/1000}')
        if [ -n "$freq" ]; then
            echo "$freq"
            return
        fi
    fi
    
    echo "Unknown"
}

get_disk_type() {
    local disk_type="HDD"
    for device in $(lsblk -d -o name | grep -v "loop\|sr"); do
        if [ -f "/sys/block/$device/queue/rotational" ]; then
            if [ "$(cat /sys/block/$device/queue/rotational)" -eq 0 ]; then
                disk_type="SSD"
                break
            fi
        fi
    done
    echo "$disk_type"
}

get_ram_type() {
    if command -v dmidecode >/dev/null 2>&1; then
        local ram_type=$(sudo dmidecode -t memory | grep -i "DDR" | head -n 1 | grep -o "DDR[0-9]*" || echo "")
        if [ -n "$ram_type" ]; then
            echo "$ram_type"
        else
            echo "RAM"  # Fallback если не удалось определить тип
        fi
    else
        echo "RAM"  # Fallback если нет dmidecode
    fi
}

# Get name from user or generate using petname
echo -n "Enter name for the configuration (press Enter for random pet name): "
read custom_name
if [ -z "$custom_name" ]; then
    custom_name=$(petname)
    name="Super $(format_name "$custom_name")"
else
    name="$(format_name "$custom_name")"
fi

# Format the hostname to be like "super-firm-toucan"
hostname_safe=$(format_hostname "$name")
set_system_hostname "$hostname_safe"

# Get CPU cores (including HyperThreading) and calculate reserved cores
total_cores=$(nproc)
reserved_cores=$(( total_cores / 16 ))
# Ensure at least 1 core is reserved
# Ensure at least 1 core is reserved and no more than 6
[[ $reserved_cores -lt 1 ]] && reserved_cores=1
[[ $reserved_cores -gt 6 ]] && reserved_cores=6
adjusted_cores=$((total_cores - reserved_cores))

# Get CPU frequency
cpu_freq=$(get_cpu_freq)
freq_text=""
if [ "$cpu_freq" != "Unknown" ]; then
    freq_text=" @ ${cpu_freq}GHz"
fi

# Get total RAM in bytes
total_ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
total_ram_gb=$(calc "$total_ram_kb / 1024 / 1024")
adjusted_ram_gb=$(calc "$total_ram_gb - 20")
adjusted_ram_gb=$(calc "$adjusted_ram_gb * 0.9")
ram_bytes=$(to_bytes "$adjusted_ram_gb")

# Get largest disk size in GB and adjust it
disk_size_gb=$(get_largest_disk_size)
adjusted_disk_gb=$(adjust_disk_size "$disk_size_gb")
disk_bytes=$(to_bytes "$adjusted_disk_gb")

# Get RAM and disk types
ram_type=$(get_ram_type)
disk_type=$(get_disk_type)

# GPU detection
gpu_present=0
vram_bytes=0
gpu_cores=0
gpu_info=""
gpu_name=""
gpu_count=0

# First, ensure lspci is installed
if ! command -v lspci &> /dev/null; then
    echo "Installing pciutils..."
    sudo apt update && sudo apt install -y pciutils
fi

# Get list of NVIDIA GPUs
gpu_list=$(lspci -nnk -d 10de: | grep -E '3D controller' || true)
if [ ! -z "$gpu_list" ]; then
    gpu_present=1
    # Count number of GPUs
    gpu_cores=$(echo "$gpu_list" | wc -l)
    
    # Get first GPU info for name template
    first_gpu=$(echo "$gpu_list" | head -n 1)
    gpu_name=$(echo "$first_gpu" | sed -n 's/.*NVIDIA Corporation \([^[]*\).*/\1/p' | sed 's/[[:space:]]*$//')
    
    # Extract VRAM size from nvidia-smi if available
    if command -v nvidia-smi &> /dev/null; then
        vram_per_gpu=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1)
        if [ ! -z "$vram_per_gpu" ]; then
            total_vram_gb=$((vram_per_gpu * gpu_cores / 1024))
            vram_bytes=$(to_bytes "$total_vram_gb")
        fi
    else
        # If nvidia-smi not available, try to extract from device name
        if [[ $gpu_name =~ ([0-9]+)GB ]]; then
            vram_per_gpu="${BASH_REMATCH[1]}"
            total_vram_gb=$((vram_per_gpu * gpu_cores))
            vram_bytes=$(to_bytes "$total_vram_gb")
        fi
    fi
    
    # Format GPU name with count for description
    if [ $gpu_cores -gt 1 ]; then
        gpu_name="${gpu_cores}x${gpu_name}"
    fi
    
    echo "Detected GPUs: $gpu_name with total VRAM: $((vram_bytes / 1024 / 1024 / 1024))GB"
else
    echo "No NVIDIA GPU detected"
fi

# Calculate network bandwidth for detected speed in bytes per second
# Speed in Mbps = (speed * 1000 * 1000) / 8 bytes per second
network_speed=$(get_max_network_speed)
bandwidth=$(calc "$network_speed * 1000 * 1000 / 8")

# Get CPU model
cpu_model=$(grep "model name" /proc/cpuinfo | head -n 1 | cut -d':' -f2 | xargs)
if [ -z "$cpu_model" ]; then
    cpu_model="Unknown CPU"
fi

# Get network speed in Gbps (convert from Mbps)
network_speed_gbps=$(calc "$network_speed / 1000")

# Create description in English with CPU frequency
if [ $gpu_present -eq 1 ]; then
    description="CPU: $cpu_model${freq_text}, $adjusted_cores cores, $adjusted_ram_gb GB ${ram_type}, GPU: $gpu_name, $adjusted_disk_gb GB ${disk_type}, ${network_speed_gbps} Gbps network"
else
    description="CPU: $cpu_model${freq_text}, $adjusted_cores cores, $adjusted_ram_gb GB ${ram_type}, $adjusted_disk_gb GB ${disk_type}, ${network_speed_gbps} Gbps network"
fi

floor_divide_precision() {
    local dividend=$1
    local divisor=$2
    echo "$dividend $divisor" | awk '{printf "%.3f", $1/$2}'
}

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

# Define base prices
base_price_slot1="167000000000000000"
base_price_slot2="500000000000000000"

# Calculate final prices based on GPU presence
if [ $gpu_present -eq 1 ]; then
    slot1_price=$(calc "$base_price_slot1 * 4")
    slot2_price=$(calc "$base_price_slot2 * 4")
else
    slot1_price=$base_price_slot1
    slot2_price=$base_price_slot2
fi

# Calculate values for slot1
slot1_gpu_cores=$(floor_divide_precision $gpu_cores $adjusted_cores)
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
      "price":"$slot1_price",
      "priceType":"0"
   }
}
EOF

# Calculate values for slot2
slot2_gpu_cores=$(floor_divide_precision $((gpu_cores * 3)) $adjusted_cores)
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
      "price":"$slot2_price",
      "priceType":"0"
   }
}
EOF

echo "offer.json has been generated successfully."
echo "slot1.json has been generated successfully."
echo "slot2.json has been generated successfully."
