"""Sampled dashboard, historical ranking and on-demand cluster availability."""
import curses
import time
from collections import Counter
from datetime import datetime
from .analytics import HISTORY_DAYS, report
from .idle_stats import idle_report
from .cluster import ClusterConfig, LiveScan, cluster_path, gpu_status
from .cli import number
from .models import user_key
from .tui_theme import Theme, fit, pad, rich, width as cell_length, colors as theme_colors
from .tui_render import Frame, layout

HELP = [
    ('q', 'Quit immediately'), ('h / ?', 'Show or close this help'),
    ('1 / 3 / 7 / 5 / 0', 'History range: 1 / 3 / 7 / 15 / 30 days'),
    ('t', 'Toggle historical idle ranking (default: all 30 days)'),
    ('c', 'Query cluster now / refresh live GPU availability'),
    ('b (cluster)', 'Back to previous view; Esc also returns'),
    ('g / Left / Right', 'Cycle GPU selection; a selects all GPUs'),
    ('u', 'Switch users / processes'), ('n / m / i', 'Sort by name / VRAM / idle streak'),
    ('s', 'Cycle sort order'), ('Tab', 'Focus current usage / history'),
    ('Up / Down', 'Select user, or scroll the focused table'),
    ('Enter', 'Open / close selected user details'), ('Esc', 'Close help or details; otherwise quit'),
    ('PgUp / PgDn', 'Scroll focused table by one page'), ('Home / End', 'First / last table page'),
    ('d', 'Group history by local calendar date'), ('r', 'Refresh SQLite now (no NVML query)'),
    ('Space', 'Pause / resume automatic database refresh'),
]
STATUS = {'ACTIVE': 'ACTIVE', 'LOW_ACTIVITY': 'LOW', 'NO_ACTIVITY_OBSERVED': 'NOACT',
          'SUSPECTED_IDLE': 'IDLE?', 'UNKNOWN': 'UNKNOWN'}
RANGE_KEYS = dict(zip(map(ord, '13750'), HISTORY_DAYS))


def cluster_panel(scan, error=None, width=78, now=None):
    now = time.monotonic() if now is None else now
    columns = [('HOST', max(8, min(16, width-48)), 'l'), ('GPU', 3, 'r'), ('STATUS', 11, 'l'),
               ('FREE GiB', 8, 'r'), ('USED GiB', 8, 'r'), ('UTIL', 6, 'r'), ('N', 3, 'r')]
    if width >= 100:
        columns.append(('DEVICE', max(8, width-68), 'l'))
    lines = [('HOST SUMMARY / FREE GPU indexes', 'columns')]
    counts, cards = Counter(), []
    if error:
        lines.append((fit(error, width), 'UNKNOWN'))
    if not scan.results and not error:
        lines += [(fit('No cluster nodes configured. Copy cluster.example.json to:', width), 'UNKNOWN'),
                  (fit(str(cluster_path()), width), 'dim')]
    for result in scan.results:
        if result['status'] != 'DONE':
            label = 'SCANNING' if result['status'] == 'SCANNING' else 'ERROR: '+str(result['error'])
            lines.append((fit(rich(pad(result['name'], 12), (' '+label, 'UNKNOWN')), width), ''))
            continue
        free = [str(g['index']) for g in result['gpus'] if gpu_status(g, result, scan.config, now) == 'FREE']
        summary = rich(pad(result['name'], 12), ' FREE ',
            (','.join(free) if free else 'none', 'load_low' if free else 'dim'),
            '  [%s/%s]' % (len(free), len(result['gpus'])))
        lines.append((fit(summary, width), ''))
        for g in result['gpus']:
            status = gpu_status(g, result, scan.config, now)
            counts[status] += 1
            cards.append((status != 'FREE', result['name'], g['index'], status, g))
    if cards:
        lines += [('GPU DETAILS / free cards first', 'section'),
                  (fit(table(columns, [c[0] for c in columns]), width), 'columns')]
    for _, host, index, status, g in sorted(cards, key=lambda row: row[:3]):
        role = 'load_low' if status == 'FREE' else 'LOW_ACTIVITY' if status in ('IN_USE', 'UNAVAILABLE') else 'UNKNOWN'
        values = [host, index, status, number(g.get('free_mib'), scale=1024),
                  number(g.get('used_mib'), scale=1024), number(g.get('gpu_util'), '%'),
                  g['process_count'] if g.get('process_count') is not None else 'N/A']
        roles = ['load_low' if status == 'FREE' else '', '', role, role if status == 'FREE' else '', '', '', '']
        if width >= 100:
            values.append(g.get('name', 'N/A'))
            roles.append('dim')
        lines.append((fit(table(columns, values, roles), width), ''))
    return lines, counts


