"""
Pinecone 100 % asíncrono: `PineconeAsyncio` + `IndexAsyncio` para crear el índice, subir vectores y
consultar, y `OpenAIEmbeddings.aembed_*` (httpx async) para los embeddings. Ninguna llamada bloquea
el event loop de la API.
"""

import asyncio
import logging
from typing import Any

from langchain_core.documents import Document

from app.core.config import get_settings

logger = logging.getLogger("rag.pinecone")


class PineconeVectorStore:
    def __init__(self) -> None:
        from langchain_openai import OpenAIEmbeddings

        self.s = get_settings()
        self.embeddings = OpenAIEmbeddings(model=self.s.openai_embedding_model)
        self._host: str | None = None

    async def _host_del_indice(self, crear: bool = False) -> str:
        if self._host:
            return self._host
        from pinecone import PineconeAsyncio, ServerlessSpec

        async with PineconeAsyncio() as pc:          # lee PINECONE_API_KEY del entorno
            if not await pc.has_index(self.s.pinecone_index_name):
                if not crear:
                    raise RuntimeError(f"No existe el índice '{self.s.pinecone_index_name}' (corré la ingesta)")
                logger.info("Creando índice serverless '%s' (%d dims)", self.s.pinecone_index_name,
                            self.s.embedding_dim)
                await pc.create_index(name=self.s.pinecone_index_name, dimension=self.s.embedding_dim,
                                      metric="cosine",
                                      spec=ServerlessSpec(cloud=self.s.pinecone_cloud, region=self.s.pinecone_region))
            desc = await pc.describe_index(self.s.pinecone_index_name)
            self._host = desc.host
        return self._host

    async def contar(self) -> int:
        """Vectores en el namespace configurado (0 si no existe)."""
        from pinecone import PineconeAsyncio

        host = await self._host_del_indice(crear=True)
        async with PineconeAsyncio() as pc, pc.IndexAsyncio(host=host) as idx:
            stats = await idx.describe_index_stats()
        ns = (stats.namespaces or {}).get(self.s.pinecone_namespace)
        return int(ns.vector_count) if ns else 0

    async def subir(self, chunks: list[Document], lote: int = 64) -> int:
        from pinecone import PineconeAsyncio

        host = await self._host_del_indice(crear=True)
        vectores = await self.embeddings.aembed_documents([c.page_content for c in chunks])
        registros: list[dict[str, Any]] = [
            {"id": c.metadata["chunk_id"], "values": v,
             "metadata": {**{k: v2 for k, v2 in c.metadata.items()}, "text": c.page_content}}
            for c, v in zip(chunks, vectores)]
        async with PineconeAsyncio() as pc, pc.IndexAsyncio(host=host) as idx:
            await asyncio.gather(*(idx.upsert(vectors=registros[i:i + lote], namespace=self.s.pinecone_namespace)
                                   for i in range(0, len(registros), lote)))
        return len(registros)

    async def buscar(self, consulta: str, k: int) -> list[tuple[Document, float]]:
        from pinecone import PineconeAsyncio

        host = await self._host_del_indice()
        vector = await self.embeddings.aembed_query(consulta)
        async with PineconeAsyncio() as pc, pc.IndexAsyncio(host=host) as idx:
            res = await idx.query(vector=vector, top_k=k, include_metadata=True, namespace=self.s.pinecone_namespace)
        salida = []
        for m in res.matches:
            meta = dict(m.metadata or {})
            texto = meta.pop("text", "")
            salida.append((Document(page_content=texto, metadata=meta), float(m.score)))
        return salida
