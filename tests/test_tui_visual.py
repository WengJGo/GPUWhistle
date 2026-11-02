import pytest
from gpumonitor.analytics import report
from gpumonitor.storage import Storage
from gpumonitor.tui import gpu_panel, usage_panel, history_panel, status_bar
from gpumonitor.tui_theme import Theme, width
from gpumonitor.tui_render import Frame, layout


@pytest.mark.parametrize('columns,rows', [(80, 24), (100, 30), (120, 35), (160, 45)])
def test_responsive_geometry_and_long_model_names(rig, config, columns, rows):
    clock, _, backend, sample = rig
    backend.cards = {'GPU-' + str(i): [10+i] for i in range(8)}
    w = sample()
    for i, g in enumerate(w['gpus']):
        g['name'] = ['NVIDIA RTX 6000D', 'NVIDIA A100-SXM4-80GB', '显卡型号 超长 GPU name ' * 4][i%3]
    overview = gpu_panel(w, width=columns-1, compact=columns < 120)
    geometry = layout(columns, rows, len(overview))
    assert geometry.side_by_side == (columns >= 120)
    assert geometry.history_y + geometry.history_height <= rows-1
    assert all(width(line) <= columns-1 for line, _ in overview)
    assert not any('+' in line or '---' in line for line, _ in overview)
    if columns >= 120:
        assert len(overview) == 8 and 'GPU 0' in overview[0][0] and 'GPU 4' in overview[0][0]
    with Storage(config.database) as db:
        db.save(w, config)
        hist = report(db, config, now=clock.time())
    usage, chosen, _ = usage_panel(w, width=geometry.panel_width)
    historical = history_panel(hist, chosen, width=geometry.panel_width)
    assert all(width(line) <= geometry.panel_width for line, _ in usage+historical)


def test_gpu_and_vram_independent_load_colors(rig):
    _, _, _, sample = rig
    w = sample()
    g = w['gpus'][0]
    g['gpu_util']['avg'] = 100
    g['memory_total'] = 100*2**30
    g['memory_used']['avg'] = 31*2**30
    for columns, compact in ((79, True), (119, False)):
        lines = gpu_panel(w, width=columns, compact=compact)
        spans = [span for line, _ in lines for span in line.spans]
        assert any('100.0%' in text and role == 'load_high' for text, role in spans)
        assert any('31%' in text and role == 'load_low' for text, role in spans)
    _, freshness = status_bar(w, w['gpus'], 1, None, False, 600, Theme())
    assert 'CHECK' not in freshness and 'PARTIAL' not in freshness


def test_unknown_na_and_idle_keep_semantics_and_cell_styles(mixed_rig, config):
    clock, sample = mixed_rig
    w = sample()
    rows, chosen, _ = usage_panel(w, width=78)
    spans = rows[1][0].spans
    assert ('root', '') in spans
    assert any(text == '1' and role == 'UNKNOWN' for text, role in spans)
    process, _, _ = usage_panel(w, mode='process', width=118)
    unknown = next(line for line, _ in process if '1102' in line)
    assert ('UNKNOWN', 'UNKNOWN') in unknown.spans and ('N/A', 'na') in unknown.spans
    active = w['processes'][0]
    active['status'] = 'SUSPECTED_IDLE'
    active['sm']['avg'] = active['sm']['max'] = 0
    rows, _, _ = usage_panel(w, mode='process', width=118)
    idle = next(line for line, _ in rows if '1101' in line)
    assert ('0.0%', 'load_low') in idle.spans and ('IDLE?', 'SUSPECTED_IDLE') in idle.spans
    with Storage(config.database) as db:
        db.save(w, config)
        hist = report(db, config, now=clock.time())
    rows = history_panel(hist, width=78, memory_totals={'GPU-A': 100*2**30})
    spans = rows[1][0].spans
    assert any(role == 'load_low' and text == '13.0' for text, role in spans)
    assert any(role == 'UNKNOWN' and text == '1' for text, role in spans)


class Screen:
    def __init__(self, rows=24, columns=80):
        self.rows, self.columns = rows, columns
        self.calls, self.clears = [], 0
    def getmaxyx(self):
        return self.rows, self.columns
    def addnstr(self, *args):
        self.calls.append(args)
    def refresh(self):
        pass
    def erase(self):
        self.clears += 1


def test_differential_frames_clear_old_text_without_repainting_everything():
    screen = Screen()
    frame = Frame(screen, {})
    frame.begin()
    frame.write(2, 0, 'long previous line', 40)
    frame.flush()
    count = len(screen.calls)
    frame.begin()
    frame.write(2, 0, 'long previous line', 40)
    frame.flush()
    assert len(screen.calls) == count and screen.clears == 0
    frame.begin()
    frame.write(2, 0, 'short', 40)
    frame.flush()
    assert len(screen.calls) == count+1 and screen.calls[-1][0] == 2
    assert 'previous' not in screen.calls[-1][2]
    assert screen.calls[-1][2].startswith('short ')  # Spaces remove trailing old characters.
    screen.rows, screen.columns = 30, 100
    frame.begin()
    frame.flush()
    assert screen.clears == 1


def test_ascii_frame_replaces_wide_names_without_shifting_columns():
    from gpumonitor.tui_theme import rich, pad
    screen = Screen(rows=4, columns=60)
    frame = Frame(screen, {}, ascii=True)
    frame.begin()
    frame.write(0, 0, rich(pad('中文GPU', 12), 'NEXT'), 30)
    frame.flush()
    row = next(call[2] for call in screen.calls if call[0] == 0)
    assert row.isascii() and row.index('NEXT') == 12
