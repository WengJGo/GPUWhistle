# Third-party notices

GPUWhistle's original implementation is MIT licensed under [LICENSE](LICENSE). Upstream dependencies keep their own licenses.

| Component | Use | License / source |
| --- | --- | --- |
| nvidia-ml-py | Only third-party runtime dependency; imports pynvml | NVIDIA's three-clause BSD license; [retained notice](licenses/nvidia-ml-py.LICENSE), [upstream distribution](https://pypi.org/project/nvidia-ml-py/) |
| Python standard library | curses, SQLite, argparse and system interfaces | Python's own licensing; not vendored |
| pytest | Optional development tests | MIT; not vendored |
| setuptools / wheel | Build and installation tooling | Their upstream licenses and bundled notices; not vendored |
| pyte | Optional terminal preview replay | GNU LGPL version 3; installed separately, not vendored; [upstream](https://github.com/selectel/pyte) |
| Pillow | Optional preview PNG rendering | MIT-CMU and upstream bundled notices; installed separately, not vendored; [upstream](https://github.com/python-pillow/Pillow) |

Portable archives may include the unmodified nvidia-ml-py wheel. Its source header and copyright/license remain present; the retained BSD notice accompanies the archive. Optional preview tools are not included as runtime dependencies or bundled libraries.

The interface is visually inspired by [nvitop](https://github.com/XuehaiPan/nvitop), [nvtop](https://github.com/Syllo/nvtop), and htop. The nvitop CLI/TUI and nvtop have GPL license boundaries. GPUWhistle does not copy, import, or vendor their implementation code. Its NVML adapter, layout, renderer, statistics and tests are original code. API/library use and visual inspiration do not replace upstream license notices.
