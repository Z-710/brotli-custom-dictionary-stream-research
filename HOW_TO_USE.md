# How to Use the Brotli PDF Stream Research Tools

This guide explains how to set up and run the PDF stream Brotli experiment tools after downloading this repository.

The tools are intended for users who want to test whether custom Brotli dictionaries improve compression for different types of PDF streams.

## 1. What this project does

This project provides a small workflow for analysing PDF stream compression.

It can:

1. Scan a folder of PDFs.
2. Identify useful PDF stream categories.
3. Extract raw and decoded stream payloads.
4. Build custom Brotli dictionaries from extracted streams.
5. Benchmark normal Brotli compression.
6. Benchmark Brotli compression using a custom dictionary.
7. Compare the results and export CSV summaries.

The tools do not rewrite complete PDF files. They work at the PDF stream level so that each stream category can be tested separately.

## 2. Requirements

Users need:

- Python 3.10 or newer
- `pip`
- the Python packages listed in `requirements.txt`
- an external `brotli.exe` if they want to run dictionary-assisted Brotli compression

Install the Python requirements from the repository root:

```powershell
pip install -r requirements.txt
```

The main Python dependencies are:

```text
brotli
pikepdf
```

## 3. Repository layout

The repository should look like this:

```text
brotli-pdf-stream-research/
├─ README.md
├─ LICENSE
├─ requirements.txt
├─ HOW_TO_USE.md
├─ scripts/
│  ├─ pdf_stream_scanner_v3.py
│  ├─ pdf_stream_extractor_v3.py
│  ├─ build_custom_brotli_dict.py
│  ├─ brotli_csv_benchmark.py
│  ├─ compare_stream_sizes.py
│  └─ pdf_stream_experiment_gui_v3.py
├─ docs/
│  └─ workflow.md
└─ examples/
   └─ example_commands.md
```

## 4. Create a working folder

The experiment outputs should be kept in a separate working folder.

From the repository root, run:

```powershell
mkdir pdf_stream_experiment
mkdir pdf_stream_experiment\extracted
mkdir pdf_stream_experiment\dicts
mkdir pdf_stream_experiment\results
```

These folders are normally ignored by Git because they contain generated files.

## 5. Prepare input PDFs

Create a folder containing the PDF files to test.

Example:

```text
C:\BrotliPDFTest\pdfs
```

Avoid using private, confidential, or copyrighted documents unless you have permission to analyse them.

The tools can process many PDFs, but it is better to start with a small test folder first.

## 6. Scan the PDFs

The scanner checks which stream categories are present in the PDF set.

Example:

```powershell
python scripts\pdf_stream_scanner_v3.py `
  --input-dir "C:\BrotliPDFTest\pdfs" `
  --output-csv "pdf_stream_experiment\stream_inventory_v3.csv" `
  --verbose
```

This creates:

```text
pdf_stream_experiment\stream_inventory_v3.csv
```

Use this CSV to check whether the PDF set has enough data for the stream categories being tested.

## 7. Extract PDF streams

The extractor saves selected PDF stream payloads to disk.

Example for object streams:

```powershell
python scripts\pdf_stream_extractor_v3.py `
  --input-dir "C:\BrotliPDFTest\pdfs" `
  --output-dir "pdf_stream_experiment\extracted" `
  --category object_streams `
  --verbose
```

Common categories are:

```text
content_streams
object_streams
xref_streams
font_streams
icc_profiles
tagged_structures
```

The extractor creates folders like:

```text
pdf_stream_experiment\extracted\object_streams\raw
pdf_stream_experiment\extracted\object_streams\decoded
```

The `decoded` folder is normally used for Brotli benchmarking.

The extractor also creates a manifest file:

```text
pdf_stream_experiment\extracted\manifest.csv
```

This manifest is needed later when comparing results.

## 8. Build a custom dictionary

A dictionary should usually be trained separately for each stream category.

Example for object streams:

```powershell
python scripts\build_custom_brotli_dict.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-dir "pdf_stream_experiment\dicts\object_streams" `
  --sizes 65536 `
  --train-pct 70 `
  --seed 42 `
  --extensions .bin
```

This creates a dictionary such as:

```text
pdf_stream_experiment\dicts\object_streams\custom_dict_65536.bin
```

A 65536-byte dictionary is a useful default because it is large enough to capture repeated patterns but still small enough to discuss as realistic dictionary overhead.

## 9. Run vanilla Brotli benchmark

Vanilla Brotli means normal Brotli compression without a custom dictionary.

Example:

```powershell
python scripts\brotli_csv_benchmark.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-csv "pdf_stream_experiment\results\object_streams\metrics_vanilla.csv" `
  --qualities 11 `
  --trials 3 `
  --extensions .bin
```

This creates:

```text
pdf_stream_experiment\results\object_streams\metrics_vanilla.csv
```

This file is the baseline for comparison.

## 10. Link the external Brotli executable

Dictionary-assisted Brotli requires an external Brotli command-line executable.

The Python `brotli` package is enough for normal Brotli compression, but it does not expose custom-dictionary compression. That is why the benchmark script needs an external `brotli.exe` for dictionary mode.

### Option A: Use the full path to `brotli.exe`

This is the simplest and most reliable method.

Example Brotli executable path:

```text
C:\brotli\out\Release\brotli.exe
```

Check that it works:

```powershell
"C:\brotli\out\Release\brotli.exe" --help
```