def duration(seconds):
    if seconds is None:
        return 'N/A'
    if seconds < 60:
        return '%.0fs' % seconds
    if seconds < 3600:
        return '%.1fm' % (seconds / 60)
    return '%.1fh' % (seconds / 3600)


def idle_panel(result, selected=0, detail=False, width=78):
    """Historical rows are independent of the latest ownership snapshot."""
    columns = [('USER', max(8, min(20, width - 49)), 'l'), ('GPU', 3, 'r'),
               ('ZERO~', 8, 'r'), ('SAMP', 7, 'r'), ('HELD~', 8, 'r'),
               ('GiB', 6, 'r'), ('PUNK', 5, 'r')]
    if width >= 90:
        columns.append(('LAST SEEN', 14, 'r'))
    lines = [(table(columns, [c[0] for c in columns]), 'columns')]
    selection_row = None
    for index, row in enumerate(result['rows']):
        values = [row['username'], row['gpu_index'], duration(row['zero_estimated_seconds']),
                  duration(row['zero_sample_seconds']), duration(row['held_estimated_seconds']),
                  number(row['zero_memory_avg'], scale=2**30), row['unknown_process_windows']]
        roles = ['', '', 'SUSPECTED_IDLE' if row['zero_windows'] else 'dim', 'dim', '', '',
                 count_role(row['unknown_process_windows'], 'UNKNOWN')]
        if width >= 90:
            values.append(stamp(row['last_seen'])[:14])
            roles.append('dim')
        if index == selected:
            selection_row = len(lines)
        lines.append((fit(table(columns, values, roles), width), 'selected' if index == selected else ''))
        if detail and index == selected:
            counts = row['latest_counts']
            details = [
                'Last observed: %s (historical ownership; may have exited)' % stamp(row['last_seen']),
                'Last zero: %s | zero windows %s / observed %s' % (
                    stamp(row['last_zero']) if row['last_zero'] is not None else 'N/A', row['zero_windows'], row['observed_windows']),
                'Sampled owner time %s | range coverage %.2f%%' % (duration(row['observed_seconds']), row['sample_coverage'] * 100),
                'IDLE windows %s | mixed active+zero windows %s' % (row['idle_windows'], row['mixed_active_windows']),
                'Last counts ACT=%s LOW=%s NOACT=%s IDLE=%s UNK=%s' % tuple(counts[k] for k in (
                    'ACTIVE', 'LOW_ACTIVITY', 'NO_ACTIVITY_OBSERVED', 'SUSPECTED_IDLE', 'UNKNOWN')),
                'PUNK %s / %s process-windows; UNKNOWN adds no zero time' % (row['unknown_process_windows'], row['process_windows']),
                'GiB = avg memory of zero-activity processes in qualifying samples',
                'Minimum process VRAM %.1f GiB; NOACT + IDLE qualify' % (result['idle_memory_bytes'] / 2**30),
                'ZERO~/HELD~: one %.0fs period per observation, overlaps once' % result['period_seconds'],
                'Periods end at observation, clipped to range; gaps not filled',
                'Estimates cannot prove continuous inactivity between samples',
            ]
            lines += [(fit('  ' + line, width), 'dim') for line in details]
    if not result['rows']:
        lines.append(('No historical owners in this range.', 'dim'))
    return lines, selection_row


def stamp(value):
    return datetime.fromtimestamp(value).strftime('%m-%d %H:%M:%S')


def bar(value, width=8, theme=None, metric='gpu'):
    return (theme or Theme()).progress(value, width, metric)


def table(columns, values, roles=None):
    roles = roles or [''] * len(values)
    cells = []
    for (_, size, align), value, role in zip(columns, values, roles):
        if str(value) == 'N/A':
            role = 'na'
        cells.append(pad(rich((str(value), role)), size, right=align == 'r'))
    return rich(*[part for index, value in enumerate(cells) for part in ((' ' if index else ''), value)])


def memory_ratio(used, total):
    return used / total * 100 if used is not None and total is not None and total > 0 else None


def count_role(value, role):
    return role if value else 'dim'


def ordered_users(window, gpu=None, sort='memory'):
    processes = [p for p in window.get('processes', []) if gpu is None or p['gpu_uuid'] == gpu]
    users = [u for u in window.get('users', []) if gpu is None or u['gpu_uuid'] == gpu]
    def key(u):
        if sort == 'name':
            return u['username'], u['gpu_uuid']
        if sort == 'idle':
            return -max((p['idle_streak'] for p in processes if p['gpu_uuid'] == u['gpu_uuid']
                         and user_key(p) == u['user_key']), default=0), u['username']
        return -(u['memory']['avg'] or 0), u['username']
    return sorted(users, key=key)


