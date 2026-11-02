#!/usr/bin/env bash
set -euo pipefail
install_root="${GPUMONITOR_PREFIX:-$HOME/.local}"
venv_dir="$install_root/share/gpumonitor/venv"
marker="$venv_dir/.gpumonitor-managed"
if [[ -f "$marker" && ! -L "$venv_dir" ]]; then
    "$venv_dir/bin/python" -m gpumonitor schedule remove
    if "$venv_dir/bin/python" -c "import importlib.util; raise SystemExit(importlib.util.find_spec('gpumonitor.user_setup') is None)"; then
        "$venv_dir/bin/python" -m gpumonitor.user_setup --prefix "$install_root" --remove
    fi
    for name in gpumonitor seegpu; do
        entry="$install_root/bin/$name"
        if [[ -L "$entry" && "$(readlink -- "$entry")" == "$venv_dir/bin/$name" ]]; then
            rm -- "$entry"
        fi
    done
    rm -rf -- "$venv_dir"
    echo 'Uninstalled. Configuration, history database and project source preserved.'
else
    echo "No managed installation found at $venv_dir; nothing removed."
fi
