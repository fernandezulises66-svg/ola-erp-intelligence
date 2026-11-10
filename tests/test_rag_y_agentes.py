"""RAG híbrido, contratos Pydantic de las herramientas, compuerta de criticidad, reducer y sintetizador."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.agents.schemas import RespuestaFinal
from app.agents.synthesizer import crear_cadena
from app.agents.tools import aplicar_porcentaje, calcular, crear_herramientas_investigador, dias_entre_fechas, sumar_dias
from app.core.config import Settings
from app.graph.hitl import detectar_criticidad
from app.graph.state import REINICIAR, acumular
from app.rag.documents import cargar_chunks
from app.rag.retriever import HybridRetriever
from tests.fakes import FakeRole, FakeVectorStore


def buscar(retriever, consulta, k=4):
    return asyncio.run(retriever.buscar(consulta, k))


# --- RAG híbrido -------------------------------------------------------------------------
def test_manual_se_carga_con_metadatos_e_ids_estables():
    chunks = cargar_chunks()
    assert len({c.metadata["source"] for c in chunks}) == 8
    assert all({"doc_id", "source", "page", "chunk_id", "category"} <= c.metadata.keys() for c in chunks)
    assert len({c.metadata["chunk_id"] for c in chunks}) == len(chunks)


def test_identificador_exacto_tiene_prioridad():
    resultado = buscar(HybridRetriever(list(cargar_chunks()), FakeVectorStore()), "¿Qué significa VTA-410-PRES?")
    assert "VTA-410-PRES" in resultado[0].texto and "exacto" in resultado[0].metodos


def test_fusion_combina_bm25_y_vectorial():
    vs = FakeVectorStore()
    resultado = buscar(HybridRetriever(list(cargar_chunks()), vs), "anticipo para pedidos especiales")
    assert vs.consultas == ["anticipo para pedidos especiales"]
    assert any("vectorial" in f.metodos for f in resultado)


def test_stemming_encuentra_singular_y_plural():
    # El manual dice "aprobar descuentos mayores al 15 %"; la consulta usa singular y otra conjugación.
    resultado = buscar(HybridRetriever(list(cargar_chunks()), None), "quién aprueba un descuento del 18 %")
    assert any("descuentos mayores al 15" in f.texto for f in resultado)


def test_si_pinecone_falla_degrada_a_bm25():
    resultado = buscar(HybridRetriever(list(cargar_chunks()), FakeVectorStore(falla=True)), "validez de un presupuesto")
    assert resultado and all(f.metodos == ["bm25"] for f in resultado)
    assert any("15 días" in f.texto for f in resultado)


def test_buscar_manual_valida_entrada_y_salida():
    herramienta = crear_herramientas_investigador(HybridRetriever(list(cargar_chunks()), None))[0]
    salida = json.loads(asyncio.run(herramienta.ainvoke({"consulta": "seña de muebles a medida"})))
    assert salida["fragmentos"][0]["fuente"].endswith(".md") and "chunk_id" in salida["fragmentos"][0]
    with pytest.raises(ValidationError):
        asyncio.run(herramienta.ainvoke({"consulta": "ab"}))         # menos de 3 caracteres
    with pytest.raises(ValidationError):
        asyncio.run(herramienta.ainvoke({"consulta": "seña", "k": 50}))


# --- Herramientas del analista (Pydantic) ---------------------------------------------------
def usar(herramienta, args):
    return json.loads(asyncio.run(herramienta.ainvoke(args)))


def test_herramientas_del_analista():
    assert usar(calcular, {"expresion": "1180000 * (1 - 0.18)"})["resultado"] == 967600
    assert usar(dias_entre_fechas, {"desde": "28/10/2026", "hasta": "2026-11-10"})["dias_corridos"] == 13
    assert usar(sumar_dias, {"fecha": "28/10/2026", "dias": 15})["resultado"] == "2026-11-12"
    assert usar(aplicar_porcentaje, {"monto": 967600, "porcentaje": 50, "operacion": "porcion"})["resultado"] == 483800


@pytest.mark.parametrize("herramienta, args", [
    (calcular, {"expresion": "__import__('os').system('ls')"}),          # el patrón solo admite aritmética
    (aplicar_porcentaje, {"monto": 1000, "porcentaje": 150, "operacion": "descuento"}),
    (aplicar_porcentaje, {"monto": -5, "porcentaje": 10, "operacion": "descuento"}),
    (aplicar_porcentaje, {"monto": 1000, "porcentaje": 10, "operacion": "regalar"}),
    (dias_entre_fechas, {"desde": "31/02/2026", "hasta": "2026-11-10"}),
])
def test_entradas_invalidas_se_rechazan(herramienta, args):
    with pytest.raises(ValidationError):
        asyncio.run(herramienta.ainvoke(args))


def test_division_por_cero_devuelve_error_tipado():
    assert "error" in usar(calcular, {"expresion": "1 / 0"})


# --- Grafo -------------------------------------------------------------------------------
@pytest.mark.parametrize("pregunta, critica", [
    ("Aplicá un 18 % de descuento y registralo en el ERP.", True),
    ("Por favor anulá la venta 5042.", True),
    ("emitilo hoy", True),
    ("Un cliente quiere confirmarlo con un 18 % de descuento. ¿Sigue vigente?", False),
    ("¿Qué rol puede aplicar descuentos?", False),
])
def test_deteccion_de_tareas_criticas(pregunta, critica):
    assert detectar_criticidad(pregunta)[0] is critica


def test_reducer_acumula_y_reinicia_por_turno():
    assert acumular([1, 2], [3]) == [1, 2, 3]
    assert acumular([1, 2], [REINICIAR]) == []
    assert acumular([1, 2], [REINICIAR, 9]) == [9]


def test_sintetizador_usa_texto_si_la_salida_estructurada_falla():
    class SinEstructura(FakeRole):
        def with_structured_output(self, schema, **kwargs):
            from langchain_core.runnables import RunnableLambda

            def romper(_):
                raise ValueError("JSON inválido")
            return RunnableLambda(romper)

    sintetizar = crear_cadena(SinEstructura(rol="sintetizador", recibidos=[]))
    final = asyncio.run(sintetizar({"contexto": "", "pregunta": "¿sigue vigente?", "pizarra": "..."}))
    assert isinstance(final, RespuestaFinal) and final.completa is False and final.respuesta


def test_configuracion_invalida_no_arranca(monkeypatch):
    monkeypatch.setenv("WORKER_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_modelos_e_indice_no_tienen_valor_por_defecto(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    with pytest.raises(ValidationError, match="ANTHROPIC_MODEL"):
        Settings(_env_file=None)
    monkeypatch.setenv("ANTHROPIC_MODEL", "x")
    monkeypatch.setenv("RAG_MODE", "hybrid")
    for var in ("PINECONE_INDEX_NAME", "PINECONE_NAMESPACE", "OPENAI_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError, match="PINECONE_INDEX_NAME"):
        Settings(_env_file=None)


def test_mensajes_de_la_cola_se_validan():
    from app.services.jobs import MensajeCola

    assert MensajeCola(job_id="a" * 32, tipo="resume").payload == {}
    with pytest.raises(ValidationError):
        MensajeCola(job_id="no-es-un-id")
    with pytest.raises(ValidationError):
        MensajeCola(job_id="a" * 32, tipo="borrar_todo")
