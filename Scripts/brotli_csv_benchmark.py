#!/usr/bin/env python3
"""
Run Brotli compression experiments over a corpus and export per-file results to CSV.

Baseline mode uses the Python `brotli` package directly.
Dictionary mode is supported through a user-supplied external encoder command
because the common Python Brotli binding does not expose custom-dictionary
compression.

New in this version:
- Optional persistent saving of compressed .br files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import brotli


@dataclass
class TrialResult:
    timestamp_utc: str
    trial: int
    file_path: str
    file_name: str
    relative_path: str
    file_ext: str
    original_size: int
    quality: int
    lgwin: int
    mode: int
    backend: str
    dictionary_path: str
    dictionary_size: int
    compressed_size: int
    compression_ratio: float
    savings_bytes: int
    savings_pct: float
    compress_ms: float
    decompress_ms: float
    roundtrip_ok: bool
    sha256_original: str
    notes: str
    compressed_output_path: str


@dataclass
class SummaryRow:
    scope: str
    file_count: int
    total_original_size: int
    total_compressed_size: int
    overall_ratio: float
    total_savings_bytes: int
    total_savings_pct: float
    mean_compress_ms: float
    median_compress_ms: float
    mean_decompress_ms: float
    median_decompress_ms: float
    qualities: str
    trials: int
    backend: str
    dictionary_path: str


class ExperimentError(RuntimeError):
    pass


def parse_int_list(value: str) -> List[int]:
    items = []
    for part in value.split(','):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    if not items:
        raise argparse.ArgumentTypeError('Expected at least one integer.')
    return items


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover_files(root: Path, extensions: Optional[set[str]] = None) -> List[Path]:
    files: List[Path] = []
    for path in root.rglob('*'):
        if path.is_file():
            if extensions and path.suffix.lower() not in extensions:
                continue
            files.append(path)
    return sorted(files)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def compress_python_brotli(data: bytes, quality: int, lgwin: int, mode: int) -> tuple[bytes, float]:
    start = time.perf_counter()
    out = brotli.compress(data, quality=quality, lgwin=lgwin, mode=mode)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return out, elapsed_ms


def decompress_python_brotli(data: bytes) -> tuple[bytes, float]:
    start = time.perf_counter()
    out = brotli.decompress(data)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return out, elapsed_ms


def split_command_template(template: str, mapping: dict[str, str | int]) -> List[str]:
    rendered = template.format(**mapping)
    parts = shlex.split(rendered, posix=False)
    cleaned: List[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == '"' and part[-1] == '"':
            part = part[1:-1]
        cleaned.append(part)
    return cleaned


def run_external_compress(
    input_path: Path,
    output_path: Path,
    quality: int,
    lgwin: int,
    mode: int,
    dict_path: Optional[Path],
    encoder_cmd_template: str,
) -> float:
    cmd = split_command_template(
        encoder_cmd_template,
        {
            'input': str(input_path),
            'output': str(output_path),
            'quality': quality,
            'lgwin': lgwin,
            'mode': mode,
            'dict': '' if dict_path is None else str(dict_path),
        },
    )
    start = time.perf_counter()
    completed = subprocess.run(cmd, capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if completed.returncode != 0:
        raise ExperimentError(
            f'External encoder failed for {input_path}:\n'
            f'CMD: {cmd}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}'
        )
    if not output_path.exists():
        raise ExperimentError(f'External encoder did not create output file: {output_path}')
    return elapsed_ms


def run_external_decompress(
    input_path: Path,
    output_path: Path,
    quality: int,
    lgwin: int,
    mode: int,
    dict_path: Optional[Path],
    decoder_cmd_template: str,
) -> float:
    cmd = split_command_template(
        decoder_cmd_template,
        {
            'input': str(input_path),
            'output': str(output_path),
            'quality': quality,
            'lgwin': lgwin,
            'mode': mode,
            'dict': '' if dict_path is None else str(dict_path),
        },
    )
    start = time.perf_counter()
    completed = subprocess.run(cmd, capture_output=True, text=True)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if completed.returncode != 0:
        raise ExperimentError(
            f'External decoder failed for {input_path}:\n'
            f'CMD: {cmd}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}'
        )
    if not output_path.exists():
        raise ExperimentError(f'External decoder did not create output file: {output_path}')
    return elapsed_ms


def build_saved_output_path(save_dir: Path, root_dir: Path, file_path: Path, quality: int, trial: int) -> Path:
    rel = file_path.relative_to(root_dir)
    rel_parent = rel.parent
    stem = file_path.name
    out_dir = save_dir / f'q{quality}' / f'trial_{trial}' / rel_parent
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f'{stem}.br'


def process_one_file(
    file_path: Path,
    root_dir: Path,
    trial: int,
    quality: int,
    lgwin: int,
    mode: int,
    dictionary: Optional[Path],
    encoder_cmd_template: Optional[str],
    decoder_cmd_template: Optional[str],
    save_compressed: bool = False,
    save_dir: Optional[Path] = None,
) -> TrialResult:
    raw = file_path.read_bytes()
    sha = sha256_bytes(raw)
    backend = 'python-brotli' if not encoder_cmd_template else 'external-template'
    notes = ''
    compressed_output_path = ''

    if encoder_cmd_template:
        with tempfile.TemporaryDirectory(prefix='brotli_exp_') as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_copy = tmpdir_path / 'input.bin'
            compressed_path = tmpdir_path / 'compressed.br'
            decompressed_path = tmpdir_path / 'decompressed.bin'
            input_copy.write_bytes(raw)

            compress_ms = run_external_compress(
                input_copy,
                compressed_path,
                quality,
                lgwin,
                mode,
                dictionary,
                encoder_cmd_template,
            )
            compressed = compressed_path.read_bytes()

            if save_compressed:
                if save_dir is None:
                    raise ExperimentError('save_compressed=True requires save_dir.')
                persistent_path = build_saved_output_path(save_dir, root_dir, file_path, quality, trial)
                shutil.copy2(compressed_path, persistent_path)
                compressed_output_path = str(persistent_path)

            if decoder_cmd_template:
                decompress_ms = run_external_decompress(
                    compressed_path,
                    decompressed_path,
                    quality,
                    lgwin,
                    mode,
                    dictionary,
                    decoder_cmd_template,
                )
                decompressed = decompressed_path.read_bytes()
                roundtrip_ok = decompressed == raw
            else:
                decompress_ms = 0.0
                roundtrip_ok = False
                notes = 'Decoder template not supplied; round-trip verification skipped.'
    else:
        if dictionary:
            raise ExperimentError(
                'A dictionary path was supplied, but no external encoder template was given. '
                'The Python brotli binding used here does not expose custom-dictionary compression.'
            )
        compressed, compress_ms = compress_python_brotli(raw, quality, lgwin, mode)
        if save_compressed:
            if save_dir is None:
                raise ExperimentError('save_compressed=True requires save_dir.')
            persistent_path = build_saved_output_path(save_dir, root_dir, file_path, quality, trial)
            ensure_parent(persistent_path)
            persistent_path.write_bytes(compressed)
            compressed_output_path = str(persistent_path)
        decompressed, decompress_ms = decompress_python_brotli(compressed)
        roundtrip_ok = decompressed == raw

    compressed_size = len(compressed)
    original_size = len(raw)
    ratio = (compressed_size / original_size) if original_size else 0.0
    savings = original_size - compressed_size
    savings_pct = (savings / original_size * 100.0) if original_size else 0.0

    return TrialResult(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        trial=trial,
        file_path=str(file_path),
        file_name=file_path.name,
        relative_path=str(file_path.relative_to(root_dir)),
        file_ext=file_path.suffix.lower(),
        original_size=original_size,
        quality=quality,
        lgwin=lgwin,
        mode=mode,
        backend=backend,
        dictionary_path='' if dictionary is None else str(dictionary),
        dictionary_size=0 if dictionary is None else dictionary.stat().st_size,
        compressed_size=compressed_size,
        compression_ratio=ratio,
        savings_bytes=savings,
        savings_pct=savings_pct,
        compress_ms=compress_ms,
        decompress_ms=decompress_ms,
        roundtrip_ok=roundtrip_ok,
        sha256_original=sha,
        notes=notes,
        compressed_output_path=compressed_output_path,
    )


def write_trial_csv(results: List[TrialResult], output_csv: Path) -> None:
    ensure_parent(output_csv)
    with output_csv.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        for row in results:
            writer.writerow(asdict(row))


def build_summary(results: List[TrialResult], qualities: List[int], trials: int) -> SummaryRow:
    total_original = sum(r.original_size for r in results)
    total_compressed = sum(r.compressed_size for r in results)
    total_savings = total_original - total_compressed
    overall_ratio = (total_compressed / total_original) if total_original else 0.0
    total_savings_pct = (total_savings / total_original * 100.0) if total_original else 0.0
    compress_values = [r.compress_ms for r in results]
    decompress_values = [r.decompress_ms for r in results if r.decompress_ms > 0]
    example = results[0]
    return SummaryRow(
        scope='all_rows',
        file_count=len({r.file_path for r in results}),
        total_original_size=total_original,
        total_compressed_size=total_compressed,
        overall_ratio=overall_ratio,
        total_savings_bytes=total_savings,
        total_savings_pct=total_savings_pct,
        mean_compress_ms=statistics.mean(compress_values) if compress_values else 0.0,
        median_compress_ms=statistics.median(compress_values) if compress_values else 0.0,
        mean_decompress_ms=statistics.mean(decompress_values) if decompress_values else 0.0,
        median_decompress_ms=statistics.median(decompress_values) if decompress_values else 0.0,
        qualities=','.join(map(str, qualities)),
        trials=trials,
        backend=example.backend,
        dictionary_path=example.dictionary_path,
    )


def write_summary_csv(summary: SummaryRow, output_csv: Path) -> None:
    path = output_csv.with_name(output_csv.stem + '_summary.csv')
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(summary).keys()))
        writer.writeheader()
        writer.writerow(asdict(summary))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Brotli compression experiments and export CSV results.')
    parser.add_argument('--input-dir', required=True, help='Root folder to scan recursively.')
    parser.add_argument('--output-csv', required=True, help='Output CSV path for per-file results.')
    parser.add_argument('--qualities', type=parse_int_list, default=[5, 11], help='Comma-separated Brotli quality values, e.g. 5,11')
    parser.add_argument('--trials', type=int, default=1, help='Number of repeated trials for each file/quality pair.')
    parser.add_argument('--lgwin', type=int, default=22, help='Brotli lgwin value (10..24).')
    parser.add_argument('--mode', type=int, default=0, help='Brotli mode: 0=generic, 1=text, 2=font.')
    parser.add_argument('--dictionary', help='Optional dictionary file path.')
    parser.add_argument('--encoder-cmd-template', help='Optional external encoder command template for dictionary-capable runs.')
    parser.add_argument('--decoder-cmd-template', help='Optional external decoder command template for round-trip verification in external mode.')
    parser.add_argument('--extensions', help='Optional comma-separated extension filter, e.g. .pdf,.txt,.json')
    parser.add_argument('--save-compressed', action='store_true', help='Save compressed .br files for each run.')
    parser.add_argument('--compressed-output-dir', help='Folder for saved compressed files. Required with --save-compressed.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root_dir = Path(args.input_dir).resolve()
    output_csv = Path(args.output_csv).resolve()
    dictionary = Path(args.dictionary).resolve() if args.dictionary else None
    save_dir = Path(args.compressed_output_dir).resolve() if args.compressed_output_dir else None

    if not root_dir.exists() or not root_dir.is_dir():
        raise ExperimentError(f'Input directory does not exist or is not a directory: {root_dir}')
    if args.save_compressed and save_dir is None:
        raise ExperimentError('--compressed-output-dir is required when --save-compressed is used.')

    ext_filter = None
    if args.extensions:
        ext_filter = {e.strip().lower() if e.strip().startswith('.') else '.' + e.strip().lower() for e in args.extensions.split(',') if e.strip()}

    files = discover_files(root_dir, ext_filter)
    if not files:
        raise ExperimentError(f'No files found under: {root_dir}')

    results: List[TrialResult] = []
    for quality in args.qualities:
        for trial in range(1, args.trials + 1):
            for file_path in files:
                row = process_one_file(
                    file_path=file_path,
                    root_dir=root_dir,
                    trial=trial,
                    quality=quality,
                    lgwin=args.lgwin,
                    mode=args.mode,
                    dictionary=dictionary,
                    encoder_cmd_template=args.encoder_cmd_template,
                    decoder_cmd_template=args.decoder_cmd_template,
                    save_compressed=args.save_compressed,
                    save_dir=save_dir,
                )
                results.append(row)
                print(
                    f'[q={quality} trial={trial}] {file_path.name}: '
                    f'{row.original_size} -> {row.compressed_size} bytes '
                    f'({row.savings_pct:.2f}% saved)'
                )

    write_trial_csv(results, output_csv)
    summary = build_summary(results, args.qualities, args.trials)
    write_summary_csv(summary, output_csv)

    print(f'Wrote trial results: {output_csv}')
    print(f'Wrote summary results: {output_csv.with_name(output_csv.stem + "_summary.csv")}')
    if args.save_compressed and save_dir is not None:
        print(f'Saved compressed files under: {save_dir}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
