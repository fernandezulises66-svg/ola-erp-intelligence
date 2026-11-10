"""Agente de Investigación: busca reglas y datos en el manual (RAG híbrido). No calcula: encuentra y cita."""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from app.agents.common import con_historial, correr_especialista, pregunta_actual
from app.agents.tools import crear_herramientas_investigador
from app.graph.state import OrchestratorState
from app.rag.retriever import HybridRetriever

logger = logging.getLogger("agentes.investigador")

PROMPT = """Sos el Agente de Investigación del ERP de OLA Muebles. Tu única fuente es el manual técnico
del ERP, que consultás con la herramienta `buscar_manual`.

- Buscá los datos que te pide el encargo: una búsqueda por tema, como máximo 5 en total. Si una
  búsqueda no trae lo que necesitás, reformulá la consulta con otras palabras.
- Leé el texto COMPLETO de cada fragmento: una regla puede estar al final, en una tabla o en una lista.
- No hagas cálculos ni saques conclusiones numéricas: eso lo hace el Agente de Análisis.
- Respondé breve (máximo 10 líneas) con una lista de HALLAZGOS: el dato textual y su fuente entre
  corchetes, por ejemplo: "- Un presupuesto vale 15 días corridos [03-api-ventas.md]".
- Si algo de lo que se te pidió no figura en el manual, decilo ("No encontrado en el manual: ...").
  No agregues temas que el encargo no pidió."""


def crear_nodo(llm: BaseChatModel, retriever: HybridRetriever):
    agente = create_react_agent(llm, crear_herramientas_investigador(retriever), prompt=PROMPT, name="investigador")

    async def investigador(state: OrchestratorState) -> dict[str, Any]:
        ronda = state.get("delegaciones", 0)
        encargo = con_historial(f"Encargo del supervisor: {state['instruccion']}\n\n"
                                f"Pregunta del usuario: {pregunta_actual(state)}", state)
        respuesta, herramientas, resultados = await correr_especialista(agente, encargo)
        fuentes = sorted({f["fuente"] for r in resultados if isinstance(r, dict) for f in r.get("fragmentos", [])})
        logger.info("ronda %d · %d búsquedas · fuentes: %s", ronda, len(herramientas), fuentes)
        return {"contribuciones": [{"agente": "investigador", "ronda": ronda, "instruccion": state["instruccion"],
                                    "resultado": respuesta, "herramientas": herramientas, "fuentes": fuentes}]}

    return investigador
