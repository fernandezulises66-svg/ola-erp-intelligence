"""
Ingesta del manual en Pinecone (asíncrona e idempotente).

    python -m app.rag.ingest            # sube los chunks (mismos ids = se sobrescriben)

Al arrancar la API, `asegurar_indice()` crea el índice si no existe y sube el manual si el namespace
tiene menos vectores que chunks: en una máquina limpia, `docker compose up` deja el RAG listo solo.
"""

import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.rag.documents import cargar_chunks
from app.rag.vectorstore import PineconeVectorStore

logger = logging.getLogger("rag.ingesta")


async def asegurar_indice(store: PineconeVectorStore) -> int:
    chunks = list(cargar_chunks())
    actuales = await store.contar()
    if actuales >= len(chunks):
        logger.info("Namespace '%s' listo: %d vectores", get_settings().pinecone_namespace, actuales)
        return actuales
    logger.info("Namespace con %d/%d vectores: subiendo el manual…", actuales, len(chunks))
    subidos = await store.subir(chunks)
    logger.info("Subidos %d chunks a '%s'/'%s'", subidos, get_settings().pinecone_index_name,
                get_settings().pinecone_namespace)
    return subidos


async def main() -> None:
    setup_logging()
    store = PineconeVectorStore()
    subidos = await store.subir(list(cargar_chunks()))
    logger.info("Ingesta completa: %d chunks", subidos)


if __name__ == "__main__":
    asyncio.run(main())
