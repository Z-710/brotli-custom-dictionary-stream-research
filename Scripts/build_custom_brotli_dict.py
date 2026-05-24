#!/usr/bin/env python3
"""
Build heuristic custom dictionary files from a training corpus.

Patched version:
- emits frequent progress logs for GUI visibility
- flushes all status output immediately
- validates inputs earlier and more clearly
- supports optional --progress-every to tune logging cadence
- supports optional --max-files to run quick smoke tests
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


@dataclass
class Candidate:
    data: bytes
    source: str
    freq: int
    score: int


TEXT_TOKEN_RE = re.compile(rb"[A-Za-z0-9_./:<>{}\[\]()=\-+,'\";]{4,64}")


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_int_list(value: str) -> List[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def discover_files(root: Path, extensions: set[str] | None = None) -> List[Path]:
    files: List[Path] = []
    for path in root.rglob("*"):
        if path.is_file():
            if extensions and path.suffix.lower() not in extensions:
                continue
            files.append(path)
    return sorted(files)


def split_train_eval(files: Sequence[Path], train_pct: float, seed: int) -> tuple[List[Path], List[Path]]:
    files = list(files)
    rng = random.Random(seed)
    rng.shuffle(files)
    cut = int(round(len(files) * train_pct / 100.0))
    cut = max(1, min(len(files), cut))
    return sorted(files[:cut]), sorted(files[cut:])


def text_tokens(data: bytes) -> Iterable[bytes]:
    for match in TEXT_TOKEN_RE.finditer(data):
        token = match.group(0)
        if len(token) >= 4:
            yield token


def mine_ngrams(data: bytes, min_len: int, max_len: int, stride: int) -> Counter[bytes]:
    counts: Counter[bytes] = Counter()
    n = len(data)
    for length in range(min_len, max_len + 1):
        if n < length:
            continue
        step = max(1, min(stride, length))
        for i in range(0, n - length + 1, step):
            gram = data[i:i + length]
            if len(set(gram)) <= 2 and len(gram) >= 8:
                continue
            counts[gram] += 1
    return counts


def estimated_savings(token: bytes, freq: int) -> int:
    return max(0, (freq - 1) * len(token) - 3)


def build_candidates(
    files: Sequence[Path],
    min_len: int,
    max_len: int,
    stride: int,
    max_token_candidates: int,
    max_ngram_candidates: int,
    progress_every: int,
) -> List[Candidate]:
    token_counts: Counter[bytes] = Counter()
    ngram_counts: Counter[bytes] = Counter()
    total_bytes = 0
    start = time.perf_counter()

    for idx, path in enumerate(files, start=1):
        data = path.read_bytes()
        total_bytes += len(data)
        token_counts.update(text_tokens(data))
        ngram_counts.update(mine_ngrams(data, min_len=min_len, max_len=max_len, stride=stride))

        if idx == 1 or idx % progress_every == 0 or idx == len(files):
            elapsed = time.perf_counter() - start
            mib = total_bytes / (1024 * 1024)
            log(
                f"Processed {idx}/{len(files)} training files, "
                f"{mib:.1f} MiB scanned, "
                f"{len(token_counts):,} unique tokens, "
                f"{len(ngram_counts):,} unique ngrams, "
                f"elapsed {elapsed:.1f}s"
            )

    candidates: List[Candidate] = []

    log(f"Scoring top {max_token_candidates:,} token candidates...")
    for token, freq in token_counts.most_common(max_token_candidates):
        score = estimated_savings(token, freq)
        if score > 0:
            candidates.append(Candidate(data=token, source="token", freq=freq, score=score))

    log(f"Scoring top {max_ngram_candidates:,} ngram candidates...")
    for gram, freq in ngram_counts.most_common(max_ngram_candidates):
        score = estimated_savings(gram, freq)
        if score > 0:
            candidates.append(Candidate(data=gram, source="ngram", freq=freq, score=score))

    log(f"Deduplicating {len(candidates):,} candidates...")
    best: dict[bytes, Candidate] = {}
    for cand in candidates:
        prev = best.get(cand.data)
        if prev is None or cand.score > prev.score:
            best[cand.data] = cand

    out = list(best.values())
    out.sort(key=lambda c: (c.score, len(c.data), c.freq), reverse=True)
    log(f"Retained {len(out):,} unique ranked candidates.")
    return out


def select_dictionary_bytes(candidates: Sequence[Candidate], target_size: int) -> bytes:
    chosen = bytearray()
    chosen_set: list[bytes] = []

    for cand in candidates:
        token = cand.data
        if len(token) > target_size:
            continue
        if any(token in prev for prev in chosen_set if len(prev) >= len(token)):
            continue
        if len(chosen) + len(token) > target_size:
            continue
        chosen.extend(token)
        chosen_set.append(token)
        if len(chosen) >= target_size:
            break

    return bytes(chosen)


def write_manifest(
    manifest_path: Path,
    train_files: Sequence[Path],
    eval_files: Sequence[Path],
    sizes: Sequence[int],
    output_dir: Path,
) -> None:
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f,
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writerow(["section", "path_or_value"])
        writer.writerow(["train_file_count", len(train_files)])
        for path in train_files:
            writer.writerow(["train_file", str(path)])
        writer.writerow(["eval_file_count", len(eval_files)])
        for path in eval_files:
            writer.writerow(["eval_file", str(path)])
        for size in sizes:
            writer.writerow(["dictionary_target_size", size])
            writer.writerow(["dictionary_output_path", str(output_dir / f"custom_dict_{size}.bin")])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build heuristic custom Brotli dictionaries from a corpus.")
    parser.add_argument("--input-dir", required=True, help="Root folder to scan recursively.")
    parser.add_argument("--output-dir", required=True, help="Folder where dictionary files will be written.")
    parser.add_argument(
        "--sizes",
        type=parse_int_list,
        default=[16384, 32768, 65536, 131072],
        help="Comma-separated target dictionary sizes in bytes.",
    )
    parser.add_argument("--train-pct", type=float, default=100.0, help="Percentage of files to use for training.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for train/eval split.")
    parser.add_argument("--min-len", type=int, default=4, help="Minimum candidate n-gram length.")
    parser.add_argument("--max-len", type=int, default=24, help="Maximum candidate n-gram length.")
    parser.add_argument("--stride", type=int, default=8, help="Sampling stride for n-gram mining.")
    parser.add_argument("--max-token-candidates", type=int, default=50000, help="Maximum token candidates to keep before scoring.")
    parser.add_argument("--max-ngram-candidates", type=int, default=50000, help="Maximum n-gram candidates to keep before scoring.")
    parser.add_argument("--extensions", help="Optional comma-separated extension filter, e.g. .pdf,.txt,.json")
    parser.add_argument("--progress-every", type=int, default=25, help="Print progress every N training files.")
    parser.add_argument("--max-files", type=int, default=0, help="Optional limit on discovered files for quick testing. 0 means no limit.")
    return parser.parse_args()


def _safe_preview_text(data: bytes, limit: int = 120) -> str:
    preview = data[:limit].decode("utf-8", errors="replace")
    preview = preview.replace("\x00", "\\0")
    preview = preview.replace("\r", "\\r").replace("\n", "\\n")
    return preview


def _safe_csv_text(value: object) -> str:
    text = str(value)
    text = text.replace("\x00", "\\0")
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text


def main() -> int:
    args = parse_args()
    start = time.perf_counter()
    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"Input directory: {input_dir}")
    log(f"Output directory: {output_dir}")
    log(f"Target dictionary sizes: {args.sizes}")

    ext_filter = None
    if args.extensions:
        ext_filter = {
            e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
            for e in args.extensions.split(",")
            if e.strip()
        }
        log(f"Extension filter: {sorted(ext_filter)}")

    files = discover_files(input_dir, ext_filter)
    if args.max_files and args.max_files > 0:
        files = files[: args.max_files]
        log(f"Limiting run to first {len(files)} files because --max-files was set.")

    if not files:
        raise SystemExit(f"No files found under {input_dir}")

    total_input_bytes = sum(path.stat().st_size for path in files)
    log(f"Discovered {len(files)} input files, total size {total_input_bytes / (1024 * 1024):.1f} MiB")

    train_files, eval_files = split_train_eval(files, train_pct=args.train_pct, seed=args.seed)
    log(f"Training files: {len(train_files)}")
    log(f"Evaluation files: {len(eval_files)}")

    candidates = build_candidates(
        train_files,
        min_len=args.min_len,
        max_len=args.max_len,
        stride=args.stride,
        max_token_candidates=args.max_token_candidates,
        max_ngram_candidates=args.max_ngram_candidates,
        progress_every=max(1, args.progress_every),
    )

    report_rows = []
    for size in args.sizes:
        log(f"Selecting entries for dictionary size {size} bytes...")
        blob = select_dictionary_bytes(candidates, size)
        out_path = output_dir / f"custom_dict_{size}.bin"
        out_path.write_bytes(blob)
        sha = hashlib.sha256(blob).hexdigest()
        report_rows.append(
            {
                "target_size": size,
                "actual_size": len(blob),
                "sha256": sha,
                "output_path": str(out_path),
            }
        )
        log(f"Wrote {out_path} ({len(blob)} bytes)")

    report_path = output_dir / "dictionary_report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["target_size", "actual_size", "sha256", "output_path"],
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writeheader()
        writer.writerows(report_rows)

    manifest_path = output_dir / "dictionary_manifest.csv"
    write_manifest(manifest_path, train_files, eval_files, args.sizes, output_dir)

    top_path = output_dir / "top_candidates.csv"
    with top_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f,
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writerow(["rank", "source", "freq", "score", "length", "preview_utf8"])
        for i, cand in enumerate(candidates[:1000], start=1):
            preview = _safe_preview_text(cand.data, limit=120)
            source = _safe_csv_text(cand.source)
            writer.writerow([i, source, cand.freq, cand.score, len(cand.data), preview])

    log(f"Wrote report: {report_path}")
    log(f"Wrote manifest: {manifest_path}")
    log(f"Wrote top candidates: {top_path}")
    log(f"Completed in {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
