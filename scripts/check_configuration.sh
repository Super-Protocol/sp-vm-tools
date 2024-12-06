#!/bin/bash

# Function to convert bytes to human readable format
convert_size() {
    local bytes=$1
    if [ $bytes -ge 1073741824 ]; then # 1 GiB
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1024/1024/1024}") GiB"
    elif [ $bytes -ge 1048576 ]; then # 1 MiB
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1024/1024}") MiB"
    else
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1024}") KiB"
    fi
}

echo "=== Hardware Configuration ==="
echo

# 1. CPU Information
echo "CPU Information:"
# Physical CPUs (Sockets)
physical_cpus=$(lscpu | grep "Socket(s):" | awk '{print $2}')
# Cores per socket
cores_per_socket=$(lscpu | grep "Core(s) per socket:" | awk '{print $4}')
# Total threads (including hyperthreading)
total_threads=$(nproc)
# CPU Model
cpu_model=$(lscpu | grep "Model name:" | sed 's/Model name:[[:space:]]*//g')

echo "  Model: $cpu_model"
echo "  Physical CPUs (Sockets): $physical_cpus"
echo "  Cores per socket: $cores_per_socket"
echo "  Total CPU threads: $total_threads"
echo

# 2. Memory Information
echo "Memory Information:"
# Get total memory in bytes
total_mem=$(grep MemTotal /proc/meminfo | awk '{print $2 * 1024}')
# Get available memory in bytes
avail_mem=$(grep MemAvailable /proc/meminfo | awk '{print $2 * 1024}')
# Get used memory
used_mem=$((total_mem - avail_mem))

echo "  Total Memory: $(convert_size $total_mem)"
echo "  Used Memory: $(convert_size $used_mem)"
echo "  Available Memory: $(convert_size $avail_mem)"
echo

# 3. Network Information
echo "Network Information:"
for interface in $(ls /sys/class/net/ | grep -v "lo"); do
    echo "  Interface: $interface"
    # Get interface speed (if available)
    if [ -f "/sys/class/net/$interface/speed" ]; then
        speed=$(cat "/sys/class/net/$interface/speed" 2>/dev/null)
        [ ! -z "$speed" ] && echo "    Speed: ${speed} Mbps"
    fi
    # Get interface state
    state=$(cat "/sys/class/net/$interface/operstate")
    echo "    State: $state"
    # Get IP address
    ip_addr=$(ip addr show $interface | grep "inet " | awk '{print $2}')
    [ ! -z "$ip_addr" ] && echo "    IP Address: $ip_addr"
    # Get MAC address
    mac=$(cat "/sys/class/net/$interface/address")
    echo "    MAC Address: $mac"
    echo
done

# 4. Disk Information
echo "Disk Information:"
# Using lsblk for disk information
echo "  Block Devices:"
lsblk --output NAME,SIZE,TYPE,MOUNTPOINT | grep -v "loop" | sed 's/^/    /'
echo

# Additional disk usage information
echo "  Filesystem Usage:"
df -h | grep -v "loop" | grep -v "tmpfs" | sed 's/^/    /'

# Optional: SMART information for physical disks
if command -v smartctl &> /dev/null; then
    echo
    echo "  SMART Information (if available):"
    for disk in $(lsblk -d -n -o NAME); do
        if [[ $disk == sd* ]] || [[ $disk == nvme* ]]; then
            echo "    Disk /dev/$disk:"
            sudo smartctl -i /dev/$disk 2>/dev/null | grep -E "Device Model|User Capacity|Rotation Rate|Form Factor|Transport protocol" | sed 's/^/      /'
        fi
    done
fi
