"""
file_scanner.py - Scan a directory for paired-end Illumina FASTQ files.

Conventions supported
---------------------
The default regex expects filenames like:
  SampleA_R1.fastq.gz  /  SampleA_R2.fastq.gz
  SampleA_R1_001.fastq.gz  /  SampleA_R2_001.fastq.gz
  SampleA_1.fastq.gz  /  SampleA_2.fastq.gz

Capture group 1 = sample name, capture group 2 = read direction ("1" or "2").
You can override this with --regex on the command line.
"""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# Supported uncompressed assembly / genome formats recognised by PathogenWatch
# ---------------------------------------------------------------------------
SUPPORTED_ASSEMBLY_EXTS: Set[str] = {
    "fa", "fas", "fna", "ffn", "faa", "frn",
    "fasta", "genome", "contig", "dna", "mfa", "mga", "csv",
}


def _iter_files(input_dir: str) -> List[Path]:
    """
    Return a flat sorted list of all files under *input_dir*.
    When *input_dir* contains shell-glob characters (``*``, ``?``, ``[``),
    the pattern is expanded first; each matching entry is then walked
    recursively if it is a directory.
    """
    if any(c in input_dir for c in ("*", "?", "[")):
        expanded = _glob.glob(input_dir, recursive=True)
        if not expanded:
            print(f"[scanner] WARNING: No paths matched glob pattern '{input_dir}'")
            return []
        collected: List[Path] = []
        for entry in expanded:
            ep = Path(entry)
            if ep.is_file():
                collected.append(ep)
            elif ep.is_dir():
                collected.extend(p for p in ep.rglob("*") if p.is_file())
        return sorted(set(collected))
    else:
        root = Path(input_dir)
        if not root.is_dir():
            raise ValueError(f"Input directory not found: {root}")
        return sorted(p for p in root.rglob("*") if p.is_file())


def _ext_matches(filename: str, exts: Set[str]) -> bool:
    """Return True if *filename* (case-insensitive) ends with '.<ext>' for any ext in *exts*."""
    name_lower = filename.lower()
    return any(name_lower.endswith("." + ext) for ext in exts)


def find_assembly_files(
    input_dir: str,
    extensions: List[str],
) -> List[Path]:
    """
    Find assembly/genome files matching *extensions* under *input_dir*.

    Parameters
    ----------
    input_dir : str
        Directory to scan, or a **quoted** glob pattern such as
        ``'/data/reads/*/*'``.  Matching is recursive.
    extensions : list of str
        File extensions to accept, e.g. ``['fa', 'fna', 'fa.gz']``.
        Leading dots are stripped; matching is case-insensitive.

    Returns
    -------
    Sorted list of matching :class:`~pathlib.Path` objects.
    """
    exts: Set[str] = {e.strip().lstrip(".").lower() for e in extensions if e.strip()}
    if not exts:
        return []
    return [p for p in _iter_files(input_dir) if _ext_matches(p.name, exts)]


def find_pairs(
    input_dir: str,
    regex_pattern: str,
) -> List[Tuple[Path, Path]]:
    """
    Walk *input_dir* recursively and collect paired-end FASTQ files whose
    names match *regex_pattern*.

    The regex MUST contain exactly two capturing groups:
      - Group 1: sample name  (used to link R1 with R2)
      - Group 2: read direction, either "1" or "2"

    Parameters
    ----------
    input_dir : str
        Root directory to search.
    regex_pattern : str
        Regular expression applied to each filename (not the full path).

    Returns
    -------
    list of (r1_path, r2_path) tuples, sorted by sample name.
    Samples missing one of the two reads are reported and excluded.
    """
    pattern = re.compile(regex_pattern)

    # sample_name -> {"1": Path, "2": Path}
    samples: Dict[str, Dict[str, Path]] = {}

    for path in _iter_files(input_dir):
        m = pattern.search(path.name)
        if not m:
            continue
        if len(m.groups()) < 2:
            raise ValueError(
                f"Regex '{regex_pattern}' must contain at least 2 capture groups "
                "(sample name and read direction)."
            )
        sample_name, read_dir = m.group(1), m.group(2)
        samples.setdefault(sample_name, {})[read_dir] = path

    pairs: List[Tuple[Path, Path]] = []
    for sample, reads in sorted(samples.items()):
        r1 = reads.get("1")
        r2 = reads.get("2")
        if r1 is None or r2 is None:
            missing = "R1" if r1 is None else "R2"
            print(f"[scanner] WARNING: {sample!r} - {missing} missing, skipping.")
            continue
        pairs.append((r1, r2))

    return pairs
