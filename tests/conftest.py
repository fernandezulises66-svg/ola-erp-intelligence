import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Los tests no usan servicios externos: sin trazas, RAG solo BM25 (el vectorial se inyecta falso)
os.environ.update({"PHOENIX_ENABLED": "false", "RAG_MODE": "bm25", "RAG_AUTO_INGEST": "false",
                   "LLM_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "modelo-de-prueba"})
