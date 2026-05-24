# Workflow

This document explains the intended workflow for the Brotli PDF stream research tools.

The scripts are designed to be run in stages. Each stage produces files that are used by the next stage.

## 1. Scan PDFs

Script:

```text
pdf_stream_scanner_v3.py
```

Purpose:

The scanner reads a folder of PDF files and records which stream categories are present. It produces a CSV inventory containing stream counts and byte totals for each PDF.

Typical output:

```text
pdf_stream_experiment/stream_inventory_v3.csv
```

This step helps identify whether the PDF set contains enough relevant stream data to test. For example, a folder with very few object streams or cross-reference streams may not be useful for those categories.

## 2. Extract PDF streams

Script:

```text
pdf_stream_extractor_v3.py
```

Purpose:

The extractor saves selected stream categories to disk. For each extracted stream, it can save both:

- the raw stored stream bytes
- the decoded stream bytes

The decoded version is normally used for Brotli benchmarking because it represents the stream content before recompression.

Typical output structure:

```text
pdf_stream_experiment/
└─ extracted/
   ├─ manifest.csv
   ├─ content_streams/
   │  ├─ raw/
   │  └─ decoded/
   ├─ object_streams/
   │  ├─ raw/
   │  └─ decoded/
   ├─ xref_streams/
   │  ├─ raw/
   │  └─ decoded/
   ├─ font_streams/
   │  ├─ raw/
   │  └─ decoded/
   └─ icc_profiles/
      ├─ raw/
      └─ decoded/
```

The `manifest.csv` file is important because later comparison steps use it to connect extracted streams back to their original PDF, category, item name, and original stored stream size.

## 3. Build custom Brotli dictionaries

Script:

```text
build_custom_brotli_dict.py
```

Purpose:

The dictionary builder trains heuristic dictionary files from a folder of decoded stream payloads. Dictionaries are usually trained separately for each stream category.

Example dictionary sizes:

```text
16384
32768
65536
131072
```

The final project mainly used a 65536-byte dictionary because it is large enough to capture useful repeated patterns while still being small enough to discuss as a realistic per-category dictionary size.

Typical output:

```text
pdf_stream_experiment/dicts/object_streams/custom_dict_65536.bin
```

The script can also write a training manifest showing which files were used for training and evaluation.

## 4. Benchmark vanilla Brotli

Script:

```text
brotli_csv_benchmark.py
```

Purpose:

This benchmark compresses each decoded stream using normal Brotli without a custom dictionary. It records output size, compression ratio, timing, round-trip verification, and other metrics.

Typical output:

```text
pdf_stream_experiment/results/object_streams/metrics_vanilla.csv
```

This is the baseline used to judge whether the custom dictionary improves on standard Brotli.

## 5. Benchmark dictionary-assisted Brotli

Script:

```text
brotli_csv_benchmark.py
```

Purpose:

The same benchmark script is used again, but this time with:

- a custom dictionary path
- an external Brotli encoder command template
- optionally, an external decoder command template for round-trip verification

Typical output:

```text
pdf_stream_experiment/results/object_streams/metrics_dict_65536.csv
```

The Python `brotli` package is used for vanilla Brotli, but dictionary mode requires an external Brotli executable because common Python bindings do not expose custom-dictionary compression.

## 6. Compare stream sizes

Script:

```text
compare_stream_sizes.py
```

Purpose:

The comparison script combines:

- the extraction manifest
- the vanilla Brotli benchmark CSV
- the dictionary-assisted Brotli benchmark CSV

It then calculates percentage changes between:

- original stored stream size and vanilla Brotli
- original stored stream size and dictionary Brotli
- vanilla Brotli and dictionary Brotli

Typical outputs:

```text
pdf_stream_experiment/results/object_streams/compare_65536_detail.csv
pdf_stream_experiment/results/object_streams/compare_65536_summary.csv
```

The detail CSV gives per-stream results. The summary CSV gives category-level averages.

## Recommended experiment order

For each stream category, use this order:

```text
scan PDFs
extract streams
build dictionary for category
benchmark vanilla Brotli for category
benchmark dictionary Brotli for category
compare results
```

For example, run the full process for `object_streams`, then repeat for `content_streams`, `font_streams`, `icc_profiles`, and `xref_streams`.

## Suggested categories for reporting

The most useful categories to report are:

- object streams, because they often contain repeated PDF object syntax
- ICC profiles, because they showed strong dictionary suitability in later testing
- font streams, because they are important to PDF file size but can vary heavily
- content streams, because they are common but may show weaker dictionary gains
- cross-reference streams, with caution, because normal corpora may not contain many of them

Synthetic or supplementary cross-reference stream tests should be labelled clearly as supplementary stress tests, not as ordinary corpus results.

## Files not intended for Git

Do not commit generated experiment data unless there is a specific reason. In most cases, exclude:

```text
pdf_stream_experiment/
*.bin
*.br
*.csv
*.pdf
*.zip
*.icc
```

The repository should contain the source scripts and documentation, while experiment outputs should be stored separately.
