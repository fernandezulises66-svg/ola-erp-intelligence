"""Utilidades compartidas por los agentes."""

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from app.core.config import get_settings


def texto_de(msg: BaseMessage) -> str:
    """Texto plano de un mensaje (Anthropic puede devolver una lista de bloques)."""
    if isinstance(msg.content, str):
        return msg.content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in msg.content
                   if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))


def pregunta_actual(state: dict) -> str:
    """La pregunta del turno: el último mensaje del usuario."""
    for m in reversed(state["messages"]):
        if m.type == "human":
            return texto_de(m)
    return ""


def historial(state: dict, turnos: int | None = None) -> str:
    """Los turnos ANTERIORES de la conversación (pregunta + respuesta final), para dar contexto a los
    agentes sin pasarles toda la metadata interna. Vacío si es la primera pregunta."""
    turnos = get_settings().historial_turnos if turnos is None else turnos
    mensajes = state["messages"]
    ultimo_humano = max((i for i, m in enumerate(mensajes) if m.type == "human"), default=-1)
    pares: list[tuple[str, str]] = []
    for m in mensajes[:ultimo_humano]:
        if m.type == "human":
            pares.append((texto_de(m), ""))
        elif isinstance(m, AIMessage) and pares:
            pares[-1] = (pares[-1][0], (pares[-1][1] + "\n" + texto_de(m)).strip())
    if not pares or turnos == 0:
        return ""
    return "\n\n".join(f"Usuario: {p}\nSistema: {r[:1500]}" for p, r in pares[-turnos:])


def con_historial(encabezado: str, state: dict) -> str:
    previo = historial(state)
    return f"{encabezado}\n\nContexto de la conversación (turnos anteriores):\n{previo}" if previo else encabezado


def parse_json(contenido: Any) -> Any:
    try:
        return json.loads(contenido)
    except (TypeError, ValueError):
        return contenido


async def correr_especialista(agente: CompiledStateGraph, encargo: str) -> tuple[str, list[str], list[Any]]:
    """Ciclo ReAct de un especialista con SOLO su encargo. Devuelve (respuesta, herramientas, resultados)."""
    salida = await agente.ainvoke({"messages": [("user", encargo)]},
                                  {"recursion_limit": get_settings().agent_recursion_limit})
    mensajes: list[BaseMessage] = salida["messages"]
    herramientas = [tc["name"] for m in mensajes if isinstance(m, AIMessage) for tc in m.tool_calls]
    resultados = [parse_json(m.content) for m in mensajes if isinstance(m, ToolMessage)]
    respuesta = texto_de(mensajes[-1]).strip() or "(el especialista no devolvió texto)"
    return respuesta, herramientas, resultados
