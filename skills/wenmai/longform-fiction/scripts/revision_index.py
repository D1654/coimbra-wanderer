"""Locate paragraphs and punctuation units in prose Markdown with the stdlib.

This is a chapter locator, not a Markdown parser or a proof of human reading.
Blank lines and ATX headings separate paragraphs; headings are excluded.
Offsets count Unicode characters in UTF-8 text after an optional leading BOM
is removed. Original CR/LF characters remain present. IDs apply only together
with source_sha256. Index output must be a new file.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


HEADING = re.compile(r'^ {0,3}#{1,6}(?:[ \t]|$)')
PUNCTUATION = re.compile(r'''(?:[。！？!?；;]+|…+|\.{3,})+[”’」』）》】)\]"']*''')


class LocatorError(ValueError):
    """The source, index or declaration violates the locator contract."""


def punctuation_units(text, paragraph_id):
    units, start = [], 0
    ends = [match.end() for match in PUNCTUATION.finditer(text)]
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    for number, end in enumerate(ends, 1):
        units.append({'id': f'{paragraph_id}.u{number:06d}', 'number': number,
                      'start_char': start, 'end_char': end, 'text': text[start:end]})
        start = end
    return units


def build_index(raw):
    text = raw.decode('utf-8-sig')
    paragraphs, first, last = [], None, None

    def finish_paragraph():
        if first is None:
            return
        number = len(paragraphs) + 1
        paragraph_id = f'p{number:06d}'
        paragraph_text = text[first[0]:last[0]]
        paragraphs.append({'id': paragraph_id, 'number': number,
                           'start_line': first[1], 'end_line': last[1],
                           'start_char': first[0], 'end_char': last[0],
                           'text': paragraph_text,
                           'units': punctuation_units(paragraph_text, paragraph_id)})

    for line_number, match in enumerate(re.finditer(r'[^\r\n]*(?:\r\n|\r|\n|$)', text), 1):
        if match.start() == match.end():
            continue
        body = match.group().rstrip('\r\n')
        if not body.strip() or HEADING.match(body):
            finish_paragraph()
            first = last = None
            continue
        if first is None:
            first = (match.start(), line_number)
        last = (match.start() + len(body), line_number)
    finish_paragraph()
    if not paragraphs:
        raise LocatorError('Source has no body paragraphs.')
    return {'schema_version': 1, 'source_sha256': hashlib.sha256(raw).hexdigest(),
            'size_bytes': len(raw), 'paragraphs': paragraphs}


def write_index(source, output):
    if (source.resolve() == output.resolve()
            or output.exists() and source.samefile(output)):
        raise LocatorError('Output must not overwrite the source, including path aliases.')
    document = build_index(source.read_bytes())
    # Exclusive creation also protects unrelated files and aliases introduced
    # after the check. Regeneration uses a fresh destination.
    with output.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return {'status': 'indexed', 'source_sha256': document['source_sha256'],
            'paragraphs': len(document['paragraphs']),
            'units': sum(len(p['units']) for p in document['paragraphs'])}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LocatorError(f'Duplicate JSON field: {key}')
        result[key] = value
    return result


def reject_constant(value):
    raise LocatorError(f'Non-standard JSON number: {value}')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'),
                      object_pairs_hook=unique_object, parse_constant=reject_constant)


def validate_record(record, document):
    if (not isinstance(record, dict) or set(record) != {'source_sha256', 'ranges'}
            or record['source_sha256'] != document['source_sha256']):
        raise LocatorError('Record must have source_sha256 and ranges for these exact source bytes.')
    ranges = record['ranges']
    if not isinstance(ranges, list) or not ranges:
        raise LocatorError('Record ranges must be a nonempty list.')
    total = len(document['paragraphs'])
    for item in ranges:
        if (not isinstance(item, dict) or set(item) != {'start', 'end', 'note'}
                or type(item['start']) is not int or type(item['end']) is not int
                or not isinstance(item['note'], str) or not item['note'].strip()
                or not 1 <= item['start'] <= item['end'] <= total):
            raise LocatorError('Each range needs valid integer start/end and a nonempty note.')
    next_paragraph = 1
    for item in sorted(ranges, key=lambda value: value['start']):
        if item['start'] < next_paragraph:
            raise LocatorError(f'Overlapping declaration at paragraph {item["start"]}.')
        if item['start'] > next_paragraph:
            raise LocatorError(f'Missing declaration at paragraph {next_paragraph}.')
        next_paragraph = item['end'] + 1
    if next_paragraph != total + 1:
        raise LocatorError(f'Missing declaration at paragraph {next_paragraph}.')
    return len(ranges)


def verify_index(source, index, record_path=None):
    document = build_index(source.read_bytes())
    supplied = read_json(index)
    # JSON serialization preserves the distinctions that Python equality loses
    # (True == 1 == 1.0). Compare the entire deterministic index, including text,
    # field sets, numbering, hashes and every span; never trust supplied spans.
    if json.dumps(supplied, sort_keys=True) != json.dumps(document, sort_keys=True):
        raise LocatorError('Index differs from source bytes or locator schema; regenerate it.')
    report = {'status': 'integrity_only', 'source_sha256': document['source_sha256'],
              'paragraphs': len(document['paragraphs']),
              'units': sum(len(p['units']) for p in document['paragraphs'])}
    if record_path is not None:
        range_count = validate_record(read_json(record_path), document)
        report.update(status='declared_coverage_complete', declaration_only=True,
                      declared_paragraphs=len(document['paragraphs']), range_count=range_count)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    index = commands.add_parser('index', help='Write a new, version-bound paragraph index.')
    index.add_argument('--source', required=True, type=Path)
    index.add_argument('--out', required=True, type=Path)
    verify = commands.add_parser('verify', help='Check integrity and optional declared ranges.')
    verify.add_argument('--source', required=True, type=Path)
    verify.add_argument('--index', required=True, type=Path)
    verify.add_argument('--record', type=Path)
    args = parser.parse_args()
    try:
        result = (write_index(args.source, args.out) if args.command == 'index'
                  else verify_index(args.source, args.index, args.record))
    except (OSError, ValueError, UnicodeError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
