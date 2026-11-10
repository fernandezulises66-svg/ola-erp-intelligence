"""
El grafo de agentes: supervisor + especialistas + síntesis + compuerta humana, con checkpointer.

    START → supervisor ─┬─► investigador (RAG híbrido) ─┐
               ▲        ├─► analista (cálculos) ────────┤
               └────────┼───────────────────────────────┘
                        └─► sintetizador → evaluar_riesgo ─┬──────────────────────────────► END
                                                           └► aprobacion_humana ─┬► ejecutar_accion → END
                                                               (interrupt)        └────────────────────► END

El checkpointer (AsyncRedisSaver) guarda el estado de cada conversación (`thread_id`) después de
cada nodo: las conversaciones sobreviven a un reinicio y la pausa humana puede reanudarse días después.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents import analyst, research, supervisor, synthesizer
from app.core.config import get_settings
from app.core.llm import crear_llm
from app.graph import hitl
from app.graph.state import OrchestratorState
from app.rag.retriever import HybridRetriever

ROLES = ("supervisor", "investigador", "analista", "sintetizador")


def build_graph(checkpointer: BaseCheckpointSaver, retriever: HybridRetriever, registrar: hitl.Registrador,
                llms: dict[str, BaseChatModel] | None = None) -> CompiledStateGraph:
    llms = llms or {}
    base = None if all(r in llms for r in ROLES) else crear_llm()
    modelo = {r: llms.get(r, base) for r in ROLES}

    g = StateGraph(OrchestratorState)
    g.add_node("supervisor", supervisor.crear_nodo(modelo["supervisor"]))
    g.add_node("investigador", research.crear_nodo(modelo["investigador"], retriever))
    g.add_node("analista", analyst.crear_nodo(modelo["analista"]))
    g.add_node("sintetizador", synthesizer.crear_nodo(modelo["sintetizador"]))
    g.add_node("evaluar_riesgo", hitl.evaluar_riesgo)
    g.add_node("aprobacion_humana", hitl.aprobacion_humana)
    g.add_node("ejecutar_accion", hitl.crear_nodo_ejecutar(registrar))

    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", supervisor.route_supervisor,
                            {"investigador": "investigador", "analista": "analista", "sintetizador": "sintetizador"})
    g.add_edge("investigador", "supervisor")
    g.add_edge("analista", "supervisor")
    g.add_edge("sintetizador", "evaluar_riesgo")
    g.add_conditional_edges("evaluar_riesgo", hitl.route_riesgo, {"aprobacion_humana": "aprobacion_humana", END: END})
    g.add_conditional_edges("aprobacion_humana", hitl.route_aprobacion, {"ejecutar_accion": "ejecutar_accion", END: END})
    g.add_edge("ejecutar_accion", END)
    return g.compile(checkpointer=checkpointer)


@asynccontextmanager
async def redis_checkpointer(url: str | None = None) -> AsyncIterator[BaseCheckpointSaver]:
    """AsyncRedisSaver (langgraph-checkpoint-redis). Necesita Redis 8+ (trae RedisJSON y RediSearch)."""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    async with AsyncRedisSaver.from_conn_string(url or get_settings().redis_url) as saver:
        await saver.asetup()
        yield saver


def run_config(conversation_id: str, job_id: str, tipo: str) -> dict[str, Any]:
    """thread_id = conversación: todas sus preguntas comparten memoria en el checkpointer."""
    return {"configurable": {"thread_id": conversation_id}, "recursion_limit": get_settings().recursion_limit,
            "run_name": f"orquestador.{tipo}", "tags": ["intelligence", tipo],
            "metadata": {"job_id": job_id, "conversation_id": conversation_id}}
