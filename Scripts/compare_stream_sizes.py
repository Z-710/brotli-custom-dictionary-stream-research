#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class DetailRow:
    source_pdf: str
    category: str
    item_name: str
    decoded_relative_path: str
    initial_stream_size: int
    base_brotli_quality: int
    base_brotli_mean_size: int
    dict_label: str
    dict_quality: int
    dict_mean_size: int
    base_vs_initial_pct: float
    dict_vs_initial_pct: float
    dict_vs_base_pct: float


@dataclass
class SummaryRow:
    category: str
    row_count: int
    mean_initial_stream_size: float
    mean_base_brotli_size: float
    mean_dict_brotli_size: float
    mean_base_vs_initial_pct: float
    mean_dict_vs_initial_pct: float
    mean_dict_vs_base_pct: float


@dataclass
class ManifestDecodedRow:
    source_pdf: str
    category: str
    item_name: str
    relative_path: str
    size_bytes: int


def pct_change(new_value: float, old_value: float) -> float:
    if old_value == 0:
        return 0.0
    return ((new_value - old_value) / old_value) * 100.0


def normalize_rel_path(value: str) -> str:
    value = (value or "").strip().replace("\\", "/")
    while "//" in value:
        value = value.replace("//", "/")
    value = value.lstrip("./")
    return value


def basename_of(value: str) -> str:
    return Path(normalize_rel_path(value)).name


def load_manifest_decoded(manifest_csv: Path) -> dict[str, ManifestDecodedRow]:
    out: dict[str, ManifestDecodedRow] = {}
    with manifest_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("variant") != "decoded":
                continue
            rel = normalize_rel_path(row["relative_path"])
            out[rel] = ManifestDecodedRow(
                source_pdf=row["source_pdf"],
                category=row["category"],
                item_name=row["item_name"],
                relative_path=rel,
                size_bytes=int(row["size_bytes"]),
            )
    return out


def load_manifest_raw_sizes(manifest_csv: Path) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    with manifest_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("variant") != "raw":
                continue
            out[(row["category"], row["item_name"])] = int(row["size_bytes"])
    return out


def load_benchmark_grouped(csv_path: Path) -> dict[tuple[str, int], list[int]]:
    grouped: dict[tuple[str, int], list[int]] = defaultdict(list)
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rel = normalize_rel_path(row["relative_path"])
            quality = int(row["quality"])
            grouped[(rel, quality)].append(int(row["compressed_size"]))
    return grouped


def build_manifest_indexes(
    decoded_rows: dict[str, ManifestDecodedRow],
) -> tuple[dict[str, ManifestDecodedRow], dict[str, list[ManifestDecodedRow]], dict[tuple[str, str], ManifestDecodedRow]]:
    by_exact: dict[str, ManifestDecodedRow] = {}
    by_basename: dict[str, list[ManifestDecodedRow]] = defaultdict(list)
    by_category_and_basename: dict[tuple[str, str], ManifestDecodedRow] = {}

    for rel, row in decoded_rows.items():
        by_exact[rel] = row
        base = basename_of(rel)
        by_basename[base].append(row)
        by_category_and_basename[(row.category, base)] = row

    return by_exact, by_basename, by_category_and_basename


def resolve_manifest_row_for_benchmark_rel(
    bench_rel: str,
    category_hint: Optional[str],
    by_exact: dict[str, ManifestDecodedRow],
    by_basename: dict[str, list[ManifestDecodedRow]],
    by_category_and_basename: dict[tuple[str, str], ManifestDecodedRow],
) -> Optional[ManifestDecodedRow]:
    bench_rel = normalize_rel_path(bench_rel)

    exact = by_exact.get(bench_rel)
    if exact is not None:
        return exact

    base = basename_of(bench_rel)

    if category_hint:
        row = by_category_and_basename.get((category_hint, base))
        if row is not None:
            return row

        reconstructed = f"{category_hint}/decoded/{base}"
        row = by_exact.get(reconstructed)
        if row is not None:
            return row

    matches = by_basename.get(base, [])
    if len(matches) == 1:
        return matches[0]

    return None


def choose_quality_for_row(
    grouped: dict[tuple[str, int], list[int]],
    row: ManifestDecodedRow,
    requested_quality: Optional[int],
) -> tuple[int, int]:
    candidate_keys: list[tuple[str, int]] = []

    exact_rel = normalize_rel_path(row.relative_path)
    base = basename_of(exact_rel)
    reconstructed = normalize_rel_path(f"{row.category}/decoded/{base}")

    seen: set[tuple[str, int]] = set()
    for rel, quality in grouped.keys():
        if rel == exact_rel or rel == reconstructed or basename_of(rel) == base:
            key = (rel, quality)
            if key not in seen:
                candidate_keys.append(key)
                seen.add(key)

    if not candidate_keys:
        raise KeyError(f"No benchmark rows matched manifest row: {row.relative_path}")

    if requested_quality is not None:
        filtered = [(rel, q) for (rel, q) in candidate_keys if q == requested_quality]
        if not filtered:
            raise KeyError(
                f"No benchmark rows matched manifest row {row.relative_path} at quality {requested_quality}"
            )
        values: list[int] = []
        for rel, q in filtered:
            values.extend(grouped[(rel, q)])
        return requested_quality, int(round(statistics.mean(values)))

    qualities = sorted({q for (_, q) in candidate_keys})
    chosen_q = qualities[-1]
    values = []
    for rel, q in candidate_keys:
        if q == chosen_q:
            values.extend(grouped[(rel, q)])
    return chosen_q, int(round(statistics.mean(values)))


