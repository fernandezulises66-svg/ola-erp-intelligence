"""
Estado en Redis (todo con redis.asyncio): jobs, conversaciones, cola, progreso y el "ERP" simulado.

Claves (prefijo `ois:`):
- ois:job:{id}               hash con el estado del job (status, pregunta, conversación, resultado, error…)
- ois:job:{id}:eventos       transiciones de estado (auditoría)
- ois:job:{id}:progreso      pasos del grafo a medida que ocurren (lo que transmite el endpoint SSE)
- ois:conv:{id}              hash de la conversación (job activo, cantidad de turnos)
- ois:queue                  cola de trabajo (RPUSH / BLPOP)
- erp:acciones               acciones ejecutadas en el ERP tras una aprobación humana
Los checkpoints de LangGraph (la memoria de cada conversación) los guarda aparte el AsyncRedisSaver.
"""

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings

P = "ois"
COLA = f"{P}:queue"
ERP_ACCIONES = "erp:acciones"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


TERMINALES = {JobStatus.DONE, JobStatus.FAILED}


def ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _texto(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else v


class JobStore:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.ttl = get_settings().job_ttl_s

    # --- Conversaciones -----------------------------------------------------------------
    async def crear_conversacion(self) -> str:
        cid = uuid.uuid4().hex
        await self.redis.hset(f"{P}:conv:{cid}", mapping={"created_at": ahora(), "turnos": 0, "job_activo": ""})
        return cid

    async def conversacion(self, cid: str) -> dict[str, Any] | None:
        datos = await self.redis.hgetall(f"{P}:conv:{cid}")
        return {_texto(k): _texto(v) for k, v in datos.items()} if datos else None

    async def reservar_conversacion(self, cid: str, job_id: str) -> bool:
        """Una pregunta por vez por conversación (dos turnos simultáneos pisarían el mismo checkpoint).
        WATCH/MULTI: si otro request la reserva al mismo tiempo, solo uno gana."""
        clave = f"{P}:conv:{cid}"
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.watch(clave)
            activo = _texto(await pipe.hget(clave, "job_activo") or b"")
            if activo:
                estado = _texto(await pipe.hget(f"{P}:job:{activo}", "status") or b"")
                if estado and estado not in TERMINALES:
                    await pipe.unwatch()
                    return False
            pipe.multi()
            pipe.hset(clave, mapping={"job_activo": job_id, "updated_at": ahora()})
            pipe.hincrby(clave, "turnos", 1)
            await pipe.execute()
        return True

    async def liberar_conversacion(self, cid: str, job_id: str) -> None:
        clave = f"{P}:conv:{cid}"
        if _texto(await self.redis.hget(clave, "job_activo") or b"") == job_id:
            await self.redis.hset(clave, "job_activo", "")

    # --- Jobs -------------------------------------------------------------------------
    async def crear(self, pregunta: str, conversation_id: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        t = ahora()
        job = {"job_id": job_id, "conversation_id": conversation_id, "status": JobStatus.PENDING,
               "pregunta": pregunta, "created_at": t, "updated_at": t}
        clave = f"{P}:job:{job_id}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(clave, mapping=job)
            pipe.rpush(f"{clave}:eventos", json.dumps({"status": JobStatus.PENDING, "at": t}))
            for k in (clave, f"{clave}:eventos"):
                pipe.expire(k, self.ttl)
            await pipe.execute()
        return job

    async def obtener(self, job_id: str) -> dict[str, Any] | None:
        clave = f"{P}:job:{job_id}"
        datos = await self.redis.hgetall(clave)
        if not datos:
            return None
        job: dict[str, Any] = {_texto(k): _texto(v) for k, v in datos.items()}
        for campo in ("resultado", "aprobacion_pendiente", "aprobacion"):
            if campo in job:
                job[campo] = json.loads(job[campo])
        job["eventos"] = [json.loads(e) for e in await self.redis.lrange(f"{clave}:eventos", 0, -1)]
        return job

    async def actualizar(self, job_id: str, status: JobStatus | None = None, **campos: Any) -> None:
        clave = f"{P}:job:{job_id}"
        valores = {k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v))
                   for k, v in campos.items() if v is not None}
        t = ahora()
        valores["updated_at"] = t
        async with self.redis.pipeline(transaction=True) as pipe:
            if status:
                valores["status"] = status
                pipe.rpush(f"{clave}:eventos", json.dumps({"status": status, "at": t}))
            pipe.hset(clave, mapping=valores)
            await pipe.execute()

    async def borrar_campo(self, job_id: str, campo: str) -> None:
        await self.redis.hdel(f"{P}:job:{job_id}", campo)

    # --- Progreso (para el streaming SSE) ----------------------------------------------------
    async def progreso(self, job_id: str, nodo: str, detalle: str) -> None:
        clave = f"{P}:job:{job_id}:progreso"
        await self.redis.rpush(clave, json.dumps({"at": ahora(), "nodo": nodo, "detalle": detalle}, ensure_ascii=False))
        await self.redis.expire(clave, self.ttl)

    async def leer_progreso(self, job_id: str, desde: int = 0) -> list[dict[str, Any]]:
        return [json.loads(e) for e in await self.redis.lrange(f"{P}:job:{job_id}:progreso", desde, -1)]

    # --- Cola --------------------------------------------------------------------------
    async def encolar(self, job_id: str, tipo: str = "start", payload: dict[str, Any] | None = None) -> None:
        await self.redis.rpush(COLA, json.dumps({"job_id": job_id, "tipo": tipo, "payload": payload or {}}))

    async def desencolar(self, timeout: float = 1.0) -> dict[str, Any] | None:
        item = await self.redis.blpop([COLA], timeout=timeout)
        return json.loads(item[1]) if item else None

    # --- "ERP" simulado: la herramienta con efectos secundarios -------------------------------
    async def registrar_accion_erp(self, conversation_id: str, accion: dict[str, Any], aprobacion: dict[str, Any]) -> str:
        registro = {"conversation_id": conversation_id, "accion": accion,
                    "aprobado_por": aprobacion.get("revisor", "?"), "comentario": aprobacion.get("comentario", ""),
                    "registrado_at": ahora()}
        n = await self.redis.rpush(ERP_ACCIONES, json.dumps(registro, ensure_ascii=False))
        return f"Acción '{accion['tipo']}' registrada en el ERP (registro #{n}), aprobada por {registro['aprobado_por']}."
