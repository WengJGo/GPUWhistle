#!/usr/bin/env python3
"""Capture real curses PTY output using synthetic data, then render PNG previews.

Developer-only requirements: pytest pyte Pillow. Never queries NVML or installs a schedule.
"""
import argparse
import codecs
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def demo(database):
    from conftest import Backend, Clock, Reader
    from gpumonitor.collector import collect
    from gpumonitor.config import Config
    from gpumonitor.storage import Storage
    config = Config(database=str(database)).validate()
    clock, reader = Clock(time.time()-6*86400-55), Reader()
    backend = Backend(clock, {'GPU-demo-%s' % i: [10+i*10] + ([11] if i == 0 else []) for i in range(8)})
    read = reader.read
    names = ['root', 'alice', 'charlie', 'bob', 'diana', 'edgar', 'frank', 'grace']
    reader.read = lambda pid: dict(read(pid), username=names[(pid//10)-1],
        name='model_worker' if pid == 11 else 'train_worker')
    snapshot = backend.snapshot
    loads = [97.3, 12, 55, 100, 32, 0, 84, 27]
    memories = [13.0, 8.1, 72.6, 80.4, 44, 1, 67, 34.5]
    def snapshot_demo(since):
        gpus, errors = snapshot(since)
        for index, gpu in enumerate(gpus):
            gpu.update(name='Demo GPU 84G', memory_total=int(83.6*2**30),
                       memory_used=int(memories[index]*2**30), gpu_util=loads[index],
                       power=[223, 78, 160, 285, 123, 34, 215, 100][index],
                       temperature=[45, 35, 52, 66, 40, 31, 58, 38][index])
            for process in gpu['processes']:
                process['memory'] = int((12.0 if process['pid'] == 10 else 1.0 if process['pid'] == 11 else memories[index])*2**30)
            gpu['util_samples'] = [dict(s, sm=3 if index == 2 else 0 if index == 3 else loads[index])
                                   for s in gpu['util_samples'] if s['pid'] not in (11, 60)]
        return gpus, errors
    backend.snapshot = snapshot_demo
    with Storage(str(database)) as storage:
        for day in range(7):
            window = collect(config, backend, reader, clock, clock.time)
            for process in window['processes']:
                if process['pid'] == 40:
                    process['status'] = 'SUSPECTED_IDLE'  # Deliberate example for the status palette.
            storage.save(window, config)
            if day < 6:
                clock.sleep(86400-10)
    return config


def capture(config_file, columns, rows, keys=b'', terminal='xterm-256color', extra_env=None):
    import pyte
    class TerminalScreen(pyte.Screen):
        # pyte 0.8 does not implement CSI S/T used by ncurses scroll optimization.
        def scroll_up(self, count=1):
            self.save_cursor()
            self.cursor.y = self.margins.bottom if self.margins else self.lines-1
            for _ in range(count or 1):
                self.index()
            self.restore_cursor()
        def scroll_down(self, count=1):
            self.save_cursor()
            self.cursor.y = self.margins.top if self.margins else 0
            for _ in range(count or 1):
                self.reverse_index()
            self.restore_cursor()
    class TerminalStream(pyte.Stream):
        csi = dict(pyte.Stream.csi, S='scroll_up', T='scroll_down')
        events = pyte.Stream.events | {'scroll_up', 'scroll_down'}
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
    env = dict(os.environ, TERM=terminal)
    env.pop('NO_COLOR', None)
    env.pop('GPUMONITOR_ASCII', None)
    env.update(extra_env or {})
    child = subprocess.Popen([sys.executable, '-m', 'gpumonitor', '--config', str(config_file)],
        cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    output, sent = bytearray(), False
    last = time.monotonic()
    terminal_screen = TerminalScreen(columns, rows)
    stream = TerminalStream(terminal_screen)
    decoder = codecs.getincrementaldecoder('utf8')(errors='replace')
    try:
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], .1)
            if ready:
                chunk = os.read(master, 65536)
                output.extend(chunk)
                stream.feed(decoder.decode(chunk))
                last = time.monotonic()
            elif b'GPUWhistle' in output and time.monotonic()-last >= .2:
                if not sent and keys:
                    os.write(master, keys)
                    sent, last = True, time.monotonic()
                else:
                    break
        assert child.poll() is None, output.decode(errors='replace')
        os.write(master, b'q')
        child.wait(timeout=3)
        assert child.returncode == 0
        return terminal_screen, bytes(output)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)


def render(screen, path, light=False):
    from PIL import Image, ImageDraw, ImageFont
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
    font = ImageFont.truetype(font_path, 16)
    bold = ImageFont.truetype(font_path.replace('.ttf', '-Bold.ttf'), 16)
    background = '#f5f5f2' if light else '#15191f'
    foreground = '#30363d' if light else '#d4d9e2'
    palette = {'default': foreground, 'black': '#20252d', 'red': '#ca4949' if light else '#e58c8c',
               'green': '#367a45' if light else '#98c68b', 'brown': '#9a6b05' if light else '#d9bd77',
               'yellow': '#9a6b05' if light else '#d9bd77', 'cyan': '#397d85' if light else '#80b6be',
               'blue': '#7399c6', 'magenta': '#b490ba', 'white': foreground}
    cw, ch, margin, banner = 10, 22, 18, 40
    image = Image.new('RGB', (screen.columns*cw+margin*2, screen.lines*ch+margin*2+banner), background)
    draw = ImageDraw.Draw(image)
    draw.text((margin, 12), 'CURSES PTY SNAPSHOT  /  SYNTHETIC DEMO DATA', font=font, fill=palette['cyan'])
    for y in range(screen.lines):
        for x in range(screen.columns):
            char = screen.buffer[y][x]
            if not char.data or char.data == ' ':
                continue
            color = palette.get(char.fg, '#' + char.fg if len(char.fg) == 6 else foreground)
            draw.text((margin+x*cw, margin+banner+y*ch), char.data,
                      font=bold if char.bold else font, fill=color)
            if char.underscore:
                draw.line((margin+x*cw, margin+banner+y*ch+19, margin+(x+1)*cw, margin+banner+y*ch+19), fill=color)
    image.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/tui-snapshots')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gpumonitor-ui-snapshot-') as temporary:
        root = Path(temporary)
        config = demo(root / 'demo.db')
        config_file = root / 'config.json'
        config_file.write_text(json.dumps({'database': config.database}))
        cases = [('eight-gpu-wide', 160, 45, b'', {}), ('single-gpu-detail', 120, 35, b'g\n', {}),
                 ('history-7d', 120, 35, b'7', {}), ('compact-80', 80, 24, b'', {}),
                 ('ascii-no-color', 100, 30, b'', {'NO_COLOR': '1', 'GPUMONITOR_ASCII': '1'})]
        for name, columns, rows, keys, env in cases:
            screen, output = capture(config_file, columns, rows, keys, extra_env=env)
            (args.output / (name + '.ansi')).write_bytes(output)
            (args.output / (name + '.txt')).write_text('\n'.join(screen.display) + '\n')
            render(screen, args.output / (name + '.png'))
            if name == 'eight-gpu-wide':
                render(screen, args.output / (name + '-light.png'), light=True)
            print(args.output / (name + '.png'))


if __name__ == '__main__':
    main()
