"""
Recover, for every line of an already-extracted corpus split, the OPUS alignment it came from.

The released corpus only ships IMDb IDs and scene summaries, so users need a pointer to the exact
aligned subtitle pair at the centre of each context window. This script streams the OPUS alignment
file, rebuilds every aligned pair of the titles present in the split, and matches them against the
corpus text. It writes one line per corpus line:

    <fromDoc>\t<toDoc>\t<link id>

e.g. `de/2016/6168742_280289_13_5/1958056852.xml.gz\ten/2016/6168742/1957180084.xml.gz\tSL412`.
fromDoc/toDoc/link id are exactly the attributes of the <linkGrp>/<link> in `<pair>.xml`, so they
identify the pair independently of this parser. Unmatched lines are written as empty lines.

Matching is done on normalised text (lowercase, letters/digits only, bracketed text and speaker tags
removed), so it does not depend on the exact cleaning code that produced the corpus. Passes:
  1. same title, same source/target segment, same previous and next source segment
  2. same title, same source/target segment
  3. (optional, --cross_title) same source/target segment and context, any title; for lines whose
     IMDb ID differs from the one in the OpenSubtitles path (e.g. OMDb redirects)
Each alignment link is assigned to at most one corpus line.
"""
import os
import re
import sys
import logging
import xml.etree.ElementTree as ET
from argparse import ArgumentParser
from collections import defaultdict, deque, Counter

from utils import parse_subtitles, build_subtitle, get_imdb_id

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

# Same gap the extractor uses to reset the context window (ms)
MAX_GAP = 7000


def normalise(text: str) -> str:
    text = re.sub(r" *[\(\[\{].*?[\)\]\}] *", "", text)
    text = re.sub(r"^ *-* *", "", text.strip())
    # Speaker tags; raw OPUS tokens give "JOHN : ...", the cleaned corpus "JOHN: ..."
    text = re.sub(r"^[A-Z]+ ?: ", "", text)
    return ''.join(re.findall(r'\w', text.lower()))


def title_key(imdb_id: str):
    """IMDb ID as an integer, so 'tt0023547' (OMDb) and 'tt00023547' (zero-padded path) compare equal."""
    digits = re.sub(r'\D', '', imdb_id)
    return int(digits) if digits else None


def read_lines(path: str):
    """Read a file split on '\\n' only, so the count matches `wc -l`."""
    with open(path, encoding='utf-8', newline='') as f:
        lines = f.read().split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return [l.rstrip('\r') for l in lines]


def find_file(corpus_dir: str, name: str):
    for candidate in (os.path.join(corpus_dir, name), os.path.join(corpus_dir, 'context', name)):
        if os.path.exists(candidate):
            return candidate
    return None


def truncate_to(files: dict, n: int, truncate: bool):
    """
    Check every file in {name: lines} has n lines. With truncate, drop extra lines at the end (e.g. left behind
    when an extraction job was killed between writing the text and the metadata) and log them.
    """
    for name, lines in files.items():
        if len(lines) == n:
            continue
        if len(lines) < n or not truncate:
            sys.exit(f"Line counts differ: {name} has {len(lines)} lines, {n} expected from imdb_id.cxt. "
                     + ("Use --truncate if the extra lines are at the end of the file." if len(lines) > n else ""))
        logging.warning(f"--truncate: dropping the last {len(lines) - n} line(s) of {name}:")
        for i, line in enumerate(lines[n:], n + 1):
            logging.warning(f"    line {i}: {line!r}")
        del lines[n:]


def load_corpus(corpus_dir: str, split_set: str, tgt_language: str, truncate: bool = False):
    src = read_lines(os.path.join(corpus_dir, f"{split_set}.en"))
    tgt = read_lines(os.path.join(corpus_dir, f"{split_set}.{tgt_language}"))
    imdb_path = find_file(corpus_dir, f"{split_set}.imdb_id.cxt")
    assert imdb_path, f"No {split_set}.imdb_id.cxt in {corpus_dir} or its context/ folder"
    imdb = read_lines(imdb_path)
    truncate_to({f"{split_set}.en": src, f"{split_set}.{tgt_language}": tgt}, len(imdb), truncate)

    # Neighbouring source segments, used to tell apart repeated lines ("Yes.", "Thank you.")
    before, after = [''] * len(src), [''] * len(src)
    b1, a1 = find_file(corpus_dir, f"{split_set}.b1.en"), find_file(corpus_dir, f"{split_set}.a1.en")
    if b1 and a1:
        before, after = read_lines(b1), read_lines(a1)
        truncate_to({b1: before, a1: after}, len(imdb), truncate)
    else:
        logging.warning("No b1/a1 context files found: pass 1 will match on text only")

    return [title_key(i) for i in imdb], [normalise(s) for s in src], [normalise(t) for t in tgt], \
        [normalise(b) for b in before], [normalise(a) for a in after]


