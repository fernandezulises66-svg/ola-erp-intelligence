"""
Observabilidad con Arize Phoenix (OpenTelemetry + OpenInference).

- `init_observability()` registra el exportador OTLP hacia Phoenix e instrumenta LangChain/LangGraph:
  cada nodo del grafo, cada llamada al LLM (con tokens de entrada/salida, de donde Phoenix calcula
  el costo) y cada herramienta quedan como spans de una misma traza.
- `span_job()` abre la traza raíz de cada ejecución y la agrupa por sesión (= conversación): las
  preguntas de una misma conversación, la pausa humana y la reanudación se ven juntas en Phoenix.
- `@observar(...)` decora funciones propias que no son Runnables de LangChain (ej.: la búsqueda en
  la búsqueda híbrida del RAG) para que también aparezcan en la traza.
"""

import functools
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.core.config import get_settings

logger = logging.getLogger("observabilidad")

P = ParamSpec("P")
R = TypeVar("R")
_tracer: Any = None


def init_observability() -> bool:
    """Conecta con Phoenix e instrumenta LangChain. Devuelve False si está desactivado o falla."""
    global _tracer
    s = get_settings()
    if not s.phoenix_enabled:
        logger.info("Observabilidad desactivada (PHOENIX_ENABLED=false)")
        return False
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from phoenix.otel import register

        # set_global_tracer_provider=False: si el provider es global, FastAPI (las versiones nuevas traen telemetría
        # propia) traza cada GET /tasks/{id} del polling y llena Phoenix de trazas HTTP que no son del agente.
        provider = register(project_name=s.phoenix_project_name, endpoint=s.phoenix_collector_endpoint,
                            batch=True, verbose=False, set_global_tracer_provider=False)
        LangChainInstrumentor().instrument(tracer_provider=provider)
        _tracer = provider.get_tracer("ola-erp-intelligence")
        logger.info("Trazas → Phoenix %s (proyecto '%s')", s.phoenix_collector_endpoint, s.phoenix_project_name)
        return True
    except Exception as e:  # la API sigue funcionando aunque Phoenix no esté disponible
        logger.warning("No se pudo inicializar Phoenix: %s", e)
        return False


def tracer() -> Any:
    return _tracer or trace.get_tracer("ola-erp-intelligence")   # sin Phoenix: tracer no-op de OpenTelemetry


def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)[:4000]


@contextmanager
def span_job(job_id: str, conversation_id: str, tipo: str, entrada: Any) -> Iterator[Any]:
    """Traza raíz de una ejecución: kind CHAIN, sesión = conversación (todas sus preguntas, pausas y
    reanudaciones se ven juntas en Phoenix), metadata con el job y el tipo."""
    from openinference.instrumentation import using_metadata, using_session

    with using_session(conversation_id), using_metadata({"job_id": job_id, "tipo": tipo}):
        with tracer().start_as_current_span(f"job.{tipo}", attributes={
                "openinference.span.kind": "CHAIN", "input.value": _json(entrada),
                "input.mime_type": "application/json", "session.id": conversation_id}) as span:
            yield span


def _documentos(span: Any, docs: list[Any]) -> None:
    """Atributos `retrieval.documents.*` de OpenInference: Phoenix los muestra en el panel Documents."""
    for i, d in enumerate(docs):
        datos = d.model_dump() if hasattr(d, "model_dump") else dict(d)
        base = f"retrieval.documents.{i}.document"
        span.set_attribute(f"{base}.id", str(datos.get("chunk_id", i)))
        span.set_attribute(f"{base}.content", str(datos.get("texto", ""))[:4000])
        span.set_attribute(f"{base}.score", float(datos.get("score", 0.0)))
        span.set_attribute(f"{base}.metadata", _json({k: v for k, v in datos.items() if k not in ("texto",)}))


def observar(nombre: str, kind: str = "CHAIN") -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorador para funciones async propias: crea un span con su entrada, salida y errores."""
    def decorador(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def envuelta(*args: P.args, **kwargs: P.kwargs) -> R:
            entrada = kwargs or (args[1:] if args and hasattr(args[0], "__dict__") else args)
            with tracer().start_as_current_span(nombre, attributes={
                    "openinference.span.kind": kind, "input.value": _json(entrada)}) as span:
                try:
                    resultado = await fn(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
                span.set_attribute("output.value", _json(resultado))
                if kind == "RETRIEVER" and isinstance(resultado, list):
                    _documentos(span, resultado)
                return resultado
        return envuelta
    return decorador
