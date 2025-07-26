#!/bin/bash

# Parse command line arguments
CAPACITY_DIVIDER=1
while [[ $# -gt 0 ]]; do
    case $1 in
        --capacity-divider)
            CAPACITY_DIVIDER="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [--capacity-divider NUMBER]"
            echo "  --capacity-divider: Divide system capacity by this number (default: 1)"
            echo "                     Example: --capacity-divider 2 uses half of the system resources"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate capacity divider
if ! [[ "$CAPACITY_DIVIDER" =~ ^[0-9]+(\.[0-9]+)?$ ]] || (( $(echo "$CAPACITY_DIVIDER <= 0" | bc -l) )); then
    echo "Error: capacity-divider must be a positive number"
    exit 1
fi

echo "Using capacity divider: $CAPACITY_DIVIDER"

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

#!/bin/bash

# Enhanced function to get GPU memory from name or known models
get_gpu_memory_from_name() {
    local gpu_name=$1
    
    # First try to extract memory size from GPU name (e.g., "H200 SXM 141GB" or "H100 PCIe 80GB")
    if [[ $gpu_name =~ [^0-9]([0-9]+)GB ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
    fi
    
    # Fallback: Known GPU memory sizes based on model names
    # Convert to lowercase for easier matching
    local gpu_lower=$(echo "$gpu_name" | tr '[:upper:]' '[:lower:]')
    
    # NVIDIA H-series
    if [[ $gpu_lower =~ h200 ]]; then
        echo "141"  # H200 typically has 141GB
        return 0
    elif [[ $gpu_lower =~ h100 ]]; then
        if [[ $gpu_lower =~ sxm ]]; then
            echo "80"   # H100 SXM typically 80GB
        else
            echo "80"   # H100 PCIe typically 80GB
        fi
        return 0
    elif [[ $gpu_lower =~ h800 ]]; then
        echo "80"   # H800 typically 80GB
        return 0
    fi
    
    # NVIDIA A-series
    if [[ $gpu_lower =~ a100 ]]; then
        if [[ $gpu_lower =~ sxm ]]; then
            echo "80"   # A100 SXM4 80GB
        else
            echo "40"   # A100 PCIe typically 40GB, some 80GB
        fi
        return 0
    elif [[ $gpu_lower =~ a800 ]]; then
        echo "80"   # A800 typically 80GB
        return 0
    elif [[ $gpu_lower =~ a6000 ]]; then
        echo "48"   # RTX A6000 48GB
        return 0
    elif [[ $gpu_lower =~ a5000 ]]; then
        echo "24"   # RTX A5000 24GB
        return 0
    elif [[ $gpu_lower =~ a4000 ]]; then
        echo "16"   # RTX A4000 16GB
        return 0
    fi
    
    # NVIDIA RTX series
    if [[ $gpu_lower =~ rtx.*4090 ]]; then
        echo "24"   # RTX 4090 24GB
        return 0
    elif [[ $gpu_lower =~ rtx.*4080 ]]; then
        echo "16"   # RTX 4080 16GB
        return 0
    elif [[ $gpu_lower =~ rtx.*3090 ]]; then
        echo "24"   # RTX 3090 24GB
        return 0
    elif [[ $gpu_lower =~ rtx.*3080 ]]; then
        echo "10"   # RTX 3080 10GB (some 12GB variants exist)
        return 0
    fi
    
    # NVIDIA V-series
    if [[ $gpu_lower =~ v100 ]]; then
        if [[ $gpu_lower =~ sxm ]]; then
            echo "32"   # V100 SXM2 32GB
        else
            echo "16"   # V100 PCIe 16GB
        fi
        return 0
    fi
    
    # NVIDIA T-series
    if [[ $gpu_lower =~ t4 ]]; then
        echo "16"   # Tesla T4 16GB
        return 0
    fi
    
    # GB100/B200 series (newer models)
    if [[ $gpu_lower =~ gb100 ]] || [[ $gpu_lower =~ b200 ]]; then
        echo "192"  # GB100/B200 typically 192GB (adjust based on actual specs)
        return 0
    elif [[ $gpu_lower =~ b100 ]]; then
        echo "128"  # B100 estimated (adjust based on actual specs)
        return 0
    fi
    
    # Default fallback - try to detect using nvidia-smi if available
    return 1
}

# Enhanced function to get VRAM using multiple methods
get_vram_size() {
    local gpu_name=$1
    local vram_gb=""
    
    # Method 1: Try to get from GPU name
    vram_gb=$(get_gpu_memory_from_name "$gpu_name")
    if [ $? -eq 0 ] && [ $vram_gb -gt 0 ]; then
        echo "$vram_gb"
        return 0
    fi
    
    # Method 2: Try nvidia-smi if available
    if command -v nvidia-smi >/dev/null 2>&1; then
        local vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n 1 | tr -d ' ')
        if [[ $vram_mb =~ ^[0-9]+$ ]] && [ $vram_mb -gt 0 ]; then
            vram_gb=$((vram_mb / 1024))
            echo "$vram_gb"
            return 0
        fi
    fi
    
    # Method 3: Try to read from /proc/driver/nvidia/gpus/
    if [ -d "/proc/driver/nvidia/gpus/" ]; then
        for gpu_dir in /proc/driver/nvidia/gpus/*/information; do
            if [ -f "$gpu_dir" ]; then
                local vram_info=$(grep -i "Video Memory" "$gpu_dir" 2>/dev/null || true)
                if [ ! -z "$vram_info" ]; then
                    # Extract memory size in MB and convert to GB
                    local vram_mb=$(echo "$vram_info" | grep -o '[0-9]\+' | head -n 1)
                    if [ ! -z "$vram_mb" ] && [ $vram_mb -gt 0 ]; then
                        vram_gb=$((vram_mb / 1024))
                        echo "$vram_gb"
                        return 0
                    fi
                fi
            fi
        done
    fi
    
    # Method 4: Default based on GPU type (conservative estimates)
    echo "Attempting fallback VRAM detection for: $gpu_name" >&2
    local gpu_lower=$(echo "$gpu_name" | tr '[:upper:]' '[:lower:]')
    
    if [[ $gpu_lower =~ gb100|b200 ]]; then
        echo "192"  # Conservative estimate for GB100/B200
    elif [[ $gpu_lower =~ h100|h200 ]]; then
        echo "80"   # Conservative estimate for H100/H200
    elif [[ $gpu_lower =~ a100 ]]; then
        echo "40"   # Conservative estimate for A100
    else
        echo "16"   # Very conservative fallback
    fi
    
    return 0
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
            echo "RAM"  # Fallback if unable to determine type
        fi
    else
        echo "RAM"  # Fallback if dmidecode is not available
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
# Ensure at least 1 core is reserved and no more than 6
[[ $reserved_cores -lt 1 ]] && reserved_cores=1
[[ $reserved_cores -gt 6 ]] && reserved_cores=6
adjusted_cores=$((total_cores - reserved_cores))

# Apply capacity divider to cores
final_cores=$(calc "$adjusted_cores / $CAPACITY_DIVIDER")
# Ensure at least 1 core
if (( $(echo "$final_cores < 1" | bc -l) )); then
    final_cores=1
fi

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

# Apply capacity divider to RAM
final_ram_gb=$(calc "$adjusted_ram_gb / $CAPACITY_DIVIDER")
# Ensure minimum RAM
if (( $(echo "$final_ram_gb < 1" | bc -l) )); then
    final_ram_gb=1
fi
ram_bytes=$(to_bytes "$final_ram_gb")

# Get largest disk size in GB and adjust it
disk_size_gb=$(get_largest_disk_size)
adjusted_disk_gb=$(adjust_disk_size "$disk_size_gb")

# Apply capacity divider to disk
final_disk_gb=$(calc "$adjusted_disk_gb / $CAPACITY_DIVIDER")
# Ensure minimum disk size
if (( $(echo "$final_disk_gb < 1" | bc -l) )); then
    final_disk_gb=1
fi
disk_bytes=$(to_bytes "$final_disk_gb")

# Get RAM and disk types
ram_type=$(get_ram_type)
disk_type=$(get_disk_type)

# Replace the GPU detection section in your main script with this enhanced version:
# GPU detection (enhanced version)
gpu_present=0
vram_bytes=0
gpu_cores=0
gpu_info=""
gpu_name=""
gpu_count=0
gpu_description=""
final_gpu_cores=0

# First, ensure lspci is installed
if ! command -v lspci &> /dev/null; then
    echo "Installing pciutils..."
    sudo apt update && sudo apt install -y pciutils
fi

# Get list of NVIDIA GPUs
gpu_list=$(lspci -nnk -d 10de: | grep -E '3D controller|VGA compatible controller' || true)
if [ ! -z "$gpu_list" ]; then
    gpu_present=1
    gpu_cores=$(echo "$gpu_list" | wc -l)
    
    # Apply capacity divider to GPU cores
    final_gpu_cores=$(calc "$gpu_cores / $CAPACITY_DIVIDER")
    # Ensure minimum GPU cores (can be fractional)
    if (( $(echo "$final_gpu_cores < 0.1" | bc -l) )); then
        final_gpu_cores=0.1
    fi
    
    # Get first GPU info for name
    first_gpu=$(echo "$gpu_list" | head -n 1)
    first_gpu_id=$(echo "$first_gpu" | cut -d' ' -f1)
    
    # Get full GPU name from lspci
    gpu_info=$(lspci -v -s "$first_gpu_id" | grep "NVIDIA Corporation")
    if [ ! -z "$gpu_info" ]; then
        # Extract full name including "NVIDIA Corporation"
        gpu_name=$(echo "$gpu_info" | sed -n 's/.*\(NVIDIA Corporation.*\[[^]]*\]\).*/\1/p' | sed 's/[[:space:]]*$//')
        
        # If the above regex fails, try a simpler extraction
        if [ -z "$gpu_name" ]; then
            gpu_name=$(echo "$gpu_info" | cut -d':' -f3 | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')
        fi
    fi
    
    # Get VRAM size using enhanced detection
    echo "Detecting VRAM for GPU: $gpu_name"
    vram_gb=$(get_vram_size "$gpu_name")
    if [ $? -eq 0 ] && [ $vram_gb -gt 0 ]; then
        total_vram_gb=$((vram_gb * gpu_cores))
        
        # Apply capacity divider to VRAM
        final_vram_gb=$(calc "$total_vram_gb / $CAPACITY_DIVIDER")
        vram_bytes=$(to_bytes "$final_vram_gb")
        
        echo "Detected VRAM: ${vram_gb}GB per GPU, Total: ${total_vram_gb}GB, Final (after capacity divider): ${final_vram_gb}GB"
    else
        echo "Warning: Could not detect VRAM size for GPU: $gpu_name"
        vram_bytes=0
    fi
    
    # Format GPU description with capacity divider applied
    if (( $(echo "$CAPACITY_DIVIDER == 1" | bc -l) )); then
        gpu_description="GPUs: ${gpu_cores} x ${gpu_name}"
    else
        gpu_description="GPUs: ${final_gpu_cores} x ${gpu_name} (${gpu_cores} total, divided by ${CAPACITY_DIVIDER})"
    fi
    
    echo "$gpu_description"
    echo "Total VRAM: $((vram_bytes / 1024 / 1024 / 1024))GB"
else
    echo "No NVIDIA GPU detected"
fi

# Calculate network bandwidth for detected speed in bytes per second
# Speed in Mbps = (speed * 1000 * 1000) / 8 bytes per second
network_speed=$(get_max_network_speed)

# Apply capacity divider to network speed
final_network_speed=$(calc "$network_speed / $CAPACITY_DIVIDER")
bandwidth=$(calc "$final_network_speed * 1000 * 1000 / 8")

# Get CPU model
cpu_model=$(grep "model name" /proc/cpuinfo | head -n 1 | cut -d':' -f2 | xargs)
if [ -z "$cpu_model" ]; then
    cpu_model="Unknown CPU"
fi

# Get network speed in Gbps (convert from Mbps)
network_speed_gbps=$(calc "$final_network_speed / 1000")

if [ $gpu_present -eq 1 ]; then
    description="CPU: $cpu_model${freq_text}, $final_cores cores, $final_ram_gb GB ${ram_type}, GPUs: ${final_gpu_cores} x ${gpu_name}, $final_disk_gb GB ${disk_type}, ${network_speed_gbps} Gbps network"
else
    description="CPU: $cpu_model${freq_text}, $final_cores cores, $final_ram_gb GB ${ram_type}, $final_disk_gb GB ${disk_type}, ${network_speed_gbps} Gbps network"
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

# Generate JSON file using final values (after capacity divider applied)
cat > offer.json << EOF
{
   "name": "$name",
   "description": "$description",
   "teeType": "0",
   "subType": "2",
   "properties": "0",
   "hardwareInfo": {
      "slotInfo": {
         "cpuCores": $final_cores,
         "gpuCores": $final_gpu_cores,
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

# Calculate values for slot1 using final values
slot1_gpu_cores=$(floor_divide_precision $final_gpu_cores $final_cores)
slot1_disk=$(floor_divide $disk_bytes $final_cores)
slot1_ram=$(floor_divide $ram_bytes $final_cores)
slot1_vram=$(floor_divide $vram_bytes $final_cores)

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

# Calculate values for slot2 using final values
slot2_gpu_cores=$(floor_divide_precision $((final_gpu_cores * 3)) $final_cores)
slot2_disk=$(floor_divide $((disk_bytes * 3)) $final_cores)
slot2_ram=$(floor_divide $((ram_bytes * 3)) $final_cores)
slot2_vram=$(floor_divide $((vram_bytes * 3)) $final_cores)

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

echo "Configuration files generated successfully with capacity divider: $CAPACITY_DIVIDER"
echo "Final configuration:"
echo "- CPU cores: $final_cores (from $adjusted_cores adjusted cores)"
echo "- RAM: ${final_ram_gb}GB (from ${adjusted_ram_gb}GB adjusted RAM)"
echo "- GPU cores: $final_gpu_cores (from $gpu_cores total GPU cores)"
echo "- Disk: ${final_disk_gb}GB (from ${adjusted_disk_gb}GB adjusted disk)"
echo "- Network: ${network_speed_gbps}Gbps (from $(calc "$network_speed / 1000")Gbps total speed)"
echo ""
echo "offer.json has been generated successfully."
echo "slot1.json has been generated successfully."
echo "slot2.json has been generated successfully."