def doc_candidates(link_grp, path_to_xml: str, en_is_lg1: bool):
    """
    Yield (link_id, en_norm, tgt_norm, prev_en_norm, next_en_norm) for every 1:n / n:1 / n:m link of a document.
    Context follows the extractor: neighbours only count if consecutive and at most MAX_GAP apart.
    """
    lg1_file = os.path.join(path_to_xml, link_grp.attrib['fromDoc'][:-3])
    lg2_file = os.path.join(path_to_xml, link_grp.attrib['toDoc'][:-3])
    try:
        lg1_subtitles = parse_subtitles(ET.parse(lg1_file).getroot())
        lg2_subtitles = parse_subtitles(ET.parse(lg2_file).getroot())
    except (FileNotFoundError, ET.ParseError) as e:
        logging.warning(f"Cannot read {lg1_file} / {lg2_file}: {e}")
        return

    segments = []
    for link in link_grp:
        try:
            lg1, lg2 = link.attrib['xtargets'].split(';')
            lg1, lg2 = lg1.split(), lg2.split()
            if not lg1 or not lg2:
                segments.append(None)
                continue
            lg1_text = normalise(build_subtitle(lg1_subtitles, lg1))
            lg2_text = normalise(build_subtitle(lg2_subtitles, lg2))
            segments.append({
                'link_id': link.attrib['id'],
                'num': int(link.attrib['id'][2:]),
                'en': lg1_text if en_is_lg1 else lg2_text,
                'tgt': lg2_text if en_is_lg1 else lg1_text,
                'start': lg1_subtitles[lg1[0]][1],
                'end': lg1_subtitles[lg1[-1]][2],
            })
        except (KeyError, ValueError, IndexError):
            segments.append(None)

    def contiguous(a, b):
        return a is not None and b is not None and b['num'] == a['num'] + 1 and b['start'] - a['end'] <= MAX_GAP

    for i, seg in enumerate(segments):
        if seg is None:
            continue
        prev = segments[i - 1] if i > 0 else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        yield (seg['link_id'], seg['en'], seg['tgt'],
               prev['en'] if contiguous(prev, seg) else '',
               nxt['en'] if contiguous(seg, nxt) else '')


def stream_link_groups(align_filename: str):
    for _, elem in ET.iterparse(align_filename, events=('end',)):
        if elem.tag == 'linkGrp':
            yield elem
            elem.clear()


def run_pass(name, align_filename, path_to_xml, en_is_lg1, tgt_language, key_fn, cand_key_fn,
             pending_titles, assignments, used_links):
    """Generic streaming pass: index unassigned lines by key_fn, assign candidates in file order."""
    index = defaultdict(deque)
    for i, assigned in enumerate(assignments):
        if assigned is None:
            key = key_fn(i)
            if key is not None:
                index[key].append(i)
    if not index:
        return 0
    logging.info(f"Pass '{name}': {sum(len(v) for v in index.values())} lines to match")

    matched, n_docs, n_read = 0, 0, 0
    for n_docs, link_grp in enumerate(stream_link_groups(align_filename), 1):
        if n_docs % 10000 == 0:
            logging.info(f"Pass '{name}': {n_docs} documents scanned, {n_read} read, {matched} lines matched")
        from_doc, to_doc = link_grp.attrib['fromDoc'], link_grp.attrib['toDoc']
        lg1_file, lg2_file = from_doc[:-3], to_doc[:-3]
        titles = {title_key(get_imdb_id(lg1_file)), title_key(get_imdb_id(lg2_file))} - {None}
        if pending_titles is not None and not titles & pending_titles:
            continue
        n_read += 1
        for link_id, en, tgt, prev_en, next_en in doc_candidates(link_grp, path_to_xml, en_is_lg1):
            if (from_doc, link_id) in used_links:
                continue
            for key in cand_key_fn(titles, en, tgt, prev_en, next_en):
                queue = index.get(key)
                while queue and assignments[queue[0]] is not None:
                    queue.popleft()
                if queue:
                    line = queue.popleft()
                    assignments[line] = (from_doc, to_doc, link_id)
                    used_links.add((from_doc, link_id))
                    matched += 1
                    break
    logging.info(f"Pass '{name}': {n_docs} documents in {align_filename}, {n_read} belong to titles "
                 f"in the split, matched {matched} lines")
    if n_docs == 0:
        logging.error(f"No <linkGrp> found in {align_filename}")
    elif n_read == 0 and pending_titles:
        logging.error(f"No document matches the split's IMDb IDs, e.g. corpus {sorted(pending_titles)[:3]} "
                      f"vs alignment file {sorted(titles)}. Check --language and --path_to_opensubs.")
    return matched


