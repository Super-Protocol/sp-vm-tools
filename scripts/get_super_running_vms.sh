#!/bin/bash
# Save as /etc/update-motd.d/99-get_super_running_vms

# Variable for binary name (configurable)
BINARY_NAME="qemu-system-x86_64"

# Get all qemu processes and calculate max widths dynamically
temp_file=$(mktemp)
ps aux | grep "$BINARY_NAME" | grep -v grep > "$temp_file"

if [ ! -s "$temp_file" ]; then
    echo "No QEMU processes running"
    rm "$temp_file"
    exit 0
fi

headers=("pid" "start" "cores" "mem" "disk" "mode" "debug" "env/branch" "release" "provider_config" "cache" "ip" "http/https/ssh" "cid")

# Initialize column widths with header lengths
declare -a col_widths
for i in "${!headers[@]}"; do
    col_widths[$i]=${#headers[$i]}
done

# Parse processes and calculate max widths
declare -a rows
while IFS= read -r line; do
    # Extract fields from ps aux output
    pid=$(echo "$line" | awk '{print $2}')
    started=$(echo "$line" | awk '{print $9}')  # Only the START field (Jul01, Jul03)
    cmd=$(echo "$line" | cut -d' ' -f11-)

    # Extract parameters using correct QEMU patterns
    cores=$(echo "$cmd" | grep -o -- '-smp cores=[0-9]*' | cut -d'=' -f2 | head -1)
    mem=$(echo "$cmd" | grep -o -- '-m [0-9]*[GM]' | cut -d' ' -f2 | head -1)

    # Extract disk size using qemu-img from state.qcow2 file with force-share
    # Show both disk size and virtual size as "210G/4.39T"
    disk_path=$(echo "$cmd" | grep -o -- '-drive file=[^,]*state\.qcow2' | cut -d'=' -f2 | head -1)
    if [ -n "$disk_path" ] && [ -f "$disk_path" ]; then
        qemu_info=$(qemu-img info --force-share "$disk_path" 2>/dev/null)
        if [ $? -eq 0 ]; then
            # Extract both number and unit, then convert to short format
            virtual_size=$(echo "$qemu_info" | grep "virtual size:" | awk '{print $3 $4}' | sed 's/GiB/G/g; s/TiB/T/g; s/MiB/M/g; s/KiB/K/g' | head -1 | tr -d '\n')
            disk_size=$(echo "$qemu_info" | grep "disk size:" | awk '{print $3 $4}' | sed 's/GiB/G/g; s/TiB/T/g; s/MiB/M/g; s/KiB/K/g' | head -1 | tr -d '\n')
            if [ -n "$disk_size" ] && [ -n "$virtual_size" ]; then
                disk="$disk_size/$virtual_size"
            else
                disk="-"
            fi
        else
            disk="-"
        fi
    else
        disk="-"
    fi

    # Extract mode from -machine confidential-guest-support= or memory-encryption=
    mode=$(echo "$cmd" | grep -o 'confidential-guest-support=[^,]*' | cut -d'=' -f2 | head -1)
    if [ -z "$mode" ]; then
        mode=$(echo "$cmd" | grep -o 'memory-encryption=[^,]*' | cut -d'=' -f2 | head -1)
    fi

    debug=$(echo "$cmd" | grep -o 'sp-debug=[^[:space:]]*' | cut -d'=' -f2 | head -1)

    # Extract release from rootfs.img path (e.g., /data/virtualmachine/build-113/rootfs.img -> build-113)
    release=$(echo "$cmd" | grep -o -- '-drive file=[^,]*rootfs\.img' | sed 's/.*\/\(build-[^\/]*\)\/.*/\1/' | head -1)

    # Extract cache from drive file path (e.g., /data/virtualmachine/state.qcow2 -> /data/virtualmachine)
    cache=$(echo "$cmd" | grep -o -- '-drive file=[^,]*state\.qcow2' | cut -d'=' -f2 | sed 's/\/[^\/]*$//' | head -1)

    cid=$(echo "$cmd" | grep -o 'guest-cid=[0-9]*' | cut -d'=' -f2 | head -1)
    env=$(echo "$cmd" | grep -o 'argo_sp_env=[^[:space:]]*' | cut -d'=' -f2 | head -1)
    branch=$(echo "$cmd" | grep -o 'argo_branch=[^[:space:]]*' | cut -d'=' -f2 | head -1)

    # Extract provider_config from fsdev path (e.g., /data/virtualmachine/provider_config)
    provider_config=$(echo "$cmd" | grep -o 'path=[^[:space:]]*provider_config[^[:space:]]*' | cut -d'=' -f2 | head -1)

    # Extract IP from hostfwd (first IP found, excluding 127.0.0.1)
    ip=$(echo "$cmd" | grep -o 'hostfwd=tcp:[^:]*' | grep -v '127.0.0.1' | head -1 | cut -d':' -f2)

    # Combine env/branch
    if [ -n "$env" ] && [ -n "$branch" ]; then
        env_branch="$env/$branch"
    elif [ -n "$env" ]; then
        env_branch="$env"
    elif [ -n "$branch" ]; then
        env_branch="$branch"
    else
        env_branch="-"
    fi

    # Extract ONLY port numbers from hostfwd parameters
    ports=$(echo "$cmd" | grep -o 'hostfwd=tcp:[^:]*:[0-9]*' | sed 's/.*://' | paste -sd '/' -)

    # Set defaults for empty values
    cores=${cores:--}
    mem=${mem:--}
    disk=${disk:--}
    mode=${mode:--}
    debug=${debug:--}
    release=${release:--}
    provider_config=${provider_config:--}
    cache=${cache:--}
    ip=${ip:--}
    cid=${cid:--}
    ports=${ports:--}

    # Store row data
    row=("$pid" "$started" "$cores" "$mem" "$disk" "$mode" "$debug" "$env_branch" "$release" "$provider_config" "$cache" "$ip" "$ports" "$cid")
    rows+=("$(IFS='|'; echo "${row[*]}")")

    # Update column widths
    for i in "${!row[@]}"; do
        if [ ${#row[$i]} -gt ${col_widths[$i]} ]; then
            col_widths[$i]=${#row[$i]}
        fi
    done

done < "$temp_file"

# Build table borders
line_top="┌"
line_sep="├"
line_bottom="└"

for width in "${col_widths[@]}"; do
    line_top+="$(printf '─%.0s' $(seq 1 $((width + 2))))┬"
    line_sep+="$(printf '─%.0s' $(seq 1 $((width + 2))))┼"
    line_bottom+="$(printf '─%.0s' $(seq 1 $((width + 2))))┴"
done

line_top="${line_top%┬}┐"
line_sep="${line_sep%┼}┤"
line_bottom="${line_bottom%┴}┘"

# Print table
echo "$line_top"

# Print header
header_line="│"
for i in "${!headers[@]}"; do
    printf -v padded_header " %-*s " "${col_widths[$i]}" "${headers[$i]}"
    header_line+="$padded_header│"
done
echo "$header_line"

echo "$line_sep"

# Print data rows
for row_data in "${rows[@]}"; do
    IFS='|' read -ra row_array <<< "$row_data"
    data_line="│"
    for i in "${!row_array[@]}"; do
        printf -v padded_data " %-*s " "${col_widths[$i]}" "${row_array[$i]}"
        data_line+="$padded_data│"
    done
    echo "$data_line"
done

echo "$line_bottom"

# Cleanup
rm "$temp_file"
