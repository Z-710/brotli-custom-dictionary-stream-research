#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Object, Stream
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "pikepdf is required. Install it with: pip install pikepdf\n"
        f"Import error: {exc}"
    )


@dataclass
class ScanRow:
    pdf_path: str
    sha1: str
    file_size: int
    pdf_version: str
    is_tagged: bool
    has_object_streams: bool
    has_xref_streams: bool
    has_struct_tree: bool
    has_icc_profiles: bool
    has_font_streams: bool
    page_count: int
    count_content_streams: int
    bytes_content_streams_raw: int
    bytes_content_streams_decoded: int
    count_object_streams: int
    bytes_object_streams_raw: int
    bytes_object_streams_decoded: int
    count_xref_streams: int
    bytes_xref_streams_raw: int
    bytes_xref_streams_decoded: int
    count_tagged_indirect_objects: int
    bytes_tagged_serialized: int
    count_icc_streams: int
    bytes_icc_streams_raw: int
    bytes_icc_streams_decoded: int
    count_font_streams: int
    bytes_font_streams_raw: int
    bytes_font_streams_decoded: int
    notes: str


class ObjKey:
    __slots__ = ("objgen",)

    def __init__(self, obj: Object):
        self.objgen = self._extract_objgen(obj)

    @staticmethod
    def _extract_objgen(obj: Object) -> tuple[int, int]:
        objgen = getattr(obj, "objgen", None)
        if objgen is None:
            raise ValueError("Object is not indirect and cannot be keyed")
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


def read_raw_len(stream: Stream) -> int:
    try:
        return len(stream.read_raw_bytes())
    except Exception:
        return 0


def read_decoded_len(stream: Stream) -> int:
    try:
        return len(stream.read_bytes())
    except Exception:
        return 0


def is_name(obj: Object, value: str) -> bool:
    try:
        return str(obj) == value
    except Exception:
        return False


def is_indirect(obj: Object) -> bool:
    return getattr(obj, "objgen", None) is not None


def iter_indirect_objects(pdf: pikepdf.Pdf) -> Iterator[Object]:
    for obj in pdf.objects:
        if is_indirect(obj):
            yield obj


def obj_key(obj: Object) -> Optional[ObjKey]:
    try:
        return ObjKey(obj)
    except Exception:
        return None


def container_key(obj: Object) -> tuple[str, object]:
    key = obj_key(obj)
    if key is not None:
        return ("indirect", key.objgen)
    return ("direct", id(obj))


def iter_page_content_streams(pdf: pikepdf.Pdf) -> Iterator[Stream]:
    for page in pdf.pages:
        try:
            contents = page.obj.get("/Contents")
        except Exception:
            contents = None
        if contents is None:
            continue
        if isinstance(contents, Stream):
            yield contents
        elif isinstance(contents, Array):
            for item in contents:
                if isinstance(item, Stream):
                    yield item


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
    try:
        resources = page_obj.get("/Resources")
    except Exception:
        resources = None
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
    found: dict[tuple[int, int], Stream] = {}

    def add_if_icc(maybe_stream: Optional[Stream]) -> None:
        if maybe_stream is None:
            return
        key = obj_key(maybe_stream)
        if key is None:
            return
        found[key.objgen] = maybe_stream

    for page in pdf.pages:
        for resources in iter_resource_dicts(page.obj):
            colorspaces = resources.get("/ColorSpace")
            if isinstance(colorspaces, Dictionary):
                for value in colorspaces.values():
                    add_if_icc(_icc_stream_from_colorspace_obj(value))

    for obj in iter_indirect_objects(pdf):
        if isinstance(obj, Stream):
            try:
                if "/N" in obj and "/Alternate" in obj:
                    key = obj_key(obj)
                    if key is not None:
                        found[key.objgen] = obj
            except Exception:
                pass

    return sorted(found.values(), key=lambda s: ObjKey(s).objgen)


def collect_font_streams(pdf: pikepdf.Pdf) -> list[Stream]:
    found: dict[tuple[int, int], Stream] = {}
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
                    key = obj_key(maybe_stream)
                    if key is not None:
                        found[key.objgen] = maybe_stream
    return sorted(found.values(), key=lambda s: ObjKey(s).objgen)


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


def collect_tagged_objects(pdf: pikepdf.Pdf, *, max_nodes: int = 5000) -> tuple[list[Object], bool]:
    """Collect tagged-structure objects, with a safety cap.

    Some PDFs generated by browsers contain large or deeply nested logical
    structure trees. For the normal stream scan this data is optional, so this
    helper is capped to avoid one file making the whole scan look stuck.

    Returns (objects, capped).
    """
    struct_root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(struct_root, Dictionary):
        return [], False

    visited: set[tuple[str, object]] = set()
    out: list[Object] = []
    stack: list[Object] = [struct_root]

    capped = False
    while stack:
        if len(out) >= max_nodes:
            capped = True
            break
        current = stack.pop()
        ckey = container_key(current)
        if ckey in visited:
            continue
        visited.add(ckey)

        if isinstance(current, (Dictionary, Array, Stream)):
            out.append(current)

        if isinstance(current, Dictionary):
            for value in current.values():
                if isinstance(value, (Dictionary, Array, Stream)):
                    stack.append(value)
        elif isinstance(current, Array):
            for item in current:
                if isinstance(item, (Dictionary, Array, Stream)):
                    stack.append(item)
        elif isinstance(current, Stream):
            for value in current.values():
                if isinstance(value, (Dictionary, Array, Stream)):
                    stack.append(value)

    out.sort(key=lambda obj: (obj_key(obj).objgen if obj_key(obj) is not None else (10**12, id(obj))))
    return out, capped


