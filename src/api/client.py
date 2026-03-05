"""
client.py - Low-level HTTP wrapper around the PathogenWatch REST API.

Upload workflow (per OpenAPI spec):
  Step 1) Compute SHA-1 checksum of each FASTQ file.
  Step 2) POST /api/genomes/store?checksum=<sha1>&type=reads
            → { fileUrl, upload (bool), uploadUrl? }
  Step 3) If upload=True  → PUT file bytes to uploadUrl (signed object-storage URL).
  Step 4) POST /api/genomes/create  { folderId, reads: [fileUrl_r1, fileUrl_r2], name }
            → { id (int), uuid }

Collection workflow:
  POST /api/collections/create  { name, description, organismId, genomeIds: [int] }
    → { id, url, name, description, uuid }

Folder workflow:
  POST /api/folders/create  { name }
    → { id, url, uuid, name }

Authentication: X-API-Key header (APIKeyHeader scheme).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

import requests

from .auth import AuthSession


class PathogenWatchClient:
    """
    Wraps every PathogenWatch REST call.
    All methods raise RuntimeError on non-2xx responses.
    """

    def __init__(self, api_key: str, base_url: str, timeout: int = 120):
        self._auth = AuthSession(api_key, base_url)
        self._session = self._auth.session
        self._base = self._auth.base_url
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_for_status(self, response: requests.Response, context: str) -> None:
        if not response.ok:
            raise RuntimeError(
                f"[{context}] HTTP {response.status_code}: {response.text[:600]}"
            )

    @staticmethod
    def _sha1(file_path: Path) -> str:
        """Return the hex SHA-1 digest of a file."""
        h = hashlib.sha1()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def create_folder(self, name: str) -> dict:
        """
        POST /api/folders/create
        Returns: { id, url, uuid, name }
        """
        resp = self._session.post(
            f"{self._base}/api/folders/create",
            json={"name": name},
            timeout=self._timeout,
        )
        self._raise_for_status(resp, "create_folder")
        return resp.json()

    # ------------------------------------------------------------------
    # Genome upload (3-step: store → PUT → create)
    # ------------------------------------------------------------------

    def _store_file(self, file_path: Path, checksum: str, file_type: str = "reads") -> dict:
        """
        Step 2: POST /api/genomes/store?checksum=<sha1>&type=<file_type>
        file_type is either "reads" (FASTQ pairs) or "assembly" (FASTA/CSV/etc.).
        Returns { fileUrl, upload (bool), uploadUrl? }
        """
        resp = self._session.post(
            f"{self._base}/api/genomes/store",
            params={"checksum": checksum, "type": file_type},
            timeout=self._timeout,
        )
        self._raise_for_status(resp, f"genomes/store [{file_path.name}]")
        return resp.json()

    def _put_file(self, signed_url: str, file_path: Path) -> None:
        """
        Step 3: PUT the file bytes directly to the signed object-storage URL.
        This call goes to a CDN/storage provider, not to PathogenWatch itself,
        so we use a plain requests.put (no auth headers needed).
        """
        with open(file_path, "rb") as fh:
            put_resp = requests.put(
                signed_url,
                data=fh,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self._timeout,
            )
        if not put_resp.ok:
            raise RuntimeError(
                f"[put_file] Signed-URL PUT failed for {file_path.name}: "
                f"HTTP {put_resp.status_code}: {put_resp.text[:400]}"
            )

    def store_and_get_url(self, file_path: Path) -> str:
        """
        Execute steps 1-3 for a single file:
          compute SHA-1 → store → conditional PUT.
        Returns the permanent fileUrl to pass to create_genome.
        """
        print(f"    [store] {file_path.name} – computing checksum …")
        checksum = self._sha1(file_path)

        store_info = self._store_file(file_path, checksum)
        file_url: str = store_info["fileUrl"]

        if store_info.get("upload", False):
            signed_url = store_info.get("uploadUrl", "")
            if not signed_url:
                raise RuntimeError(
                    f"[store] upload=True but no uploadUrl returned for {file_path.name}"
                )
            print(f"    [store] {file_path.name} – uploading to object storage …")
            self._put_file(signed_url, file_path)
        else:
            print(f"    [store] {file_path.name} – already in storage, skipping upload.")

        return file_url

    def create_genome(
        self,
        folder_id: int,
        r1_url: str,
        r2_url: str,
        name: Optional[str] = None,
    ) -> dict:
        """
        Step 4: POST /api/genomes/create
        Payload: { folderId, reads: [r1_url, r2_url], name? }
        Returns: { id (int), uuid }
        """
        payload: dict = {
            "folderId": folder_id,
            "reads": [r1_url, r2_url],
        }
        if name:
            payload["name"] = name

        resp = self._session.post(
            f"{self._base}/api/genomes/create",
            json=payload,
            timeout=self._timeout,
        )
        self._raise_for_status(resp, "genomes/create")
        return resp.json()

    def create_assembly_genome(
        self,
        folder_id: int,
        file_path: Path,
        name: Optional[str] = None,
    ) -> dict:
        """
        Upload a single assembly file (FASTA, CSV, etc.) to PathogenWatch.

        Executes steps 1-3 internally:
          SHA-1  →  POST /api/genomes/store?type=assembly  →  conditional PUT
        Then registers the genome with the checksum reference:
          POST /api/genomes/create { folderId, checksum, name? }

        Returns: { id (int), uuid }
        """
        print(f"    [store] {file_path.name} – computing checksum …")
        checksum = self._sha1(file_path)

        store_info = self._store_file(file_path, checksum, file_type="assembly")

        if store_info.get("upload", False):
            signed_url = store_info.get("uploadUrl", "")
            if not signed_url:
                raise RuntimeError(
                    f"[store] upload=True but no uploadUrl returned for {file_path.name}"
                )
            print(f"    [store] {file_path.name} – uploading to object storage …")
            self._put_file(signed_url, file_path)
        else:
            print(f"    [store] {file_path.name} – already in storage, skipping upload.")

        payload: dict = {"folderId": folder_id, "checksum": checksum}
        if name:
            payload["name"] = name

        resp = self._session.post(
            f"{self._base}/api/genomes/create",
            json=payload,
            timeout=self._timeout,
        )
        self._raise_for_status(resp, "genomes/create")
        return resp.json()

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def create_collection(
        self,
        name: str,
        genome_ids: List[int],
        organism_id: str,
        description: str = "",
    ) -> dict:
        """
        POST /api/collections/create
        Payload: { name, genomeIds: [int], organismId, description? }
        Returns: { id, url, name, description, uuid }
        """
        payload = {
            "name": name,
            "genomeIds": genome_ids,
            "organismId": organism_id,
        }
        if description:
            payload["description"] = description

        resp = self._session.post(
            f"{self._base}/api/collections/create",
            json=payload,
            timeout=self._timeout,
        )
        self._raise_for_status(resp, "collections/create")
        return resp.json()
