# GPUWhistle quick start

Linux, Python >=3.9 with venv/pip/curses, and an existing accessible NVIDIA driver are required. No sudo is needed.

```bash
git clone https://github.com/WengJGo/GPUWhistle.git
cd GPUWhistle
source ./setup.sh
seegpu
```

Use Bash for `source`. Full setup installs a user-level periodic sampler and samples once. To install without background scheduling:

```bash
source ./setup.sh --no-sample --no-schedule
seegpu once
seegpu
```

A locally built portable archive can be extracted and installed the same way:

```bash
tar -xzf GPUWhistle-0.3.0-linux.tar.gz
cd GPUWhistle
source ./setup.sh
seegpu
```

Portable archives contain runtime wheels and need no package-index access. Source installations fetch dependencies. Neither method installs system Python, missing venv components, or GPU drivers.

Press q to exit, h for help, 1/3/7 for history, g for GPU selection, and Enter for user details. Use `seegpu doctor` for diagnostics; `seegpu schedule status` / `remove` inspect or remove automatic collection. User systemd timers may stop on logout without linger; use `schedule install --backend cron` when a system cron daemon is available.

Press t for historical idle occupancy ranking, including users who have already exited. Default range is all retained history (up to 30 days); 0/1/3/7 changes the range. ZERO~ is a period-weighted estimate, SAMP is measured zero-activity window time, and UNKNOWN is never counted as idle. Enter shows evidence; t returns. Export with `seegpu idle --format csv` or `--format json`.

See [README](README.md) / [中文说明](README.zh-CN.md) for metrics and limits. UNKNOWN means unknown, and sampled observations do not describe unobserved time.
