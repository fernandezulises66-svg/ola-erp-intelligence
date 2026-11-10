"""Agente de Análisis: hace los cálculos con herramientas deterministas a partir de lo que encontró el investigador."""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.prebuilt import create_react_agent

from app.agents.common import con_historial, correr_especialista, pregunta_actual
from app.agents.tools import HERRAMIENTAS_ANALISTA
from app.graph.state import OrchestratorState

logger = logging.getLogger("agentes.analista")

PROMPT = """Sos el Agente de Análisis del ERP de OLA Muebles. Procesás datos que ya encontró el
Agente de Investigación: no buscás información nueva.

- Hacé TODOS los cálculos con tus herramientas (calcular, dias_entre_fechas, sumar_dias,
  aplicar_porcentaje). Nunca hagas cuentas mentalmente.
- Usá solo las reglas de los hallazgos del investigador. Si falta un dato, decilo ("Falta el dato: ...").
- Respondé con los RESULTADOS: valor, cuenta que lo justifica y regla usada. Montos con separador de
  miles (ej.: $967.600) y fechas dd/mm/aaaa."""


def crear_nodo(llm: BaseChatModel):
    agente = create_react_agent(llm, HERRAMIENTAS_ANALISTA, prompt=PROMPT, name="analista")

    async def analista(state: OrchestratorState) -> dict[str, Any]:
        ronda = state.get("delegaciones", 0)
        hallazgos = [c for c in state.get("contribuciones", []) if c["agente"] == "investigador"]
        datos = hallazgos[-1]["resultado"] if hallazgos else "(el investigador todavía no aportó datos)"
        encargo = con_historial(f"Encargo del supervisor: {state['instruccion']}\n\n"
                                f"Pregunta del usuario: {pregunta_actual(state)}\n\n"
                                f"Hallazgos del Agente de Investigación:\n{datos}", state)
        respuesta, herramientas, _ = await correr_especialista(agente, encargo)
        logger.info("ronda %d · herramientas: %s", ronda, herramientas)
        return {"contribuciones": [{"agente": "analista", "ronda": ronda, "instruccion": state["instruccion"],
                                    "resultado": respuesta, "herramientas": herramientas, "fuentes": []}]}

    return analista
