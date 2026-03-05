"""
upload.py - Batch upload genome files to PathogenWatch.

Short-read upload flow (--file_type short_read_assembly):
  1. Compute SHA-1 checksum of each FASTQ file.
  2. POST /api/genomes/store?checksum=<sha1>&type=reads
       Response: { fileUrl, upload (bool), uploadUrl? }
  3. If upload=True  →  PUT file bytes to uploadUrl (signed storage URL).
  4. POST /api/genomes/create  { folderId, reads: [r1_fileUrl, r2_fileUrl], name }
       Response: { id (int), uuid }

Assembly upload flow (--file_type fa,fna,fasta,…):
  1. Optionally decompress .gz / .tar.gz to a temp file.
  2. Compute SHA-1 of the (decompressed) file.
  3. POST /api/genomes/store?checksum=<sha1>&type=assembly
  4. If upload=True  →  PUT file bytes to uploadUrl.
  5. POST /api/genomes/create  { folderId, checksum, name }
       Response: { id (int), uuid }

Returns a list of (sample_name, genome_id) tuples.
"""

from __future__ import annotations

import gzip
import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from api.client import PathogenWatchClient
from utils.file_scanner import find_pairs, find_assembly_files, SUPPORTED_ASSEMBLY_EXTS


def upload_genomes(
    client: PathogenWatchClient,
    input_dir: str,
    regex_pattern: str,
    folder_id: int,
) -> List[Tuple[str, int]]:
    """
    Discover paired-end FASTQ files in *input_dir*, store each file in
    PathogenWatch object storage, then register each pair as a genome.

    Parameters
    ----------
    client : PathogenWatchClient
        Authenticated API client.
    input_dir : str
        Directory to scan recursively for FASTQ read files.
    regex_pattern : str
        Regex with two capture groups:
          group 1 = sample name, group 2 = read direction (1 or 2).
    folder_id : int
        ID of the PathogenWatch folder that will own the uploaded genomes.

    Returns
    -------
    list of (sample_name, genome_id) tuples
        genome_id is the integer ID returned by /api/genomes/create.
    """
    pairs: List[Tuple[Path, Path]] = find_pairs(input_dir, regex_pattern)

    if not pairs:
        print(
            f"[upload] No paired-end files found in '{input_dir}' "
            f"matching regex: {regex_pattern}"
        )
        return []

    print(f"[upload] Found {len(pairs)} sample pair(s). Folder ID: {folder_id}\n")

    results: List[Tuple[str, int]] = []
    failed: List[str] = []

    for idx, (r1, r2) in enumerate(pairs, start=1):
        sample_name = _common_stem(r1.name, r2.name)
        print(f"[upload] ({idx}/{len(pairs)}) '{sample_name}'")
        print(f"  R1: {r1}")
        print(f"  R2: {r2}")

        try:
            # Steps 1-3: store each file and obtain permanent URLs
            r1_url = client.store_and_get_url(r1)
            r2_url = client.store_and_get_url(r2)

            # Step 4: register the genome
            genome = client.create_genome(
                folder_id=folder_id,
                r1_url=r1_url,
                r2_url=r2_url,
                name=sample_name,
            )
            genome_id: int = genome["id"]
            print(f"  → Genome registered. ID: {genome_id}  UUID: {genome.get('uuid', '')}\n")
            results.append((sample_name, genome_id))

        except RuntimeError as exc:
            print(f"  → ERROR: {exc}\n")
            failed.append(sample_name)

    # Summary
    print("─" * 60)
    print(f"[upload] Done.  {len(results)}/{len(pairs)} sample(s) uploaded successfully.")
    if failed:
        print(f"[upload] Failed: {', '.join(failed)}")
    print("─" * 60)

    return results


def _common_stem(name1: str, name2: str) -> str:
    """
    Return the longest common prefix of two filenames, stripped of
    trailing underscores, dashes, or dots.

    Examples
    --------
    >>> _common_stem("SampleA_R1_001.fastq.gz", "SampleA_R2_001.fastq.gz")
    'SampleA'
    """
    common = []
    for a, b in zip(name1, name2):
        if a == b:
            common.append(a)
        else:
            break
    stem = "".join(common).rstrip("_-.")
    return stem if stem else name1.split(".")[0]


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------

_STRIP_EXTS = {
    ".gz", ".tar",
    ".fa", ".fas", ".fna", ".ffn", ".faa", ".frn",
    ".fasta", ".genome", ".contig", ".dna", ".mfa", ".mga", ".csv",
    ".fastq", ".fq",
}


def _assembly_stem(filename: str) -> str:
    """Strip all known genome/compression suffixes to derive a sample name.

    Examples
    --------
    >>> _assembly_stem("SampleA.fna.gz")
    'SampleA'
    >>> _assembly_stem("SampleA.csv.tar.gz")
    'SampleA'
    """
    p = Path(filename)
    while p.suffix.lower() in _STRIP_EXTS:
        p = p.with_suffix("")
    return p.name or Path(filename).stem


