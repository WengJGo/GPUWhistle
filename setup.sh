#!/usr/bin/env bash
# source this file in Bash to update the current terminal as well.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    if bash "${BASH_SOURCE[0]}" "$@"; then
        if [[ ! -x "${GPUMONITOR_PREFIX:-$HOME/.local}/bin/seegpu" ]]; then
            return 0
        fi
        case ":$PATH:" in
            *:"${GPUMONITOR_PREFIX:-$HOME/.local}/bin":*) ;;
            *) export PATH="${GPUMONITOR_PREFIX:-$HOME/.local}/bin:$PATH" ;;
        esac
        echo '当前终端已配置好，输入 seegpu 即可打开监看页面。'
        return 0
    else
        return "$?"
    fi
fi
set -euo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sample=1
schedule=1
for arg in "$@"; do
    case "$arg" in
        --no-sample) sample=0 ;;
        --no-schedule) schedule=0 ;;
        --help|-h)
            echo 'Usage: source ./setup.sh [--no-sample] [--no-schedule]'
            echo 'Installs seegpu, configures shell PATH, samples once and enables automatic collection by default.'
            exit 0 ;;
        *) echo "Unknown setup option: $arg" >&2; exit 2 ;;
    esac
done
bash "$project_dir/install.sh"
install_root="${GPUMONITOR_PREFIX:-$HOME/.local}"
python="$install_root/share/gpumonitor/venv/bin/python"
cli="$install_root/bin/seegpu"
"$python" -m gpumonitor.user_setup --prefix "$install_root"
if [[ $sample == 1 ]]; then
    echo '正在检查 GPU 并进行首次采样（约 10 秒）...'
    if "$cli" doctor; then
        if ! "$cli" once; then
            echo '首次采样不完整，已保留诊断信息；可使用 seegpu doctor 检查。' >&2
        fi
    else
        echo '安装已完成，GPU 环境检查未通过。修复环境后运行 seegpu once。' >&2
    fi
fi
if [[ $schedule == 1 ]]; then
    backend=auto
    if command -v crontab >/dev/null 2>&1; then
        backend=cron
    fi
    if ! "$cli" schedule install --backend "$backend"; then
        echo '软件已安装，但自动采集未启用。请修复调度环境后重新运行 setup.sh；仅安装可使用 --no-schedule。' >&2
        exit 1
    fi
    echo '自动采集已启用；采集周期和历史保留天数使用配置文件（默认 10 分钟、30 天）。'
fi
echo '安装配置完成。新终端输入 seegpu；在当前 Bash 中使用 source ./setup.sh 可立即生效。'
echo '页面快捷键：q 退出，1/3/7 切换历史，g 切换 GPU，u 切换用户/进程。'