def scan_pdf(path: Path, *, scan_tagged_structures: bool = False, tagged_max_nodes: int = 5000) -> ScanRow:
    with pikepdf.Pdf.open(path) as pdf:
        pdf_version = getattr(pdf, "pdf_version", "unknown")
        page_count = len(pdf.pages)

        content_streams = list(iter_page_content_streams(pdf))
        object_streams = list(iter_object_streams(pdf))
        xref_streams = list(iter_xref_streams(pdf))
        has_struct_tree_light = isinstance(pdf.Root.get("/StructTreeRoot"), Dictionary)
        is_tagged_light = bool(pdf.Root.get("/MarkInfo") and pdf.Root.get("/StructTreeRoot")) or has_struct_tree_light

        tagged_objects: list[Object] = []
        tagged_capped = False
        if scan_tagged_structures:
            tagged_objects, tagged_capped = collect_tagged_objects(pdf, max_nodes=tagged_max_nodes)

        icc_streams = collect_icc_streams(pdf)
        font_streams = collect_font_streams(pdf)

        return ScanRow(
            pdf_path=str(path),
            sha1=sha1_file(path),
            file_size=path.stat().st_size,
            pdf_version=str(pdf_version),
            is_tagged=is_tagged_light,
            has_object_streams=bool(object_streams),
            has_xref_streams=bool(xref_streams),
            has_struct_tree=has_struct_tree_light,
            has_icc_profiles=bool(icc_streams),
            has_font_streams=bool(font_streams),
            page_count=page_count,
            count_content_streams=len(content_streams),
            bytes_content_streams_raw=sum(read_raw_len(s) for s in content_streams),
            bytes_content_streams_decoded=sum(read_decoded_len(s) for s in content_streams),
            count_object_streams=len(object_streams),
            bytes_object_streams_raw=sum(read_raw_len(s) for s in object_streams),
            bytes_object_streams_decoded=sum(read_decoded_len(s) for s in object_streams),
            count_xref_streams=len(xref_streams),
            bytes_xref_streams_raw=sum(read_raw_len(s) for s in xref_streams),
            bytes_xref_streams_decoded=sum(read_decoded_len(s) for s in xref_streams),
            count_tagged_indirect_objects=len(tagged_objects),
            bytes_tagged_serialized=sum(len(serialize_pdfish(obj)) for obj in tagged_objects),
            count_icc_streams=len(icc_streams),
            bytes_icc_streams_raw=sum(read_raw_len(s) for s in icc_streams),
            bytes_icc_streams_decoded=sum(read_decoded_len(s) for s in icc_streams),
            count_font_streams=len(font_streams),
            bytes_font_streams_raw=sum(read_raw_len(s) for s in font_streams),
            bytes_font_streams_decoded=sum(read_decoded_len(s) for s in font_streams),
            notes=(
                "tagged_structure_scan_skipped" if (has_struct_tree_light and not scan_tagged_structures)
                else "tagged_structure_scan_capped" if tagged_capped
                else ""
            ),
        )


def write_csv(rows: Iterable[ScanRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    fieldnames = list(ScanRow.__dataclass_fields__.keys())
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scan PDFs for internal structural stream categories")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-csv", required=True)
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--scan-tagged-structures", action="store_true", help="Also traverse /StructTreeRoot. Slower; off by default because it is not needed for normal stream scanning.")
    p.add_argument("--tagged-max-nodes", type=int, default=5000, help="Safety cap when --scan-tagged-structures is enabled.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    input_dir = Path(args.input_dir)
    output_csv = Path(args.output_csv)

    rows: list[ScanRow] = []
    pdfs = list(iter_pdf_files(input_dir))
    if not pdfs:
        raise SystemExit(f"No PDFs found under {input_dir}")

    total = len(pdfs)
    print(f"Found {total} PDF files to scan under: {input_dir}", flush=True)

    for index, path in enumerate(pdfs, start=1):
        try:
            if args.verbose:
                print(f"Scanning {index}/{total}: {path}", flush=True)
            row = scan_pdf(path, scan_tagged_structures=args.scan_tagged_structures, tagged_max_nodes=args.tagged_max_nodes)
            rows.append(row)
            if args.verbose:
                print(f"Scanned {index}/{total}: {path}", flush=True)
        except Exception as exc:
            if args.fail_fast:
                raise
            rows.append(
                ScanRow(
                    pdf_path=str(path),
                    sha1=sha1_file(path),
                    file_size=path.stat().st_size,
                    pdf_version="unknown",
                    is_tagged=False,
                    has_object_streams=False,
                    has_xref_streams=False,
                    has_struct_tree=False,
                    has_icc_profiles=False,
                    has_font_streams=False,
                    page_count=0,
                    count_content_streams=0,
                    bytes_content_streams_raw=0,
                    bytes_content_streams_decoded=0,
                    count_object_streams=0,
                    bytes_object_streams_raw=0,
                    bytes_object_streams_decoded=0,
                    count_xref_streams=0,
                    bytes_xref_streams_raw=0,
                    bytes_xref_streams_decoded=0,
                    count_tagged_indirect_objects=0,
                    bytes_tagged_serialized=0,
                    count_icc_streams=0,
                    bytes_icc_streams_raw=0,
                    bytes_icc_streams_decoded=0,
                    count_font_streams=0,
                    bytes_font_streams_raw=0,
                    bytes_font_streams_decoded=0,
                    notes=f"ERROR: {exc}",
                )
            )
            if args.verbose:
                print(f"Error scanning {index}/{total}: {path}: {exc}", flush=True)

    write_csv(rows, output_csv)
    print(f"Scanned {len(rows)} of {total} PDF files successfully.", flush=True)
    print(f"Wrote scan CSV: {output_csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
