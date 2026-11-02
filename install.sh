#!/usr/bin/env bash
set -euo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_root="${GPUMONITOR_PREFIX:-$HOME/.local}"
venv_dir="$install_root/share/gpumonitor/venv"
marker="$venv_dir/.gpumonitor-managed"
mkdir -p "$install_root/bin" "$(dirname -- "$venv_dir")"
if [[ -e "$venv_dir" || -L "$venv_dir" ]]; then
    [[ ! -L "$venv_dir" && -f "$marker" ]] || { echo "Refusing unmanaged venv: $venv_dir" >&2; exit 1; }
fi
for name in gpumonitor seegpu; do
    entry="$install_root/bin/$name"
    if [[ -e "$entry" || -L "$entry" ]]; then
        [[ -L "$entry" && "$(readlink -- "$entry")" == "$venv_dir/bin/$name" ]] || { echo "Refusing unmanaged entry: $entry" >&2; exit 1; }
    fi
done
if [[ ! -f "$marker" ]]; then
    "${PYTHON:-python3}" -m venv "$venv_dir"
    touch "$marker"
fi
if [[ -d "$project_dir/wheelhouse" ]]; then
    wheels=("$project_dir"/wheelhouse/gpuwhistle-*.whl)
    [[ ${#wheels[@]} == 1 && -f "${wheels[0]}" ]] || { echo 'Expected exactly one bundled GPUWhistle wheel' >&2; exit 1; }
fi
if "$venv_dir/bin/python" -m pip show gpumonitor >/dev/null 2>&1; then
    "$venv_dir/bin/python" -m pip uninstall -y gpumonitor
fi
if [[ -d "$project_dir/wheelhouse" ]]; then
    "$venv_dir/bin/python" -m pip install --no-index --find-links "$project_dir/wheelhouse" --force-reinstall "${wheels[0]}"
else
    "$venv_dir/bin/python" -m pip install "$project_dir"
fi
for name in gpumonitor seegpu; do
    ln -sfn -- "$venv_dir/bin/$name" "$install_root/bin/$name"
done
echo "Installed: $install_root/bin/seegpu (also gpumonitor)"
echo 'For automatic configuration: source ./setup.sh'
