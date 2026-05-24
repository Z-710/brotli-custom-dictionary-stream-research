# Brotli PDF Stream Research Tools

This repository contains Python tools used for a capstone research project investigating whether custom Brotli dictionaries can improve compression performance for different PDF stream categories.

The project focuses on stream-level analysis rather than whole-document PDF rewriting. The workflow scans PDF files, extracts selected stream types, trains category-specific dictionaries, benchmarks vanilla Brotli and dictionary-assisted Brotli, and compares the resulting compressed sizes.

## Supported PDF stream categories

The tools support the following categories:

- `content_streams`
- `object_streams`
- `xref_streams`
- `font_streams`
- `icc_profiles`
- `tagged_structures`

The main categories used in the final research workflow were content streams, object streams, cross-reference streams, font streams, and ICC profile streams.

## Repository structure

```text
brotli-pdf-stream-research/
│
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ .gitignore
│
├─ scripts/
│  ├─ pdf_stream_scanner_v3.py
│  ├─ pdf_stream_extractor_v3.py
│  ├─ build_custom_brotli_dict.py
│  ├─ brotli_csv_benchmark.py
│  ├─ compare_stream_sizes.py
│  └─ pdf_stream_experiment_gui_v3.py
│
├─ docs/
│  └─ workflow.md
│
└─ examples/
   └─ example_commands.md
```

## Requirements

Python dependencies are listed in `requirements.txt`.

Install them with:

```bash
pip install -r requirements.txt
```

The main Python packages are:

- `pikepdf`, used to inspect and extract PDF streams
- `brotli`, used for vanilla Brotli benchmarking

Dictionary-assisted Brotli compression also requires an external Brotli executable that supports custom dictionaries using the `-D` option. The common Python `brotli` package does not expose custom-dictionary compression, so dictionary mode is run through a command template.

## Basic workflow

The workflow has six main stages:

1. Scan a folder of PDFs and generate a stream inventory CSV.
2. Extract raw and decoded stream payloads by category.
3. Build a category-specific Brotli dictionary from decoded streams.
4. Benchmark vanilla Brotli on decoded streams.
5. Benchmark Brotli with a trained dictionary.
6. Compare the original stored stream size, vanilla Brotli size, and dictionary-assisted Brotli size.

See [`docs/workflow.md`](docs/workflow.md) for the full workflow explanation.

See [`examples/example_commands.md`](examples/example_commands.md) for Windows PowerShell command examples.

## GUI option

The repository also includes a Tkinter GUI wrapper:

```bash
python scripts/pdf_stream_experiment_gui_v3.py
```

The GUI wraps the same pipeline steps as the command-line tools. It is mainly intended to make long-running experiments easier to run and monitor.

## Generated outputs

The tools generate files such as:

- stream inventory CSVs
- extracted raw and decoded stream payloads
- trained dictionary `.bin` files
- Brotli benchmark CSVs
- comparison summary CSVs
- optional compressed `.br` files

