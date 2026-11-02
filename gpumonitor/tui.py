"""SQLite-only dashboard with independent, scrollable current/history panels."""
import curses
import time
from collections import Counter
from datetime import datetime
from .analytics import report
from .cli import number
from .models import user_key
from .tui_theme import Theme, fit, pad, rich, width as cell_length, colors as theme_colors
from .tui_render import Frame, layout

HELP = [
    ('q', 'Quit immediately'), ('h / ?', 'Show or close this help'),
    ('1 / 3 / 7', 'History: last 24 / 72 / 168 hours'),
    ('g / Left / Right', 'Cycle GPU selection; a selects all GPUs'),
    ('u', 'Switch users / processes'), ('n / m / i', 'Sort by name / VRAM / idle streak'),
    ('s', 'Cycle sort order'), ('Tab', 'Focus current usage / history'),
    ('Up / Down', 'Select user, or scroll the focused table'),
    ('Enter', 'Open / close selected user details'), ('Esc', 'Close help or details; otherwise quit'),
    ('PgUp / PgDn', 'Scroll focused table by one page'), ('Home / End', 'First / last table page'),
    ('d', 'Group history by local calendar date'), ('r', 'Refresh SQLite now (no NVML query)'),
    ('Space', 'Pause / resume automatic database refresh'),
]
STATUS = {'ACTIVE': 'ACTIVE', 'LOW_ACTIVITY': 'LOW', 'NO_ACTIVITY_OBSERVED': 'NO EVIDENCE',
          'SUSPECTED_IDLE': 'IDLE?', 'UNKNOWN': 'UNKNOWN'}


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
    columns = [('USER', max(8, min(22, width - 42)), 'l'), ('GPU', 3, 'r'), ('N', 3, 'r'),
               ('VRAM GiB', 8, 'r'), ('SM PK', 7, 'r'), ('ACT', 3, 'r'), ('LOW', 3, 'r'),
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
            ('NO EVIDENCE=%s / N=%s' % (counts['NO_ACTIVITY_OBSERVED'], sum(counts.values())), 'dim'),
        ])
    selection_row = None
    if mode == 'user' and not detail:
        for index, u in enumerate(users):
            procs = members(u)
            counts = Counter(p['status'] for p in procs)
            sm = [p['sm']['max'] for p in procs if p['sm']['max'] is not None]
            active, low, idle, unknown = (counts[k] for k in ('ACTIVE', 'LOW_ACTIVITY', 'SUSPECTED_IDLE', 'UNKNOWN'))
            row = table(columns, [u['username'], indexes.get(u['gpu_uuid'], '?'), len(procs),
                number(u['memory']['avg'], scale=2**30), number(max(sm) if sm else None, '%'), active, low, idle, unknown],
                roles=['', '', '', '', theme.load_role(max(sm) if sm else None),
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
    rows = ['seegpu  KEYBOARD HELP', 'q always exits; h / ? / Esc closes help.']
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
    for value in (1, 3, 7):
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
        second = rich(second, sep, ('STALE', 'LOW_ACTIVITY'))
    if window['errors']:
        second = rich(second, sep, ('CHECK: ' + window['errors'][0], 'LOW_ACTIVITY'))
    return title, second


def _loop(screen, storage, config):
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
    focus, offsets, refreshed, window, history = 0, [0, 0], 0, None, None
    while True:
        if history is None or refreshed == 0 or (not paused and time.monotonic() - refreshed >= 5):
            window = storage.latest()
            history = report(storage, config, days, gpu=gpu, daily=daily)
            refreshed = time.monotonic()
        height, width = screen.getmaxyx()
        frame.begin()
        cards = (window or {}).get('gpus', [])
        users = ordered_users(window or {}, gpu, sort)
        selected = min(selected, max(0, len(users)-1))
        title, fresh = status_bar(window, cards, days, gpu, paused, config.period, theme)
        put(frame, 0, 0, title, width-1)
        put(frame, 1, 0, fresh, width-1)
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
        put(frame, height-1, 0, '1/3/7 History   g GPU   u User   Enter Detail   h Help   q Quit', width-1, colors.get('dim', 0))
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
            elif detail:
                detail = False
            else:
                return
            continue
        if help_open:
            continue
        if key in (ord('1'), ord('3'), ord('7')):
            days, refreshed, offsets = int(chr(key)), 0, [0, 0]
        elif key in (ord('g'), curses.KEY_LEFT, curses.KEY_RIGHT, ord('a')):
            devices = [None] + [g['uuid'] for g in cards]
            position = devices.index(gpu) if gpu in devices else 0
            gpu = None if key == ord('a') else devices[(position + (-1 if key == curses.KEY_LEFT else 1)) % len(devices)]
            refreshed, selected, offsets = 0, 0, [0, 0]
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
