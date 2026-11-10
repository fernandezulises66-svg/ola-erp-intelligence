"""
Configuración centralizada y validada con pydantic-settings.

Todo sale de variables de entorno (o del archivo .env): modelos, índice y namespace de Pinecone,
URL de Redis, límites del grafo, observabilidad. No hay IDs ni nombres de modelo escritos en el
código. Si una variable tiene un valor inválido (ej.: WORKER_CONCURRENCY=cero), la app no arranca
y el error dice qué variable revisar.

Las API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, PINECONE_API_KEY) las leen directamente los SDKs
desde el entorno; acá solo se valida que estén presentes cuando hacen falta.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")   # exporta las keys al entorno para los SDKs (en Docker ya vienen por env_file)

EMBEDDING_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore", case_sensitive=False)

    # --- LLM ------------------------------------------------------------------------
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_workspace_id: str = ""
    openai_chat_model: str = "gpt-6-luna"
    llm_max_tokens: int = Field(2048, ge=256, le=16000)
    llm_timeout_s: float = Field(60, gt=0)
    llm_max_retries: int = Field(2, ge=0, le=6)

    # --- RAG (Pinecone + BM25) ----------------------------------------------------------
    rag_mode: Literal["hybrid", "bm25"] = "hybrid"        # bm25 = sin Pinecone/OpenAI (modo degradado)
    pinecone_index_name: str = "ola-erp-docs"
    pinecone_namespace: str = "intelligence-v1"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    openai_embedding_model: str = "text-embedding-3-small"
    data_dir: Path = BASE_DIR / "data" / "manual"
    chunk_size_tokens: int = Field(600, ge=100)
    chunk_overlap_tokens: int = Field(80, ge=0)
    rag_top_k: int = Field(4, ge=1, le=10)
    rag_candidates: int = Field(10, ge=1, le=50)          # candidatos de cada recuperador antes de fusionar
    rag_bm25_weight: float = Field(0.5, ge=0, le=1)       # peso de BM25 en la fusión RRF (vectorial = 1 - peso)
    rag_fragment_max_chars: int = Field(3000, ge=200)
    rag_auto_ingest: bool = True                          # al arrancar, sube el manual si el namespace está vacío

    # --- Redis, cola y worker ------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_s: int = Field(7 * 24 * 3600, ge=60)
    worker_concurrency: int = Field(5, ge=1, le=50)
    worker_embedded: bool = True

    # --- Grafo de agentes ---------------------------------------------------------------
    max_delegaciones: int = Field(6, ge=1, le=20)
    max_revisiones: int = Field(2, ge=0, le=5)
    recursion_limit: int = Field(40, ge=10)
    agent_recursion_limit: int = Field(12, ge=4)
    historial_turnos: int = Field(3, ge=0, le=10)          # turnos previos que ve cada agente

    # --- Observabilidad -----------------------------------------------------------------
    phoenix_enabled: bool = True
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project_name: str = "ola-erp-intelligence"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIMENSIONS.get(self.openai_embedding_model, 1536)

    def faltantes(self) -> list[str]:
        """API keys que faltan para el modo configurado (se informan al arrancar)."""
        requeridas = ["ANTHROPIC_API_KEY" if self.llm_provider == "anthropic" else "OPENAI_API_KEY"]
        if self.rag_mode == "hybrid":
            requeridas += ["PINECONE_API_KEY", "OPENAI_API_KEY"]
        return sorted({k for k in requeridas if not os.getenv(k)})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
