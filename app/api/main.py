"""
API REST del sistema (FastAPI). Al arrancar arma todas las capas:

    config validada → observabilidad (Phoenix) → Redis → checkpointer (AsyncRedisSaver)
    → RAG híbrido (manual + Pinecone, con ingesta automática) → grafo de agentes → worker

    uvicorn app.api.main:app      (docs interactivas en /docs)
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis, from_url

from app.api.routes import router
from app.core.config import BASE_DIR, get_settings
from app.core.logging import setup_logging
from app.graph.builder import build_graph, redis_checkpointer
from app.observability import init_observability
from app.rag.documents import cargar_chunks
from app.rag.retriever import HybridRetriever
from app.services.jobs import JobStore
from app.services.worker import Worker

logger = logging.getLogger("api")


@dataclass
class Deps:
    redis: Redis
    store: JobStore
    retriever: HybridRetriever
    graph: CompiledStateGraph


async def _vector_store():
    """Pinecone si el modo es híbrido y hay keys; si algo falla, None (el RAG degrada a BM25)."""
    s = get_settings()
    if s.rag_mode != "hybrid" or s.faltantes():
        logger.warning("Búsqueda vectorial desactivada (modo=%s, keys faltantes=%s): RAG solo con BM25",
                       s.rag_mode, s.faltantes())
        return None
    from app.rag.ingest import asegurar_indice
    from app.rag.vectorstore import PineconeVectorStore

    store = PineconeVectorStore()
    try:
        if s.rag_auto_ingest:
            await asegurar_indice(store)
        return store
    except Exception as e:
        logger.warning("Pinecone no disponible (%s): RAG solo con BM25", e)
        return None


@asynccontextmanager
async def construir_dependencias() -> AsyncIterator[Deps]:
    s = get_settings()
    async with AsyncExitStack() as stack:
        redis = from_url(s.redis_url)
        stack.push_async_callback(redis.aclose)
        store = JobStore(redis)
        saver = await stack.enter_async_context(redis_checkpointer())
        chunks = await asyncio.to_thread(cargar_chunks)   # lectura de archivos + tiktoken: fuera del event loop
        retriever = HybridRetriever(list(chunks), await _vector_store())
        yield Deps(redis, store, retriever, build_graph(saver, retriever, store.registrar_accion_erp))


def create_app(deps_factory: Callable[[], AbstractAsyncContextManager[Deps]] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging()
        s = get_settings()
        if faltan := s.faltantes():
            logger.warning("Faltan variables de entorno: %s (revisá el .env)", ", ".join(faltan))
        init_observability()
        async with AsyncExitStack() as stack:
            deps = await stack.enter_async_context((deps_factory or construir_dependencias)())
            app.state.deps = deps
            if s.worker_embedded:
                worker = Worker(deps.store, deps.graph)
                worker.start()
                stack.push_async_callback(worker.stop)
            logger.info("Sistema listo · RAG=%s · vectorial=%s · worker embebido=%s", s.rag_mode,
                        deps.retriever.vector_store is not None, s.worker_embedded)
            yield

    app = FastAPI(title="OLA Muebles · ERP Intelligence System", version="1.0.0", lifespan=lifespan,
                  description="RAG híbrido + agentes con supervisor + aprobación humana, con trazas en Arize Phoenix.")
    app.include_router(router)

    web = BASE_DIR / "web"
    if web.is_dir():   # demo web: chat + grafo en vivo (SSE) + aprobación humana
        app.mount("/ui", StaticFiles(directory=web, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def inicio() -> RedirectResponse:
            return RedirectResponse("/ui/")
    return app


app = create_app()
