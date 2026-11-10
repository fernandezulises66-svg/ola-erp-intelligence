"""
Recuperador híbrido (Módulo 4): BM25 local + búsqueda vectorial en Pinecone, fusionados con
Reciprocal Rank Fusion (RRF), con prioridad para identificadores exactos.

- BM25 encuentra términos y códigos literales ("VTA-410-PRES", "seña").
- La búsqueda vectorial encuentra paráfrasis ("¿cuánto dura un presupuesto?").
- RRF suma 1/(60 + posición) de cada lista, ponderado: premia lo que aparece arriba en las dos.
- Si la consulta trae un identificador (ERR-AFIP-102, OLA-RIB-0042), los chunks que lo contienen
  van primero: en la PE4 vimos que RRF podía bajar un chunk que BM25 ponía 1.º y la vectorial no traía.
- Si Pinecone falla o no está configurado, degrada a solo BM25 y lo deja registrado (no se cae).
"""

import asyncio
import logging
import re
import unicodedata
from typing import Protocol

import snowballstemmer
from langchain_core.documents import Document

from app.agents.schemas import Fragmento
from app.core.config import get_settings
from app.observability import observar

logger = logging.getLogger("rag.hibrido")

IDENTIFICADOR = re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
RRF_K = 60
STOPWORDS = set("""a al algo como con cual cuando de del desde donde el ella en entre es esa ese esta este hay la las
le lo los mas me mi no o para pero por que se si sin sobre su sus un una uno y ya qué cómo cuál cuándo dónde
hace hacer tiene tienen son ser está están puede pueden quien quién cuánto cuanto cuántos cuantos según""".split())


_STEMMER = snowballstemmer.stemmer("spanish")


def _raiz(tok: str) -> str:
    """Stemming en español solo para palabras: "descuentos"/"descuento" → "descuent". Los códigos y
    números (VTA-410-PRES, 15) quedan intactos para que la coincidencia literal siga funcionando."""
    return tok if any(c.isdigit() for c in tok) or "-" in tok else _STEMMER.stemWord(tok)


def tokenize(texto: str) -> list[str]:
    """Minúsculas, sin acentos y con stemming; conserva códigos con guiones como un token y además sus partes."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    tokens = []
    for tok in re.findall(r"[a-z0-9]+(?:[-_/][a-z0-9]+)*", texto):
        if tok in STOPWORDS:
            continue
        tokens.append(_raiz(tok))
        if re.search(r"[-_/]", tok):
            tokens += [_raiz(p) for p in re.split(r"[-_/]", tok) if p and p not in STOPWORDS]
    return tokens


class VectorStore(Protocol):
    async def buscar(self, consulta: str, k: int) -> list[tuple[Document, float]]: ...


class HybridRetriever:
    def __init__(self, chunks: list[Document], vector_store: VectorStore | None) -> None:
        from rank_bm25 import BM25Okapi

        self.s = get_settings()
        self.chunks = list(chunks)
        self.vector_store = vector_store
        self.bm25 = BM25Okapi([tokenize(c.metadata.get("title", "") + " " + c.page_content) for c in self.chunks])

    def _bm25(self, consulta: str) -> list[tuple[Document, float]]:
        scores = self.bm25.get_scores(tokenize(consulta))
        ranking = sorted(zip(self.chunks, scores), key=lambda x: -x[1])
        return [(c, float(s)) for c, s in ranking[: self.s.rag_candidates] if s > 0]

    def _exactos(self, consulta: str, bm25: list[tuple[Document, float]]) -> list[Document]:
        ids = {i.lower() for i in IDENTIFICADOR.findall(consulta)}
        if not ids:
            return []
        return [c for c, _ in bm25 if any(i in c.page_content.lower() for i in ids)]

    async def _vectorial(self, consulta: str) -> list[tuple[Document, float]]:
        if self.vector_store is None:
            return []
        try:
            return await self.vector_store.buscar(consulta, self.s.rag_candidates)
        except Exception as e:   # degradación controlada: seguimos con BM25
            logger.warning("Búsqueda vectorial no disponible (%s): se usa solo BM25", e)
            return []

    @observar("rag.busqueda_hibrida", kind="RETRIEVER")
    async def buscar(self, consulta: str, k: int | None = None) -> list[Fragmento]:
        k = k or self.s.rag_top_k
        bm25, vectorial = await asyncio.gather(asyncio.to_thread(self._bm25, consulta), self._vectorial(consulta))
        exactos = self._exactos(consulta, bm25)

        puntaje: dict[str, float] = {}
        docs: dict[str, Document] = {}
        metodos: dict[str, set[str]] = {}
        for nombre, lista, peso in (("bm25", bm25, self.s.rag_bm25_weight),
                                    ("vectorial", vectorial, 1 - self.s.rag_bm25_weight)):
            for pos, (doc, _) in enumerate(lista, start=1):
                cid = doc.metadata["chunk_id"]
                puntaje[cid] = puntaje.get(cid, 0) + peso / (RRF_K + pos)
                docs.setdefault(cid, doc)
                metodos.setdefault(cid, set()).add(nombre)

        orden = [c.metadata["chunk_id"] for c in exactos]
        orden += [cid for cid, _ in sorted(puntaje.items(), key=lambda x: -x[1]) if cid not in orden]
        for c in exactos:
            metodos.setdefault(c.metadata["chunk_id"], set()).add("exacto")
            docs.setdefault(c.metadata["chunk_id"], c)

        resultado = [Fragmento(fuente=docs[cid].metadata.get("source", "?"), titulo=docs[cid].metadata.get("title", ""),
                               pagina=int(docs[cid].metadata.get("page", 1)), chunk_id=cid,
                               texto=" ".join(docs[cid].page_content.split())[: self.s.rag_fragment_max_chars],
                               score=round(puntaje.get(cid, 0.0), 5), metodos=sorted(metodos.get(cid, set())))
                     for cid in orden[:k]]
        logger.info("'%s' → %s", consulta[:60], [f"{f.chunk_id}({'+'.join(f.metodos)})" for f in resultado])
        return resultado
