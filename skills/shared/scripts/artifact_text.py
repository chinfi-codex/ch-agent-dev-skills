#!/usr/bin/env python3
"""Lossless source bundling and hash-guarded revision; never certifies semantics."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def source(root, item):
    path = (root / item['path']).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        raise ValueError(f'source hash mismatch: {path}')
    return path, raw


def bundle(root, manifest):
    grouped = {}
    for item in manifest['sources']:
        path, raw = source(root, item)
        lines = raw.decode('utf-8').splitlines(keepends=True)
        start, end = item.get('start', 1), item.get('end', len(lines))
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError(f'invalid source range: {path}')
        if path in grouped and grouped[path][0] != raw:
            raise ValueError(f'source changed during bundle: {path}')
        grouped.setdefault(path, (raw, []))[1].append((start, end))
    result = []
    for path, (raw, ranges) in grouped.items():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        lines = raw.decode('utf-8').splitlines(keepends=True)
        for start, end in merged:
            result.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                           'start': start, 'end': end, 'text': ''.join(lines[start - 1:end])})
    return {'sources': result}


def revise(root, manifest):
    path, raw = source(root, {'path': manifest['source'], 'sha256': manifest['sha256']})
    original = raw.decode('utf-8')
    edits = []
    for replacement in manifest['replacements']:
        old, new = replacement['old'], replacement['new']
        if not isinstance(old, str) or not isinstance(new, str) or not old or original.count(old) != 1:
            raise ValueError('each old text must match exactly once in the original')
        start = original.index(old)
        edits.append((start, start + len(old), new))
    edits.sort()
    if any(a[1] > b[0] for a, b in zip(edits, edits[1:])):
        raise ValueError('overlapping replacements')
    text = original
    for start, end, new in reversed(edits):
        text = text[:start] + new + text[end:]
    output = (root / manifest['output']).resolve()
    if output == path:
        raise ValueError('revision must use a new path')
    data = text.encode('utf-8')
    # Exclusive creation protects existing approved or frozen revisions.
    with output.open('xb') as stream:
        stream.write(data)
    return {'path': str(output), 'sha256': hashlib.sha256(data).hexdigest(),
            'source_sha256': manifest['sha256'], 'replacements': len(edits)}


def render(root, manifest):
    """Fill a hash-pinned report skeleton without rewriting authored judgments."""
    import re
    path, raw = source(root, {'path': manifest['template'], 'sha256': manifest['sha256']})
    template = raw.decode('utf-8')
    fields = manifest['fields']
    keys = set(re.findall(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}', template))
    if not isinstance(fields, dict) or set(fields) != keys:
        raise ValueError('report fields must exactly cover template placeholders')
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError('report fields must contain non-empty authored text')
    text = re.sub(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}', lambda m: fields[m[1]], template)
    output = (root / manifest['output']).resolve()
    with output.open('xb') as stream:
        stream.write(text.encode('utf-8'))
    return {'path': str(output), 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'template_sha256': manifest['sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bundle', 'revise', 'render'])
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        result = {'bundle': bundle, 'revise': revise, 'render': render}[args.action](args.manifest.resolve().parent, manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
