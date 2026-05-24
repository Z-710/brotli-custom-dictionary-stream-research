# Example Commands

These examples assume a Windows PowerShell environment.

Adjust paths to match your own project folder.

## 1. Create the working folders

```powershell
mkdir pdf_stream_experiment
mkdir pdf_stream_experiment\extracted
mkdir pdf_stream_experiment\dicts
mkdir pdf_stream_experiment\results
```

## 2. Scan PDFs

```powershell
python scripts\pdf_stream_scanner_v3.py `
  --input-dir "C:\Code\BrotliProject\pdfs" `
  --output-csv "pdf_stream_experiment\stream_inventory_v3.csv" `
  --verbose
```

## 3. Extract streams

Extract the main stream categories:

```powershell
python scripts\pdf_stream_extractor_v3.py `
  --input-dir "C:\Code\BrotliProject\pdfs" `
  --output-dir "pdf_stream_experiment\extracted" `
  --category content_streams `
  --category object_streams `
  --category xref_streams `
  --category font_streams `
  --category icc_profiles `
  --verbose
```

If your extractor version expects one category at a time, run the command once per category instead:

```powershell
python scripts\pdf_stream_extractor_v3.py `
  --input-dir "C:\Code\BrotliProject\pdfs" `
  --output-dir "pdf_stream_experiment\extracted" `
  --category object_streams `
  --verbose
```

## 4. Build a 64 KiB dictionary for object streams

```powershell
python scripts\build_custom_brotli_dict.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-dir "pdf_stream_experiment\dicts\object_streams" `
  --sizes 65536 `
  --train-pct 70 `
  --seed 42 `
  --extensions .bin
```

## 5. Benchmark vanilla Brotli for object streams

```powershell
python scripts\brotli_csv_benchmark.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-csv "pdf_stream_experiment\results\object_streams\metrics_vanilla.csv" `
  --qualities 11 `
  --trials 3 `
  --extensions .bin
```

## 6. Benchmark dictionary-assisted Brotli for object streams

Update the Brotli executable path to match your local build.

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

If you also have a decoder command available, include it for round-trip verification:

```powershell
python scripts\brotli_csv_benchmark.py `
  --input-dir "pdf_stream_experiment\extracted\object_streams\decoded" `
  --output-csv "pdf_stream_experiment\results\object_streams\metrics_dict_65536.csv" `
  --qualities 11 `
  --trials 3 `
  --extensions .bin `
  --dictionary "pdf_stream_experiment\dicts\object_streams\custom_dict_65536.bin" `
  --encoder-cmd-template '"C:\brotli\out\Release\brotli.exe" -f -q {quality} -D "{dict}" -o "{output}" "{input}"' `
  --decoder-cmd-template '"C:\brotli\out\Release\brotli.exe" -d -f -D "{dict}" -o "{output}" "{input}"'
```

## 7. Compare vanilla Brotli and dictionary Brotli

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

## 8. Repeat for other categories

Change `object_streams` to another category, for example:

```text
content_streams
font_streams
icc_profiles
xref_streams
```

Example for ICC profiles:

```powershell
python scripts\build_custom_brotli_dict.py `
  --input-dir "pdf_stream_experiment\extracted\icc_profiles\decoded" `
  --output-dir "pdf_stream_experiment\dicts\icc_profiles" `
  --sizes 65536 `
  --train-pct 70 `
  --seed 42 `
  --extensions .bin
```

## 9. Run the GUI

```powershell
python scripts\pdf_stream_experiment_gui_v3.py
```

Use the GUI if you prefer selecting paths and running each stage with buttons.

## 10. Git commands for upload

From the repository root:

```powershell
git init
git add README.md LICENSE requirements.txt .gitignore scripts docs examples
git commit -m "Initial upload of Brotli PDF stream research tools"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/brotli-pdf-stream-research.git
git push -u origin main
```
