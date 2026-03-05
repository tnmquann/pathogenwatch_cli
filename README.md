# PathogenWatch CLI

## Overview

**PathogenWatch CLI** is a command-line tool for bulk-uploading genome data and creating collections in [PathogenWatch](https://pathogen.watch). It supports:

- **Short-read (Illumina) uploads** — paired-end FASTQ files with automatic assembly pipeline integration
- **Assembly file uploads** — FASTA, CSV, and other supported genome formats
- **Compressed file handling** — automatic decompression of `.gz` and `.tar.gz` archives
- **Glob pattern support** — recursively scan directories using shell-style patterns
- **Flexible collection creation** — per-sample or combined collections with retry logic for indexing delays

## Features

- Upload paired-end Illumina reads (`short_read_assembly` mode)
- Upload assembled genomes (FASTA, CSV, etc.)
- Automatic decompression (`.gz`, `.tar.gz`) while preserving original filenames
- Glob pattern support in `--input_dir` (e.g., `/data/reads/*/*`)
- Regex-based file filtering for short-read mode
- Create collections per-sample, combined, or skip entirely
- Automatic retry with exponential back-off for indexing delays
- Save genome IDs to JSON for recovery and manual retries
- One command-line tool, no configuration files needed

## Installation

### Prerequisites
- Python 3.8 or later
- `requests>=2.28.0` (standard library dependencies only)

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/pathogenwatch-cli.git
   cd pathogenwatch-cli
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Obtain your API key:**
   - Log in to [PathogenWatch](https://next.pathogen.watch)
   - Navigate to your account settings
   - Generate or copy your API key

4. **(Optional) Set the API key in config.py:**
   ```python
   # config.py
   API_KEY = "your-api-key-here"
   ```
   If set here, you can omit `--api_key` from command-line arguments.

## Usage

### Basic Command Structure

```bash
python src/main.py \
    --api_key YOUR_API_KEY \
    --input_dir /path/to/files \
    --file_type {short_read_assembly|fa,fna,fasta|...} \
    --folder_name "Project Name" \
    --collection_mode {per_sample|all|none} \
    --collection_name "Collection Name" \
    --organism_id NCBI_TAXONOMY_ID
```

### Supported File Types

**Short-read (Illumina) assembly:**
```bash
--file_type short_read_assembly  # Default — matches *.fastq.gz, *.fq.gz, etc.
```
Regex pattern: `^(.+?)_R?(1|2)(?:_\d+)?\.f(?:astq|q)(?:\.gz)?$`

**Assembled genomes** (comma-separated extensions):
```bash
--file_type fa,fna,fasta           # FASTA files
--file_type fa.gz,fna.gz,fasta.gz  # Gzipped FASTA files
--file_type fa,fa.gz,csv.tar.gz    # Mixed formats
```

Supported uncompressed formats: `.fa, .fas, .fna, .ffn, .faa, .frn, .fasta, .genome, .contig, .dna, .mfa, .mga, .csv`

---

## Examples

### Example 1: Upload paired-end Illumina reads and create one collection per sample

```bash
python src/main.py \
    --api_key MY_API_KEY \
    --input_dir /data/illumina_reads \
    --folder_name "Salmonella Outbreak Q1" \
    --collection_mode per_sample \
    --collection_name "Outbreak Q1" \
    --organism_id 28901
```

**Result:**
- Scans `/data/illumina_reads` recursively for paired-end FASTQ files
- Creates folder "Salmonella Outbreak Q1" in PathogenWatch
- Uploads all read pairs
- Creates **one collection per sample** named "Outbreak Q1 – SampleA", "Outbreak Q1 – SampleB", etc.
- Saves genome IDs to `pathogenwatch_upload_*.json`

---

### Example 2: Upload assembled genomes with a glob pattern

```bash
python src/main.py \
    --api_key MY_API_KEY \
    --input_dir '/data/results/*/assemblies' \
    --file_type fa,fna,fasta \
    --folder_id 42 \
    --collection_mode all \
    --collection_name "Reference Genomes" \
    --organism_id 562
```

**Result:**
- Expands glob pattern `/data/results/*/assemblies` recursively
- Finds all `.fa`, `.fna`, `.fasta` files
- Uploads to existing folder (ID: 42)
- Creates **one combined collection** named "Reference Genomes"
- **Note:** Collection creation is skipped; see below to retry with `--genome_ids`

---

### Example 3: Upload compressed assembly files (mixed formats)

```bash
python src/main.py \
    --api_key MY_API_KEY \
    --input_dir /data/genomes \
    --file_type fa.gz,fna.gz,csv.tar.gz \
    --folder_name "Hybrid Assemblies" \
    --collection_mode none
```

**Result:**
- Finds `.fa.gz`, `.fna.gz`, and `.csv.tar.gz` files
- Decompresses to temp files/dirs, preserving original filenames
- Uploads using the original filenames (e.g., `GCA_000005845.2.fna`)
- **No collection created** (mode: `none`)
- Genome IDs saved for later use

---

### Example 4: Create a collection from previously uploaded genomes

```bash
python src/main.py \
    --api_key MY_API_KEY \
    --genome_ids 3315366,3315367,3315368 \
    --collection_mode all \
    --collection_name "My Collection" \
    --organism_id 573
```

**Result:**
- Skips upload entirely
- Creates a single collection named "My Collection" containing genome IDs 3315366, 3315367, 3315368
- With retry logic if genomes aren't indexed yet

---

## Collection Modes

### `per_sample`
Creates **one collection per uploaded sample**.
- Each collection named after the sample
- Optionally prefix with `--collection_name`
- **Result names:** `Sample1`, `Sample2`, ... or `Prefix – Sample1`, `Prefix – Sample2`, ...

```bash
python src/main.py \
    --input_dir /data/reads \
    --file_type short_read_assembly \
    --folder_name "MyProject" \
    --collection_mode per_sample \
    --collection_name "Outbreak May24" \  # Optional prefix
    --organism_id 28901 \
    --api_key MY_API_KEY
```

### `all`
Creates **one combined collection** for all uploaded samples.
- **Requires** `--collection_name`

```bash
python src/main.py \
    --input_dir /data/reads \
    --file_type short_read_assembly \
    --folder_name "MyProject" \
    --collection_mode all \
    --collection_name "Salmonella Q1 2024" \
    --organism_id 28901 \
    --api_key MY_API_KEY
```

### `none`
Skips collection creation entirely.
- Useful when you only want to upload and organize genomes in folders
- Save genome IDs to JSON for later collection creation with `--genome_ids`

```bash
python src/main.py \
    --input_dir /data/reads \
    --file_type short_read_assembly \
    --folder_name "MyProject" \
    --collection_mode none \
    --api_key MY_API_KEY
```

---

## Handling Indexing Delays

### Short-read uploads (`short_read_assembly`)
The script **automatically retries** collection creation 4 times with exponential back-off (10s, 30s, 60s delays) before giving up.

```
[collection] Mode: all – creating 1 collection 'Klebsiella' with 5 genome(s) …
  [retry] Attempt 1/4 failed (genomes not ready yet). Retrying in 10s …
  [retry] Attempt 2/4 failed (genomes not ready yet). Retrying in 30s …
  [retry] Attempt 3/4 failed (genomes not ready yet). Retrying in 60s …
  → OK  https://next.pathogen.watch/collections/...
```

### Assembly file uploads
Collections are **automatically skipped** after uploading assembly files (they also need server-side indexing time).

The script prints exact retry instructions:
```
[main] Collection creation skipped after assembly upload.
[main] PathogenWatch needs time to index the genomes.
[main] Once ready, create the collection with:
[main]   python src/main.py --api_key MY_API_KEY \
[main]     --genome_ids 3315366,3315367,3315368 \
[main]     --collection_mode all --collection_name 'Klebsiella' \
[main]     --organism_id 573
```

---

## Output Files

### Genome ID JSON file
After a successful upload, a file named `pathogenwatch_upload_YYYYMMDD_HHMMSS.json` is created:

```json
{
  "genome_ids": [3315366, 3315367, 3315368],
  "samples": [
    {"name": "GCA_901563875.1", "genome_id": 3315366},
    {"name": "GCA_902158585.1", "genome_id": 3315367},
    {"name": "GCF_000005845.2", "genome_id": 3315368}
  ]
}
```

Use the `genome_ids` list with `--genome_ids` to create collections later once genomes are indexed.

---

## Advanced Usage

### Using glob patterns

Glob patterns in `--input_dir` must be **quoted** to prevent shell expansion:

```bash
# ✓ Correct: quoted pattern
python src/main.py --input_dir '/data/samples/*/fastq' --file_type fa,fna

# ✗ Wrong: unquoted pattern (shell expands before Python sees it)
python src/main.py --input_dir /data/samples/*/fastq --file_type fa,fna
```

Supported glob features:
- `*` — matches any characters except `/`
- `**` — matches zero or more directories (when used with `input_dir`)
- `?` — matches a single character
- `[abc]` — matches any of `a`, `b`, `c`

**Examples:**
```bash
--input_dir '/data/batch*/*/*.fastq.gz'       # All FASTQ files in nested dirs
--input_dir '/results/**/contigs/*.fasta'     # FASTA files at any depth
--input_dir '/genomes/GC[AF]_*.fa.gz'         # GC*_*.fa.gz files
```

### Custom regex for short-reads

Override the default regex pattern:

```bash
python src/main.py \
    --input_dir /data/reads \
    --file_type short_read_assembly \
    --regex '^(.+)\.R([12])\.fastq$' \
    --folder_name "MyProject" \
    --collection_mode all \
    --collection_name "Custom Reads" \
    --organism_id 28901 \
    --api_key MY_API_KEY
```

Regex requirements:
- Must have **exactly 2 capture groups**
  - Group 1: sample name
  - Group 2: read direction (`1` or `2`)
- Applied to filename only (not full path)

### Decompression behavior

Files are decompressed automatically, preserving original filenames:

| Input file | Stored as |
|---|---|
| `sample.fa.gz` | `sample.fa` (temp location) |
| `data.csv.tar.gz` → contains `data.csv` | `data.csv` (temp location) |
| `genome.fasta` | `genome.fasta` (uploaded as-is) |

---

## Troubleshooting

### "HTTP 404: /api/user/access returned 404"
This is a harmless warning. It means your API key is valid but the endpoint doesn't report access details. The script continues normally.

### "HTTP 400: One or more genomes were not found or you do not have access to them"
- **For short-reads:** The script automatically retries. If it still fails after retries, try again in a few minutes.
- **For assembly files:** This is expected. Wait for indexing to complete, then use the `--genome_ids` command provided in the output.

### "No paired-end files found" (short-read mode)
- Check that your files match the regex pattern: `^(.+?)_R?(1|2)(?:_\d+)?\.f(?:astq|q)(?:\.gz)?$`
- Examples of matched filenames:
  - `SampleA_R1.fastq.gz` ✓
  - `SampleA_R2.fastq.gz` ✓
  - `SampleA_1.fq.gz` ✓
  - `SampleA_2.fq` ✓
  - `sample_R1_001.fastq.gz` ✓
- Verify your `--input_dir` path is correct and readable

### "No assembly files found" (assembly mode)
- Check that file extensions match your `--file_type` argument (case-insensitive)
- If using a glob pattern, ensure it's **quoted**: `--input_dir '/data/reads/*/*'`
- Verify the directory contains files with the expected extensions

### "Inner format is not a supported assembly format"
When decompressing `.gz` or `.tar.gz` files, the inner format wasn't recognized. Supported formats are:
`.fa, .fas, .fna, .ffn, .faa, .frn, .fasta, .genome, .contig, .dna, .mfa, .mga, .csv`

---

## Argument Reference

| Argument | Type | Required | Description |
|---|---|---|---|
| `--api_key` | string | No* | PathogenWatch API key (X-API-Key header) |
| `--input_dir` | string | No** | Directory or glob pattern to scan |
| `--file_type` | string | No | Type: `short_read_assembly` (default) or comma-separated extensions |
| `--regex` | string | No | Custom regex for short-read filenames |
| `--folder_id` | int | No** | Upload to existing folder (mutually exclusive with `--folder_name`) |
| `--folder_name` | string | No** | Create folder and upload (mutually exclusive with `--folder_id`) |
| `--collection_mode` | enum | No | `per_sample`, `all`, or `none` (default: `all`) |
| `--collection_name` | string | No | Collection name (required for `all` mode, optional prefix for `per_sample`) |
| `--organism_id` | string | Yes*** | NCBI taxonomy ID (required to create collections) |
| `--description` | string | No | Optional collection description |
| `--genome_ids` | string | No | Comma-separated genome IDs (skips upload) |

*: Required if not set in `config.py`
**: Either `--input_dir` or `--genome_ids` required
***: Required if `--collection_mode` is not `none`

---

## API Key Lookup

Find NCBI taxonomy IDs at [NCBI Taxonomy Browser](https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/):

| Organism | Taxonomy ID |
|---|---|
| *Salmonella enterica* subsp. *enterica* | 28901 |
| *Staphylococcus aureus* | 1280 |
| *Klebsiella pneumoniae* | 573 |
| *Escherichia coli* | 562 |
| *Mycobacterium tuberculosis* | 1773 |

---

## Contributing

Contributions are welcome! Please submit a pull request or open an issue for enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
