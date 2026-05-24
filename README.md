# Brotli PDF Stream Research Tools

This repository contains Python tools used for a capstone research project investigating whether custom Brotli dictionaries can improve compression performance for different PDF stream categories.

The project focuses on stream-level analysis rather than whole-document PDF rewriting. The workflow scans PDF files, extracts selected stream types, trains category-specific dictionaries, benchmarks vanilla Brotli and dictionary-assisted Brotli, and compares the resulting compressed sizes.

## Project purpose

Brotli is a modern lossless compression format that combines LZ77-style matching, Huffman coding, and dictionary-based matching. Standard Brotli already performs well on many text-like inputs, but its built-in dictionary is mainly designed around web content. This project investigates whether PDF-specific stream data contains repeated patterns that can be captured by custom dictionaries.

The research question behind these tools is:

> How much additional PDF stream size reduction can be achieved by training PDF-specific Brotli dictionaries for different stream types, compared with vanilla Brotli?

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

These generated files are excluded from Git by `.gitignore` because they can be large, machine-specific, or derived from copyrighted PDF inputs.

## Notes on data and reproducibility

The PDF corpus used for experiments should not be committed unless all files are small, public, and safe to redistribute. For normal use, keep input PDFs and generated experiment outputs outside the repository or inside the ignored `pdf_stream_experiment/` folder.

To reproduce an experiment, record:

- the PDF corpus used
- stream categories tested
- Brotli quality level
- dictionary size
- train/evaluation split
- number of trials
- command templates used for external Brotli dictionary compression

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.