def infer_category_hint_from_output_prefix(output_prefix: Path) -> Optional[str]:
    parent_name = output_prefix.parent.name.strip()
    if parent_name:
        return parent_name
    return None


def write_csv(rows: Iterable[object], path: Path) -> None:
    rows = list(rows)
    if not rows:
        raise SystemExit(f"No rows to write for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare initial raw stream sizes against vanilla Brotli and Brotli+dictionary outputs"
    )
    parser.add_argument("--manifest-csv", required=True)
    parser.add_argument("--base-brotli-csv", required=True)
    parser.add_argument("--dict-brotli-csv", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--dict-label", default="trained_dictionary")
    parser.add_argument("--base-quality", type=int)
    parser.add_argument("--dict-quality", type=int)
    args = parser.parse_args()

    manifest_csv = Path(args.manifest_csv)
    base_csv = Path(args.base_brotli_csv)
    dict_csv = Path(args.dict_brotli_csv)
    output_prefix = Path(args.output_prefix)

    decoded_rows = load_manifest_decoded(manifest_csv)
    raw_sizes = load_manifest_raw_sizes(manifest_csv)
    base_grouped = load_benchmark_grouped(base_csv)
    dict_grouped = load_benchmark_grouped(dict_csv)

    by_exact, by_basename, by_category_and_basename = build_manifest_indexes(decoded_rows)

    category_hint = infer_category_hint_from_output_prefix(output_prefix)

    matched_manifest_keys: set[str] = set()
    details: list[DetailRow] = []

    unresolved_base_paths: list[str] = []
    for bench_rel, _q in sorted(base_grouped.keys()):
        manifest_row = resolve_manifest_row_for_benchmark_rel(
            bench_rel,
            category_hint,
            by_exact,
            by_basename,
            by_category_and_basename,
        )
        if manifest_row is None:
            unresolved_base_paths.append(bench_rel)
            continue

        if manifest_row.relative_path in matched_manifest_keys:
            continue

        raw_key = (manifest_row.category, manifest_row.item_name)
        if raw_key not in raw_sizes:
            continue

        try:
            base_quality, base_mean = choose_quality_for_row(base_grouped, manifest_row, args.base_quality)
            dict_quality, dict_mean = choose_quality_for_row(dict_grouped, manifest_row, args.dict_quality)
        except KeyError:
            continue

        initial_size = raw_sizes[raw_key]
        details.append(
            DetailRow(
                source_pdf=manifest_row.source_pdf,
                category=manifest_row.category,
                item_name=manifest_row.item_name,
                decoded_relative_path=manifest_row.relative_path,
                initial_stream_size=initial_size,
                base_brotli_quality=base_quality,
                base_brotli_mean_size=base_mean,
                dict_label=args.dict_label,
                dict_quality=dict_quality,
                dict_mean_size=dict_mean,
                base_vs_initial_pct=pct_change(base_mean, initial_size),
                dict_vs_initial_pct=pct_change(dict_mean, initial_size),
                dict_vs_base_pct=pct_change(dict_mean, base_mean),
            )
        )
        matched_manifest_keys.add(manifest_row.relative_path)

    if not details:
        sample_manifest = list(sorted(decoded_rows.keys()))[:5]
        sample_base = list(sorted({rel for (rel, _q) in base_grouped.keys()}))[:5]
        sample_dict = list(sorted({rel for (rel, _q) in dict_grouped.keys()}))[:5]
        raise SystemExit(
            "No comparable rows found.\n"
            f"Category hint: {category_hint or 'none'}\n"
            f"Decoded manifest rows: {len(decoded_rows)}\n"
            f"Base benchmark unique paths: {len({rel for (rel, _q) in base_grouped.keys()})}\n"
            f"Dict benchmark unique paths: {len({rel for (rel, _q) in dict_grouped.keys()})}\n"
            f"Sample manifest paths: {sample_manifest}\n"
            f"Sample base benchmark paths: {sample_base}\n"
            f"Sample dict benchmark paths: {sample_dict}"
        )

    summary_rows: list[SummaryRow] = []
    grouped_details: dict[str, list[DetailRow]] = defaultdict(list)
    for row in details:
        grouped_details[row.category].append(row)

    for category, rows in sorted(grouped_details.items()):
        summary_rows.append(
            SummaryRow(
                category=category,
                row_count=len(rows),
                mean_initial_stream_size=statistics.mean(r.initial_stream_size for r in rows),
                mean_base_brotli_size=statistics.mean(r.base_brotli_mean_size for r in rows),
                mean_dict_brotli_size=statistics.mean(r.dict_mean_size for r in rows),
                mean_base_vs_initial_pct=statistics.mean(r.base_vs_initial_pct for r in rows),
                mean_dict_vs_initial_pct=statistics.mean(r.dict_vs_initial_pct for r in rows),
                mean_dict_vs_base_pct=statistics.mean(r.dict_vs_base_pct for r in rows),
            )
        )

    detail_path = output_prefix.with_name(output_prefix.name + "_detail.csv")
    summary_path = output_prefix.with_name(output_prefix.name + "_summary.csv")
    write_csv(details, detail_path)
    write_csv(summary_rows, summary_path)

    unresolved_count = len(unresolved_base_paths)
    if unresolved_count:
        print(f"Matched {len(details)} rows. Unresolved base benchmark paths: {unresolved_count}")
        print(f"Example unresolved paths: {unresolved_base_paths[:5]}")
    else:
        print(f"Matched {len(details)} rows with manifest and benchmark CSVs.")

    print(f"Wrote detail CSV: {detail_path}")
    print(f"Wrote summary CSV: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())