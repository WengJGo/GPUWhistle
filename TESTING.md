# Development and release checks

Tests run on Linux without an NVIDIA GPU. NVML behavior is mocked; `/proc` self-inspection and pseudo-terminal tests use ordinary Linux facilities.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Coverage includes process/user aggregation, missing and unsupported utilization, PID identity reuse, permissions, activity/inactivity boundaries, unknown sample preservation, rolling history, SQLite retention and locking, scheduler idempotence, CLI exports, responsive layout, semantic colors, NO_COLOR, ASCII, and repeated PTY resizing. Fixtures are synthetic; they contain no production PID, process command line, host address, or monitoring database.

## Reproduce terminal previews

```bash
.venv/bin/python -m pip install pyte Pillow
.venv/bin/python tools/render_tui_snapshots.py
```

These are optional development tools, not runtime dependencies. Preview databases are temporary and use generated data. PNG images are safe illustrative assets; raw terminal streams and grids are ignored by Git.

## Portable archive

```bash
.venv/bin/python -m pip install setuptools wheel
.venv/bin/python tools/build_release.py
.venv/bin/python tools/validate_release.py dist/GPUWhistle-0.2.1-linux.tar.gz
```

Validation installs from bundled wheels with package-index access disabled, checks extraction checksums, repeated setup, PATH configuration, and configuration preservation in isolated directories. `--sample` is an explicit opt-in to actual read-only GPU sampling; it does not install a scheduler.

## Publication audit

After selecting files explicitly, audit the staged Git objects and final history:

```bash
python3 tools/audit_publication.py --staged
python3 tools/audit_publication.py --history main
```

The local audit reports finding category, relative path, and line number, never matched secret values. It rejects monitoring artifacts, local machine paths/identifiers, credentials and unexpected commit identities. It supplements manual review; no scanner can prove every possible secret is absent. Local deployment notes, real observations, caches, archives, and databases must remain outside published history.
