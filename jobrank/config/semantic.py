"""
Embedding and vector-store settings. All local, all free.
"""

# "auto": the ONNX model when it loads, hashed vectors otherwise.
# "model": require the model. "hashing": never load a model.
BACKEND = "auto"

MODEL_NAME = "BAAI/bge-small-en-v1.5"   # 384 dims, ~67 MB, runs on CPU via onnxruntime
MODEL_CACHE_DIR = ".cache/embeddings"
# Downloading the model needs the network once. When False, only an already
# cached model is used, and a missing one falls back to hashing.
ALLOW_MODEL_DOWNLOAD = True

HASHING_NAME = "hashing-v1"
HASHING_DIM = 1024

VECTOR_DB_PATH = ".cache/vectors.db"
BATCH_SIZE = 256

# How much of a job description goes into its embedding.
#
# The model truncates at 512 tokens, so more than about a thousand characters
# is thrown away regardless — and what gets thrown away is the end, which is
# where the EEO statement, the benefits list and the legal boilerplate live.
# Those are near-identical across employers, so they make every posting look
# more alike, which is the opposite of what an embedding is for.
#
# Measured on this corpus: 2000 chars embeds at 3.3/s, 1000 at 6.1/s. The
# whole corpus is ~13 minutes rather than ~24, for strictly better signal.
EMBED_DESCRIPTION_CHARS = 1000
