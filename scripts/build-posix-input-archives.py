"""Build deterministic POSIX-path input archives for UIT Linux."""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

FIXED_ZIP_TIME = (2026, 7, 26, 0, 0, 0)


def _write_archive(
    archive: Path,
    members: list[tuple[Path, str]],
    *,
    compression_level: int,
) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(f"{archive.suffix}.building")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=compression_level,
        allowZip64=True,
    ) as handle:
        for source, member in sorted(members, key=lambda item: item[1]):
            info = zipfile.ZipInfo(member, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with source.open("rb") as input_handle, handle.open(
                info, "w", force_zip64=True
            ) as output_handle:
                for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                    output_handle.write(chunk)
    temporary.replace(archive)


def _directory_members(source: Path, prefix: str) -> list[tuple[Path, str]]:
    return [
        (path, f"{prefix.rstrip('/')}/{path.relative_to(source).as_posix()}")
        for path in source.rglob("*")
        if path.is_file()
    ]


def _privacy_members(root: Path) -> list[tuple[Path, str]]:
    base = (
        root
        / "raw"
        / "confirmatory"
        / "CONFIRMATORY_Q1_20260712"
        / "CONF_CORE_W08_P24"
    )
    paths = sorted(base.rglob("events.jsonl"))
    if len(paths) != 250:
        raise ValueError(f"expected 250 privacy core event logs, found {len(paths)}")
    return [(path, path.relative_to(root).as_posix()) for path in paths]


def _verify_posix_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as handle:
        bad = [name for name in handle.namelist() if "\\" in name or name.startswith("/")]
        if bad:
            raise ValueError(f"non-POSIX archive members in {path}: {bad[:3]}")
        corrupt = handle.testzip()
        if corrupt:
            raise ValueError(f"corrupt archive member in {path}: {corrupt}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compression-level", type=int, default=6)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()

    trace = output / "sociopatterns-normalized.zip"
    privacy = output / "privacy-core-events.zip"
    _write_archive(
        trace,
        _directory_members(
            root / "external" / "sociopatterns" / "normalized", "normalized"
        ),
        compression_level=args.compression_level,
    )
    _write_archive(
        privacy,
        _privacy_members(root),
        compression_level=args.compression_level,
    )
    for archive in (trace, privacy):
        _verify_posix_archive(archive)
        print(f"{_sha256(archive)}  {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
