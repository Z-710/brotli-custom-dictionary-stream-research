#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Object, Stream
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "pikepdf is required. Install it with: pip install pikepdf\n"
        f"Import error: {exc}"
    )


VALID_CATEGORIES = {
    "content_streams",
    "object_streams",
    "xref_streams",
    "tagged_structures",
    "icc_profiles",
    "font_streams",
}


@dataclass
class ManifestRow:
    source_pdf: str
    source_sha1: str
    category: str
    item_name: str
    object_id: str
    page_num: int
    variant: str
    relative_path: str
    size_bytes: int
    notes: str


class ObjKey:
    __slots__ = ("objgen",)

    def __init__(self, obj: Object):
        self.objgen = self._extract_objgen(obj)

    @staticmethod
    def _extract_objgen(obj: Object) -> tuple[int, int]:
        objgen = getattr(obj, "objgen", None)
        if objgen is None:
            raise ValueError("Object is not indirect")
        if isinstance(objgen, tuple) and len(objgen) == 2:
            return int(objgen[0]), int(objgen[1])
        raise ValueError(f"Unexpected objgen value: {objgen!r}")


def iter_pdf_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.pdf"):
        if path.is_file():
            yield path


def sha1_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_indirect(obj: Object) -> bool:
    return getattr(obj, "objgen", None) is not None


def slugify(value: str) -> str:
    value = value.replace("\\", "/")
    value = re.sub(r"[^A-Za-z0-9._/-]+", "_", value)
    value = value.strip("._/")
    return value or "unnamed"


def obj_id_str(obj: Object) -> str:
    try:
        key = ObjKey(obj)
        return f"{key.objgen[0]}_{key.objgen[1]}"
    except Exception:
        return "na"


def write_bytes(path: Path, payload: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return len(payload)


def is_name(obj: Object, value: str) -> bool:
    try:
        return str(obj) == value
    except Exception:
        return False


def normalize_whitespace_ascii(data: bytes) -> bytes:
    try:
        text = data.decode("latin-1")
    except Exception:
        return data
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.encode("latin-1", errors="replace")


def container_key(obj: Object) -> tuple[str, object]:
    try:
        key = ObjKey(obj)
        return ("indirect", key.objgen)
    except Exception:
        return ("direct", id(obj))


def iter_page_content_streams(pdf: pikepdf.Pdf) -> Iterator[tuple[int, Stream]]:
    for page_num, page in enumerate(pdf.pages, start=1):
        contents = page.obj.get("/Contents")
        if contents is None:
            continue
        if isinstance(contents, Stream):
            yield page_num, contents
        elif isinstance(contents, Array):
            for item in contents:
                if isinstance(item, Stream):
                    yield page_num, item


def iter_indirect_objects(pdf: pikepdf.Pdf) -> Iterator[Object]:
    for obj in pdf.objects:
        if getattr(obj, "objgen", None) is not None:
            yield obj


def iter_object_streams(pdf: pikepdf.Pdf) -> Iterator[Stream]:
    for obj in iter_indirect_objects(pdf):
        if isinstance(obj, Stream):
            try:
                if is_name(obj.get("/Type"), "/ObjStm"):
                    yield obj
            except Exception:
                continue


def iter_xref_streams(pdf: pikepdf.Pdf) -> Iterator[Stream]:
    for obj in iter_indirect_objects(pdf):
        if isinstance(obj, Stream):
            try:
                if is_name(obj.get("/Type"), "/XRef"):
                    yield obj
            except Exception:
                continue


def _icc_stream_from_colorspace_obj(obj: Object) -> Optional[Stream]:
    if not isinstance(obj, Array) or len(obj) < 2:
        return None
    try:
        if is_name(obj[0], "/ICCBased") and isinstance(obj[1], Stream):
            return obj[1]
    except Exception:
        return None
    return None


def iter_resource_dicts(page_obj: Dictionary) -> Iterator[Dictionary]:
    stack: list[Object] = []
    resources = page_obj.get("/Resources")
    if resources is not None:
        stack.append(resources)
    seen: set[tuple[str, object]] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, Dictionary):
            ckey = container_key(current)
            if ckey in seen:
                continue
            seen.add(ckey)
            yield current
            for value in current.values():
                if isinstance(value, Dictionary):
                    stack.append(value)
                elif isinstance(value, Array):
                    for item in value:
                        if isinstance(item, Dictionary):
                            stack.append(item)


