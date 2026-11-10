"""
Graba corridas reales de la API para el modo replay de la demo web (web/demo/escenarios.json).

    python scripts/grabar_demo.py            # con la API levantada (docker compose up)

Para cada escenario guarda los pasos del grafo tal como los transmitió el endpoint SSE (nodo, detalle
y tiempo real), la pausa humana si la hubo, y el resultado final del job. La demo estática (Vercel)
reproduce exactamente eso: no inventa respuestas ni pasos.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SALIDA = Path(__file__).resolve().parent.parent / "web" / "demo" / "escenarios.json"

ESCENARIOS = [
    {"id": "presupuesto", "capacidad": "Multi-step reasoning",
     "pregunta": "Hoy es 10/11/2026. Un cliente pidió el 28/10/2026 un presupuesto por una biblioteca a medida de "
                 "$1.180.000 y quiere confirmarlo con un 18 % de descuento. Según el manual del ERP: ¿el presupuesto "
                 "sigue vigente?, ¿cuánto queda el precio con el descuento y cuánto tiene que pagar de seña?, ¿qué rol "
                 "puede aprobar ese descuento?"},
    {"id": "seguimiento", "capacidad": "Conversation memory", "seguimiento_de": "presupuesto",
     "pregunta": "¿Y si el descuento fuera del 20 %? ¿Cuánto quedaría el precio, cuánto la seña y quién lo aprueba?"},
    {"id": "codigo", "capacidad": "Hybrid RAG · exact code",
     "pregunta": "¿Qué significa el error VTA-410-PRES y cómo se soluciona?"},
    {"id": "caja", "capacidad": "Multi-step · cash desk",
     "pregunta": "Hoy es 10/11/2026. Un cierre de caja dio una diferencia de $7.300. Según el manual, ¿hace falta "
                 "justificación? ¿Por cuánto supera el umbral?"},
    {"id": "aprobacion", "capacidad": "Human approval",
     "pregunta": "Hoy es 10/11/2026. Aplicá un descuento del 18 % al presupuesto P-1042 de $1.180.000 (biblioteca a "
                 "medida, emitido el 28/10/2026) y registralo en el ERP con la seña que corresponda."},
]


async def pasos(c: httpx.AsyncClient, job_id: str) -> list[dict]:
    """Lee el stream SSE completo del job (devuelve todos los pasos registrados hasta ahora)."""
    eventos = []
    async with c.stream("GET", f"/tasks/{job_id}/events") as r:
        async for linea in r.aiter_lines():
            if linea.startswith("data: ") and '"nodo"' in linea:
                eventos.append(json.loads(linea[6:]))
    return eventos


def relativos(eventos: list[dict]) -> list[dict]:
    if not eventos:
        return []
    t0 = datetime.fromisoformat(eventos[0]["at"])
    return [{"t": int((datetime.fromisoformat(e["at"]) - t0).total_seconds() * 1000), "nodo": e["nodo"],
             "detalle": e["detalle"]} for e in eventos]


async def esperar(c: httpx.AsyncClient, job_id: str) -> dict:
    while True:
        await asyncio.sleep(1.5)
        job = (await c.get(f"/tasks/{job_id}")).json()
        if job["status"] in ("DONE", "FAILED", "WAITING_APPROVAL"):
            return job


async def grabar(c: httpx.AsyncClient, esc: dict, conversaciones: dict[str, str]) -> dict:
    conv = conversaciones.get(esc.get("seguimiento_de", ""))
    inicio = time.perf_counter()
    creado = (await c.post("/tasks", json={"pregunta": esc["pregunta"]} | ({"conversation_id": conv} if conv else {}))).json()
    conversaciones[esc["id"]] = creado["conversation_id"]
    job = await esperar(c, creado["job_id"])
    fases, previos = [], []
    while True:
        todos = await pasos(c, creado["job_id"])
        nuevos = todos[len(previos):]
        previos = todos
        eventos = relativos(nuevos)
        fase = {"estado": job["status"], "eventos": eventos, "duracion_ms": eventos[-1]["t"] if eventos else 0}
        if job["status"] != "WAITING_APPROVAL":
            fases.append(fase)
            break
        fase["aprobacion_pendiente"] = job["aprobacion_pendiente"]
        fases.append(fase)
        print(f"  {esc['id']}: WAITING_APPROVAL → apruebo como gerencia")
        await c.post(f"/tasks/{creado['job_id']}/approve",
                     json={"aprobado": True, "revisor": "gerencia", "comentario": "grabación de la demo"})
        job = await esperar(c, creado["job_id"])
    latencia = round(time.perf_counter() - inicio, 1)
    if job["status"] != "DONE":
        raise RuntimeError(f"{esc['id']} terminó en {job['status']}: {job.get('error')}")
    print(f"  {esc['id']}: DONE en {latencia} s · {' → '.join(job['resultado']['recorrido'])}")
    return {**esc, "fases": fases, "latencia_s": latencia, "resultado": job["resultado"]}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    async with httpx.AsyncClient(base_url=args.url, timeout=60) as c:
        salud = (await c.get("/health")).json()
        if not salud.get("busqueda_vectorial"):
            print("Aviso: la API no tiene búsqueda vectorial activa (RAG solo BM25).")
        conversaciones: dict[str, str] = {}
        grabados = []
        for esc in ESCENARIOS:            # en serie: los tiempos reflejan a un solo usuario
            print(f"Grabando '{esc['id']}'…")
            grabados.append(await grabar(c, esc, conversaciones))
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps({"grabado": datetime.now(UTC).isoformat(timespec="seconds"),
                                  "escenarios": grabados}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nListo: {SALIDA.relative_to(SALIDA.parents[2])} ({len(grabados)} escenarios)")


if __name__ == "__main__":
    asyncio.run(main())
