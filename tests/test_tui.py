from gpumonitor.tui import screen_lines
from gpumonitor.analytics import report
from gpumonitor.storage import Storage


def test_empty():
    assert 'gpumonitor once' in screen_lines(None, {})[1][0]


def test_user_process_detail_views(rig, config):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-A': [10, 11, 20], 'GPU-B': [30]}
    w = sample()
    with Storage(config.database) as db:
        db.save(w, config)
        history = report(db, config, now=clock.time())
        for width in (80, 120):
            for mode in ('user', 'process'):
                lines = screen_lines(w, history, mode=mode, detail=True, width=width)
                text = '\n'.join(line for line, _ in lines)
                assert 'HISTORY' in text and 'DETAIL' in text and 'GPU 0' in text


def test_table_widths_and_terminal_escape_sanitizing(rig, config):
    from gpumonitor.tui import fit, gpu_panel, usage_panel, history_panel
    clock, _, backend, sample = rig
    backend.cards = {'GPU-' + str(i): [10+i] for i in range(8)}
    w = sample()
    with Storage(config.database) as db:
        db.save(w, config)
        history = report(db, config, now=clock.time())
    for width in (78, 118):
        overview = gpu_panel(w, width=width)
        usage, chosen, _ = usage_panel(w, width=width)
        hist = history_panel(history, chosen, width=width)
        assert all(len(line) <= width for line, _ in overview + usage + hist)
    assert '\x1b' not in fit('user\x1b[31m', 40)


def test_fixed_regions_stay_visible_with_eight_cards(rig, config, monkeypatch):
    import gpumonitor.tui as tui
    clock, _, backend, sample = rig
    backend.cards = {'GPU-' + str(i): [10+i] for i in range(8)}
    with Storage(config.database) as db:
        db.save(sample(), config)
        history = report(db, config, now=clock.time())
        monkeypatch.setattr(tui, 'report', lambda *args, **kwargs: history)
        monkeypatch.setattr(tui.curses, 'has_colors', lambda: False)
        monkeypatch.setattr(tui.curses, 'curs_set', lambda value: None)
        class Screen:
            def __init__(self, width, height):
                self.width, self.height = width, height
                self.erase()
            def erase(self):
                self.rows = [' ' * self.width for _ in range(self.height)]
            def getmaxyx(self):
                return self.height, self.width
            def keypad(self, value):
                pass
            def timeout(self, value):
                pass
            def refresh(self):
                pass
            def getch(self):
                return ord('q')
            def addnstr(self, y, x, text, count, style):
                text = text[:count]
                self.rows[y] = self.rows[y][:x] + text + self.rows[y][x+len(text):]
        for width, height in ((80, 24), (120, 40)):
            screen = Screen(width, height)
            tui._loop(screen, db, config)
            text = '\n'.join(screen.rows)
            assert 'GPU OVERVIEW' in text and 'CURRENT' in text and 'HISTORY' in text
            assert 'q Quit' in screen.rows[-1] and 'Last ' in screen.rows[1]
            current = next(i for i, row in enumerate(screen.rows) if 'CURRENT' in row)
            historical = next(i for i, row in enumerate(screen.rows) if 'HISTORY' in row)
            assert (current == historical) == (width >= 120)


def test_current_mixed_user_counts_and_low_separation(mixed_rig):
    from copy import deepcopy
    from gpumonitor.tui import usage_panel
    _, sample = mixed_rig
    window = sample()
    lines, _, _ = usage_panel(window, width=58)
    assert lines[0][0].split()[-4:] == ['ACT', 'LOW', 'IDLE', 'UNK']
    values = lines[1][0].split()
    assert values[0] == 'root' and values[2] == '2'
    assert values[-4:] == ['1', '0', '0', '1']
    # LOW_ACTIVITY is displayed independently, rather than folded into ACT.
    low = deepcopy(window['processes'][0])
    low.update(pid=42, identity='boot:42', status='LOW_ACTIVITY')
    window['processes'].append(low)
    lines, _, _ = usage_panel(window, width=58)
    assert lines[1][0].split()[-4:] == ['1', '1', '0', '1']
    low['status'] = 'SUSPECTED_IDLE'
    lines, _, _ = usage_panel(window, width=58)
    assert lines[1][0].split()[-4:] == ['1', '0', '1', '1']


