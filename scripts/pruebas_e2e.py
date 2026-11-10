"""
Cinco pruebas de punta a punta contra la API levantada (cada una deja su traza en Arize Phoenix).

    python scripts/pruebas_e2e.py                 # las 5 pruebas (la crítica se aprueba como "gerencia")
    python scripts/pruebas_e2e.py --tras-reinicio # después de `docker compose restart api`: retoma la
                                                  # conversación de la prueba 2 y verifica que recuerde

| # | Qué prueba                    | Capacidad                                             |
|---|-------------------------------|-------------------------------------------------------|
| 1 | Código de error exacto        | RAG híbrido con prioridad de identificadores          |
| 2 | Vigencia, descuento, seña, rol| Razonamiento multi-paso: investigador → analista      |
| 3 | "¿Y si fuera el 20 %?"        | Memoria de conversación (misma conversation_id)       |
| 4 | Diferencia de caja            | Multi-paso en otro módulo del manual                  |
| 5 | "Aplicá el descuento…"        | Human-in-the-loop: pausa, aprobación y acción en el ERP|

Solo necesita `httpx` (pip install httpx).
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SALIDA = Path(__file__).resolve().parent.parent / "resultados_pruebas.json"

PRUEBAS = {
    1: "¿Qué significa el error VTA-410-PRES y cómo se soluciona?",
    2: "Hoy es 10/11/2026. Un cliente pidió el 28/10/2026 un presupuesto por una biblioteca a medida de $1.180.000 "
       "y quiere confirmarlo con un 18 % de descuento. Según el manual del ERP: ¿el presupuesto sigue vigente?, "
       "¿cuánto queda el precio con el descuento y cuánto tiene que pagar de seña?, ¿qué rol puede aprobar ese descuento?",
    3: "¿Y si el descuento fuera del 20 %? ¿Cuánto quedaría el precio, cuánto la seña y quién lo aprueba?",
    4: "Hoy es 10/11/2026. Un cierre de caja dio una diferencia de $7.300. Según el manual, ¿hace falta "
       "justificación? ¿Por cuánto supera el umbral?",
    5: "Hoy es 10/11/2026. Aplicá un descuento del 18 % al presupuesto P-1042 de $1.180.000 (biblioteca a medida, "
       "emitido el 28/10/2026) y registralo en el ERP con la seña que corresponda.",
}
TRAS_REINICIO = "Recordame de qué presupuesto estábamos hablando y cuál era la seña con el 20 % de descuento."


async def esperar(c: httpx.AsyncClient, job_id: str, n: int, aprobar: bool = True) -> dict:
    while True:
        await asyncio.sleep(2)
        job = (await c.get(f"/tasks/{job_id}")).json()
        if job["status"] in ("DONE", "FAILED"):
            return job
        if job["status"] == "WAITING_APPROVAL" and aprobar:
            accion = job["aprobacion_pendiente"]["accion_propuesta"]["tipo"]
            print(f"  [{n}] WAITING_APPROVAL ({accion}) → apruebo como gerencia")
            await c.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "revisor": "gerencia",
                                                           "comentario": "prueba e2e"})


async def mostrar_progreso(c: httpx.AsyncClient, job_id: str, n: int) -> None:
    """Consume el endpoint SSE e imprime cada paso del grafo en vivo."""
    async with c.stream("GET", f"/tasks/{job_id}/events") as r:
        async for linea in r.aiter_lines():
            if linea.startswith("data: ") and '"nodo"' in linea:
                paso = json.loads(linea[6:])
                print(f"  [{n}] {paso['nodo']:<18} {paso['detalle'][:110]}")


async def correr(c: httpx.AsyncClient, n: int, pregunta: str, conv: str | None = None, sse: bool = False) -> dict:
    inicio = time.perf_counter()
    r = await c.post("/tasks", json={"pregunta": pregunta} | ({"conversation_id": conv} if conv else {}))
    r.raise_for_status()
    creado = r.json()
    print(f"[{n}] encolado {creado['job_id'][:8]} (conversación {creado['conversation_id'][:8]}) "
          f"en {(time.perf_counter() - inicio) * 1000:.0f} ms")
    tareas = [esperar(c, creado["job_id"], n)] + ([mostrar_progreso(c, creado["job_id"], n)] if sse else [])
    job = (await asyncio.gather(*tareas))[0]
    res = job.get("resultado") or {}
    return {"prueba": n, "job_id": creado["job_id"], "conversation_id": creado["conversation_id"],
            "status": job["status"], "latencia_s": round(time.perf_counter() - inicio, 1),
            "recorrido": " → ".join(res.get("recorrido", [])), "fuentes": res.get("fuentes", []),
            "accion": res.get("accion_resultado", ""), "error": job.get("error", ""),
            "respuesta": res.get("respuesta_final", "")}


def imprimir(resultados: list[dict]) -> None:
    print(f"\n{'#':<3}{'estado':<8}{'latencia':>9}  recorrido")
    for r in sorted(resultados, key=lambda x: x["prueba"]):
        extra = f"  [{r['accion']}]" if r["accion"] else ""
        print(f"{r['prueba']:<3}{r['status']:<8}{r['latencia_s']:>8}s  {r['recorrido']}{extra}"
              + (f"  ERROR: {r['error']}" if r["error"] else ""))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--tras-reinicio", action="store_true")
    args = parser.parse_args()

    async with httpx.AsyncClient(base_url=args.url, timeout=60) as c:
        if args.tras_reinicio:
            previo = json.loads(SALIDA.read_text(encoding="utf-8"))
            conv = next(r["conversation_id"] for r in previo["resultados"] if r["prueba"] == 2)
            historial = (await c.get(f"/conversations/{conv}")).json()
            print(f"Conversación {conv[:8]} después del reinicio: {len(historial['turnos'])} turnos guardados")
            r = await correr(c, 6, TRAS_REINICIO, conv, sse=True)
            print(f"\nRespuesta:\n{r['respuesta']}")
            return

        inicio = time.perf_counter()
        r1, r2, r4, r5 = await asyncio.gather(correr(c, 1, PRUEBAS[1]), correr(c, 2, PRUEBAS[2], sse=True),
                                              correr(c, 4, PRUEBAS[4]), correr(c, 5, PRUEBAS[5]))
        print("\n[3] pregunta de seguimiento en la conversación de la prueba 2")
        r3 = await correr(c, 3, PRUEBAS[3], r2["conversation_id"])
        resultados = [r1, r2, r3, r4, r5]
        imprimir(resultados)
        print(f"\nTiempo total: {time.perf_counter() - inicio:.1f} s · respuestas completas en {SALIDA.name}")
        SALIDA.write_text(json.dumps({"resultados": resultados}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
