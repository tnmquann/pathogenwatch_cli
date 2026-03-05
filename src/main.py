"""
PathogenWatch CLI
=================
Automate bulk uploads and collection creation via the PathogenWatch REST API.

Upload workflow (per openapi.json):
  1. Compute SHA-1 checksum of each FASTQ file.
  2. POST /api/genomes/store       → get signed object-storage URL.
  3. PUT file to signed URL        → store in object storage.
  4. POST /api/genomes/create      → register genome in PathogenWatch.

Then create collection(s) from the registered genome IDs.

Collection modes
----------------
  per_sample   One collection per uploaded sample (named after the sample,
               or "<prefix> – <sample>" when --collection_name is given).
  all          One combined collection for ALL uploaded samples
               (requires --collection_name).
  none         Skip collection creation.

Authentication: X-API-Key header.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

_SRC_DIR = os.path.abspath(os.path.dirname(__file__))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in (_SRC_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from api.client import PathogenWatchClient
from commands.upload import upload_genomes, upload_assembly_genomes
from commands.collection import (
    create_per_sample_collections,
    create_combined_collection,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pathogenwatch-cli",
        description="Bulk-upload Illumina reads and manage collections on PathogenWatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Authentication ─────────────────────────────────────────────────────
    parser.add_argument(
        "--api_key",
        default=None,
        help=(
            "PathogenWatch API key (sent as X-API-Key header). "
            "Falls back to API_KEY in config.py."
        ),
    )

    # ── Upload options ──────────────────────────────────────────────────────
    upload_grp = parser.add_argument_group("Upload options")
    upload_grp.add_argument(
        "--input_dir",
        type=str,
        help=(
            "Directory to scan for genome files, or a quoted glob pattern "
            "such as '/data/reads/*/*'."
        ),
    )
    upload_grp.add_argument(
        "--file_type",
        type=str,
        default="short_read_assembly",
        help=(
            "Type of genome files to upload. "
            "Use 'short_read_assembly' (default) for paired-end Illumina reads "
            "(_{1,2,R1,R2}.{fastq,fq}.gz). "
            "For assembled genomes, provide a comma-separated list of file extensions, "
            "e.g. 'fa,fna,fasta' or 'fa,fa.gz,fna.gz,csv.tar.gz'. "
            "Compressed extensions (.gz, .tar.gz) are decompressed automatically "
            "before upload when the inner format is a supported assembly type "
            "(fa, fas, fna, ffn, faa, frn, fasta, genome, contig, dna, mfa, mga, csv)."
        ),
    )
    upload_grp.add_argument(
        "--regex",
        type=str,
        default=None,
        help=(
            "Regular expression to match read filenames "
            "(only used with --file_type short_read_assembly). "
            "Must contain TWO capture groups: (1) sample name, (2) read direction (1 or 2). "
            f"Default: r'{config.DEFAULT_REGEX}'"
        ),
    )

    # ── Folder options ──────────────────────────────────────────────────────
    folder_grp = parser.add_argument_group("Folder options (required when uploading)")
    folder_ex = folder_grp.add_mutually_exclusive_group()
    folder_ex.add_argument(
        "--folder_id",
        type=int,
        help="ID of an existing PathogenWatch folder to upload genomes into.",
    )
    folder_ex.add_argument(
        "--folder_name",
        type=str,
        help="Create a new folder with this name and upload genomes into it.",
    )

    # ── Collection options ─────────────────────────────────────────────────
    col_grp = parser.add_argument_group("Collection options")
    col_grp.add_argument(
        "--collection_mode",
        choices=["per_sample", "all", "none"],
        default="all",
        help=(
            "Collection creation strategy. "
            "'per_sample': one collection per uploaded sample. "
            "'all': one combined collection for all samples (requires --collection_name). "
            "'none': skip collection creation entirely. "
            "Default: all"
        ),
    )
    col_grp.add_argument(
        "--collection_name",
        "--create_collection",
        dest="collection_name",
        type=str,
        default=None,
        help=(
            "Name for the collection. "
            "Required for mode 'all'. "
            "Optional prefix for mode 'per_sample' (result: '<prefix> – <sample>')."
        ),
    )
    col_grp.add_argument(
        "--organism_id",
        type=str,
        default=None,
        help=(
            "NCBI taxonomy ID of the organism "
            "(e.g. '28901' for Salmonella enterica, '1280' for S. aureus). "
            "Required when creating a collection."
        ),
    )
    col_grp.add_argument(
        "--description",
        type=str,
        default="",
        help="Optional free-text description for the collection.",
    )
    col_grp.add_argument(
        "--genome_ids",
        type=str,
        default=None,
        help=(
            "Comma-separated integer genome IDs to include in the collection "
            "(bypasses the upload step). Example: --genome_ids 101,102,103"
        ),
    )

    return parser


def resolve_folder_id(
    client: PathogenWatchClient,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> int:
    """Return a valid folder ID, creating a new folder when --folder_name was given."""
    if args.folder_id:
        print(f"[main] Using existing folder ID: {args.folder_id}")
        return args.folder_id
    if args.folder_name:
        print(f"[main] Creating folder '{args.folder_name}' …")
        result = client.create_folder(args.folder_name)
        folder_id = int(result["id"])
        print(f"[main] Folder created. ID: {folder_id}  URL: {result.get('url', '')}")
        return folder_id
    parser.error(
        "When using --input_dir you must specify either "
        "--folder_id (existing) or --folder_name (new)."
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve API key: CLI flag > config.py
    api_key = args.api_key or config.API_KEY
    if api_key == "MY_API_KEY":
        parser.error(
            "No API key provided. Use --api_key <key> or set API_KEY in config.py."
        )

    # Build client and verify the key
    client = PathogenWatchClient(
        api_key=api_key,
        base_url=config.BASE_URL,
        timeout=config.TIMEOUT,
    )
    client._auth.verify()

    regex = args.regex or config.DEFAULT_REGEX

    # ── Require at least one source of genomes ────────────────────────────
    if not args.input_dir and not args.genome_ids:
        parser.print_help()
        print(
            "\n[error] Nothing to do.\n"
            "  Use --input_dir <dir> to upload reads from a directory, or\n"
            "  use --genome_ids <id1,id2,...> to create a collection from existing genomes."
        )
        sys.exit(1)

    # ── Step 1: Upload genomes ────────────────────────────────────────────
    # List of (sample_name: str, genome_id: int)
    upload_results = []

    if args.input_dir:
        folder_id = resolve_folder_id(client, args, parser)
        if args.file_type == "short_read_assembly":
            upload_results = upload_genomes(
                client=client,
                input_dir=args.input_dir,
                regex_pattern=regex,
                folder_id=folder_id,
            )
        else:
            extensions = [e.strip() for e in args.file_type.split(",") if e.strip()]
            upload_results = upload_assembly_genomes(
                client=client,
                input_dir=args.input_dir,
                extensions=extensions,
                folder_id=folder_id,
            )

    # ── Step 2: Merge any manually supplied genome IDs ────────────────────
    if args.genome_ids:
        for raw_id in args.genome_ids.split(","):
            raw_id = raw_id.strip()
            if raw_id:
                upload_results.append((f"genome_{raw_id}", int(raw_id)))

    # ── Save genome IDs to a JSON file for later reference ─────────────────
    if upload_results and args.input_dir:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(os.getcwd(), f"pathogenwatch_upload_{ts}.json")
        payload = {
            "genome_ids": [gid for _, gid in upload_results],
            "samples": [
                {"name": name, "genome_id": gid} for name, gid in upload_results
            ],
        }
        with open(save_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        ids_str = ",".join(str(gid) for _, gid in upload_results)
        print(f"[main] Genome IDs saved → {save_path}")
        print(f"[main] To retry collection creation later:")
        print(f"[main]   --genome_ids {ids_str}")

    # ── Assembly uploads: skip collection creation (genomes need indexing time) ──
    # Only short_read_assembly goes through PathogenWatch's pipeline; assembly
    # files (FASTA etc.) also require server-side indexing before a collection
    # can be created.  Instruct the user to retry with --genome_ids once ready.
    if args.input_dir and args.file_type != "short_read_assembly":
        if args.collection_mode != "none":
            ids_str = ",".join(str(gid) for _, gid in upload_results)
            print(
                "\n[main] Collection creation skipped after assembly upload.\n"
                "[main] PathogenWatch needs time to index the genomes.\n"
                "[main] Once ready, create the collection with:\n"
                f"[main]   python src/main.py --api_key {args.api_key or 'YOUR_KEY'} \\"
            )
            print(
                f"[main]     --genome_ids {ids_str} \\"
            )
            print(
                f"[main]     --collection_mode {args.collection_mode} "
                + (f"--collection_name '{args.collection_name}' \\" if args.collection_name else "")
            )
            if args.organism_id:
                print(f"[main]     --organism_id {args.organism_id}")
        return

    # ── Step 3: Create collection(s) ──────────────────────────────────────
    if args.collection_mode == "none":
        print("[main] Collection mode: none – done.")
        return

    if not upload_results:
        print("[main] No genomes available – skipping collection creation.")
        return

    if not args.organism_id:
        parser.error(
            "--organism_id is required when creating a collection "
            "(e.g. --organism_id 28901 for Salmonella enterica)."
        )

    if args.collection_mode == "per_sample":
        create_per_sample_collections(
            client=client,
            upload_results=upload_results,
            organism_id=args.organism_id,
            name_prefix=args.collection_name,
            description=args.description,
        )

    elif args.collection_mode == "all":
        if not args.collection_name:
            parser.error(
                "--collection_name is required when --collection_mode=all."
            )
        create_combined_collection(
            client=client,
            upload_results=upload_results,
            collection_name=args.collection_name,
            organism_id=args.organism_id,
            description=args.description,
        )


if __name__ == "__main__":
    main()
