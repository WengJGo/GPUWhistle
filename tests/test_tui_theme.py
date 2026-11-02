import curses
import json
import pytest
from gpumonitor.tui_theme import Theme, colors, fit, rich, width


@pytest.mark.parametrize('metric,value,role', [
    ('gpu', 0, 'load_low'), ('gpu', 29.9, 'load_low'), ('gpu', 30, 'load_medium'),
    ('gpu', 79.9, 'load_medium'), ('gpu', 80, 'load_high'), ('gpu', 100, 'load_high'),
    ('vram', 49.9, 'load_low'), ('vram', 50, 'load_medium'), ('vram', 80, 'load_high'),
    ('gpu', None, 'na'), ('gpu', float('nan'), 'na'), ('gpu', 101, 'na')])
def test_load_boundaries(metric, value, role):
    assert Theme().load_role(value, metric) == role


def test_theme_configuration_and_ascii(tmp_path, monkeypatch):
    path = tmp_path / 'theme.json'
    path.write_text(json.dumps({'gpu_thresholds': [20, 70], 'vram_thresholds': [40, 90]}))
    monkeypatch.setenv('GPUMONITOR_THEME', str(path))
    monkeypatch.setenv('GPUMONITOR_ASCII', '1')
    theme = Theme.load()
    assert theme.load_role(75) == 'load_high'
    assert theme.load_role(75, 'vram') == 'load_medium'
    assert theme.ascii and str(theme.progress(50, 8)).isascii()
    path.write_text('{"gpu_thresholds": [90, 20]}')
    with pytest.raises(ValueError, match='gpu_thresholds'):
        Theme.load()


@pytest.mark.parametrize('reason', ['NO_COLOR', 'monochrome', 'unsupported'])
def test_safe_monochrome_palette(monkeypatch, reason):
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr(curses, 'has_colors', lambda: reason != 'monochrome')
    called = []
    def start():
        called.append(True)
        raise curses.error('no terminal')
    monkeypatch.setattr(curses, 'start_color', start)
    if reason == 'NO_COLOR':
        monkeypatch.setenv('NO_COLOR', '')
    palette = colors()
    assert 'load_low' not in palette and palette['UNKNOWN'] == curses.A_DIM
    assert not (palette['columns'] & curses.A_REVERSE)
    assert not (palette['selected'] & curses.A_REVERSE)
    assert bool(called) == (reason == 'unsupported')


def test_status_and_load_colors_are_separate(monkeypatch):
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr(curses, 'has_colors', lambda: True)
    monkeypatch.setattr(curses, 'start_color', lambda: None)
    monkeypatch.setattr(curses, 'use_default_colors', lambda: None)
    pairs = []
    monkeypatch.setattr(curses, 'init_pair', lambda *args: pairs.append(args))
    monkeypatch.setattr(curses, 'color_pair', lambda i: i << 8)
    palette = colors()
    assert palette['load_low'] == palette['ACTIVE']
    assert palette['load_high'] == palette['SUSPECTED_IDLE']
    assert palette['UNKNOWN'] != palette['ACTIVE'] and palette['na'] != palette['ACTIVE']
    assert all(background == -1 for _, _, background in pairs)


def test_cell_width_and_style_preserving_clip():
    text = rich(('显卡型号', 'section'), (' longer name', 'load_high'))
    clipped = fit(text, 7)
    assert width(clipped) <= 7 and str(clipped).startswith('显卡型')
    assert 'longer' not in clipped
    assert clipped.spans[0][1] == 'section'
    assert '\x1b' not in fit('bad\x1b[31m', 40)
    assert width(fit('e\u0301 model', 3)) <= 3
