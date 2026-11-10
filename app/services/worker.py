"""
Worker: consume la cola de Redis y corre el grafo en segundo plano.

Mensajes: {"job_id", "tipo": "start" | "resume", "payload"}.
- start:  nueva pregunta en la conversación del job (`entrada_turno`).
- resume: reanuda una pausa humana con `Command(resume=decisión)`.

El grafo se ejecuta con `astream(stream_mode="updates")`: cada nodo que termina se publica como
progreso (lo transmite GET /tasks/{id}/events por SSE). Al final se lee el estado del checkpointer
para saber si terminó o quedó pausado. Cualquier excepción deja el job en FAILED con el error.

Corre dentro de la API (WORKER_EMBEDDED=true) con N tareas asyncio, o aparte: `python -m app.services.worker`.
"""

import asyncio
import logging
import time
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import ValidationError

from app.core.config import get_settings
from app.graph.builder import run_config
from app.graph.state import entrada_turno
from app.observability import span_job
from app.services.jobs import JobStatus, JobStore, MensajeCola

logger = logging.getLogger("worker")


def describir(nodo: str, update: dict[str, Any]) -> str:
    """Una línea legible por paso del grafo (para el progreso en vivo)."""
    if nodo == "supervisor" and update.get("decisiones"):
        d = update["decisiones"][-1]
        return f"→ {d['siguiente']} ({d['origen']}): {d['motivo']}"
    if nodo in ("investigador", "analista") and update.get("contribuciones"):
        c = update["contribuciones"][-1]
        fuentes = f" · fuentes: {', '.join(c['fuentes'])}" if c["fuentes"] else ""
        return f"herramientas: {', '.join(c['herramientas']) or 'ninguna'}{fuentes}"
    if nodo == "sintetizador":
        return f"respuesta lista ({len(update.get('respuesta_final', ''))} caracteres)"
    if nodo == "evaluar_riesgo":
        return update.get("motivo_criticidad", "")
    if nodo == "aprobacion_humana":
        return f"decisión: {update.get('aprobacion')}"
    if nodo == "ejecutar_accion":
        return update.get("accion_resultado", "")
    return ""


def resumen(estado: dict[str, Any]) -> dict[str, Any]:
    return {
        "respuesta_final": estado.get("respuesta_final", ""),
        "fuentes": estado.get("fuentes", []),
        "supuestos": estado.get("supuestos", []),
        "recorrido": [d["siguiente"] for d in estado.get("decisiones", [])],
        "delegaciones": estado.get("delegaciones", 0),
        "contribuciones": [{"agente": c["agente"], "ronda": c["ronda"], "herramientas": c["herramientas"],
                            "fuentes": c["fuentes"]} for c in estado.get("contribuciones", [])],
        "es_critica": estado.get("es_critica", False),
        "motivo_criticidad": estado.get("motivo_criticidad", ""),
        "accion_propuesta": estado.get("accion_propuesta"),
        "aprobacion": estado.get("aprobacion"),
        "accion_resultado": estado.get("accion_resultado", ""),
    }


class Worker:
    def __init__(self, store: JobStore, graph: CompiledStateGraph, concurrency: int | None = None) -> None:
        self.store, self.graph = store, graph
        self.concurrency = concurrency or get_settings().worker_concurrency
        self._tareas: list[asyncio.Task] = []
        self._activo = False

    def start(self) -> None:
        self._activo = True
        self._tareas = [asyncio.create_task(self._loop(i), name=f"worker-{i}") for i in range(self.concurrency)]
        logger.info("Worker iniciado con %d tareas concurrentes", self.concurrency)

    async def stop(self) -> None:
        self._activo = False
        for t in self._tareas:
            t.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)

    async def _loop(self, n: int) -> None:
        while self._activo:
            try:
                mensaje = await self.store.desencolar(timeout=1)
            except asyncio.CancelledError:
                raise
            except ValidationError as e:     # mensaje mal formado: se descarta sin frenar al worker
                logger.error("worker-%d: mensaje inválido en la cola, descartado: %s", n, e)
                continue
            except Exception:
                logger.exception("worker-%d: error leyendo la cola; reintento en 1 s", n)
                await asyncio.sleep(1)
                continue
            if mensaje:
                await self.procesar(mensaje)

    async def procesar(self, mensaje: MensajeCola) -> None:
        job_id, tipo = mensaje.job_id, mensaje.tipo
        job = await self.store.obtener(job_id)
        if job is None:
            logger.warning("Job %s no existe (¿expiró?): se descarta", job_id)
            return
        conv = job["conversation_id"]
        cfg = run_config(conv, job_id, tipo)
        entrada: Any = entrada_turno(job["pregunta"]) if tipo == "start" else Command(resume=mensaje.payload)
        inicio = time.perf_counter()
        with span_job(job_id, conv, tipo, {"pregunta": job["pregunta"], "payload": mensaje.payload}) as span:
            try:
                await self.store.actualizar(job_id, JobStatus.RUNNING)
                async for evento in self.graph.astream(entrada, cfg, stream_mode="updates"):
                    for nodo, update in evento.items():
                        if nodo != "__interrupt__":
                            await self.store.progreso(job_id, nodo, describir(nodo, update or {}))
                snapshot = await self.graph.aget_state(cfg)
                estado = snapshot.values
                if snapshot.interrupts:
                    await self.store.progreso(job_id, "aprobacion_humana", "pausado: esperando aprobación humana")
                    await self.store.actualizar(job_id, JobStatus.WAITING_APPROVAL,
                                                aprobacion_pendiente=snapshot.interrupts[0].value,
                                                resultado=resumen(estado))
                    span.set_attribute("output.value", "WAITING_APPROVAL")
                else:
                    await self.store.actualizar(job_id, JobStatus.DONE, resultado=resumen(estado),
                                                duracion_s=round(time.perf_counter() - inicio, 2))
                    salida = estado.get("accion_resultado") if tipo == "resume" else estado.get("respuesta_final")
                    span.set_attribute("output.value", (salida or "")[:4000])
                    await self.store.liberar_conversacion(conv, job_id)
                logger.info("job %s (%s) → %s en %.1f s", job_id, tipo,
                            "WAITING_APPROVAL" if snapshot.interrupts else "DONE", time.perf_counter() - inicio)
            except asyncio.CancelledError:
                await self.store.actualizar(job_id, JobStatus.FAILED, error="Worker detenido durante la ejecución")
                await self.store.liberar_conversacion(conv, job_id)
                raise
            except Exception as e:   # sin esto, el cliente haría polling para siempre
                logger.exception("job %s falló", job_id)
                span.record_exception(e)
                await self.store.actualizar(job_id, JobStatus.FAILED, error=f"{type(e).__name__}: {e}"[:1000])
                await self.store.liberar_conversacion(conv, job_id)


async def main() -> None:
    """Worker como proceso independiente (escalable con varios procesos)."""
    from app.api.main import construir_dependencias

    async with construir_dependencias() as deps:
        worker = Worker(deps.store, deps.graph)
        worker.start()
        try:
            await asyncio.Event().wait()
        finally:
            await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