@contextmanager
def _temp_decompressed(file_path: Path) -> Generator[List[Path], None, None]:
    """
    Context-manager that yields a list of paths ready to upload.

    * Plain assembly file  → yields ``[file_path]`` (no copy made).
    * ``.gz`` file         → gunzips to one temp file, yields it, deletes on exit.
    * ``.tar.gz`` file     → extracts supported members to a temp dir,
                             yields each, deletes the dir on exit.

    If the inner format is not in SUPPORTED_ASSEMBLY_EXTS the context yields
    an empty list and prints a warning.
    """
    name_lower = file_path.name.lower()
    temp_paths: List[Path] = []
    temp_dir: str = ""

    try:
        if name_lower.endswith(".tar.gz"):
            temp_dir = tempfile.mkdtemp(prefix="pwcli_")
            with tarfile.open(file_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    # Security: reject path-traversal members
                    parts = Path(member.name).parts
                    if any(part == ".." for part in parts) or Path(member.name).is_absolute():
                        print(f"  [warn] Skipping unsafe archive entry: {member.name}")
                        continue
                    inner_ext = Path(member.name).suffix.lstrip(".").lower()
                    if inner_ext not in SUPPORTED_ASSEMBLY_EXTS:
                        continue
                    out_path = Path(temp_dir) / Path(member.name).name
                    fobj = tar.extractfile(member)
                    if fobj is None:
                        continue
                    with fobj, open(out_path, "wb") as fout:
                        shutil.copyfileobj(fobj, fout)
                    temp_paths.append(out_path)
            if not temp_paths:
                print(f"  [warn] No supported files found inside {file_path.name}")
            yield temp_paths

        elif name_lower.endswith(".gz"):
            inner_name = file_path.name[:-3]          # strip ".gz"
            inner_ext = Path(inner_name).suffix.lstrip(".").lower()
            if inner_ext not in SUPPORTED_ASSEMBLY_EXTS:
                print(
                    f"  [warn] Inner format '.{inner_ext}' is not a supported assembly "
                    f"format after decompressing '{file_path.name}' – skipping."
                )
                yield []
            else:
                # Decompress into a temp dir using the original inner filename
                temp_dir = tempfile.mkdtemp(prefix="pwcli_")
                tmp_path = Path(temp_dir) / inner_name
                temp_paths.append(tmp_path)
                with gzip.open(file_path, "rb") as fin, open(tmp_path, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                yield temp_paths

        else:
            # Not compressed – upload direct, no cleanup needed
            yield [file_path]

    finally:
        for p in temp_paths:
            try:
                p.unlink()
            except OSError:
                pass
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def upload_assembly_genomes(
    client: PathogenWatchClient,
    input_dir: str,
    extensions: List[str],
    folder_id: int,
) -> List[Tuple[str, int]]:
    """
    Discover assembly/genome files in *input_dir* (supports glob patterns),
    decompress when necessary, then register each file as a genome.

    Parameters
    ----------
    client : PathogenWatchClient
    input_dir : str
        Directory to scan, or a **quoted** glob pattern (e.g. ``'/data/*/*'``).
    extensions : list of str
        Extensions to look for, e.g. ``['fa', 'fna', 'fa.gz']``.
    folder_id : int
        Destination folder ID in PathogenWatch.

    Returns
    -------
    list of (sample_name, genome_id) tuples
    """
    files = find_assembly_files(input_dir, extensions)

    if not files:
        print(
            f"[upload] No assembly files found in '{input_dir}' "
            f"with extension(s): {', '.join(extensions)}"
        )
        return []

    print(f"[upload] Found {len(files)} assembly file(s). Folder ID: {folder_id}\n")

    results: List[Tuple[str, int]] = []
    failed: List[str] = []

    for idx, file_path in enumerate(files, start=1):
        print(f"[upload] ({idx}/{len(files)}) {file_path.name}")
        print(f"  Path: {file_path}")

        with _temp_decompressed(file_path) as actual_paths:
            if not actual_paths:
                print("  → SKIPPED (no supported content after decompression)\n")
                failed.append(file_path.name)
                continue

            for actual_path in actual_paths:
                sample_name = _assembly_stem(actual_path.name)
                try:
                    genome = client.create_assembly_genome(
                        folder_id=folder_id,
                        file_path=actual_path,
                        name=sample_name,
                    )
                    genome_id: int = genome["id"]
                    print(
                        f"  → Genome registered. ID: {genome_id}  "
                        f"UUID: {genome.get('uuid', '')}\n"
                    )
                    results.append((sample_name, genome_id))
                except RuntimeError as exc:
                    print(f"  → ERROR: {exc}\n")
                    failed.append(sample_name)

    print("─" * 60)
    print(f"[upload] Done.  {len(results)} genome(s) uploaded from {len(files)} file(s).")
    if failed:
        print(f"[upload] Failed / skipped: {', '.join(failed)}")
    print("─" * 60)

    return results