def collect_icc_streams(pdf: pikepdf.Pdf) -> list[Stream]:
    found: dict[str, Stream] = {}
    for page in pdf.pages:
        for resources in iter_resource_dicts(page.obj):
            colorspaces = resources.get("/ColorSpace")
            if isinstance(colorspaces, Dictionary):
                for value in colorspaces.values():
                    stream = _icc_stream_from_colorspace_obj(value)
                    if isinstance(stream, Stream):
                        found[obj_id_str(stream)] = stream

    for obj in iter_indirect_objects(pdf):
        if isinstance(obj, Stream):
            try:
                if "/N" in obj and "/Alternate" in obj:
                    found[obj_id_str(obj)] = obj
            except Exception:
                pass

    return [found[k] for k in sorted(found)]


def collect_font_streams(pdf: pikepdf.Pdf) -> list[Stream]:
    found: dict[str, Stream] = {}
    for page in pdf.pages:
        resources = page.obj.get("/Resources")
        if not isinstance(resources, Dictionary):
            continue
        fonts = resources.get("/Font")
        if not isinstance(fonts, Dictionary):
            continue
        for font_ref in fonts.values():
            if not isinstance(font_ref, Dictionary):
                continue
            font_desc = font_ref.get("/FontDescriptor")
            if not isinstance(font_desc, Dictionary):
                continue
            for key_name in ("/FontFile", "/FontFile2", "/FontFile3"):
                maybe_stream = font_desc.get(key_name)
                if isinstance(maybe_stream, Stream):
                    found[obj_id_str(maybe_stream)] = maybe_stream
    return [found[k] for k in sorted(found)]


def serialize_pdfish(
    obj: Object,
    *,
    sort_dict_keys: bool = True,
    _seen: Optional[set[tuple[str, object]]] = None,
) -> bytes:
    if _seen is None:
        _seen = set()

    if isinstance(obj, (Stream, Dictionary, Array)):
        ckey = container_key(obj)
        if ckey in _seen:
            if is_indirect(obj):
                try:
                    key = ObjKey(obj)
                    return f"{key.objgen[0]} {key.objgen[1]} R".encode("ascii")
                except Exception:
                    pass
            return b"<cycle>"
        _seen = set(_seen)
        _seen.add(ckey)

    if isinstance(obj, Stream):
        keys = list(obj.keys())
        if sort_dict_keys:
            keys = sorted(keys, key=str)
        parts: list[bytes] = [b"<<"]
        for key in keys:
            parts.append(str(key).encode("utf-8", errors="replace"))
            parts.append(b" ")
            parts.append(serialize_pdfish(obj[key], sort_dict_keys=sort_dict_keys, _seen=_seen))
            parts.append(b" ")
        parts.append(b">>")
        parts.append(b" stream_placeholder")
        return b"".join(parts)
    if isinstance(obj, Dictionary):
        keys = list(obj.keys())
        if sort_dict_keys:
            keys = sorted(keys, key=str)
        parts = [b"<<"]
        for key in keys:
            parts.append(str(key).encode("utf-8", errors="replace"))
            parts.append(b" ")
            parts.append(serialize_pdfish(obj[key], sort_dict_keys=sort_dict_keys, _seen=_seen))
            parts.append(b" ")
        parts.append(b">>")
        return b"".join(parts)
    if isinstance(obj, Array):
        parts = [b"["]
        for item in obj:
            parts.append(serialize_pdfish(item, sort_dict_keys=sort_dict_keys, _seen=_seen))
            parts.append(b" ")
        parts.append(b"]")
        return b"".join(parts)
    if obj is None:
        return b"null"
    if isinstance(obj, bool):
        return b"true" if obj else b"false"
    if isinstance(obj, (int, float)):
        return str(obj).encode("ascii", errors="replace")
    try:
        if is_indirect(obj):
            key = ObjKey(obj)
            return f"{key.objgen[0]} {key.objgen[1]} R".encode("ascii")
    except Exception:
        pass
    return str(obj).encode("utf-8", errors="replace")


