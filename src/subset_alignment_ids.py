"""
Copy alignment IDs from a full split to a subset sampled from it (e.g. v2024_train_dev_subset_with_scenes).

Each subset line is looked up in the full split by its text and context, and gets the alignment ID of that line from
the full split's <split>.alignment_id.cxt. No OPUS files are needed.

    python src/subset_alignment_ids.py -l de -s train \\
        --full_dir data/opensubtitles/v2024/en-de --subset_dir data/opensubtitles/v2024_train_dev_subset_with_scenes/en-de

Lookup keys, from strictest to loosest:
  1. IMDb ID, source and target segment, and every b*/a* source context file present in both folders
  2. IMDb ID, source and target segment
  3. source and target segment
Each full-split line is used at most once. Lines found nowhere are written as empty lines.
"""
import os
import sys
import logging
from argparse import ArgumentParser
from collections import defaultdict, Counter
from contextlib import ExitStack
from itertools import islice

from extract_alignment_ids import read_lines, find_file, truncate_to, title_key

logging.basicConfig(
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')

CONTEXT_FILES = [f"b{i}" for i in range(1, 6)] + [f"a{i}" for i in range(1, 6)]


def split_files(corpus_dir, split_set, language):
    """Paths of the files used as lookup key, in a fixed order: en, tgt, imdb_id, then context."""
    files = {'en': os.path.join(corpus_dir, f"{split_set}.en"),
             language: os.path.join(corpus_dir, f"{split_set}.{language}"),
             'imdb_id': find_file(corpus_dir, f"{split_set}.imdb_id.cxt")}
    for c in CONTEXT_FILES:
        files[c] = find_file(corpus_dir, f"{split_set}.{c}.en")
    return files


def count_lines(path: str) -> int:
    """Number of '\\n'-separated lines, counted the same way as read_lines."""
    n, last = 0, b'\n'
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 24), b''):
            n += chunk.count(b'\n')
            last = chunk[-1:]
    return n + (last != b'\n')


def main():
    parser = ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('-l', '--language', required=True, help='Target language, e.g. de')
    parser.add_argument('-s', '--split_set', required=True, help='train or dev')
    parser.add_argument('--full_dir', required=True,
                        help='Full split folder with <split>.en, <split>.<lang>, <split>.imdb_id.cxt and '
                             '<split>.alignment_id.cxt (e.g. data/opensubtitles/v2024/en-de)')
    parser.add_argument('--subset_dir', required=True,
                        help='Subset folder with <split>.en, <split>.<lang> and <split>.imdb_id.cxt')
    parser.add_argument('-o', '--output', default=None,
                        help='Output file (default: context/<split>.alignment_id.cxt in --subset_dir)')
    parser.add_argument('--truncate', action='store_true',
                        help='Ignore extra lines at the end of the text files (subset and full split), '
                             'as in extract_alignment_ids.py')
    args = parser.parse_args()

    full_files = split_files(args.full_dir, args.split_set, args.language)
    subset_files = split_files(args.subset_dir, args.split_set, args.language)
    for name in ('en', args.language, 'imdb_id'):
        for files, where in ((full_files, args.full_dir), (subset_files, args.subset_dir)):
            assert files[name] and os.path.exists(files[name]), f"Missing {args.split_set} {name} file in {where}"
    ids_path = find_file(args.full_dir, f"{args.split_set}.alignment_id.cxt")
    assert ids_path, f"No {args.split_set}.alignment_id.cxt in {args.full_dir} or its context/ folder"

    # Full split: every file must have as many lines as the alignment IDs. Text files may have extra trailing lines
    # (--truncate); context files with a different count are only left out of the lookup.
    n_full = count_lines(ids_path)
    for name in ('en', args.language, 'imdb_id'):
        count = count_lines(full_files[name])
        if count != n_full and not (args.truncate and count > n_full and name != 'imdb_id'):
            sys.exit(f"{full_files[name]} has {count} lines but {ids_path} has {n_full}"
                     + (": use --truncate if the extra lines are at the end." if count > n_full else "."))
        if count != n_full:
            logging.warning(f"--truncate: ignoring the last {count - n_full} line(s) of {full_files[name]}")
    context = []
    for c in CONTEXT_FILES:
        if not (full_files[c] and subset_files[c]):
            continue
        count = count_lines(full_files[c])
        if count == n_full:
            context.append(c)
        else:
            logging.warning(f"Not using {c} in the lookup: {full_files[c]} has {count} lines, {n_full} expected")
    logging.info(f"Context files used in the lookup: {context or 'none'}")

    # Subset: small, load it all
    columns = ['en', args.language, 'imdb_id'] + context
    subset = {c: read_lines(subset_files[c]) for c in columns}
    n = len(subset['imdb_id'])
    truncate_to({subset_files[c]: subset[c] for c in columns}, n, args.truncate)
    subset['imdb_id'] = [title_key(i) for i in subset['imdb_id']]
    rows = list(zip(*(subset[c] for c in columns)))
    logging.info(f"Loaded {n} subset lines from {args.subset_dir}")

    def keys(row):
        en, tgt, imdb = row[:3]
        return [row, (imdb, en, tgt), (en, tgt)]

    # Full split: stream it, keeping only lines whose (en, tgt) occur in the subset.
    # newline='\n' splits on '\n' only, so a stray '\r' inside a subtitle cannot shift the lines.
    wanted = {(r[0], r[1]) for r in rows}
    candidates = [defaultdict(list) for _ in range(3)]
    with ExitStack() as stack:
        streams = [stack.enter_context(open(full_files[c], encoding='utf-8', newline='\n')) for c in columns]
        ids_stream = stack.enter_context(open(ids_path, encoding='utf-8', newline='\n'))
        for i, (values, ids_line) in enumerate(islice(zip(zip(*streams), ids_stream), n_full)):
            row = tuple(v.rstrip('\n').rstrip('\r') for v in values)
            if (row[0], row[1]) not in wanted:
                continue
            row = row[:2] + (title_key(row[2]),) + row[3:]
            for level, key in enumerate(keys(row)):
                candidates[level][key].append((i, ids_line.rstrip('\n').rstrip('\r')))
    logging.info(f"Read {n_full} full-split lines from {args.full_dir}")

    used = set()
    output_ids, stats = [], Counter()
    for row in rows:
        found = None
        for level, key in enumerate(keys(row)):
            free = [c for c in candidates[level].get(key, []) if c[0] not in used]
            if free:
                # Prefer a full-split line that has an alignment ID
                found = next((c for c in free if c[1]), free[0])
                stats[['context', 'title + text', 'text'][level]] += 1
                if len({c[1] for c in free}) > 1:
                    stats['ambiguous'] += 1
                break
        if found is None:
            stats['not in full split'] += 1
            output_ids.append('')
        else:
            used.add(found[0])
            if not found[1]:
                stats['no alignment ID in full split'] += 1
            output_ids.append(found[1])

    output = args.output or os.path.join(args.subset_dir, 'context', f"{args.split_set}.alignment_id.cxt")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        for a in output_ids:
            f.write(a + '\n')

    empty = sum(not a for a in output_ids)
    logging.info(f"Wrote {output}: {n} lines, {dict(stats)}, empty {empty} ({100 * empty / max(n, 1):.2f}%)")
    return 0 if stats['not in full split'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
