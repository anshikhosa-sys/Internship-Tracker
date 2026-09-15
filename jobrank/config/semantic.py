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