def test_process_sm_columns_and_compact_details(mixed_rig):
    from gpumonitor.tui import usage_panel
    _, sample = mixed_rig
    window = sample()
    for width in (56, 58, 78, 118):
        lines, _, _ = usage_panel(window, mode='process', width=width)
        assert 'SM AVG' in lines[0][0]
        unknown = next(line for line, _ in lines if '1102' in line)
        assert 'N/A' in unknown and 'UNKNOWN' in unknown
        if width >= 78:
            assert 'SM PK' in lines[0][0] and 'SM COV' in lines[0][0]
        details, _, _ = usage_panel(window, detail=True, width=width)
        text = '\n'.join(line for line, _ in details)
        assert 'ACT=1 LOW=0 IDLE=0 UNK=1' in text
        assert 'SM avg 98.5% | peak 99.0%' in text
        assert 'SM valid cov 75.0% | samples 4' in text
        assert 'SM avg N/A | peak N/A' in text
        assert all(len(line) <= width for line, _ in lines + details)
    # Older stored windows did not record SM-specific coverage: show N/A.
    for p in window['processes']:
        p.pop('sm_coverage')
        p.pop('sm_samples')
    lines, _, _ = usage_panel(window, detail=True, width=58)
    assert any('SM valid cov N/A | samples N/A' in line for line, _ in lines)


def test_history_process_unknown_visible_without_redefining_user_unknown(mixed_rig, config):
    from gpumonitor.tui import history_panel, usage_panel
    clock, sample = mixed_rig
    window = sample()
    with Storage(config.database) as db:
        db.save(window, config)
        for daily in (False, True):
            history = report(db, config, now=clock.time(), daily=daily)
            for width in (56, 58, 78, 118):
                _, chosen, _ = usage_panel(window, width=width)
                lines = history_panel(history, chosen, width=width)
                assert 'PUNK' in lines[0][0]
                assert lines[1][0].split()[-3:] == ['0', '0', '1']  # IDLE, UNK, PUNK
                details = history_panel(history, chosen, detail=True, width=width)
                assert any('Process UNKNOWN: 1/2 (50.0%)' in line for line, _ in details)
                assert any('Windows with UNKNOWN: 1/1' in line for line, _ in details)
                assert all(len(line) <= width for line, _ in lines + details)


def test_no_activity_count_is_visible_before_suspected_idle(rig, config):
    from gpumonitor.tui import usage_panel
    from gpumonitor.tui_theme import width as cell_width
    clock, _, _, sample = rig
    window = sample()
    with Storage(config.database) as db:
        db.save(window, config)
    assert window['processes'][0]['status'] == 'NO_ACTIVITY_OBSERVED'
    assert window['processes'][0]['idle_streak'] == 1
    for width in (56, 58, 78, 118):
        rows, _, _ = usage_panel(window, width=width)
        header = rows[0][0].split()
        values = rows[1][0].split()
        assert 'NOACT' in header
        assert values[-5:] == ['1', '0', '0', '0', '0']
        assert all(cell_width(line) <= width for line, _ in rows)
        details, _, _ = usage_panel(window, detail=True, width=width)
        assert any('NOACT=1 / N=1' in line for line, _ in details)
        assert any('NOACT' in line and '0.0%' in line for line, _ in details)


def test_all_five_current_status_counts_sum_to_process_count(mixed_rig):
    from copy import deepcopy
    from gpumonitor.tui import usage_panel
    from gpumonitor.models import STATUSES
    _, sample = mixed_rig
    window = sample()
    template = window['processes'][0]
    window['processes'] = []
    for index, status in enumerate(STATUSES):
        process = deepcopy(template)
        process.update(pid=100+index, identity='demo:%s' % index, status=status)
        window['processes'].append(process)
    rows, _, _ = usage_panel(window, width=58)
    values = rows[1][0].split()
    assert values[2] == '5'
    assert values[-5:] == ['1'] * 5
    assert sum(map(int, values[-5:])) == int(values[2])
