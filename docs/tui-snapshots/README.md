# GPUWhistle terminal previews

All displayed users, devices, process names, identifiers, loads and historical windows are synthetic. These are real curses PTY output rendered into PNG files, not production GPU observations. No host names, connection addresses, home paths or monitoring records are used.

- [Eight-GPU wide layout](eight-gpu-wide.png)
- [Single-GPU user details](single-gpu-detail.png)
- [Seven-day history](history-7d.png)
- [80-column compact layout](compact-80.png)
- [ASCII / NO_COLOR](ascii-no-color.png)
- [Light-background palette preview](eight-gpu-wide-light.png)

The light preview applies a light terminal palette to the same character grid. Actual colors follow the user's terminal palette. Raw terminal streams and text grids are local development outputs and are not published.

Reproduce with `python tools/render_tui_snapshots.py` after installing the optional development tools described in [TESTING.md](../../TESTING.md).
