"""Endpoints. Todos son async y solo hacen I/O async contra Redis: ninguno llama al LLM."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage

from app.agents.common import texto_de
from app.api.schemas import (ApprovalOut, ConversationHistory, ConversationOut, DecisionIn, HealthOut, JobOut,
                             TaskCreated, TaskIn, Turno)
from app.core.config import get_settings
from app.services.jobs import TERMINALES, JobStatus, JobStore

router = APIRouter()


def _store(request: Request) -> JobStore:
    return request.app.state.deps.store


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated, tags=["tareas"])
async def crear_tarea(tarea: TaskIn, request: Request) -> TaskCreated:
    """Encola la pregunta y responde enseguida con el `job_id`. Sin `conversation_id` abre una conversación nueva."""
    store = _store(request)
    conv = tarea.conversation_id
    if conv is None:
        conv = await store.crear_conversacion()
    elif await store.conversacion(conv) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe esa conversación")
    job = await store.crear(tarea.pregunta, conv)
    if not await store.reservar_conversacion(conv, job["job_id"]):
        await store.actualizar(job["job_id"], JobStatus.FAILED, error="La conversación ya tiene una pregunta en curso")
        raise HTTPException(status.HTTP_409_CONFLICT, "La conversación tiene una pregunta en curso; esperá a que termine")
    await store.encolar(job["job_id"], "start")
    jid = job["job_id"]
    return TaskCreated(job_id=jid, conversation_id=conv, status=JobStatus.PENDING,
                       links={"estado": f"/tasks/{jid}", "progreso_sse": f"/tasks/{jid}/events",
                              "aprobar": f"/tasks/{jid}/approve", "conversacion": f"/conversations/{conv}"})


@router.get("/tasks/{job_id}", response_model=JobOut, tags=["tareas"])
async def estado_tarea(job_id: str, request: Request) -> JobOut:
    job = await _store(request).obtener(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese job (o ya expiró)")
    return JobOut.model_validate(job)


@router.get("/tasks/{job_id}/events", tags=["tareas"])
async def progreso_tarea(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events: transmite cada paso del grafo a medida que ocurre, hasta que el job termina o se pausa."""
    store = _store(request)
    if await store.obtener(job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese job")

    async def eventos() -> AsyncIterator[str]:
        enviados = 0
        while True:
            for e in await store.leer_progreso(job_id, enviados):
                enviados += 1
                yield f"event: paso\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
            job = await store.obtener(job_id)
            if job is None or job["status"] in TERMINALES | {JobStatus.WAITING_APPROVAL}:
                yield f"event: estado\ndata: {json.dumps({'status': job['status'] if job else 'NO_EXISTE'})}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(eventos(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/tasks/{job_id}/approve", status_code=status.HTTP_202_ACCEPTED, response_model=ApprovalOut,
             tags=["human-in-the-loop"])
async def aprobar(job_id: str, decision: DecisionIn, request: Request) -> ApprovalOut:
    """Decisión humana para un job pausado: lo reencola y el worker reanuda el grafo desde el checkpoint."""
    store = _store(request)
    job = await store.obtener(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe ese job")
    if job["status"] != JobStatus.WAITING_APPROVAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"El job no espera aprobación (estado: {job['status']})")
    await store.actualizar(job_id, JobStatus.PENDING, aprobacion=decision.model_dump())
    await store.borrar_campo(job_id, "aprobacion_pendiente")
    await store.encolar(job_id, "resume", decision.model_dump())
    return ApprovalOut(job_id=job_id, status=JobStatus.PENDING, decision=decision)


@router.post("/conversations", status_code=status.HTTP_201_CREATED, response_model=ConversationOut,
             tags=["conversaciones"])
async def crear_conversacion(request: Request) -> ConversationOut:
    return ConversationOut(conversation_id=await _store(request).crear_conversacion())


@router.get("/conversations/{conversation_id}", response_model=ConversationHistory, tags=["conversaciones"])
async def historial_conversacion(conversation_id: str, request: Request) -> ConversationHistory:
    """Historial leído del checkpointer de LangGraph: sobrevive a un reinicio del servidor."""
    deps = request.app.state.deps
    conv = await deps.store.conversacion(conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe esa conversación")
    snapshot = await deps.graph.aget_state({"configurable": {"thread_id": conversation_id}})
    turnos: list[Turno] = []
    for m in (snapshot.values or {}).get("messages", []):
        if m.type == "human":
            turnos.append(Turno(pregunta=texto_de(m), respuesta=""))
        elif isinstance(m, AIMessage) and turnos:
            turnos[-1].respuesta = (turnos[-1].respuesta + "\n\n" + texto_de(m)).strip()
    return ConversationHistory(conversation_id=conversation_id, turnos=turnos, job_activo=conv.get("job_activo") or None)


@router.get("/health", response_model=HealthOut, tags=["operación"])
async def health(request: Request) -> HealthOut:
    deps = request.app.state.deps
    s = get_settings()
    redis_ok = bool(await deps.store.redis.ping())
    faltantes = s.faltantes()
    return HealthOut(status="ok" if redis_ok and not faltantes else "degradado", redis=redis_ok, rag_mode=s.rag_mode,
                     busqueda_vectorial=deps.retriever.vector_store is not None, worker_embebido=s.worker_embedded,
                     keys_faltantes=faltantes)
