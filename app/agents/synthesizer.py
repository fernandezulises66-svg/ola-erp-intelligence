"""
Sintetizador: una cadena LCEL con contrato de salida Pydantic (lo del Módulo 2).

    prompt | modelo.with_structured_output(RespuestaFinal)   (.with_retry: 2 intentos)
        └─ si falla dos veces: prompt | modelo | StrOutputParser  (respuesta en texto, completa=False)

La respuesta final siempre sale validada: texto, fuentes citadas, supuestos y si quedó completa.
"""

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.agents.common import historial, pregunta_actual
from app.agents.schemas import RespuestaFinal
from app.agents.supervisor import _pizarra
from app.graph.state import OrchestratorState

logger = logging.getLogger("agentes.sintetizador")

SISTEMA = """Redactás la respuesta final para un usuario del ERP de OLA Muebles, en español rioplatense.

- Usá SOLO la información de las contribuciones de los especialistas; no agregues datos propios.
- Si un especialista aportó varias veces, la versión de la ronda más alta reemplaza a las anteriores.
- Respondé TODAS las partes de la pregunta en una lista numerada, breve, citando entre corchetes el
  documento del manual de cada regla (ej.: [03-api-ventas.md]).
- Si una regla cubre el caso (ej.: "más del 15 %" cubre un 18 %), aplicala con seguridad.
- Si algo quedó sin resolver o los aportes se contradicen, decilo claramente.
- Mencioná un supuesto solo si cambia el resultado.
- Si el usuario pide EJECUTAR algo en el ERP (aplicar, registrar, anular…), no digas que no podés:
  presentá el análisis y aclará que la acción queda pendiente de aprobación humana."""

prompt = ChatPromptTemplate.from_messages([
    ("system", SISTEMA),
    ("human", "{contexto}Pregunta del usuario:\n{pregunta}\n\nContribuciones de los especialistas:\n{pizarra}"),
])


def crear_cadena(llm: BaseChatModel):
    estructurada = (prompt | llm.with_structured_output(RespuestaFinal)).with_retry(stop_after_attempt=2)
    texto = prompt | llm | StrOutputParser()

    async def sintetizar(entrada: dict[str, str]) -> RespuestaFinal:
        try:
            resultado = await estructurada.ainvoke(entrada)
            return resultado if isinstance(resultado, RespuestaFinal) else RespuestaFinal.model_validate(resultado)
        except Exception as e:   # contrato roto dos veces: no perdemos la respuesta
            logger.warning("Salida estructurada inválida (%s): se usa la respuesta en texto", e)
            return RespuestaFinal(respuesta=(await texto.ainvoke(entrada)).strip(), completa=False)

    return sintetizar


def crear_nodo(llm: BaseChatModel):
    sintetizar = crear_cadena(llm)

    async def sintetizador(state: OrchestratorState) -> dict[str, Any]:
        previo = historial(state)
        final = await sintetizar({"contexto": f"Contexto de la conversación:\n{previo}\n\n" if previo else "",
                                  "pregunta": pregunta_actual(state),
                                  "pizarra": _pizarra(state.get("contribuciones", []))})
        logger.info("respuesta final (%d caracteres, completa=%s, fuentes=%s)", len(final.respuesta), final.completa,
                    final.fuentes)
        return {"respuesta_final": final.respuesta, "fuentes": final.fuentes, "supuestos": final.supuestos,
                "task_completed": True, "messages": [AIMessage(final.respuesta, name="sintetizador")]}

    return sintetizador
