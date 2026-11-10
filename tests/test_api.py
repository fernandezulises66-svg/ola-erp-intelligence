"""
Flujos completos por HTTP, sin keys ni servicios externos (Redis falso, MemorySaver, modelos guionados).
La API, la cola, el worker, el grafo, el interrupt del HITL, la memoria y el manejo de errores son los reales.
"""

import json
import time
from typing import Any

import fakeredis
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.api.main import create_app
from app.services.jobs import ERP_ACCIONES
from tests.fakes import fabrica, modelos

CONSULTA = "Hoy es 10/11/2026. Un presupuesto del 28/10/2026, ¿sigue vigente?"
CRITICA = "Aplicá un 18 % de descuento al presupuesto P-1042 de $1.180.000 y registralo en el ERP."


def cliente(deps_factory) -> TestClient:
    return TestClient(create_app(deps_factory))


def esperar(c: TestClient, job_id: str, hasta: set[str], timeout: float = 10) -> dict[str, Any]:
    fin = time.time() + timeout
    job: dict[str, Any] = {}
    while time.time() < fin:
        job = c.get(f"/tasks/{job_id}").json()
        if job["status"] in hasta:
            return job
        time.sleep(0.05)
    raise AssertionError(f"el job no llegó a {hasta}: {job}")


def preguntar(c: TestClient, pregunta: str, conv: str | None = None) -> dict[str, Any]:
    body = {"pregunta": pregunta} | ({"conversation_id": conv} if conv else {})
    r = c.post("/tasks", json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_post_encola_y_responde_sin_esperar_al_agente():
    with cliente(fabrica(demora=0.5)) as c:
        inicio = time.perf_counter()
        job = preguntar(c, CONSULTA)
        assert time.perf_counter() - inicio < 0.3
        assert job["status"] == "PENDING" and len(job["conversation_id"]) == 32
        assert esperar(c, job["job_id"], {"RUNNING"})["status"] == "RUNNING"
        assert c.get("/health").json()["redis"] is True        # la API responde mientras el agente trabaja


def test_consulta_termina_en_done_con_salida_validada():
    with cliente(fabrica()) as c:
        job = esperar(c, preguntar(c, CONSULTA)["job_id"], {"DONE", "FAILED"})
    assert job["status"] == "DONE"
    r = job["resultado"]
    assert r["respuesta_final"] == "Sigue vigente; queda en $967.600."
    assert r["fuentes"] == ["03-api-ventas.md"]                # salida estructurada del sintetizador (Pydantic)
    assert r["recorrido"] == ["investigador", "analista", "sintetizador"]
    assert [e["status"] for e in job["eventos"]] == ["PENDING", "RUNNING", "DONE"]


def test_tarea_critica_pausa_hasta_la_aprobacion_y_ejecuta_la_accion():
    deps = fabrica()
    with cliente(deps) as c:
        jid = preguntar(c, CRITICA)["job_id"]
        job = esperar(c, jid, {"WAITING_APPROVAL", "DONE", "FAILED"})
        assert job["status"] == "WAITING_APPROVAL"
        assert job["aprobacion_pendiente"]["accion_propuesta"]["tipo"] == "aplicar_descuento"
        assert c.portal.call(deps.redis.llen, ERP_ACCIONES) == 0
        assert c.post(f"/tasks/{jid}/approve", json={"aprobado": True, "revisor": "gerencia"}).status_code == 202
        job = esperar(c, jid, {"DONE", "FAILED"})
        assert "registrada en el ERP" in job["resultado"]["accion_resultado"]
        assert c.portal.call(deps.redis.llen, ERP_ACCIONES) == 1


def test_rechazo_humano_no_toca_el_erp():
    deps = fabrica()
    with cliente(deps) as c:
        jid = preguntar(c, CRITICA)["job_id"]
        esperar(c, jid, {"WAITING_APPROVAL"})
        c.post(f"/tasks/{jid}/approve", json={"aprobado": False, "revisor": "gerencia"})
        job = esperar(c, jid, {"DONE", "FAILED"})
        assert "rechazada" in job["resultado"]["accion_resultado"]
        assert c.portal.call(deps.redis.llen, ERP_ACCIONES) == 0


def test_errores_http_y_validacion_de_entrada():
    with cliente(fabrica()) as c:
        jid = preguntar(c, CONSULTA)["job_id"]
        esperar(c, jid, {"DONE"})
        assert c.post(f"/tasks/{jid}/approve", json={"aprobado": True}).status_code == 409
        assert c.post("/tasks/x/approve", json={"aprobado": True}).status_code == 404
        assert c.get("/tasks/no-existe").status_code == 404
        assert c.post("/tasks", json={"pregunta": "hi"}).status_code == 422
        assert c.post("/tasks", json={"pregunta": CONSULTA, "conversation_id": "no-es-un-id"}).status_code == 422
        assert c.post("/tasks", json={"pregunta": CONSULTA, "conversation_id": "0" * 32}).status_code == 404
        assert c.post(f"/tasks/{jid}/approve", json={"revisor": ""}).status_code == 422


def test_excepcion_en_el_agente_deja_failed_y_libera_la_conversacion():
    with cliente(fabrica(falla_en="analista")) as c:
        job = preguntar(c, CONSULTA)
        fallido = esperar(c, job["job_id"], {"DONE", "FAILED"})
        assert fallido["status"] == "FAILED" and "el proveedor del LLM no responde" in fallido["error"]
        assert c.post("/tasks", json={"pregunta": CONSULTA, "conversation_id": job["conversation_id"]}).status_code == 202


def test_cinco_tareas_concurrentes():
    with cliente(fabrica(demora=0.2)) as c:
        inicio = time.perf_counter()
        ids = [preguntar(c, f"{CONSULTA} (#{i})")["job_id"] for i in range(5)]
        jobs = [esperar(c, i, {"DONE", "FAILED"}, timeout=20) for i in ids]
        duracion = time.perf_counter() - inicio
    assert all(j["status"] == "DONE" for j in jobs)
    assert duracion < 4   # 6 llamadas de 0,2 s por job: en serie ≈ 6 s, en paralelo ≈ 1,2 s


def test_memoria_de_conversacion_entre_preguntas():
    deps = fabrica()
    with cliente(deps) as c:
        primera = preguntar(c, CONSULTA)
        esperar(c, primera["job_id"], {"DONE"})
        conv = primera["conversation_id"]
        segunda = preguntar(c, "¿Y si el descuento fuera del 20 %?", conv)
        job = esperar(c, segunda["job_id"], {"DONE", "FAILED"})
        assert job["resultado"]["recorrido"] == ["investigador", "analista", "sintetizador"]   # pizarra reiniciada
        historial = c.get(f"/conversations/{conv}").json()
    assert [t["pregunta"] for t in historial["turnos"]] == [CONSULTA, "¿Y si el descuento fuera del 20 %?"]
    # el supervisor del 2.º turno recibió el turno anterior como contexto
    ultimo_supervisor = deps.llms["supervisor"].recibidos[-1][1].content
    assert "Contexto de la conversación" in ultimo_supervisor and CONSULTA in ultimo_supervisor


def test_una_pregunta_por_vez_por_conversacion():
    with cliente(fabrica(demora=0.3)) as c:
        primera = preguntar(c, CONSULTA)
        r = c.post("/tasks", json={"pregunta": "otra pregunta más", "conversation_id": primera["conversation_id"]})
        assert r.status_code == 409


def test_la_conversacion_sobrevive_a_un_reinicio_del_servidor():
    redis, saver = fakeredis.aioredis.FakeRedis(), MemorySaver()   # en producción: Redis + AsyncRedisSaver
    with cliente(fabrica(redis=redis, saver=saver)) as c:
        primera = preguntar(c, CONSULTA)
        esperar(c, primera["job_id"], {"DONE"})
    # "reinicio": una app nueva, con modelos nuevos, conectada al mismo almacenamiento
    deps2 = fabrica(redis=redis, saver=saver)
    with cliente(deps2) as c:
        conv = primera["conversation_id"]
        assert len(c.get(f"/conversations/{conv}").json()["turnos"]) == 1
        esperar(c, preguntar(c, "¿Y la seña cuánto sería?", conv)["job_id"], {"DONE"})
        assert CONSULTA in deps2.llms["supervisor"].recibidos[0][1].content


def test_sse_transmite_el_progreso_del_grafo():
    with cliente(fabrica(demora=0.05)) as c:
        jid = preguntar(c, CONSULTA)["job_id"]
        with c.stream("GET", f"/tasks/{jid}/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            cuerpo = "".join(r.iter_text())
    pasos = [json.loads(linea[6:]) for linea in cuerpo.splitlines() if linea.startswith("data: ") and '"nodo"' in linea]
    assert [p["nodo"] for p in pasos][:3] == ["supervisor", "investigador", "supervisor"]
    assert "sintetizador" in [p["nodo"] for p in pasos]
    assert 'event: estado\ndata: {"status": "DONE"}' in cuerpo
