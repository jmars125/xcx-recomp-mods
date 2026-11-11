"""Checks every package under mods/ and writes index.json, the list the XCX Recomp mod manager downloads.

    python tools/build_index.py           # check, then write index.json
    python tools/build_index.py --check   # check, and fail when index.json is not up to date (CI)

Two kinds of entry:
  mods/<id>/     a Lua mod hosted here (kind "lua")
  links/<id>.ini a game-file mod hosted elsewhere, such as GameBanana (kind "romfs"): the manager downloads the zip
                 from its author's page, checks its SHA-256 and unpacks its romfs/ folder into mods/<id>/romfs/.
                 Other people's files are never copied into this repository.

A package is mods/<id>/ with the files the game reads (mod.ini, main.lua) and info.ini, which only this list and the
manager read. The checks follow the game's own loader (runtime/mod_package.c in xcx-recomp): the folder name equals
the manifest id, the manifest is strict key=value with api=1 and language=lua55-basic-v1, main.lua is text of at most
64 KiB. Native providers (provider.ini, native/) are not accepted here: they are trusted code and need a review of
their own.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODS = ROOT / 'mods'
INDEX = ROOT / 'index.json'
ID = re.compile(r'[a-z][a-z0-9._-]{0,63}\Z')
VERSION = re.compile(r'\d+\.\d+\.\d+\Z')
MANIFEST_KEYS = {'id', 'api', 'language', 'version', 'name', 'requires'}
INFO_KEYS = {'description', 'author', 'license', 'homepage'}
SOURCE_LIMIT = 65536
MANIFEST_LIMIT = 4096
LINKS = ROOT / 'links'
LINK_KEYS = {'name', 'version', 'description', 'author', 'license', 'homepage', 'url', 'sha256', 'size', 'notes'}
SHA = re.compile(r'[0-9a-f]{64}\Z')
HTTPS = re.compile(r'https://[^\s"<>]+\Z')
ALLOWED_FILES = {'mod.ini', 'main.lua', 'info.ini', 'README.md', 'LICENSE', 'LICENSE.txt'}


def read_kv(path, keys, limit):
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError(f'{path.name} is larger than {limit} bytes')
    if b'\0' in data or data.startswith(b'\xef\xbb\xbf'):
        raise ValueError(f'{path.name} has a NUL or a byte-order mark')
    out = {}
    for line in data.decode('utf-8').split('\n'):
        line = line[:-1] if line.endswith('\r') else line
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key not in keys:
            raise ValueError(f'{path.name}: unknown or malformed line {line!r}')
        if key in out:
            raise ValueError(f'{path.name}: {key} appears twice')
        out[key] = value
    return out


def check_package(folder):
    mid = folder.name
    if not ID.match(mid):
        raise ValueError('folder name is not a valid id (a-z first, then a-z 0-9 . _ -, at most 64)')
    names = {p.name for p in folder.iterdir()}
    extra = names - ALLOWED_FILES
    if extra:
        raise ValueError(f'unexpected files: {", ".join(sorted(extra))}')
    for need in ('mod.ini', 'main.lua', 'info.ini'):
        if need not in names:
            raise ValueError(f'{need} is missing')
    m = read_kv(folder / 'mod.ini', MANIFEST_KEYS, MANIFEST_LIMIT)
    if m.get('id') != mid:
        raise ValueError('mod.ini id differs from the folder name')
    if m.get('api') != '1' or m.get('language') != 'lua55-basic-v1':
        raise ValueError('mod.ini needs api=1 and language=lua55-basic-v1')
    if not VERSION.match(m.get('version', '')) or len(m['version']) > 31:
        raise ValueError('mod.ini version must be three numbers, like 1.0.0')
    name = m.get('name', mid)
    if not name or len(name.encode()) > 96:
        raise ValueError('mod.ini name must be 1 to 96 bytes')
    requires = m['requires'].split(',') if m.get('requires') else []
    if any(not ID.match(r) for r in requires) or len(set(requires)) != len(requires):
        raise ValueError('mod.ini requires must be distinct ids separated by commas')
    src = (folder / 'main.lua').read_bytes()
    if not src or len(src) > SOURCE_LIMIT or src[:1] == b'\x1b':
        raise ValueError('main.lua must be Lua source of 1 byte to 64 KiB (no bytecode)')
    info = read_kv(folder / 'info.ini', INFO_KEYS, 4096)
    for need in ('description', 'author', 'license'):
        if not info.get(need):
            raise ValueError(f'info.ini needs {need}=')
    return {'id': mid, 'kind': 'lua', 'name': name, 'version': m['version'], 'requires': requires,
            'description': info['description'], 'author': info['author'], 'license': info['license'],
            'homepage': info.get('homepage', ''), 'path': f'mods/{mid}',
            'files': [{'name': p.name, 'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                      for p in sorted(folder.iterdir())]}


def check_link(path):
    mid = path.stem
    if not ID.match(mid):
        raise ValueError('file name is not a valid id (a-z first, then a-z 0-9 . _ -, at most 64)')
    m = read_kv(path, LINK_KEYS, 4096)
    for need in ('name', 'version', 'description', 'author', 'homepage', 'url', 'sha256', 'size'):
        if not m.get(need):
            raise ValueError(f'needs {need}=')
    if len(m['name'].encode()) > 96 or len(m['version']) > 31:
        raise ValueError('name must be at most 96 bytes and version at most 31')
    if not HTTPS.match(m['homepage']) or not HTTPS.match(m['url']):
        raise ValueError('homepage and url must be https:// links')
    if not SHA.match(m['sha256']):
        raise ValueError('sha256 must be 64 lowercase hex digits')
    if not m['size'].isdigit() or not 0 < int(m['size']) <= 4 << 30:
        raise ValueError('size must be the zip size in bytes')
    return {'id': mid, 'kind': 'romfs', 'name': m['name'], 'version': m['version'], 'requires': [],
            'description': m['description'], 'author': m['author'], 'license': m.get('license', ''),
            'homepage': m['homepage'], 'url': m['url'], 'sha256': m['sha256'], 'size': int(m['size']),
            'notes': m.get('notes', '')}


def build():
    mods, errors = [], []
    for folder in sorted(p for p in MODS.iterdir() if p.is_dir()):
        try:
            mods.append(check_package(folder))
        except (ValueError, UnicodeDecodeError) as e:
            errors.append(f'mods/{folder.name}: {e}')
    for path in sorted(LINKS.glob('*')) if LINKS.is_dir() else []:
        try:
            if path.suffix != '.ini' or not path.is_file():
                raise ValueError('only <id>.ini files belong in links/')
            mods.append(check_link(path))
        except (ValueError, UnicodeDecodeError) as e:
            errors.append(f'links/{path.name}: {e}')
    seen = set()
    for m in mods:
        if m['id'] in seen:
            errors.append(f'{m["id"]}: the id is used twice')
        seen.add(m['id'])
    mods.sort(key=lambda m: m['id'])
    ids = {m['id'] for m in mods}
    for m in mods:
        for r in m['requires']:
            if r not in ids:
                errors.append(f'mods/{m["id"]}: requires {r}, which is not in this repository')
    return {'format': 2, 'mods': mods}, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--check', action='store_true', help='fail when index.json is out of date')
    args = ap.parse_args()
    index, errors = build()
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        return 1
    text = json.dumps(index, indent=1, ensure_ascii=False) + '\n'
    if args.check:
        if not INDEX.is_file() or INDEX.read_text(encoding='utf-8') != text:
            print('index.json is out of date: run python tools/build_index.py and commit it', file=sys.stderr)
            return 1
        print(f'{len(index["mods"])} mods checked, index.json up to date')
        return 0
    INDEX.write_text(text, encoding='utf-8', newline='\n')
    print(f'{len(index["mods"])} mods checked, index.json written')
    return 0


if __name__ == '__main__':
    sys.exit(main())