def gpu_panel(window, gpu=None, width=80, compact=False, theme=None):
    theme = theme or Theme()
    cards = [g for g in window.get('gpus', []) if gpu is None or g['uuid'] == gpu]
    if not cards:
        return [('No GPU observations yet. Run: seegpu once', 'UNKNOWN')]
    def measurements(g):
        util = g['gpu_util']['avg']
        mem, total = g['memory_used']['avg'], g['memory_total']
        ratio = memory_ratio(mem, total)
        amount = number(mem, scale=2**30) + '/' + number(total, scale=2**30) + 'G'
        percent = number(ratio, '%') if ratio is None else '%.0f%%' % ratio
        power = 'N/A' if g['power']['avg'] is None else '%.0fW' % g['power']['avg']
        temp = 'N/A' if g['temperature']['avg'] is None else '%.0f%s' % (g['temperature']['avg'], 'C' if theme.ascii else '°C')
        return util, ratio, amount, percent, power, temp
    if width < 110 or compact:
        bar_width = max(3, min(16, (width-55)//3+3))
        columns = [('GPU', 5, 'l'), ('DEVICE', max(8, width-45-bar_width), 'l'),
                   ('UTIL', bar_width+7, 'l'), ('VRAM', 17, 'r'), ('POWER', 6, 'r'), ('TEMP', 5, 'r')]
        lines = [(table(columns, [c[0] for c in columns]), 'columns')]
        for g in cards:
            util, ratio, amount, percent, power, temp = measurements(g)
            values = ['GPU ' + str(g['index']), g['name'] + (' MIG' if g['mig'] else ''),
                rich(bar(util, bar_width, theme), ' ', (number(util, '%'), theme.load_role(util))),
                rich((amount + ' ' + percent, theme.load_role(ratio, 'vram'))),
                rich((power, 'na' if power == 'N/A' else '')), rich((temp, 'na' if temp == 'N/A' else ''))]
            # Preserve the individual UTIL and VRAM styles inside each table cell.
            cells = [pad(value, size, align == 'r')
                     for (_, size, align), value in zip(columns, values)]
            lines.append((rich(*[part for i, cell in enumerate(cells) for part in (' ' if i else '', cell)]), ''))
        return lines
    card_width = (width-3)//2
    def card(g):
        util, ratio, amount, percent, power, temp = measurements(g)
        suffix = power + '  ' + temp
        title = 'GPU %s  ' % g['index']
        name = pad(g['name'] + (' MIG' if g['mig'] else ''), max(1, card_width-cell_length(title)-cell_length(suffix)-2))
        top = rich((title, 'section'), name, '  ', (suffix, 'dim'))
        # Bar widths consume remaining space, adapting even to large VRAM values.
        util_text = number(util, '%')
        fixed = cell_length('UTIL ' + ' ' + util_text + '  VRAM ' + ' ' + amount + ' ' + percent)
        space = max(3, card_width-fixed)
        util_width = max(2, space*2//3)
        mem_width = max(1, space-util_width)
        bottom = rich('UTIL ', bar(util, util_width, theme), ' ', (util_text, theme.load_role(util)),
            '  VRAM ', bar(ratio, mem_width, theme, 'vram'), ' ',
            (amount + ' ' + percent, theme.load_role(ratio, 'vram')))
        return [fit(top, card_width), fit(bottom, card_width)]
    # Column-major order: GPUs 0..3 left, 4..7 right on an eight-device host.
    half = (len(cards)+1)//2
    lines = []
    for index in range(half):
        left = card(cards[index])
        right = card(cards[index+half]) if index+half < len(cards) else None
        for row in range(2):
            lines.append((rich(pad(left[row], card_width), '   ', right[row]) if right else left[row], ''))
    return lines


def usage_panel(window, gpu=None, mode='user', sort='memory', selected=0, detail=False, width=78, theme=None):
    theme = theme or Theme()
    users = ordered_users(window, gpu, sort)
    chosen = users[min(selected, len(users) - 1)] if users else None
    processes = [p for p in window.get('processes', []) if gpu is None or p['gpu_uuid'] == gpu]
    indexes = {g['uuid']: g['index'] for g in window.get('gpus', [])}
    def members(u):
        return [p for p in processes if p['gpu_uuid'] == u['gpu_uuid'] and user_key(p) == u['user_key']]
    columns = [('USER', max(8, min(22, width - 48)), 'l'), ('GPU', 3, 'r'), ('N', 3, 'r'),
               ('VRAM GiB', 8, 'r'), ('SM PK', 7, 'r'), ('NOACT', 5, 'r'), ('ACT', 3, 'r'), ('LOW', 3, 'r'),
               ('IDLE', 4, 'r'), ('UNK', 3, 'r')]
    wide_process = width >= 78
    if mode == 'process' or detail:
        metrics = [('SM AVG', 7, 'r')]
        if wide_process:
            metrics += [('SM PK', 7, 'r'), ('SM COV', 6, 'r')]
        columns = [('USER', max(8, min(16, width - (56 if wide_process else 41))), 'l'),
                   ('GPU', 3, 'r'), ('PID', 8, 'r'), ('GiB', 7, 'r')] + metrics + [('STATUS', 11, 'l')]
    lines = []
    if detail and chosen:
        lines.append((fit('DETAIL | %s | %s' % (chosen['username'], chosen['gpu_uuid']), width), 'title'))
    lines.append((table(columns, [c[0] for c in columns]), 'columns'))
    if detail and chosen:
        counts = Counter(p['status'] for p in members(chosen))
        lines.extend([
            ('ACT=%s LOW=%s IDLE=%s UNK=%s' % (counts['ACTIVE'], counts['LOW_ACTIVITY'],
                counts['SUSPECTED_IDLE'], counts['UNKNOWN']), 'dim'),
            ('NOACT=%s / N=%s' % (counts['NO_ACTIVITY_OBSERVED'], sum(counts.values())), 'dim'),
        ])
    selection_row = None
    if mode == 'user' and not detail:
        for index, u in enumerate(users):
            procs = members(u)
            counts = Counter(p['status'] for p in procs)
            sm = [p['sm']['max'] for p in procs if p['sm']['max'] is not None]
            active, low, idle, unknown = (counts[k] for k in ('ACTIVE', 'LOW_ACTIVITY', 'SUSPECTED_IDLE', 'UNKNOWN'))
            row = table(columns, [u['username'], indexes.get(u['gpu_uuid'], '?'), len(procs),
                number(u['memory']['avg'], scale=2**30), number(max(sm) if sm else None, '%'), counts['NO_ACTIVITY_OBSERVED'], active, low, idle, unknown],
                roles=['', '', '', '', theme.load_role(max(sm) if sm else None),
                    count_role(counts['NO_ACTIVITY_OBSERVED'], 'NO_ACTIVITY_OBSERVED'),
                    count_role(active, 'ACTIVE'), count_role(low, 'LOW_ACTIVITY'),
                    count_role(idle, 'SUSPECTED_IDLE'), count_role(unknown, 'UNKNOWN')])
            style = ''
            if index == selected:
                selection_row = len(lines)
                style = 'selected'
            lines.append((row, style))
    else:
        visible = members(chosen) if detail and chosen else processes
        visible = sorted(visible, key=lambda p: p['username'] if sort == 'name' else -p['idle_streak'] if sort == 'idle' else -(p['memory']['avg'] or 0))
        for p in visible:
            values = [p['username'], indexes.get(p['gpu_uuid'], '?'), p['pid'],
                      number(p['memory']['avg'], scale=2**30), number(p['sm']['avg'], '%')]
            coverage = p.get('sm_coverage')
            if wide_process:
                values += [number(p['sm']['max'], '%'), number(coverage * 100 if coverage is not None else None, '%')]
            roles = ['', '', '', '', theme.load_role(p['sm']['avg'])]
            if wide_process:
                roles += [theme.load_role(p['sm']['max']), 'dim' if coverage is not None else 'na']
            lines.append((table(columns, values + [STATUS[p['status']]], roles + [p['status']]), ''))
            if detail:
                lines.extend([
                    (fit(rich('  SM avg ', (number(p['sm']['avg'], '%'), theme.load_role(p['sm']['avg'])), ' | peak ', (number(p['sm']['max'], '%'), theme.load_role(p['sm']['max']))), width), ''),
                    (fit('  SM valid cov %s | samples %s' % (number(coverage * 100 if coverage is not None else None, '%'),
                        p.get('sm_samples', 'N/A')), width), 'dim'),
                    (fit('  %s [%s]  ENC %s DEC %s  idle streak %s' % (p['name'], p['state'],
                        number(p['encoder']['avg'], '%'), number(p['decoder']['avg'], '%'), p['idle_streak']), width), ''),
                    (fit('  Seen %s -> %s | util cov %.0f%%' % (stamp(p['first_seen']), stamp(p['last_seen']), p['util_coverage']*100), width), 'dim'),
                ])
                if p['error']:
                    lines.append((fit('  ' + p['error'], width), 'UNKNOWN'))
    if not processes:
        lines.append(('No visible processes in the last window.', 'dim'))
    return lines, chosen, selection_row


def history_panel(history, chosen=None, detail=False, width=78, theme=None, memory_totals=None):
    theme = theme or Theme()
    memory_totals = memory_totals or {}
    daily = any(r['date'] for r in history['rows'])
    columns = [('USER', max(6, min(18, width - (49 if daily else 43))), 'l'), ('GPU', 3, 'l'),
               ('AVG GiB', 7, 'r'), ('ACTIVE', 7, 'r'), ('V/OBS', 7, 'r'), ('IDLE', 4, 'r'), ('UNK', 3, 'r'), ('PUNK', 5, 'r')]
    if daily:
        columns.insert(0, ('DATE', 5, 'l'))
    rows = [(table(columns, [c[0] for c in columns]), 'columns')]
    for r in history['rows']:
        if detail and chosen and (r['gpu_uuid'] != chosen['gpu_uuid'] or r.get('user_key', str(r['uid'])) != chosen['user_key']):
            continue
        values = [r['username'], r.get('gpu_index', r['gpu_uuid'][-3:]), number(r['memory_avg'], scale=2**30),
            number(r['active_ratio']*100 if r['active_ratio'] is not None else None, '%'),
            '%s/%s' % (r['valid_windows'], r['observed_windows']), r['idle_windows'], r['unknown_windows'], r.get('unknown_process_windows', 'N/A')]
        if daily:
            values.insert(0, r['date'][5:])
        total = memory_totals.get(r['gpu_uuid'])
        ratio = memory_ratio(r['memory_avg'], total)
        roles = ['', '', theme.load_role(ratio, 'vram') if ratio is not None else '',
                 'ACTIVE' if r['active_ratio'] is not None else 'na', 'dim',
                 count_role(r['idle_windows'], 'SUSPECTED_IDLE'), count_role(r['unknown_windows'], 'UNKNOWN'),
                 count_role(r.get('unknown_process_windows'), 'UNKNOWN')]
        if daily:
            roles.insert(0, 'dim')
        rows.append((table(columns, values, roles), ''))
        if detail:
            ratio = r.get('unknown_process_ratio')
            rows.extend([
                (fit('  Process UNKNOWN: %s/%s (%s)' % (r.get('unknown_process_windows', 'N/A'),
                    r.get('process_windows', 'N/A'), number(ratio * 100 if ratio is not None else None, '%')), width), 'UNKNOWN'),
                (fit('  Windows with UNKNOWN: %s/%s' % (r.get('windows_with_unknown_processes', 'N/A'), r['observed_windows']), width), 'dim'),
            ])
    if len(rows) == 1:
        rows.append(('No observations in this range.', 'dim'))
    return rows


def screen_lines(window, history, gpu=None, mode='user', sort='memory', selected=0, detail=False, width=80):
    """Plain, width-bounded dashboard representation for reports and tests."""
    lines = [('GPUWhistle | seegpu - GPU User Usage Monitor', 'title')]
    if not window:
        return lines + [('Database empty. Run: seegpu once (gpumonitor once)', 'UNKNOWN')]
    age = max(0, time.time() - window['end'])
    lines.append(('Last sample: %s | age %.0fs | %s %s/%s' % (stamp(window['end']), age,
        'complete' if window['complete'] else 'PARTIAL', window['successful_samples'], window['planned_samples']), ''))
    lines += gpu_panel(window, gpu, width, compact=True)
    usage, chosen, _ = usage_panel(window, gpu, mode, sort, selected, detail, width)
    lines += [('CURRENT | ' + mode.upper(), 'title')] + usage
    lines += [('HISTORY | last %sh' % (history['days'] * 24), 'title')] + history_panel(history, chosen, detail, width)
    return [(fit(line, width), style) for line, style in lines]


def launch(storage, config):
    try:
        curses.wrapper(_loop, storage, config)
        return 0
    except curses.error as exc:
        print('Terminal unavailable: %s. Use seegpu report.' % exc)
        return 1


def put(screen, y, x, text, width, style=0):
    if isinstance(screen, Frame):
        screen.write(y, x, text, width, style)
        return
    height, columns = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < columns and width > 0:
        try:
            screen.addnstr(y, x, fit(text, width).ljust(width), min(width, columns - x - 1), style)
        except curses.error:
            pass


def draw_panel(screen, y, x, height, width, title, lines, offset, focused, colors):
    body_height = max(0, height - 2)
    offset = min(max(0, offset), max(0, len(lines) - 1 - body_height))
    label = ('> ' if focused else '  ') + title
    if len(lines) > body_height + 1:
        label += ' [%s-%s/%s]' % (offset + 1, min(len(lines)-1, offset + body_height), len(lines)-1)
    put(screen, y, x, label, width, colors.get('section', curses.A_BOLD))
    if height > 1 and lines:
        put(screen, y + 1, x, lines[0][0], width, colors.get('columns', curses.A_BOLD))
    for index, (line, style) in enumerate(lines[1 + offset:1 + offset + body_height]):
        put(screen, y + 2 + index, x, line, width, colors.get(style, 0))
    return offset, body_height


def draw_help(screen, colors):
    height, width = screen.getmaxyx()
    box_width = min(78, width-2)
    rows = ['seegpu  KEYBOARD HELP', 'q always exits; h / ? / Esc closes help.',
            'NOACT=zero activity in sample; IDLE?=repeated evidence.',
            'UNKNOWN=insufficient evidence, never counted as zero.']
    rows += ['%-18s %s' % pair for pair in HELP]
    rows += ['CURRENT UNK=processes; HISTORY UNK=user windows.',
             'HISTORY PUNK=UNKNOWN process-window observations.',
             'SM AVG/PK: valid samples; SM COV: valid coverage.']
    top, left = max(0, (height-len(rows)-2)//2), max(0, (width-box_width)//2)
    for index in range(min(height-top-1, len(rows)+2)):
        line = rows[index-1] if 1 <= index <= len(rows) else ''
        put(screen, top+index, left, ' ' + line, box_width,
            colors.get('section', curses.A_BOLD) if index == 1 else 0)


def status_bar(window, cards, days, gpu, paused, period, theme):
    sep = theme.separator
    selected = next((g['index'] for g in cards if g['uuid'] == gpu), None)
    device = '%s GPUs' % len(cards) if gpu is None else 'GPU %s' % selected
    updated = datetime.fromtimestamp(window['end']).strftime('%H:%M:%S') if window else 'N/A'
    title = rich(('GPUWhistle', 'title'), ('  seegpu', 'dim'), sep, device, sep, 'Updated ',
                 (updated, '' if window else 'na'), sep)
    for value in HISTORY_DAYS:
        title = rich(title, ('%sd' % value, 'tab' if days == value else 'dim'), ' ')
    if paused:
        title = rich(title, ('  PAUSED', 'LOW_ACTIVITY'))
    if not window:
        return title, rich(('No samples yet. Run seegpu once (gpumonitor once).', 'UNKNOWN'))
    age = max(0, time.time()-window['end'])
    second = rich(('Last sample age %.0fs' % age, 'dim'), sep,
                  ('Complete' if window['complete'] else 'PARTIAL', '' if window['complete'] else 'SUSPECTED_IDLE'),
                  ' %s/%s' % (window['successful_samples'], window['planned_samples']), sep,
                  '%s processes' % len(window['processes']))
    if age > period*2:
        second = rich(('Last sample age %.0fs' % age, 'LOW_ACTIVITY'), sep,
                      ('STALE', 'LOW_ACTIVITY'), sep,
                      'Run seegpu once; check schedule status')
    if window['errors']:
        second = rich(second, sep, ('CHECK: ' + window['errors'][0], 'LOW_ACTIVITY'))
    return title, second


def _loop(screen, storage, config):
    with LiveScan() as scan:
        return _dashboard(screen, storage, config, scan)


def _dashboard(screen, storage, config, scan):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(500)
    theme = Theme.load()
    colors = theme_colors(theme)
    frame = Frame(screen, colors, ascii=theme.ascii)
    days, gpu, mode, sort, selected = 1, None, 'user', 'memory', 0
    detail = daily = help_open = paused = False
    stats_open = stats_detail = False
    stats_days, stats_selected, stats_offset, ranking = 30, 0, 0, None
    cluster_open, cluster_offset, cluster_error = False, 0, None
    focus, offsets, refreshed, window, history = 0, [0, 0], 0, None, None
    while True:
        scan.poll()
        if history is None or refreshed == 0 or (not paused and time.monotonic() - refreshed >= 5):
            window = storage.latest()
            history = report(storage, config, days, gpu=gpu, daily=daily)
            if stats_open:
                ranking = idle_report(storage, config, stats_days, gpu=gpu)
            refreshed = time.monotonic()
        height, width = screen.getmaxyx()
        frame.begin()
        cards = ranking['gpus'] if stats_open else (window or {}).get('gpus', [])
        users = ordered_users(window or {}, gpu, sort)
        selected = min(selected, max(0, len(users)-1))
        title, fresh = status_bar(window, cards, stats_days if stats_open else days, gpu, paused, config.period, theme)
        put(frame, 0, 0, title, width-1)
        put(frame, 1, 0, fresh, width-1)
        if cluster_open:
            live, counts = cluster_panel(scan, cluster_error, width-2)
            complete = sum(r['status'] != 'SCANNING' for r in scan.results)
            age = max((time.monotonic()-r['started_monotonic'] for r in scan.results), default=0)
            put(frame, 0, 0, rich(('GPUWhistle', 'title'), '  CLUSTER LIVE', '  c refresh / b back'), width-1)
            put(frame, 1, 0, rich('%s/%s hosts returned | ' % (complete, len(scan.results)),
                ('%s FREE' % counts['FREE'], 'load_low' if counts['FREE'] else 'dim'),
                ' | %s IN_USE | %s UNKNOWN/STALE | age %.0fs' % (counts['IN_USE'], counts['UNKNOWN']+counts['STALE'], age)), width-1)
            put(frame, 2, 0, 'FREE = no GPU processes + UTIL 0%% + used <= %.0f MiB' % scan.config.free_memory_mib, width-1, colors.get('dim', 0))
            put(frame, 3, 0, 'Snapshot only; no reservation. STALE after %.0fs; c queries again.' % scan.config.stale_after, width-1, colors.get('dim', 0))
            cluster_offset, cluster_page = draw_panel(frame, 5, 0, height-6, width-2,
                'LIVE GPU AVAILABILITY', live, cluster_offset, True, colors)
            put(frame, height-1, 0, 'c Collect now  b Back  Up/Down Scroll  h Help  q Quit', width-1, colors.get('dim', 0))
        elif stats_open:
            stats_selected = min(stats_selected, max(0, len(ranking['rows'])-1))
            stat_lines, stat_row = idle_panel(ranking, stats_selected, stats_detail, width-2)
            put(frame, 2, 0, 'ZERO~: zero observations x sampling period; SAMP: measured zero time', width-1, colors.get('dim', 0))
            put(frame, 3, 0, 'Historical owners included after exit. UNKNOWN is never zero activity.', width-1, colors.get('dim', 0))
            body = max(1, height-8)
            if stat_row is not None and not stats_detail:
                stats_offset = min(stats_offset, stat_row-1)
                if stat_row-1 >= stats_offset+body:
                    stats_offset = stat_row-body
            stats_offset, stats_page = draw_panel(frame, 5, 0, height-6, width-2,
                'IDLE OCCUPANCY / %sd / descending ZERO~ / %s historical owners' % (stats_days, len(ranking['rows'])),
                stat_lines, stats_offset, True, colors)
            put(frame, height-1, 0, 'c Live t Back 1/3/7/5/0 Days Enter Evidence h Help q Quit', width-1, colors.get('dim', 0))
        else:
            overview = gpu_panel(window or {}, gpu, width-1, compact=width < 120, theme=theme)
            geometry = layout(width, height, len(overview))
            if len(overview) > geometry.overview_limit:
                overview = overview[:max(1, geometry.overview_limit-1)] + [('More GPUs: g / Left / Right selects a device', 'dim')]
            put(frame, 2, 0, 'GPU OVERVIEW', width-1, colors.get('section', curses.A_BOLD))
            for i, (line, style) in enumerate(overview):
                put(frame, 3+i, 0, line, width-1, colors.get(style, 0))
            start = geometry.start
            panel_width = geometry.panel_width
            usage, chosen, selection_row = usage_panel(window or {}, gpu, mode, sort, selected, detail, panel_width, theme)
            memory_totals = {g['uuid']: g['memory_total'] for g in cards}
            hist = history_panel(history, chosen, detail, panel_width, theme, memory_totals)
            current_title = 'CURRENT / %s / %s' % ('DETAIL' if detail else mode.upper(), sort)
            history_title = 'HISTORY %sd  %s/%s complete%s' % (days, history['completed_windows'], history['attempted_windows'], '  DAILY' if daily else '')
            panel_height = geometry.current_height
            if focus == 0 and mode == 'user' and not detail and selection_row is not None:
                body_height = max(1, panel_height-2)
                row = selection_row-1
                if row < offsets[0]:
                    offsets[0] = row
                elif row >= offsets[0]+body_height:
                    offsets[0] = row-body_height+1
            offsets[0], page0 = draw_panel(frame, start, 0, panel_height, panel_width, current_title,
                usage, offsets[0], focus == 0, colors)
            hist_y, hist_x, hist_height = geometry.history_y, geometry.history_x, geometry.history_height
            offsets[1], page1 = draw_panel(frame, hist_y, hist_x, hist_height, panel_width, history_title,
                hist, offsets[1], focus == 1, colors)
            footer = ('c Live t Stats 1/3/7/5/0 Days g GPU Enter Detail h Help q Quit' if width >= 70
                      else 'c Live t Stats 1/3/7/5/0 Days Enter Detail h Help q Quit')
            put(frame, height-1, 0, footer, width-1, colors.get('dim', 0))
        if width < 60 or height < 18:
            frame.begin()
            put(frame, 0, 0, 'GPUWhistle: need at least 60x18. q quits.', width-1)
        if help_open and width >= 60 and height >= 18:
            draw_help(frame, colors)
        frame.flush()
        key = screen.getch()
        if key == ord('q'):
            return
        if key in (ord('h'), ord('?')):
            help_open = not help_open
            continue
        if key == 27:
            if help_open:
                help_open = False
            elif cluster_open:
                cluster_open = False
                scan.close()
            elif stats_open:
                if stats_detail:
                    stats_detail, stats_offset = False, 0
                else:
                    stats_open = False
            elif detail:
                detail = False
            else:
                return
            continue
        if help_open:
            continue
        if key == ord('c'):
            cluster_open, cluster_offset, cluster_error = True, 0, None
            scan.close()
            scan.results = []
            try:
                scan.start(ClusterConfig.load())
            except (OSError, ValueError, TypeError) as exc:
                cluster_error = 'Cluster config: %s' % exc
            continue
        if cluster_open:
            if key in (ord('b'), ord('t')):
                cluster_open = False
                scan.close()
                if key == ord('t'):
                    stats_open, refreshed = not stats_open, 0
            elif key in (curses.KEY_DOWN, curses.KEY_UP):
                cluster_offset = max(0, cluster_offset+(1 if key == curses.KEY_DOWN else -1))
            elif key in (curses.KEY_HOME, curses.KEY_END, curses.KEY_PPAGE, curses.KEY_NPAGE):
                cluster_offset = (0 if key == curses.KEY_HOME else max(0, len(live)-cluster_page-1)
                    if key == curses.KEY_END else max(0, cluster_offset+(cluster_page if key == curses.KEY_NPAGE else -cluster_page)))
            continue
        if key == ord('t'):
            stats_open, refreshed, ranking = not stats_open, 0, None
            stats_offset = 0
            continue
        if stats_open and key not in (*RANGE_KEYS, ord('g'), curses.KEY_LEFT, curses.KEY_RIGHT, ord('a'), ord('r'), ord(' ')):
            if key in (curses.KEY_ENTER, 10, 13):
                stats_detail, stats_offset = not stats_detail, max(0, (stat_row or 1)-1)
            elif key in (curses.KEY_DOWN, curses.KEY_UP):
                delta = 1 if key == curses.KEY_DOWN else -1
                if stats_detail:
                    stats_offset = max(0, stats_offset+delta)
                else:
                    stats_selected = max(0, min(stats_selected+delta, len(ranking['rows'])-1))
            elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
                if stats_detail:
                    stats_offset = (0 if key == curses.KEY_HOME else max(0, len(stat_lines)-stats_page-1)
                        if key == curses.KEY_END else max(0, stats_offset+(stats_page if key == curses.KEY_NPAGE else -stats_page)))
                else:
                    stats_selected = (0 if key == curses.KEY_HOME else max(0, len(ranking['rows'])-1)
                        if key == curses.KEY_END else max(0, min(len(ranking['rows'])-1,
                            stats_selected+(stats_page if key == curses.KEY_NPAGE else -stats_page))))
            continue
        if key in RANGE_KEYS:
            if stats_open:
                stats_days, stats_selected, stats_offset = RANGE_KEYS[key], 0, 0
            else:
                days = RANGE_KEYS[key]
            refreshed, offsets = 0, [0, 0]
        elif key in (ord('g'), curses.KEY_LEFT, curses.KEY_RIGHT, ord('a')):
            devices = [None] + [g['uuid'] for g in cards]
            position = devices.index(gpu) if gpu in devices else 0
            gpu = None if key == ord('a') else devices[(position + (-1 if key == curses.KEY_LEFT else 1)) % len(devices)]
            refreshed, selected, offsets = 0, 0, [0, 0]
            stats_selected, stats_offset = 0, 0
        elif key == ord('u'):
            mode, offsets[0] = ('process' if mode == 'user' else 'user'), 0
        elif key in (ord('n'), ord('m'), ord('i'), ord('s')):
            sort = ('name', 'memory', 'idle')[(('name', 'memory', 'idle').index(sort)+1)%3] if key == ord('s') else {ord('n'):'name', ord('m'):'memory', ord('i'):'idle'}[key]
            selected, offsets[0] = 0, 0
        elif key in (curses.KEY_ENTER, 10, 13):
            detail, offsets[0] = not detail, 0
        elif key == 9:
            focus = 1-focus
        elif key in (curses.KEY_DOWN, curses.KEY_UP):
            delta = 1 if key == curses.KEY_DOWN else -1
            if focus == 0 and mode == 'user' and not detail:
                selected = max(0, min(selected+delta, len(users)-1))
            else:
                offsets[focus] = max(0, offsets[focus]+delta)
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
            page = max(1, page0 if focus == 0 else page1)
            if key == curses.KEY_HOME:
                offsets[focus] = 0
            elif key == curses.KEY_END:
                offsets[focus] = max(0, len(usage if focus == 0 else hist)-page-1)
            else:
                offsets[focus] = max(0, offsets[focus] + (page if key == curses.KEY_NPAGE else -page))
            if focus == 0 and mode == 'user' and not detail:
                selected = min(max(0, len(users)-1), offsets[0])
        elif key == ord('d'):
            daily, refreshed, offsets[1] = not daily, 0, 0
        elif key == ord('r'):
            refreshed = 0
        elif key == ord(' '):
            paused = not paused