def main():
    parser = ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('-l', '--language', required=True, help='Target language, e.g. de')
    parser.add_argument('-s', '--split_set', required=True, help='train, dev or test')
    parser.add_argument('-c', '--corpus_dir', required=True,
                        help='Folder with <split>.en, <split>.<lang>, <split>.imdb_id.cxt and context/ '
                             '(e.g. data/opensubtitles/v2024/en-de)')
    parser.add_argument('-p', '--path_to_opensubs', default='raw_os/OpenSubtitles',
                        help='Folder with <pair>/<pair>.xml and OpenSubtitles/xml/')
    parser.add_argument('-o', '--output', default=None,
                        help='Output file (default: <split>.alignment_id.cxt next to <split>.imdb_id.cxt)')
    parser.add_argument('--truncate', action='store_true',
                        help='If the text or context files have extra lines at the end compared to '
                             '<split>.imdb_id.cxt, drop them (they are logged) instead of stopping.')
    parser.add_argument('--cross_title', action='store_true',
                        help='Extra pass ignoring the IMDb ID. Reads every document in the alignment file.')
    args = parser.parse_args()

    lg1_name, lg2_name = min('en', args.language), max('en', args.language)
    pairname = f"{lg1_name}-{lg2_name}"
    align_filename = os.path.join(args.path_to_opensubs, pairname, f"{pairname}.xml")
    path_to_xml = os.path.join(args.path_to_opensubs, 'OpenSubtitles', 'xml')
    en_is_lg1 = lg1_name == 'en'

    imdb, src, tgt, before, after = load_corpus(args.corpus_dir, args.split_set, args.language, args.truncate)
    n = len(src)
    assignments = [None] * n
    used_links = set()
    logging.info(f"Loaded {n} lines, {len(set(imdb))} titles from {args.corpus_dir}")
    logging.info(f"Alignment file: {align_filename}, subtitle XML: {path_to_xml}")
    if not os.path.exists(align_filename):
        logging.error(f"{align_filename} not found. Check --path_to_opensubs.")
        return 2

    def with_titles(fn):
        return lambda titles, *seg: [fn(t, *seg) for t in titles]

    stats = Counter()
    stats['context'] = run_pass(
        'title + text + context', align_filename, path_to_xml, en_is_lg1, args.language,
        lambda i: (imdb[i], src[i], tgt[i], before[i], after[i]),
        with_titles(lambda t, en, tg, p, nx: (t, en, tg, p, nx)),
        set(imdb) - {None}, assignments, used_links)

    pending = {imdb[i] for i in range(n) if assignments[i] is None} - {None}
    stats['text'] = run_pass(
        'title + text', align_filename, path_to_xml, en_is_lg1, args.language,
        lambda i: (imdb[i], src[i], tgt[i]),
        with_titles(lambda t, en, tg, p, nx: (t, en, tg)),
        pending, assignments, used_links)

    if args.cross_title:
        stats['cross_title'] = run_pass(
            'text + context, any title', align_filename, path_to_xml, en_is_lg1, args.language,
            lambda i: (src[i], tgt[i], before[i], after[i]) if before[i] or after[i] else None,
            lambda titles, en, tg, p, nx: [(en, tg, p, nx)],
            None, assignments, used_links)

    output = args.output
    if output is None:
        imdb_path = find_file(args.corpus_dir, f"{args.split_set}.imdb_id.cxt")
        output = os.path.join(os.path.dirname(imdb_path), f"{args.split_set}.alignment_id.cxt")
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        for a in assignments:
            f.write('\t'.join(a) + '\n' if a else '\n')

    unmatched = sum(a is None for a in assignments)
    logging.info(f"Wrote {output}: {n} lines, matched by pass {dict(stats)}, unmatched {unmatched} "
                 f"({100 * unmatched / max(n, 1):.2f}%)")
    return 0 if unmatched == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
