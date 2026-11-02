#!/usr/bin/env python3
"""Build a portable source archive with platform-independent runtime wheels."""
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gpumonitor import __version__  # noqa: E402


def main():
    output = ROOT / 'dist'
    wheels = output / 'wheelhouse'
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-build-isolation', '--no-deps',
                    '--wheel-dir', str(wheels), str(ROOT)], check=True)
    dependency = wheels / 'nvidia_ml_py-13.615.71-py3-none-any.whl'
    if not dependency.exists():
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--no-deps',
                        '--dest', str(wheels), 'nvidia-ml-py==13.615.71'], check=True)
    archive = output / ('GPUWhistle-%s-linux.tar.gz' % __version__)
    with tempfile.TemporaryDirectory(prefix='gpumonitor-release-') as temporary:
        package = Path(temporary) / 'GPUWhistle'
        package.mkdir()
        for name in ('README.md', 'README.zh-CN.md', 'QUICKSTART.md', 'TESTING.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE', 'pyproject.toml',
                     'requirements.txt', 'config.example.json', 'theme.example.json', 'setup.sh', 'install.sh', 'uninstall.sh'):
            shutil.copy2(ROOT / name, package / name)
        for name in ('gpumonitor', 'packaging', 'tests', 'tools', 'docs', 'licenses'):
            shutil.copytree(ROOT / name, package / name,
                            ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc', '*.ansi', '*.txt', '*.log'))
        (package / 'wheelhouse').mkdir()
        for wheel in (wheels / ('gpuwhistle-%s-py3-none-any.whl' % __version__), dependency):
            shutil.copy2(wheel, package / 'wheelhouse' / wheel.name)
        checksums = []
        for path in sorted(p for p in package.rglob('*') if p.is_file()):
            checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(package).as_posix())
        (package / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n')
        with tarfile.open(archive, 'w:gz') as handle:
            handle.add(package, arcname='GPUWhistle')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + '.sha256').write_text(checksum + '  ' + archive.name + '\n')
    print('Release:', archive, '(%s bytes)' % archive.stat().st_size)


if __name__ == '__main__':
    main()
