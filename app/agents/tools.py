"""
Herramientas de los agentes, con entradas y salidas validadas por Pydantic (ver schemas.py).

- Investigador: `buscar_manual` → el RAG híbrido (BM25 + Pinecone) es su única fuente.
- Analista: cálculos deterministas (aritmética segura, fechas, porcentajes): el LLM no hace cuentas.
"""

import ast
import operator
from datetime import timedelta

from langchain_core.tools import BaseTool, tool

from app.agents.schemas import (AplicarPorcentajeInput, BuscarManualInput, BusquedaManualOutput, CalcularInput,
                                DiasEntreFechasInput, ErrorHerramienta, ResultadoCalculo, ResultadoDias,
                                ResultadoFecha, ResultadoPorcentaje, SumarDiasInput)
from app.rag.retriever import HybridRetriever


def crear_herramientas_investigador(retriever: HybridRetriever) -> list[BaseTool]:
    @tool(args_schema=BuscarManualInput)
    async def buscar_manual(consulta: str, k: int = 4) -> str:
        """Busca en el manual técnico del ERP de OLA Muebles (ventas, presupuestos, roles y permisos,
        caja y bancos, inventario, integraciones con AFIP y Tiendanube, códigos de error).

        Usala para encontrar reglas de negocio: plazos, porcentajes, qué rol puede hacer qué, estados
        de una venta o el significado de un código de error. Escribí la consulta como una frase corta
        sobre UN tema (ej.: "validez de un presupuesto"). Si la pregunta trae un código exacto
        (VTA-410-PRES), incluilo tal cual: la búsqueda prioriza las coincidencias literales.

        Devuelve JSON con los fragmentos más relevantes (fuente, página, texto y qué recuperador los
        encontró). Si no hay resultados, devuelve `error` y una `sugerencia` para reformular.
        """
        fragmentos = await retriever.buscar(consulta, k)
        if not fragmentos:
            salida = BusquedaManualOutput(consulta=consulta, fragmentos=[], error=f"Sin resultados para '{consulta}'.",
                                          sugerencia="Probá con sinónimos o con una consulta más general.")
        else:
            salida = BusquedaManualOutput(consulta=consulta, fragmentos=fragmentos)
        return salida.model_dump_json()

    return [buscar_manual]


_OPERADORES = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
               ast.USub: operator.neg, ast.UAdd: operator.pos}


def _evaluar(nodo: ast.AST) -> float:
    """Solo aritmética: números y + - * /. Nada de nombres, llamadas ni atributos."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.left), _evaluar(nodo.right))
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand))
    raise ValueError("solo se admiten números y + - * / con paréntesis")


@tool(args_schema=CalcularInput)
def calcular(expresion: str) -> str:
    """Evalúa una expresión aritmética y devuelve el resultado exacto.
    Ejemplos: "1180000 * (1 - 0.18)", "(4200 + 3800) / 2". Sin separadores de miles (1180000, no 1.180.000)."""
    try:
        return ResultadoCalculo(expresion=expresion,
                                resultado=round(_evaluar(ast.parse(expresion, mode="eval").body), 2)).model_dump_json()
    except (SyntaxError, ValueError, ZeroDivisionError) as e:
        return ErrorHerramienta(error=f"No pude evaluar {expresion!r}: {e}").model_dump_json()


@tool(args_schema=DiasEntreFechasInput)
def dias_entre_fechas(desde, hasta) -> str:
    """Cuenta los días corridos entre dos fechas (ej.: desde que se emitió un presupuesto hasta hoy).
    Devuelve un número positivo si `hasta` es posterior a `desde`."""
    return ResultadoDias(desde=desde, hasta=hasta, dias_corridos=(hasta - desde).days).model_dump_json()


@tool(args_schema=SumarDiasInput)
def sumar_dias(fecha, dias: int) -> str:
    """Suma (o resta, con `dias` negativo) días corridos a una fecha. Sirve para calcular vencimientos."""
    return ResultadoFecha(fecha=fecha, dias=dias, resultado=fecha + timedelta(days=dias)).model_dump_json()


@tool(args_schema=AplicarPorcentajeInput)
def aplicar_porcentaje(monto: float, porcentaje: float, operacion: str) -> str:
    """Aplica un porcentaje a un monto en pesos:
    - "descuento": monto con el descuento (1000, 10 → 900)
    - "recargo":   monto con el recargo (1000, 10 → 1100)
    - "porcion":   qué parte del monto es ese porcentaje (1000, 50 → 500), ej. una seña."""
    factor = {"descuento": 1 - porcentaje / 100, "recargo": 1 + porcentaje / 100, "porcion": porcentaje / 100}[operacion]
    return ResultadoPorcentaje(monto=monto, porcentaje=porcentaje, operacion=operacion,
                               resultado=round(monto * factor, 2)).model_dump_json()


HERRAMIENTAS_ANALISTA: list[BaseTool] = [calcular, dias_entre_fechas, sumar_dias, aplicar_porcentaje]
