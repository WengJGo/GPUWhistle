"""Responsive geometry and differential curses rendering; no data collection."""
import curses
from dataclasses import dataclass
from .tui_theme import Text, cell_width, fit, pad


@dataclass(frozen=True)
class Layout:
    columns: int
    rows: int
    overview_limit: int
    start: int
    available: int
    side_by_side: bool
    panel_width: int
    current_height: int
    history_y: int
    history_x: int
    history_height: int


def layout(columns, rows, overview_lines):
    overview_limit = max(1, rows - 13)
    visible = min(overview_lines, overview_limit)
    start = 4 + visible
    available = max(0, rows-start-1)
    side = columns >= 120 and available >= 6
    panel_width = (columns-4)//2 if side else columns-2
    current_height = available if side else max(3, (available+1)//2)
    return Layout(columns, rows, overview_limit, start, available, side, panel_width, current_height,
                  start if side else start+current_height+1, panel_width+3 if side else 0,
                  available if side else max(0, available-current_height-1))


class Frame:
    """Build a virtual frame; write only changed rows to the curses window."""
    def __init__(self, screen, palette, ascii=False):
        self.screen, self.palette, self.ascii = screen, palette, ascii
        self.previous = None
        self.size = None

    def begin(self):
        rows, columns = self.screen.getmaxyx()
        if self.size is not None and self.size != (rows, columns):
            self.screen.erase()  # Resize is the only reason to clear the curses window.
            self.previous = None
        self.size = rows, columns
        self.cells = [[(' ', 0) for _ in range(max(0, columns-1))] for _ in range(rows)]

    def getmaxyx(self):
        return self.size

    def write(self, y, x, text, columns, style=0):
        rows, terminal_width = self.size
        if not 0 <= y < rows or x < 0 or x >= terminal_width-1:
            return
        columns = min(columns, terminal_width-x-1)
        if columns <= 0:
            return
        text = pad(fit(text, columns), columns)
        if self.ascii:
            text = Text((''.join(c if ord(c) < 128 else '?' * cell_width(c) for c in value), role)
                        for value, role in text.spans)
        for segment, role in text.spans:
            for char in segment:
                size = cell_width(char)
                if size == 0:
                    if x:
                        old, attr = self.cells[y][x-1]
                        self.cells[y][x-1] = old+char, attr
                    continue
                if x + size > terminal_width-1:
                    return
                self.cells[y][x] = char, style | self.palette.get(role, 0)
                if size == 2:
                    self.cells[y][x+1] = '', style | self.palette.get(role, 0)
                x += size

    def flush(self):
        for y, row in enumerate(self.cells):
            if self.previous is not None and row == self.previous[y]:
                continue
            x = 0
            while x < len(row):
                begin, attribute = x, row[x][1]
                text = ''
                while x < len(row) and row[x][1] == attribute:
                    text += row[x][0]
                    x += 1
                try:
                    # Text already fits in terminal cells; addnstr's limit is characters.
                    self.screen.addnstr(y, begin, text, len(text), attribute)
                except curses.error:
                    pass
        self.previous = self.cells
        if hasattr(self.screen, 'noutrefresh'):
            self.screen.noutrefresh()
            curses.doupdate()
        else:
            self.screen.refresh()
