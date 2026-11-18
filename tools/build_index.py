"""Checks every package under mods/ and writes index.json and index-3.json, the lists the XCX Recomp mod managers
download.

    python tools/build_index.py           # check, then write both lists
    python tools/build_index.py --check   # check, and fail when either list is not up to date (CI)

Two lists, so no manager is handed an entry it can't install:
  index.json    format 2: the Lua mods and the game-file mods downloaded as one zip. XCX Setup up to 0.1.21 and the
                game's own library list (its F1 Mods tab) read this one.
  index-3.json  format 3: every entry, also those downloaded from their own page, in parts or as RAR. XCX Setup
                after 0.1.21 reads this one.

Two kinds of entry:
  mods/<id>/     a Lua mod hosted here (kind "lua")
  links/<id>.ini a game-file mod hosted elsewhere, such as GameBanana (kind "romfs"): the manager downloads the zip
                 or RAR from its author's page, checks its SHA-256 and unpacks its game files into mods/<id>/romfs/.
                 Other people's files are never copied into this repository.
                 With download=manual (sites that allow downloads only from their own page, such as Nexus Mods)
                 there is no url: the entry names the page to download from (page=) and the file's name (file=),
                 the player downloads it there and picks it, and the manager checks it against sha256= and size=.
                 Such entries are only in index-3.json.
                 A mod that comes as several files (a full version and optional pieces, as on Nexus Mods) lists
                 each as a part: part.<part>.name=, .description=, .file=/.page= or .url=, .sha256=, .size= and
                 .includes= (the parts a part already contains). The manager installs each part the player picks
                 as its own mod, <id>.<part>, grouped under the entry. Entries in parts are only in index-3.json.
                 archive=rar marks a url entry whose download is a RAR (the default is zip); those are only in
                 index-3.json too, since the game's own list and older managers read zips alone.

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
INDEX3 = ROOT / 'index-3.json'
ID = re.compile(r'[a-z][a-z0-9._-]{0,63}\Z')
VERSION = re.compile(r'\d+\.\d+\.\d+\Z')
MANIFEST_KEYS = {'id', 'api', 'language', 'version', 'name', 'requires'}
INFO_KEYS = {'description', 'author', 'license', 'homepage'}
SOURCE_LIMIT = 65536
MANIFEST_LIMIT = 4096
LINKS = ROOT / 'links'
LINK_KEYS = {'name', 'version', 'description', 'author', 'license', 'homepage', 'url', 'sha256', 'size', 'notes',
             'download', 'page', 'file', 'archive'}
PART_FIELDS = {'name', 'description', 'url', 'page', 'file', 'sha256', 'size', 'includes'}
PART_KEY = re.compile(r'part\.([a-z][a-z0-9-]{0,31})\.([a-z0-9]+)\Z')
LINK_LIMIT = 16384
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
        part = PART_KEY.match(key) if keys is LINK_KEYS else None
        if not sep or key not in keys and not (part and part.group(2) in PART_FIELDS):
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


def check_file_fields(m, manual, where):
    """The download fields of an entry or a part: (page and file) or url, sha256 and size."""
    link = ('page', 'file') if manual else ('url',)
    for need in (*link, 'sha256', 'size'):
        if not m.get(need):
            raise ValueError(f'{where}needs {need}=')
    if manual and m.get('url'):
        raise ValueError(f'{where}a download=manual entry has page= and file=, not url=')
    if not manual and (m.get('page') or m.get('file')):
        raise ValueError(f'{where}page= and file= belong to download=manual entries')
    if not HTTPS.match(m['page' if manual else 'url']):
        raise ValueError(f'{where}' + ('page' if manual else 'url') + ' must be an https:// link')
    if manual and ('/' in m['file'] or '\\' in m['file'] or len(m['file'].encode()) > 200):
        raise ValueError(f'{where}file must be the downloaded file\'s name, at most 200 bytes')
    if not SHA.match(m['sha256']):
        raise ValueError(f'{where}sha256 must be 64 lowercase hex digits')
    if not m['size'].isdigit() or not 0 < int(m['size']) <= 4 << 30:
        raise ValueError(f'{where}size must be the file size in bytes')
    out = {'page': m['page'], 'file': m['file']} if manual else {'url': m['url']}
    out.update(sha256=m['sha256'], size=int(m['size']))
    return out


def check_link(path):
    mid = path.stem
    if not ID.match(mid):
        raise ValueError('file name is not a valid id (a-z first, then a-z 0-9 . _ -, at most 64)')
    m = read_kv(path, LINK_KEYS, LINK_LIMIT)
    manual = m.get('download') == 'manual'
    if m.get('download') not in (None, 'manual'):
        raise ValueError('download may only be manual')
    if m.get('archive') not in (None, 'zip', 'rar'):
        raise ValueError('archive may only be zip or rar')
    for need in ('name', 'version', 'description', 'author', 'homepage'):
        if not m.get(need):
            raise ValueError(f'needs {need}=')
    if len(m['name'].encode()) > 96 or len(m['version']) > 31:
        raise ValueError('name must be at most 96 bytes and version at most 31')
    if not HTTPS.match(m['homepage']):
        raise ValueError('homepage must be an https:// link')
    parts = {}
    for key, value in m.items():
        p = PART_KEY.match(key)
        if p:
            parts.setdefault(p.group(1), {})[p.group(2)] = value
    entry = {'id': mid, 'kind': 'romfs', 'name': m['name'], 'version': m['version'], 'requires': [],
             'description': m['description'], 'author': m['author'], 'license': m.get('license', ''),
             'homepage': m['homepage']}
    if manual:
        entry['download'] = 'manual'
    if m.get('archive') == 'rar':
        entry['archive'] = 'rar'
    if not parts:
        entry.update(check_file_fields(m, manual, ''))
        entry['notes'] = m.get('notes', '')
        return entry
    if any(m.get(k) for k in ('url', 'page', 'file', 'sha256', 'size')):
        raise ValueError('an entry with parts has its url/page/file/sha256/size on each part, not at the top')
    out = []
    for pid, fields in parts.items():
        where = f'part.{pid}: '
        if not ID.match(f'{mid}.{pid}'):
            raise ValueError(f'{where}{mid}.{pid} is longer than 64 characters')
        if not fields.get('name') or len(fields['name'].encode()) > 96:
            raise ValueError(f'{where}needs a name of 1 to 96 bytes')
        part = {'id': pid, 'name': fields['name'], 'description': fields.get('description', '')}
        part.update(check_file_fields(fields, manual, where))
        includes = [i for i in fields.get('includes', '').split(',') if i]
        bad = [i for i in includes if i not in parts or i == pid]
        if bad:
            raise ValueError(f'{where}includes names no other part: {", ".join(bad)}')
        part['includes'] = includes
        out.append(part)
    if len({p['sha256'] for p in out}) != len(out):
        raise ValueError('two parts have the same sha256')
    entry['parts'] = out
    entry['notes'] = m.get('notes', '')
    return entry


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
    return mods, errors


def for_every_manager(entry):
    """Whether an entry belongs in index.json too: one every manager installs, a Lua mod or a zip downloaded from its
    url."""
    return entry['kind'] == 'lua' or not (entry.get('download') or entry.get('parts') or entry.get('archive') == 'rar')


def lists(mods):
    """{path: text} of the two lists."""
    dump = lambda data: json.dumps(data, indent=1, ensure_ascii=False) + '\n'
    return {INDEX: dump({'format': 2, 'mods': [m for m in mods if for_every_manager(m)]}),
            INDEX3: dump({'format': 3, 'mods': mods})}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--check', action='store_true', help='fail when index.json or index-3.json is out of date')
    ap.add_argument('--part', nargs=2, metavar=('PART', 'FILE'),
                    help='print the file=, sha256= and size= lines of a part from its downloaded FILE, and stop')
    args = ap.parse_args()
    if args.part:
        pid, path = args.part[0], Path(args.part[1])
        data = path.read_bytes()
        print(f'part.{pid}.file={path.name}\npart.{pid}.sha256={hashlib.sha256(data).hexdigest()}\n'
              f'part.{pid}.size={len(data)}')
        return 0
    mods, errors = build()
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        return 1
    out = lists(mods)
    if args.check:
        stale = [p.name for p, text in out.items() if not p.is_file() or p.read_text(encoding='utf-8') != text]
        if stale:
            print(f'{" and ".join(stale)} out of date: run python tools/build_index.py and commit', file=sys.stderr)
            return 1
        print(f'{len(mods)} mods checked, index.json and index-3.json up to date')
        return 0
    for path, text in out.items():
        path.write_text(text, encoding='utf-8', newline='\n')
    print(f'{len(mods)} mods checked, index.json and index-3.json written')
    return 0


if __name__ == '__main__':
    sys.exit(main())
