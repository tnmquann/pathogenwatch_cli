# config.py
# ---------------------------------------------------------------
# Global configuration for PathogenWatch CLI.
# Replace API_KEY with your actual X-API-Key token before running.
# ---------------------------------------------------------------

API_KEY = "MY_API_KEY"

# PathogenWatch next-gen platform base URL
BASE_URL = "https://next.pathogen.watch"

# HTTP request timeout (seconds)
TIMEOUT = 120

# Default regex for paired-end Illumina reads.
# Matches patterns like:
#   sample_R1.fastq.gz / sample_R2.fastq.gz
#   sample_R1_001.fastq.gz / sample_R2_001.fastq.gz
#   sample_1.fastq.gz / sample_2.fastq.gz
#   sample_R1.fq.gz / sample_R2.fq.gz
#   sample_1.fq / sample_2.fq
DEFAULT_REGEX = r"^(.+?)_R?(1|2)(?:_\d+)?\.f(?:astq|q)(?:\.gz)?$"
