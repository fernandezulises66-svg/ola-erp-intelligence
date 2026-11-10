"""Contratos Pydantic de la API: toda entrada se valida y toda salida tiene `response_model`."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.jobs import JobStatus

ID_HEX = r"^[0-9a-f]{32}$"


class TaskIn(BaseModel):
    pregunta: str = Field(min_length=5, max_length=2000,
                          examples=["Hoy es 10/11/2026. Un presupuesto del 28/10/2026, ¿sigue vigente?"])
    conversation_id: str | None = Field(None, pattern=ID_HEX,
                                        description="Para seguir una conversación existente (memoria). Vacío = nueva.")

    @field_validator("pregunta")
    @classmethod
    def sin_espacios_de_mas(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 5:
            raise ValueError("la pregunta es demasiado corta")
        return v


class TaskCreated(BaseModel):
    job_id: str
    conversation_id: str
    status: JobStatus
    links: dict[str, str]


class Evento(BaseModel):
    status: JobStatus
    at: str


class Contribucion(BaseModel):
    agente: str
    ronda: int
    herramientas: list[str]
    fuentes: list[str]


class Resultado(BaseModel):
    respuesta_final: str = ""
    fuentes: list[str] = []
    supuestos: list[str] = []
    recorrido: list[str] = []
    delegaciones: int = 0
    contribuciones: list[Contribucion] = []
    es_critica: bool = False
    motivo_criticidad: str = ""
    accion_propuesta: dict[str, Any] | None = None
    aprobacion: dict[str, Any] | None = None
    accion_resultado: str = ""


class JobOut(BaseModel):
    job_id: str
    conversation_id: str
    status: JobStatus
    pregunta: str
    created_at: str
    updated_at: str
    resultado: Resultado | None = None
    aprobacion_pendiente: dict[str, Any] | None = None
    error: str | None = None
    duracion_s: float | None = None
    eventos: list[Evento] = []


class DecisionIn(BaseModel):
    aprobado: bool = True
    revisor: str = Field("revisor", min_length=1, max_length=80)
    comentario: str = Field("", max_length=500)


class ApprovalOut(BaseModel):
    job_id: str
    status: JobStatus
    decision: DecisionIn


class ConversationOut(BaseModel):
    conversation_id: str


class Turno(BaseModel):
    pregunta: str
    respuesta: str


class ConversationHistory(BaseModel):
    conversation_id: str
    turnos: list[Turno]
    job_activo: str | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degradado"]
    redis: bool
    rag_mode: str
    busqueda_vectorial: bool
    worker_embebido: bool
    keys_faltantes: list[str]
