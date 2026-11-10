"""
Estado compartido del grafo.

Una CONVERSACIÓN es un `thread_id` del checkpointer: los mensajes (preguntas del usuario y respuestas
finales) se acumulan entre turnos y sobreviven a un reinicio del servidor (Redis).

Lo demás es estado DEL TURNO: la pizarra de contribuciones, las decisiones del supervisor y los campos
de control. Al empezar cada pregunta se reinicia con el marcador REINICIAR (ver `acumular`), para que
el supervisor de la pregunta 2 no evalúe la pizarra de la pregunta 1.
"""

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import MessagesState

Especialista = Literal["investigador", "analista"]
Destino = Literal["investigador", "analista", "sintetizador"]
REINICIAR = "__reiniciar__"


def acumular(actual: list | None, nuevo: list | None) -> list:
    """Reducer: agrega lo nuevo; si lo nuevo empieza con REINICIAR, descarta lo anterior."""
    nuevo = nuevo or []
    if nuevo and nuevo[0] == REINICIAR:
        return list(nuevo[1:])
    return (actual or []) + nuevo


class Contribucion(TypedDict):
    agente: Especialista
    ronda: int
    instruccion: str
    resultado: str
    herramientas: list[str]
    fuentes: list[str]


class Decision(TypedDict):
    ronda: int
    siguiente: Destino
    motivo: str
    origen: Literal["llm", "regla"]


class AccionPropuesta(TypedDict):
    tipo: str
    descripcion: str
    detalle: str


class Aprobacion(TypedDict, total=False):
    aprobado: bool
    revisor: str
    comentario: str


class OrchestratorState(MessagesState):
    # --- estado del turno (se reinicia en cada pregunta) ---
    contribuciones: Annotated[list[Contribucion], acumular]
    decisiones: Annotated[list[Decision], acumular]
    next_agent: Destino
    instruccion: str
    delegaciones: int
    task_completed: bool
    respuesta_final: str
    fuentes: list[str]
    supuestos: list[str]
    # --- human-in-the-loop ---
    es_critica: bool
    motivo_criticidad: str
    accion_propuesta: AccionPropuesta | None
    aprobacion: Aprobacion | None
    accion_resultado: str


def entrada_turno(pregunta: str) -> dict[str, Any]:
    """Input de cada pregunta: agrega el mensaje del usuario y reinicia el estado del turno."""
    return {"messages": [("user", pregunta)], "contribuciones": [REINICIAR], "decisiones": [REINICIAR],
            "delegaciones": 0, "instruccion": "", "task_completed": False, "respuesta_final": "", "fuentes": [],
            "supuestos": [], "es_critica": False, "motivo_criticidad": "", "accion_propuesta": None,
            "aprobacion": None, "accion_resultado": ""}