Then use that path in the command template:

```powershell
--encoder-cmd-template '"C:\brotli\out\Release\brotli.exe" -f -q {quality} -D "{dict}" -o "{output}" "{input}"'
```

The placeholders are filled by the benchmark script:

| Placeholder | Meaning |
|---|---|
| `{quality}` | Brotli quality level |
| `{dict}` | custom dictionary path |
| `{output}` | temporary compressed output path |
| `{input}` | input stream file path |

### Option B: Add Brotli to PATH

Users can add the folder containing `brotli.exe` to their PATH.

For example, if `brotli.exe` is here:

```text
C:\brotli\out\Release\brotli.exe
```

Add this folder to PATH:

```text
C:\brotli\out\Release
```

For the current PowerShell session only:

```powershell
$env:Path += ";C:\brotli\out\Release"
```

Check:

```powershell
brotli --help
```

Then the command template can use `brotli` directly:

```powershell
--encoder-cmd-template 'brotli -f -q {quality} -D "{dict}" -o "{output}" "{input}"'
```

## 11. Run dictionary-assisted Brotli benchmark

Example using the full path to `brotli.exe`:

```powershell
python scripts\brotli_csv_benchmark.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-csv "pdf_stream_experiment\results\object_streams\metrics_dict_65536.csv" `
  --qualities 11 `
  --trials 3 `
  --extensions .bin `
  --dictionary "pdf_stream_experiment\dicts\object_streams\custom_dict_65536.bin" `
  --encoder-cmd-template '"C:\brotli\out\Release\brotli.exe" -f -q {quality} -D "{dict}" -o "{output}" "{input}"'
```

This creates:

```text
pdf_stream_experiment\results\object_streams\metrics_dict_65536.csv
```

If the user also has a compatible decoder command, they can add a decoder template for round-trip checking:

```powershell
--decoder-cmd-template '"C:\brotli\out\Release\brotli.exe" -d -f -D "{dict}" -o "{output}" "{input}"'
```

If no decoder template is supplied, the benchmark may still record compressed sizes, but round-trip verification may be skipped.

## 12. Compare vanilla and dictionary results

Use the comparison script to combine the manifest, vanilla benchmark CSV, and dictionary benchmark CSV.

```powershell
python scripts\compare_stream_sizes.py `
  --manifest-csv "pdf_stream_experiment\extracted\manifest.csv" `
  --base-brotli-csv "pdf_stream_experiment\results\object_streams\metrics_vanilla.csv" `
  --dict-brotli-csv "pdf_stream_experiment\results\object_streams\metrics_dict_65536.csv" `
  --output-prefix "pdf_stream_experiment\results\object_streams\compare_65536" `
  --dict-label "object_streams_dict_65536" `
  --base-quality 11 `
  --dict-quality 11
```

Expected outputs:

```text
pdf_stream_experiment\results\object_streams\compare_65536_detail.csv
pdf_stream_experiment\results\object_streams\compare_65536_summary.csv
```

The detail file gives per-stream results.

The summary file gives category-level results.

## 13. Repeat for other stream categories

To test another category, repeat the same process and replace `object_streams` with another category.

Examples:

```text
content_streams
font_streams
icc_profiles
xref_streams
```

Each category should have its own dictionary and result folder.

Example:

```text
pdf_stream_experiment\dicts\font_streams\custom_dict_65536.bin
pdf_stream_experiment\results\font_streams\metrics_vanilla.csv
pdf_stream_experiment\results\font_streams\metrics_dict_65536.csv
```

## 14. Use the GUI instead

Users can also run the graphical interface:

```powershell
python scripts\pdf_stream_experiment_gui_v3.py
```

In the GUI, set the paths for:

- Python executable
- scanner script
- extractor script
- dictionary builder script
- benchmark script
- comparison script
- Brotli executable
- input PDF folder
- working output folder

The GUI runs the same workflow as the command-line scripts.

## 15. Common issues

### `brotli` is not recognized

PowerShell cannot find the Brotli executable.

Fix:

- use the full path to `brotli.exe`, or
- add the folder containing `brotli.exe` to PATH.

### External encoder failed

Possible causes:

- the `brotli.exe` path is wrong
- the executable does not support `-D`
- the dictionary file does not exist
- the input folder is empty
- the command template quoting is incorrect

### No files found in decoded folder

The extractor may not have produced decoded streams for that category.

Check that this folder exists and contains `.bin` files:

```text
pdf_stream_experiment\extracted\object_streams\decoded
```

### No comparable rows found

This usually means the manifest and benchmark CSVs came from different extraction or benchmark runs.

Use the same `manifest.csv` from the extraction run that produced the benchmark input files.

### Round-trip verification skipped

This usually means no decoder command template was supplied.

For size-only experiments this may be acceptable, but round-trip verification is stronger.

## 16. Recommended outputs to keep

For reporting, the most useful output files are:

```text
stream_inventory_v3.csv
manifest.csv
metrics_vanilla.csv
metrics_dict_65536.csv
compare_65536_detail.csv
compare_65536_summary.csv
```

Do not commit generated experiment files to Git unless they are small and safe to share.

## 17. Quick command order

For one category, the command order is:

```text
1. scan PDFs
2. extract streams
3. build dictionary
4. benchmark vanilla Brotli
5. benchmark dictionary Brotli
6. compare results
```

That is the complete workflow needed to reproduce a stream-level Brotli dictionary experiment.
