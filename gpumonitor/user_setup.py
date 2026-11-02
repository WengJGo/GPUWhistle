"""Idempotent user configuration for the unpack-and-install distribution."""
import argparse
import hashlib
import json
import os
import shlex
from dataclasses import asdict
from pathlib import Path
from .config import Config, config_path

PROFILE_NAMES = ('.bashrc', '.profile', '.bash_profile', '.bash_login', '.zshrc', '.zprofile')


def markers(prefix):
    token = hashlib.sha256(str(Path(prefix).expanduser().absolute()).encode()).hexdigest()[:12]
    return '# BEGIN GPUmonitor PATH ' + token, '# END GPUmonitor PATH ' + token


def without_block(text, begin, end):
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == begin]
    ends = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == end]
    if not starts and not ends:
        return text
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError('Malformed GPUmonitor PATH block; profile left untouched')
    return ''.join(lines[:starts[0]] + lines[ends[0] + 1:])


def configure(prefix, remove=False, home=None, shell=None):
    home = Path(home) if home is not None else Path.home()
    prefix = Path(prefix).expanduser().absolute()
    binary = str(prefix / 'bin')
    if any(ord(c) < 32 for c in binary):
        raise ValueError('Installation path must not contain control characters')
    begin, end = markers(prefix)
    if remove:
        names = [name for name in PROFILE_NAMES if (home / name).exists()]
    else:
        shell = Path(shell or os.environ.get('SHELL', '/bin/bash')).name
        names = ['.zshrc', '.zprofile'] if shell == 'zsh' else ['.bashrc', '.profile']
        if shell != 'zsh':
            names += [name for name in ('.bash_profile', '.bash_login') if (home / name).exists()]
    quoted = shlex.quote(binary)
    block = '\n'.join((begin, 'case ":$PATH:" in', '    *:' + quoted + ':*) ;;',
        '    *) export PATH=' + quoted + ':"$PATH" ;;', 'esac', end, ''))
    changes = []
    for name in names:
        path = home / name
        if path.is_symlink():
            if remove:
                continue
            raise ValueError('Refusing to edit symlinked shell profile: ' + str(path)
                             + '; use source ./setup.sh for this terminal')
        before = path.read_text() if path.exists() else ''
        after = without_block(before, begin, end)
        if not remove:
            after += ('' if not after or after.endswith('\n') else '\n') + block
        if after != before:
            changes.append((path, after))
    # Validate all profiles before applying changes; preserve existing file modes.
    for path, content in changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return [str(path) for path, _ in changes]


def initialize_config():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with path.open('x') as handle:
            json.dump(asdict(Config()), handle, indent=2)
            handle.write('\n')
        path.chmod(0o600)
        return True
    except FileExistsError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args(argv)
    for path in configure(args.prefix, remove=args.remove, home=os.environ.get('GPUMONITOR_SHELL_HOME')):
        print('Shell PATH: ' + path)
    if not args.remove:
        print('Default config: ' + ('created' if initialize_config() else 'existing file preserved'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