def collect_tagged_objects(pdf: pikepdf.Pdf, *, normalize_object_order: bool) -> list[Object]:
    struct_root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(struct_root, Dictionary):
        return []

    visited: set[tuple[str, object]] = set()
    out: list[Object] = []
    stack: list[Object] = [struct_root]
    while stack:
        current = stack.pop()
        ckey = container_key(current)
        if ckey in visited:
            continue
        visited.add(ckey)
        out.append(current)

        if isinstance(current, Dictionary):
            items = list(current.values())
            if normalize_object_order:
                items.sort(key=lambda x: obj_id_str(x))
            for value in items:
                if isinstance(value, (Dictionary, Array, Stream)):
                    stack.append(value)
        elif isinstance(current, Array):
            items = list(current)
            if normalize_object_order:
                items.sort(key=lambda x: obj_id_str(x))
            for item in items:
                if isinstance(item, (Dictionary, Array, Stream)):
                    stack.append(item)
        elif isinstance(current, Stream):
            items = list(current.values())
            if normalize_object_order:
                items.sort(key=lambda x: obj_id_str(x))
            for value in items:
                if isinstance(value, (Dictionary, Array, Stream)):
                    stack.append(value)

    if normalize_object_order:
        out.sort(key=lambda x: obj_id_str(x))
    return out


