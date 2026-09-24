"""
How the live posting corpus is turned into a model of what the market wants.

Data only. Nothing here describes a person; it describes the shape of the
computation that reads whatever postings happen to be in the database today.

The market model answers two questions no résumé can:
  1. How *diagnostic* is a skill? Python appears in most postings and separates
     nobody; CUDA appears in few and separates a great deal. An overlap that
     counts both equally is mostly counting Python.
  2. What does a role family actually ask for? The hand-written evidence lists
     said data engineering means spark/kafka/airflow/dbt — all specialist
     tools — so a résumé with Python, SQL, PostgreSQL and pandas scored the
     floor for it. The corpus says what those postings really demand.
"""

# A skill must appear in at least this share of a family's postings to count as
# part of what that family demands. Below it, the mention is incidental.
FAMILY_DEMAND_FLOOR = 0.04

# A family needs this many classified postings before its demand profile is
# trusted; under it, the sample is noise and the profile is not used.
MIN_POSTINGS_PER_FAMILY = 25

# Inverse document frequency is clamped so one rare skill cannot dominate a
# score on its own: idf = clamp(log(total / (1 + seen)), MIN, MAX).
IDF_MIN = 0.35
IDF_MAX = 3.0

# Distinctiveness: how much more this family wants a skill than families do on
# average. Clamped for the same reason.
DISTINCTIVENESS_MAX = 4.0

# Blending résumé evidence with market-profile overlap for role affinity.
# Evidence is direct proof (you held this title, you used these tools); the
# market profile is transfer (what you know is what this family hires for).
# Evidence leads, because it is the stronger claim.
EVIDENCE_SHARE = 0.65

# How many postings to scan when building the model. The whole corpus, but
# bounded so a runaway database cannot make a page load take minutes.
MAX_POSTINGS_SCANNED = 20000

# The model is rebuilt when the corpus changes; this is the cache location.
CACHE_PATH = ".cache/market.json"
CACHE_VERSION = 1
