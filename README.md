# opensubtitles_parser

Code to download and parse OpenSubtitles into a context-aware parallel corpus.

This is a fork of [st-vincent1/opensubtitles_parser](https://github.com/st-vincent1/opensubtitles_parser),
originally written for MTCue (ACL 2023). The fork was used to build the CTRLPost
corpus (`data/opensubtitles/v2024` in the parent repository). Compared to upstream it:

- downloads **OPUS OpenSubtitles v2024** instead of v2018;
- supports ten target languages with English as source (`ar cs de es fr hi hu pl ru tr`,
  plus `eu`);
- ships new held-out **blocklists** for every language (the original v2018 blocklists
  are kept under `blocklist/v2018/`);
- extracts **following context** (`a*` files) in addition to preceding context (`b*`);
- normalises IMDb IDs taken from v2024 paths (strips the `_<n>_<n>_<n>` suffix and
  zero-pads to 8 digits) before querying OMDb.

## Layout

```
blocklist/
  blocklist.<lang>          held-out documents for v2024 (dev + test), one XML path per line
  v2018/blocklist.<lang>    original MTCue blocklists for OpenSubtitles v2018 (reference only)
src/
  download_os_xml.sh        download OPUS v2024 XML files and sentence alignments
  extract_bitext.py         build train/dev/test bitext + context + IMDb metadata
  preprocess_opensubs.py    Moses detokenisation/normalisation and rule-based cleaning
  generate_blocklist.py     sample a new blocklist from the downloaded XML files
  utils.py                  subtitle XML parsing, IMDb ID extraction, OMDb queries
```

## Blocklists (the dev/test movie IDs)

Each `blocklist/blocklist.<lang>` lists OpenSubtitles XML files, relative to the
`OpenSubtitles/xml/` directory, e.g.

```
de/2016/6168742_280289_13_5/1958056852.xml
```

The directory name after the year encodes the IMDb ID of the title (here `tt06168742`).
The blocklist defines the data split, document by document:

| Split | Documents used |
| --- | --- |
| `train` | every aligned document **not** in the blocklist |
| `dev` | documents in the **first half** of the blocklist |
| `test` | documents in the **second half** of the blocklist |

Because the split is at the document level, no title contributes to more than one split.

| Language | `blocklist.<lang>` entries |
| --- | --- |
| ar | 60,000 |
| cs, de, es, fr, hu, pl, ru, tr | 5,000 |
| hi | 1,000 |
| eu | 200 |

The blocklists were sampled with `src/generate_blocklist.py`, which shuffles all XML
files of a language **without a fixed seed**. Re-running it produces a different split,
so use the committed blocklists to reproduce the released corpus.

## Installation

```bash
pip install pycld3 mosestokenizer tqdm requests
```

To fetch IMDb metadata you need an [OMDb](https://www.omdbapi.com/) API key (available
through the minimum Basic tier of their [Patreon](https://www.patreon.com/join/omdb)).

## Usage

Run everything from the root of this directory: the blocklist paths, the download
directory and the output directory are all relative to the working directory.

### 1. Download

```bash
bash src/download_os_xml.sh
```

Edit the `TGTS` array at the top of the script to select target languages (by default
`de fr ru`). For each language this downloads, under `raw_os/OpenSubtitles/`:

- the target-language XML files (`xml/<lang>.zip`, unzipped to `OpenSubtitles/xml/<lang>/`);
- the sentence alignment file `<pair>/<pair>.xml` (e.g. `en-de/en-de.xml`).

The script does **not** download the English XML files, which are needed by every
pair. Fetch them once with:

```bash
wget https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/en.zip -O raw_os/OpenSubtitles/xml/en.zip
```

```bash
unzip -n raw_os/OpenSubtitles/xml/en.zip -d raw_os/OpenSubtitles
```

### 2. Extract

```bash
python src/extract_bitext.py --language de --split_set test --path_to_opensubs raw_os/OpenSubtitles --apikey <OMDb API key>
```

Run once per split (`train`, `dev`, `test`) and language. `--path_to_opensubs` must
point at the directory holding the `<pair>/` alignment folders and the
`OpenSubtitles/xml/` tree, i.e. `raw_os/OpenSubtitles` when using the download script.

What the extractor does:

- keeps only alignments with an overlap score above 0.9;
- cleans each segment (Moses detokenisation and punctuation normalisation, removal of
  speaker tags, bracketed text, lyrics and other noise; see `preprocess_opensubs.py`);
- builds up to five preceding and five following segments of context, resetting the
  context when two subtitles are more than 7 seconds apart or not consecutive;
- queries OMDb for each title's metadata. Documents without metadata are dropped from
  `dev`/`test`;
- for `dev`/`test`, randomly keeps about 20% of the segments (seed 1) to diversify
  titles, and stops after about 10,000 segments.

Output is written to `data/en-<lang>/`:

```
data/en-de/
  <split>.en  <split>.de                 parallel segments
  context/
    <split>.b0.<l> … <split>.b5.<l>      b0 = current segment, b1 … b5 = preceding segments
    <split>.a0.<l> … <split>.a5.<l>      a0 = current segment, a1 … a5 = following segments
    <split>.<field>.cxt                  OMDb metadata: title, rated, year_of_release, genre,
                                         plot, country, writers, runtime, actors, language,
                                         awards, imdb_rating, imdb_votes, imdb_id, type, poster
```

All files of a split are line-aligned. Missing context is written as an empty line. The
files are opened in append mode, so delete any previous output before re-running a split.

### Regenerating a blocklist

`src/generate_blocklist.py` hard-codes the language (`lang`) and the XML root
(`pre_path`) in `main()`. Edit both before running it. It writes 5,000 randomly chosen
files to `blocklist/blocklist.<lang>`.

## License

MIT. Copyright (c) 2023 Sebastian Vincent. See `LICENSE`.