def write_manifest(rows: list[ManifestRow], output_dir: Path) -> None:
    manifest_path = output_dir / "manifest.csv"
    fieldnames = list(ManifestRow.__dataclass_fields__.keys())
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def extract_one_pdf(
    pdf_path: Path,
    output_dir: Path,
    categories: set[str],
    *,
    normalize_object_order: bool,
    normalize_dict_keys: bool,
    normalize_ascii_payloads: bool,
) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    source_sha1 = sha1_file(pdf_path)
    source_stem = slugify(pdf_path.stem)

    with pikepdf.Pdf.open(pdf_path) as pdf:
        if "content_streams" in categories:
            for idx, (page_num, stream) in enumerate(iter_page_content_streams(pdf), start=1):
                object_id = obj_id_str(stream)
                item_name = f"{source_stem}_p{page_num:04d}_content_{idx:04d}_{object_id}"
                raw_bytes = stream.read_raw_bytes()
                decoded_bytes = stream.read_bytes()
                if normalize_ascii_payloads:
                    decoded_bytes = normalize_whitespace_ascii(decoded_bytes)
                raw_rel = Path("content_streams") / "raw" / f"{item_name}.bin"
                dec_rel = Path("content_streams") / "decoded" / f"{item_name}.bin"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "content_streams", item_name, object_id, page_num, "raw", str(raw_rel), write_bytes(output_dir / raw_rel, raw_bytes), "content_raw"))
                rows.append(ManifestRow(str(pdf_path), source_sha1, "content_streams", item_name, object_id, page_num, "decoded", str(dec_rel), write_bytes(output_dir / dec_rel, decoded_bytes), "content_decoded"))

        if "object_streams" in categories:
            for idx, stream in enumerate(iter_object_streams(pdf), start=1):
                object_id = obj_id_str(stream)
                item_name = f"{source_stem}_objstm_{idx:04d}_{object_id}"
                raw_bytes = stream.read_raw_bytes()
                decoded_bytes = stream.read_bytes()
                if normalize_ascii_payloads:
                    decoded_bytes = normalize_whitespace_ascii(decoded_bytes)
                raw_rel = Path("object_streams") / "raw" / f"{item_name}.bin"
                dec_rel = Path("object_streams") / "decoded" / f"{item_name}.bin"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "object_streams", item_name, object_id, 0, "raw", str(raw_rel), write_bytes(output_dir / raw_rel, raw_bytes), "object_stream_raw"))
                rows.append(ManifestRow(str(pdf_path), source_sha1, "object_streams", item_name, object_id, 0, "decoded", str(dec_rel), write_bytes(output_dir / dec_rel, decoded_bytes), "object_stream_decoded"))

        if "xref_streams" in categories:
            for idx, stream in enumerate(iter_xref_streams(pdf), start=1):
                object_id = obj_id_str(stream)
                item_name = f"{source_stem}_xref_{idx:04d}_{object_id}"
                raw_bytes = stream.read_raw_bytes()
                decoded_bytes = stream.read_bytes()
                raw_rel = Path("xref_streams") / "raw" / f"{item_name}.bin"
                dec_rel = Path("xref_streams") / "decoded" / f"{item_name}.bin"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "xref_streams", item_name, object_id, 0, "raw", str(raw_rel), write_bytes(output_dir / raw_rel, raw_bytes), "xref_stream_raw"))
                rows.append(ManifestRow(str(pdf_path), source_sha1, "xref_streams", item_name, object_id, 0, "decoded", str(dec_rel), write_bytes(output_dir / dec_rel, decoded_bytes), "xref_stream_decoded"))

        if "tagged_structures" in categories:
            tagged_objects = collect_tagged_objects(pdf, normalize_object_order=normalize_object_order)
            for idx, obj in enumerate(tagged_objects, start=1):
                object_id = obj_id_str(obj)
                item_name = f"{source_stem}_tagged_{idx:04d}_{object_id}"
                payload = serialize_pdfish(obj, sort_dict_keys=normalize_dict_keys)
                if normalize_ascii_payloads:
                    payload = normalize_whitespace_ascii(payload)
                rel = Path("tagged_structures") / "serialized" / f"{item_name}.txt"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "tagged_structures", item_name, object_id, 0, "serialized", str(rel), write_bytes(output_dir / rel, payload), "tagged_serialized"))

        if "icc_profiles" in categories:
            for idx, stream in enumerate(collect_icc_streams(pdf), start=1):
                object_id = obj_id_str(stream)
                item_name = f"{source_stem}_icc_{idx:04d}_{object_id}"
                raw_bytes = stream.read_raw_bytes()
                decoded_bytes = stream.read_bytes()
                raw_rel = Path("icc_profiles") / "raw" / f"{item_name}.bin"
                dec_rel = Path("icc_profiles") / "decoded" / f"{item_name}.bin"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "icc_profiles", item_name, object_id, 0, "raw", str(raw_rel), write_bytes(output_dir / raw_rel, raw_bytes), "icc_raw"))
                rows.append(ManifestRow(str(pdf_path), source_sha1, "icc_profiles", item_name, object_id, 0, "decoded", str(dec_rel), write_bytes(output_dir / dec_rel, decoded_bytes), "icc_decoded"))

        if "font_streams" in categories:
            for idx, stream in enumerate(collect_font_streams(pdf), start=1):
                object_id = obj_id_str(stream)
                item_name = f"{source_stem}_font_{idx:04d}_{object_id}"
                raw_bytes = stream.read_raw_bytes()
                decoded_bytes = stream.read_bytes()
                raw_rel = Path("font_streams") / "raw" / f"{item_name}.bin"
                dec_rel = Path("font_streams") / "decoded" / f"{item_name}.bin"
                rows.append(ManifestRow(str(pdf_path), source_sha1, "font_streams", item_name, object_id, 0, "raw", str(raw_rel), write_bytes(output_dir / raw_rel, raw_bytes), "font_raw"))
                rows.append(ManifestRow(str(pdf_path), source_sha1, "font_streams", item_name, object_id, 0, "decoded", str(dec_rel), write_bytes(output_dir / dec_rel, decoded_bytes), "font_decoded"))

    return rows


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract selected PDF stream payloads to category folders")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--category", action="append", required=True, help=f"One of: {', '.join(sorted(VALID_CATEGORIES))}")
    p.add_argument("--normalize-object-order", action="store_true")
    p.add_argument("--normalize-dict-keys", action="store_true")
    p.add_argument("--normalize-ascii-payloads", action="store_true")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    categories = set(args.category)
    unknown = categories - VALID_CATEGORIES
    if unknown:
        raise SystemExit(f"Unknown categories: {sorted(unknown)}")

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[ManifestRow] = []
    pdfs = list(iter_pdf_files(input_dir))
    total = len(pdfs)
    if not pdfs:
        raise SystemExit(f"No PDFs found under {input_dir}")

    for pdf_path in pdfs:
        try:
            rows.extend(
                extract_one_pdf(
                    pdf_path,
                    output_dir,
                    categories,
                    normalize_object_order=args.normalize_object_order,
                    normalize_dict_keys=args.normalize_dict_keys,
                    normalize_ascii_payloads=args.normalize_ascii_payloads,
                )
            )
            if args.verbose:
                print(f"Extracted from: {pdf_path}", flush=True)
        except Exception as exc:
            if args.fail_fast:
                raise
            if args.verbose:
                print(f"Error extracting from {pdf_path}: {exc}")

    write_manifest(rows, output_dir)
    print(f"Processed {total} PDF files for extraction.", flush=True)
    print(f"Wrote manifest: {output_dir / 'manifest.csv'}", flush=True)
    print(f"Extracted {len(rows)} items/variants into: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
