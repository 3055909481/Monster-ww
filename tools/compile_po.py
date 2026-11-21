"""Compile a GNU .po file to binary .mo using only the Python standard library.

Usage: python tools/compile_po.py i18n/zh_CN/LC_MESSAGES/ok.po

The project has no msgfmt/polib available locally, and gettext's binary format
is simple enough to emit directly. Only untranslated (empty msgstr) and fuzzy
entries are skipped; header (msgid "") is preserved.
"""

import ast
import struct
import sys
from pathlib import Path


def _unescape(value):
    """Decode a Python-style quoted string fragment from a .po file."""
    return ast.literal_eval(value)


def parse_po(path):
    entries = {}
    msgid = msgstr = None
    target = None
    fuzzy = False

    def flush():
        nonlocal msgid, msgstr, fuzzy
        if msgid is not None and msgstr is not None and msgstr != '' and not fuzzy:
            entries[msgid] = msgstr
        msgid = msgstr = None
        fuzzy = False

    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line:
            if msgid is not None:
                flush()
            continue
        if line.startswith('#'):
            if line.startswith('#,') and 'fuzzy' in line:
                fuzzy = True
            continue
        if line.startswith('msgid '):
            if msgid is not None:
                flush()
            msgid = _unescape(line[len('msgid '):].strip())
            msgstr = None
            target = 'id'
        elif line.startswith('msgstr '):
            msgstr = _unescape(line[len('msgstr '):].strip())
            target = 'str'
        elif line.startswith('"'):
            fragment = _unescape(line)
            if target == 'id' and msgid is not None:
                msgid += fragment
            elif target == 'str' and msgstr is not None:
                msgstr += fragment
    if msgid is not None:
        flush()
    return entries


def compile_mo(entries):
    keys = sorted(entries)
    ids = b''
    strs = b''
    offsets = []
    for key in keys:
        value = entries[key]
        k = key.encode('utf-8')
        v = value.encode('utf-8')
        offsets.append((len(ids), len(k), len(strs), len(v)))
        ids += k + b'\x00'
        strs += v + b'\x00'

    count = len(keys)
    key_table_offset = 7 * 4
    value_table_offset = key_table_offset + count * 8
    ids_offset = value_table_offset + count * 8

    output = bytearray()
    output += struct.pack('<I', 0x950412DE)  # little-endian magic
    output += struct.pack('<I', 0)           # format revision
    output += struct.pack('<I', count)
    output += struct.pack('<I', key_table_offset)
    output += struct.pack('<I', value_table_offset)
    output += struct.pack('<I', 0)           # hash table size
    output += struct.pack('<I', 0)           # hash table offset
    for offset, length, _, _ in offsets:
        output += struct.pack('<II', length, ids_offset + offset)
    for _, _, offset, length in offsets:
        output += struct.pack('<II', length, ids_offset + len(ids) + offset)
    output += ids
    output += strs
    return bytes(output)


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: python tools/compile_po.py <path/to/file.po>')
    po = Path(sys.argv[1])
    entries = parse_po(po)
    mo = po.with_suffix('.mo')
    mo.write_bytes(compile_mo(entries))
    print(f'compiled {len(entries)} entries -> {mo}')


if __name__ == '__main__':
    main()
