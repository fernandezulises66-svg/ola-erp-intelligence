"""Dobles de prueba: modelos falsos por rol, vector store falso y fábrica de dependencias."""

import time
from contextlib import asynccontextmanager
from typing import Any

import fakeredis
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver

from app.agents.schemas import RespuestaFinal
from app.api.main import Deps
from app.graph.builder import build_graph
from app.rag.documents import cargar_chunks
from app.rag.retriever import HybridRetriever
from app.services.jobs import JobStore

TEXTOS = {"investigador": "- Un presupuesto vale 15 días corridos [03-api-ventas.md]",
          "analista": "13 días: vigente. Precio: $967.600.",
          "sintetizador": "Sigue vigente; queda en $967.600."}


class FakeRole(BaseChatModel):
    """El supervisor delega según la pizarra (investigador → analista → FINALIZAR); el resto devuelve texto fijo.
    Guarda lo que recibe para poder inspeccionar el contexto que le llega a cada agente."""

    rol: str
    demora: float = 0.0
    falla: bool = False
    recibidos: list = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        def responder(entrada: Any) -> RespuestaFinal:
            self._generate(entrada.to_messages())
            return RespuestaFinal(respuesta=TEXTOS["sintetizador"], fuentes=["03-api-ventas.md"], completa=True)
        return RunnableLambda(responder)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.recibidos.append(list(messages))
        time.sleep(self.demora)
        if self.falla:
            raise RuntimeError("el proveedor del LLM no responde")
        if self.rol == "supervisor":
            pizarra = messages[-1].content.split("Contribuciones hasta ahora:")[-1]
            siguiente = ("investigador" if "INVESTIGADOR (" not in pizarra
                         else "analista" if "ANALISTA (" not in pizarra else "FINALIZAR")
            texto = f'{{"siguiente": "{siguiente}", "instruccion": "hacé tu parte", "motivo": "test"}}'
        else:
            texto = TEXTOS[self.rol]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(texto))])


def modelos(demora: float = 0.0, falla_en: str | None = None) -> dict[str, FakeRole]:
    return {r: FakeRole(rol=r, demora=demora, falla=(r == falla_en), recibidos=[])
            for r in ("supervisor", "investigador", "analista", "sintetizador")}


class FakeVectorStore:
    """Devuelve siempre el chunk de la regla de la seña (para ver la fusión) o falla si se pide."""

    def __init__(self, falla: bool = False) -> None:
        self.falla = falla
        self.consultas: list[str] = []

    async def buscar(self, consulta: str, k: int) -> list[tuple[Document, float]]:
        self.consultas.append(consulta)
        if self.falla:
            raise ConnectionError("Pinecone no responde")
        chunk = next(c for c in cargar_chunks() if "seña" in c.page_content and c.metadata["doc_id"] == "api-ventas")
        return [(chunk, 0.91)]


def fabrica(redis=None, saver=None, llms=None, vector_store=None, **kw_modelos):
    """deps_factory para create_app(): Redis falso, MemorySaver y modelos guionados."""
    redis = redis or fakeredis.aioredis.FakeRedis()
    saver = saver or MemorySaver()
    llms = llms or modelos(**kw_modelos)

    @asynccontextmanager
    async def deps():
        store = JobStore(redis)
        retriever = HybridRetriever(list(cargar_chunks()), vector_store)
        yield Deps(redis, store, retriever, build_graph(saver, retriever, store.registrar_accion_erp, llms))

    deps.llms = llms
    deps.redis = redis
    return deps
