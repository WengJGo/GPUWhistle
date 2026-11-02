"""UI-only theme, semantic colors and terminal cell-width helpers."""
import curses
import json
import locale
import math
import os
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


class Text(str):
    """A normal string carrying optional per-span semantic styles."""
    def __new__(cls, spans):
        spans = tuple((str(text), role) for text, role in spans if text)
        value = super().__new__(cls, ''.join(text for text, _ in spans))
        value.spans = spans
        return value


def rich(*parts):
    spans = []
    for part in parts:
        if isinstance(part, Text):
            spans.extend(part.spans)
        elif isinstance(part, tuple):
            spans.append(part)
        else:
            spans.append((str(part), ''))
    return Text(spans)


def cell_width(char):
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1


def width(text):
    return sum(cell_width(c) for c in str(text))


def fit(text, columns):
    columns = max(0, columns)
    spans = text.spans if isinstance(text, Text) else ((str(text), ''),)
    cleaned = [( ''.join(c if c.isprintable() or unicodedata.combining(c) else ' ' if c in '\n\r\t' else '?'
                          for c in value), role) for value, role in spans]
    clipped = sum(width(value) for value, _ in cleaned) > columns
    remaining = max(0, columns - int(clipped))
    output = []
    stopped = False
    for value, role in cleaned:
        segment = ''
        for char in value:
            size = cell_width(char)
            if size > remaining:
                stopped = True
                break
            segment += char
            remaining -= size
        output.append((segment, role))
        if stopped:
            break
    if clipped and columns:
        output.append(('~', 'dim'))
    result = Text(output)
    return result if isinstance(text, Text) else str(result)


def pad(text, columns, right=False):
    text = fit(text, columns)
    spaces = ' ' * max(0, columns - width(text))
    return rich(spaces, text) if right else rich(text, spaces)


@dataclass(frozen=True)
class Theme:
    gpu_thresholds: tuple = (30, 80)
    vram_thresholds: tuple = (50, 80)
    ascii: bool = False

    @classmethod
    def load(cls):
        path = Path(os.environ.get('GPUMONITOR_THEME',
            str(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'gpumonitor/theme.json')))
        values = json.loads(path.read_text()) if path.exists() else {}
        if set(values) - {'gpu_thresholds', 'vram_thresholds', 'ascii'}:
            raise ValueError('Unknown GPUWhistle theme setting')
        for key in ('gpu_thresholds', 'vram_thresholds'):
            pair = values.get(key, getattr(cls(), key))
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in pair)
                    or not 0 <= pair[0] < pair[1] <= 100):
                raise ValueError('%s requires [low boundary, high boundary] in 0..100' % key)
            values[key] = tuple(pair)
        if not isinstance(values.get('ascii', False), bool):
            raise ValueError('theme ascii must be true or false')
        encoding = (getattr(sys.stdout, 'encoding', None) or locale.getpreferredencoding(False)).lower().replace('-', '')
        terminal_locale = next((os.environ[key] for key in ('LC_ALL', 'LC_CTYPE', 'LANG') if os.environ.get(key)), '')
        values['ascii'] = (values.get('ascii', False) or os.environ.get('GPUMONITOR_ASCII') == '1'
                           or 'utf8' not in encoding or terminal_locale.lower() in ('c', 'posix'))
        return cls(**values)

    def load_role(self, value, metric='gpu'):
        if value is None or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            return 'na'
        medium, high = self.vram_thresholds if metric == 'vram' else self.gpu_thresholds
        return 'load_high' if value >= high else 'load_medium' if value >= medium else 'load_low'

    @property
    def separator(self):
        return ' ' if self.ascii else ' · '

    def progress(self, value, columns, metric='gpu'):
        columns = max(1, columns)
        if self.load_role(value, metric) == 'na':
            return rich(('N/A'.center(columns) if columns >= 3 else '?' * columns, 'na'))
        used = round(value / 100 * columns)
        return rich((('#' if self.ascii else '█') * used, self.load_role(value, metric)),
                    (('.' if self.ascii else '·') * (columns-used), 'dim'))


def colors(theme=None):
    # Keep the terminal's own foreground/background in monochrome and light themes.
    base = {'dim': curses.A_DIM, 'na': curses.A_DIM, 'UNKNOWN': curses.A_DIM,
            'columns': curses.A_BOLD, 'title': curses.A_BOLD, 'section': curses.A_BOLD,
            'selected': curses.A_BOLD, 'tab': curses.A_BOLD | curses.A_UNDERLINE}
    if 'NO_COLOR' in os.environ or not curses.has_colors():
        return base
    try:
        curses.start_color()
        curses.use_default_colors()
        for index, foreground in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN), 1):
            curses.init_pair(index, foreground, -1)
        green, yellow, red, cyan = (curses.color_pair(i) for i in range(1, 5))
        base.update(load_low=green, load_medium=yellow, load_high=red,
                    ACTIVE=green, LOW_ACTIVITY=yellow, SUSPECTED_IDLE=red,
                    UNKNOWN=yellow | curses.A_DIM, title=cyan | curses.A_BOLD,
                    tab=cyan | curses.A_BOLD | curses.A_UNDERLINE)
    except curses.error:
        pass
    return base
