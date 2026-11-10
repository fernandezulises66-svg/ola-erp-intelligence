"""
Contratos de datos (Pydantic) de los agentes: entradas y salidas de cada herramienta, la decisión del
supervisor y la respuesta final.

- Entradas: cada `@tool` declara `args_schema`. Si el modelo pide una herramienta con argumentos
  inválidos (una fecha imposible, un porcentaje de 150), LangGraph le devuelve el error de validación
  como mensaje y el agente se corrige, en vez de ejecutar con datos basura.
- Salidas: cada herramienta devuelve un modelo serializado a JSON, con forma fija y documentada.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _fecha(valor: object) -> object:
    """Acepta aaaa-mm-dd (ISO) y dd/mm/aaaa."""
    if isinstance(valor, str) and "/" in valor:
        return datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    return valor


# --- RAG ------------------------------------------------------------------------------
class Fragmento(BaseModel):
    fuente: str = Field(description="Archivo del manual (ej.: 03-api-ventas.md)")
    titulo: str
    pagina: int = Field(ge=1)
    chunk_id: str
    texto: str
    score: float = Field(description="Puntaje RRF de la fusión híbrida")
    metodos: list[str] = Field(description="Qué recuperadores lo encontraron: bm25, vectorial, exacto")


class BuscarManualInput(BaseModel):
    consulta: str = Field(min_length=3, max_length=300, description="Frase corta sobre UN tema")
    k: int = Field(4, ge=1, le=8, description="Cantidad de fragmentos a devolver")


class BusquedaManualOutput(BaseModel):
    consulta: str
    fragmentos: list[Fragmento]
    error: str | None = None
    sugerencia: str | None = None


# --- Herramientas del analista -----------------------------------------------------------
class CalcularInput(BaseModel):
    expresion: str = Field(max_length=200, pattern=r"^[0-9\s.+\-*/()]+$",
                           description="Aritmética con números y + - * / ( ). Sin separadores de miles.")


class ResultadoCalculo(BaseModel):
    expresion: str
    resultado: float


class DiasEntreFechasInput(BaseModel):
    desde: date = Field(description="aaaa-mm-dd o dd/mm/aaaa")
    hasta: date = Field(description="aaaa-mm-dd o dd/mm/aaaa")

    _parsear = field_validator("desde", "hasta", mode="before")(_fecha)


class ResultadoDias(BaseModel):
    desde: date
    hasta: date
    dias_corridos: int


class SumarDiasInput(BaseModel):
    fecha: date = Field(description="aaaa-mm-dd o dd/mm/aaaa")
    dias: int = Field(ge=-3650, le=3650)

    _parsear = field_validator("fecha", mode="before")(_fecha)


class ResultadoFecha(BaseModel):
    fecha: date
    dias: int
    resultado: date


class AplicarPorcentajeInput(BaseModel):
    monto: float = Field(gt=0, description="Monto en pesos, sin separadores de miles")
    porcentaje: float = Field(ge=0, le=100)
    operacion: Literal["descuento", "recargo", "porcion"]


class ResultadoPorcentaje(BaseModel):
    monto: float
    porcentaje: float
    operacion: str
    resultado: float


class ErrorHerramienta(BaseModel):
    error: str


# --- Supervisor y síntesis ----------------------------------------------------------------
class DecisionSupervisor(BaseModel):
    siguiente: Literal["investigador", "analista", "FINALIZAR"]
    instruccion: str = ""
    motivo: str = ""


class RespuestaFinal(BaseModel):
    """Salida estructurada del sintetizador (cadena LCEL: prompt | modelo.with_structured_output)."""

    respuesta: str = Field(description="Respuesta para el usuario en markdown, una entrada por cada pregunta")
    fuentes: list[str] = Field(default_factory=list, description="Archivos del manual citados")
    supuestos: list[str] = Field(default_factory=list, description="Supuestos que cambian el resultado")
    completa: bool = Field(description="True si se respondieron todas las partes de la pregunta")
