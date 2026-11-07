"""Standalone, standard-library probe sent over SSH; no installation required."""
import csv
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET


def quantity(text, unit):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*' + re.escape(unit) + r'\s*', text or '')
    value = float(match[1]) if match else None
    return value if value is not None and math.isfinite(value) else None


def parse_xml(text, indexes):
    root = ET.fromstring(text)
    if root.tag != 'nvidia_smi_log':
        raise ValueError('Unexpected nvidia-smi XML root')
    result = []
    for g in root.findall('gpu'):
        uuid = g.findtext('uuid')
        if uuid not in indexes:
            raise ValueError('GPU inventory changed during query; retry')
        procs = g.find('processes')
        processes_ok = (procs is not None and not (procs.text or '').strip()
                        and all(p.tag == 'process_info' for p in procs))
        mig_text = g.findtext('mig_mode/current_mig')
        result.append(dict(index=indexes[uuid], name=g.findtext('product_name') or 'N/A',
            total_mib=quantity(g.findtext('fb_memory_usage/total'), 'MiB'),
            used_mib=quantity(g.findtext('fb_memory_usage/used'), 'MiB'),
            free_mib=quantity(g.findtext('fb_memory_usage/free'), 'MiB'),
            gpu_util=quantity(g.findtext('utilization/gpu_util'), '%'),
            process_count=len(procs.findall('process_info')) if processes_ok else None,
            mig=False if mig_text == 'Disabled' else True if mig_text == 'Enabled' else None,
            compute_mode=g.findtext('compute_mode')))
    if len(result) != len(indexes) or len({g['index'] for g in result}) != len(result):
        raise ValueError('Incomplete GPU inventory; retry')
    return result


def probe():
    inventory = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid', '--format=csv,noheader'],
                               capture_output=True, text=True, timeout=8, check=True)
    indexes = {}
    for row in csv.reader(inventory.stdout.splitlines()):
        if len(row) != 2 or not row[0].strip().isdigit() or row[1].strip() in indexes:
            raise ValueError('Invalid GPU inventory')
        indexes[row[1].strip()] = int(row[0].strip())
    reading = subprocess.run(['nvidia-smi', '-q', '-x'], capture_output=True, text=True, timeout=8, check=True)
    return dict(gpus=parse_xml(reading.stdout, indexes))


if __name__ == '__main__':
    try:
        data = probe()
        # Clock age is tracked by the caller, not a potentially skewed remote clock.
        print(json.dumps(data, allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(error='%s: %s' % (type(exc).__name__, exc))))
