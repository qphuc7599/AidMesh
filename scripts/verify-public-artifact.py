"""Verify the expanded AidMesh artifact using only the Python standard library.

Archive mode requires the published FILE_MANIFEST.json and SHA256SUMS beside all
four ZIPs. Extracted mode requires artifacts/FILE_MANIFEST.json below the root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def digest(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(block)
    return h.hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name:
        raise ValueError(f'Unsafe manifest path: {name}')
    return path


def verify_archives(folder, manifest):
    sums = {}
    for line in (folder / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
        if not line.strip():
            continue
        expected, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        safe_relative(name)
        sums[name] = expected
    if not sums or 'FILE_MANIFEST.json' not in sums:
        raise ValueError('Checksum list must include FILE_MANIFEST.json')
    for name, expected in sums.items():
        with (folder / name).open('rb') as stream:
            actual = digest(stream)
        if actual != expected:
            raise ValueError(f'Archive/manifest checksum mismatch: {name}')
        print(f'SHA256 OK {name}', flush=True)
    verified = 0
    for archive, stats in manifest['archives'].items():
        if archive not in sums:
            raise ValueError(f'Archive missing from checksum list: {archive}')
        entries = {row['path']: row for row in manifest['files'] if row['archive'] == archive}
        with zipfile.ZipFile(folder / archive) as z:
            members = [info for info in z.infolist() if not info.is_dir()]
            names = [info.filename for info in members]
            if len(names) != len(set(names)):
                raise ValueError(f'Duplicate ZIP member: {archive}')
            metadata = set(manifest.get('metadata_members', {}).get(archive, []))
            if set(names) != set(entries) | metadata:
                raise ValueError(f'Unexpected or missing ZIP members: {archive}')
            for info in members:
                safe_relative(info.filename)
                if info.filename in metadata:
                    if info.filename != 'artifacts/FILE_MANIFEST.json':
                        raise ValueError(f'Unsupported metadata member: {info.filename}')
                    if json.loads(z.read(info)) != manifest:
                        raise ValueError('Embedded manifest differs from published manifest')
                    continue
                row = entries[info.filename]
                if info.file_size != row['bytes']:
                    raise ValueError(f'ZIP size mismatch: {info.filename}')
                with z.open(info) as stream:
                    actual = digest(stream)
                if actual != row['sha256']:
                    raise ValueError(f'ZIP member hash mismatch: {info.filename}')
                verified += 1
        if len(entries) != stats['files']:
            raise ValueError(f'Archive count mismatch: {archive}')
        print(f'MEMBERS OK {archive}: {len(entries)}', flush=True)
    return verified


def verify_root(root, manifest):
    verified = 0
    unique = {}
    for row in manifest['files']:
        previous = unique.setdefault(row['path'], row)
        if previous['sha256'] != row['sha256']:
            raise ValueError(f'Overlay has inconsistent duplicates: {row["path"]}')
    for name, row in unique.items():
        relative = safe_relative(name)
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing file or symlink: {name}')
        if path.stat().st_size != row['bytes']:
            raise ValueError(f'Extracted size mismatch: {name}')
        with path.open('rb') as stream:
            actual = digest(stream)
        if actual != row['sha256']:
            raise ValueError(f'Extracted hash mismatch: {name}')
        verified += 1
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--archive-dir', type=Path)
    mode.add_argument('--root', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    manifest_path = args.manifest or (
        args.archive_dir / 'FILE_MANIFEST.json'
        if args.archive_dir else args.root / 'artifacts/FILE_MANIFEST.json'
    )
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported manifest schema')
    if args.archive_dir:
        count = verify_archives(args.archive_dir, manifest)
    else:
        count = verify_root(args.root.resolve(), manifest)
    print(json.dumps({'status': 'PASS', 'verified_files': count,
                      'mode': 'archives' if args.archive_dir else 'extracted',
                      'scope': 'file integrity only; no new experimental run'}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        sys.exit(1)
