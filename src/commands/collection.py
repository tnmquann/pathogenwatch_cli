"""
collection.py - Create PathogenWatch collections from uploaded genome IDs.

Supports two modes (controlled by --collection_mode):

  per_sample  Create one separate collection for each uploaded sample.
              Collection name = sample name (optionally prefixed with
              --collection_name if provided: "<prefix> – <sample>").

  all         Create a single collection that contains all uploaded samples.
              Requires --collection_name.

API reference (openapi.json):
  POST /api/collections/create
    Body:  { name, genomeIds: [int], organismId, description? }
    201:   { id, url, name, description, uuid }

Note on timing
--------------
Genomes submitted as raw reads go through an assembly pipeline (15-45 min).
PathogenWatch may return HTTP 400 "One or more genomes were not found" if the
genomes have not been indexed yet.  Both create functions below retry with
exponential back-off and print instructions to retry later if all attempts fail.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from api.client import PathogenWatchClient

# Type alias: list of (sample_name, genome_id) returned by upload.
UploadResult = List[Tuple[str, int]]

# Retry configuration for collection creation
_RETRY_DELAYS = [10, 30, 60]   # seconds between attempts (3 retries)


def _create_with_retry(
    client: PathogenWatchClient,
    name: str,
    genome_ids: List[int],
    organism_id: str,
    description: str,
) -> Optional[dict]:
    """
    Call client.create_collection with retry/back-off on HTTP 400.
    Returns the response dict on success, or None after all retries are exhausted.
    """
    all_delays = _RETRY_DELAYS
    attempts = 1 + len(all_delays)

    for attempt in range(1, attempts + 1):
        try:
            return client.create_collection(
                name=name,
                genome_ids=genome_ids,
                organism_id=organism_id,
                description=description,
            )
        except RuntimeError as exc:
            msg = str(exc)
            is_last = attempt == attempts
            if "400" in msg and not is_last:
                wait = all_delays[attempt - 1]
                print(
                    f"  [retry] Attempt {attempt}/{attempts} failed (genomes not ready yet). "
                    f"Retrying in {wait}s …"
                )
                time.sleep(wait)
            else:
                print(f"  → ERROR: {exc}")
                return None

    return None


# -------------------------------------------------------------------------
# Mode: per_sample
# -------------------------------------------------------------------------

def create_per_sample_collections(
    client: PathogenWatchClient,
    upload_results: UploadResult,
    organism_id: str,
    name_prefix: Optional[str] = None,
    description: str = "",
) -> List[dict]:
    """
    Create one collection per uploaded sample.

    Parameters
    ----------
    client : PathogenWatchClient
    upload_results : list of (sample_name, genome_id)
    organism_id : str
        NCBI taxonomy ID of the organism (e.g. "28901" for S. enterica).
    name_prefix : str, optional
        If provided, each collection is named "<name_prefix> – <sample_name>".
        Otherwise the collection is simply named after the sample.
    description : str, optional

    Returns
    -------
    list of API response dicts, one per created collection.
    """
    if not upload_results:
        print("[collection] No uploaded genomes to create collections for.")
        return []

    print(f"\n[collection] Mode: per_sample – creating {len(upload_results)} collection(s).")
    created = []

    for sample_name, genome_id in upload_results:
        col_name = f"{name_prefix} – {sample_name}" if name_prefix else sample_name
        print(f"  Creating collection '{col_name}' (genome ID: {genome_id}) …")
        result = _create_with_retry(
            client=client,
            name=col_name,
            genome_ids=[genome_id],
            organism_id=organism_id,
            description=description,
        )
        if result is not None:
            print(f"  → OK  {result.get('url', '')}")
            created.append(result)

    print(f"\n[collection] {len(created)}/{len(upload_results)} per-sample collection(s) created.")
    return created


# -------------------------------------------------------------------------
# Mode: all
# -------------------------------------------------------------------------

def create_combined_collection(
    client: PathogenWatchClient,
    upload_results: UploadResult,
    collection_name: str,
    organism_id: str,
    description: str = "",
) -> Optional[dict]:
    """
    Create a single collection containing all uploaded genomes.

    Parameters
    ----------
    client : PathogenWatchClient
    upload_results : list of (sample_name, genome_id)
    collection_name : str
        Name for the combined collection.
    organism_id : str
        NCBI taxonomy ID of the organism.
    description : str, optional

    Returns
    -------
    API response dict, or None on failure.
    """
    if not upload_results:
        print("[collection] No uploaded genomes – skipping collection creation.")
        return None

    genome_ids = [gid for _, gid in upload_results]
    print(
        f"\n[collection] Mode: all – creating 1 collection "
        f"'{collection_name}' with {len(genome_ids)} genome(s) …"
    )

    result = _create_with_retry(
        client=client,
        name=collection_name,
        genome_ids=genome_ids,
        organism_id=organism_id,
        description=description,
    )
    if result is not None:
        print(f"[collection] → OK  {result.get('url', '')}")
    return result
