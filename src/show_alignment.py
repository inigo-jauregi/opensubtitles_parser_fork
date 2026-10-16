"""
Resolve lines of a <split>.alignment_id.cxt file back to the OPUS sentence pair, and compare them with the corpus.

Show specific lines (1-based, as in a text editor):
    python src/show_alignment.py -l de -s test -c data/en-de --line 3661 --line 12

Check a random sample of lines and report how many agree with the corpus text:
    python src/show_alignment.py -l de -s test -c data/en-de --sample 200
"""
import os
import sys
import random
import logging
import xml.etree.ElementTree as ET
from argparse import ArgumentParser
from collections import defaultdict

from utils import parse_subtitles, build_subtitle
from extract_alignment_ids import normalise, read_lines, find_file, stream_link_groups, truncate_to


def resolve(requests, align_filename, path_to_xml, en_is_lg1):
    """
    requests: {(fromDoc, toDoc): {link_id, ...}}
    Returns {(fromDoc, toDoc, link_id): {'xtargets', 'overlap', 'en', 'tgt'}}; missing links are absent.
    """
    resolved = {}
    remaining = set(requests)
    for link_grp in stream_link_groups(align_filename):
        doc_pair = (link_grp.attrib['fromDoc'], link_grp.attrib['toDoc'])
        if doc_pair not in remaining:
            continue
        remaining.discard(doc_pair)
        links = {l.attrib['id']: l.attrib for l in link_grp if l.attrib.get('id') in requests[doc_pair]}
        try:
            lg1_subtitles = parse_subtitles(ET.parse(os.path.join(path_to_xml, doc_pair[0][:-3])).getroot())
            lg2_subtitles = parse_subtitles(ET.parse(os.path.join(path_to_xml, doc_pair[1][:-3])).getroot())
        except (FileNotFoundError, ET.ParseError) as e:
            logging.warning(f"Cannot read subtitle files of {doc_pair}: {e}")
            continue
        for link_id, attrib in links.items():
            lg1, lg2 = attrib['xtargets'].split(';')
            lg1_text = build_subtitle(lg1_subtitles, lg1.split()).strip()
            lg2_text = build_subtitle(lg2_subtitles, lg2.split()).strip()
            resolved[doc_pair + (link_id,)] = {
                'xtargets': attrib['xtargets'],
                'overlap': attrib.get('overlap', ''),
                'en': lg1_text if en_is_lg1 else lg2_text,
                'tgt': lg2_text if en_is_lg1 else lg1_text,
            }
        if not remaining:
            break
    return resolved


def main():
    parser = ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('-l', '--language', required=True, help='Target language, e.g. de')
    parser.add_argument('-s', '--split_set', required=True, help='train, dev or test')
    parser.add_argument('-c', '--corpus_dir', required=True,
                        help='Folder with <split>.en, <split>.<lang> and <split>.alignment_id.cxt')
    parser.add_argument('-p', '--path_to_opensubs', default='raw_os/OpenSubtitles',
                        help='Folder with <pair>/<pair>.xml and OpenSubtitles/xml/')
    parser.add_argument('-a', '--alignment_ids', default=None,
                        help='Alignment ID file (default: <split>.alignment_id.cxt in --corpus_dir or its context/)')
    parser.add_argument('--line', type=int, action='append', default=[],
                        help='1-based line number to show; repeat for several lines')
    parser.add_argument('--sample', type=int, default=0, help='Check this many random lines')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--truncate', action='store_true',
                        help='Drop extra lines at the end of the text files, as in extract_alignment_ids.py')
    args = parser.parse_args()

    lg1_name, lg2_name = min('en', args.language), max('en', args.language)
    pairname = f"{lg1_name}-{lg2_name}"
    align_filename = os.path.join(args.path_to_opensubs, pairname, f"{pairname}.xml")
    path_to_xml = os.path.join(args.path_to_opensubs, 'OpenSubtitles', 'xml')

    ids_path = args.alignment_ids or find_file(args.corpus_dir, f"{args.split_set}.alignment_id.cxt")
    assert ids_path, f"No {args.split_set}.alignment_id.cxt in {args.corpus_dir}; pass --alignment_ids"
    ids = read_lines(ids_path)
    src = read_lines(os.path.join(args.corpus_dir, f"{args.split_set}.en"))
    tgt = read_lines(os.path.join(args.corpus_dir, f"{args.split_set}.{args.language}"))
    truncate_to({f"{args.split_set}.en": src, f"{args.split_set}.{args.language}": tgt}, len(ids), args.truncate)

    lines = [l - 1 for l in args.line]
    for l in lines:
        assert 0 <= l < len(ids), f"Line {l + 1} out of range (1-{len(ids)})"
    if args.sample:
        lines += random.Random(args.seed).sample(range(len(ids)), min(args.sample, len(ids)))
    if not lines:
        parser.error("Pass --line and/or --sample")

    requests = defaultdict(set)
    for l in lines:
        if ids[l]:
            from_doc, to_doc, link_id = ids[l].split('\t')
            requests[(from_doc, to_doc)].add(link_id)
    resolved = resolve(requests, align_filename, path_to_xml, en_is_lg1=lg1_name == 'en')

    verbose = bool(args.line) or args.sample <= 20
    counts = defaultdict(int)
    for l in lines:
        if not ids[l]:
            status, link = 'UNMATCHED', None
        else:
            link = resolved.get(tuple(ids[l].split('\t')))
            if link is None:
                status = 'NOT FOUND'
            elif normalise(link['en']) == normalise(src[l]) and normalise(link['tgt']) == normalise(tgt[l]):
                status = 'OK'
            else:
                status = 'MISMATCH'
        counts[status] += 1
        if verbose or status != 'OK':
            print(f"--- line {l + 1}: {status}")
            print(f"  ids     : {ids[l] or '(empty)'}")
            if link:
                print(f"  link    : xtargets=\"{link['xtargets']}\" overlap=\"{link['overlap']}\"")
                print(f"  OPUS en : {link['en']}")
                print(f"  OPUS {args.language} : {link['tgt']}")
            print(f"  corpus en : {src[l]}")
            print(f"  corpus {args.language} : {tgt[l]}")

    total = len(lines)
    print(f"\n{counts['OK']}/{total} OK, {counts['MISMATCH']} mismatch, {counts['NOT FOUND']} not found, "
          f"{counts['UNMATCHED']} unmatched (empty line)")
    return 0 if counts['OK'] == total else 1


if __name__ == '__main__':
    sys.exit(main())
