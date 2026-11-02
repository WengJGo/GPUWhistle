#!/usr/bin/env python3
"""Validate offline extraction/install in isolated task-specific directories."""
import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sample', action='store_true', help='also run read-only NVML first sampling')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='gpumonitor-acceptance-') as directory:
        temporary = Path(directory)
        with tarfile.open(args.archive) as archive:
            for item in archive.getmembers():
                target = (temporary / item.name).resolve()
                if (temporary not in target.parents or not (item.isfile() or item.isdir())):
                    raise ValueError('Unsafe archive entry: ' + item.name)
            archive.extractall(temporary)
        package = temporary / 'GPUWhistle'
        subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=package, check=True, stdout=subprocess.DEVNULL)
        env = dict(os.environ, GPUMONITOR_PREFIX=str(temporary / 'install prefix [test]'),
                   GPUMONITOR_SHELL_HOME=str(temporary / 'shell'), XDG_CONFIG_HOME=str(temporary / 'config'),
                   XDG_DATA_HOME=str(temporary / 'data'), PYTHON=sys.executable,
                   PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        env.pop('PYTHONPATH', None)
        command = r'''
set -e
source "$1/setup.sh" --no-sample --no-schedule
seegpu --version
seegpu --help
seegpu report --days 1
source "$1/setup.sh" --no-sample --no-schedule
seegpu --version
if [[ "$2" == sample ]]; then
    source "$1/setup.sh" --no-schedule
fi
seegpu export --format json > "$3/export.json"
'''
        subprocess.run(['bash', '--noprofile', '--norc', '-c', command, 'release-test', str(package),
                        'sample' if args.sample else 'skip', str(temporary)], cwd=temporary, env=env, check=True)
        import json
        data = json.loads((temporary / 'export.json').read_text())
        if args.sample and data['completed_windows'] < 1:
            raise RuntimeError('Real GPU sample did not complete')
        profile = (temporary / 'shell/.bashrc').read_text()
        assert profile.count('# BEGIN GPUmonitor PATH') == 1
        assert (temporary / 'config/gpumonitor/config.json').exists()
        print('PASS: offline archive install, immediate seegpu command, repeated setup, isolated PATH/config' +
              (', real first sample' if args.sample else ''))


if __name__ == '__main__':
    main()
