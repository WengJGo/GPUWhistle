# Third-party notices

GPUWhistle's original implementation is MIT licensed under [LICENSE](LICENSE). Upstream dependencies keep their own licenses.

| Component | Use | License / source |
| --- | --- | --- |
| nvidia-ml-py | Only third-party runtime dependency; imports pynvml | NVIDIA's three-clause BSD license; [retained notice](licenses/nvidia-ml-py.LICENSE), [upstream distribution](https://pypi.org/project/nvidia-ml-py/) |
| Python standard library | curses, SQLite, argparse and system interfaces | Python's own licensing; not vendored |
| pytest | Optional development tests | MIT; not vendored |
| setuptools / wheel | Build and installation tooling | Their upstream licenses and bundled notices; not vendored |

Portable archives may include the unmodified nvidia-ml-py wheel. Its source header and copyright/license remain present; the retained BSD notice accompanies the archive.

Interface inspiration: [nvitop](https://github.com/XuehaiPan/nvitop), [nvtop](https://github.com/Syllo/nvtop), and htop. Their code is not included in GPUWhistle.
