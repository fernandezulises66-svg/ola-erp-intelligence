"""
Carga y chunking del manual técnico (Módulos 3 y 4): Markdown con front matter, JSON y PDF.

Cada chunk lleva metadatos (doc_id, título, fuente, página, categoría, tags) y un `chunk_id`
estable (`doc_id#p<página>-c<n>`): volver a ingerir sobrescribe en vez de duplicar, y BM25 (local)
y Pinecone (remoto) comparten los mismos ids, así la fusión puede deduplicar.
"""

import json
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings

logger = logging.getLogger("rag.documentos")
SEPARADORES = ["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""]


def _meta(meta: dict, source: str) -> dict:
    return {"doc_id": meta["doc_id"], "title": meta.get("title", source), "source": source,
            "category": meta.get("category", "general"), "tags": [str(t) for t in meta.get("tags", [])]}


def cargar_markdown(path: Path) -> list[Document]:
    texto = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", texto, re.DOTALL)
    if not match:
        raise ValueError(f"{path.name}: falta el front matter con doc_id")
    meta = yaml.safe_load(match.group(1))
    return [Document(page_content=texto[match.end():].strip(), metadata={**_meta(meta, path.name), "page": 1})]


def cargar_json(path: Path) -> list[Document]:
    """Un documento por módulo con todas sus entradas: chunks con contexto suficiente."""
    data = json.loads(path.read_text(encoding="utf-8"))
    por_modulo: dict[str, list[str]] = {}
    for e in data["entries"]:
        por_modulo.setdefault(e["modulo"], []).append(
            f"Código {e['codigo']} ({e['modulo']}): {e['descripcion']} Solución: {e['solucion']}")
    docs = []
    for i, (modulo, lineas) in enumerate(por_modulo.items(), start=1):
        meta = _meta(data, path.name) | {"page": i}
        meta["tags"] = meta["tags"] + [modulo.lower()]
        docs.append(Document(page_content=f"# {data['title']} — {modulo}\n\n" + "\n".join(lineas), metadata=meta))
    return docs


def cargar_pdf(path: Path, sidecar: dict) -> list[Document]:
    from pypdf import PdfReader

    if path.name not in sidecar:
        raise ValueError(f"{path.name}: agregá sus metadatos en _metadata.json")
    docs = []
    for numero, page in enumerate(PdfReader(str(path)).pages, start=1):
        texto = re.sub(r"-\n(?=[A-Z0-9])", "-", page.extract_text() or "")   # repara "ERR-\nAFIP-102"
        if texto.strip():
            docs.append(Document(page_content=texto.strip(), metadata={**_meta(sidecar[path.name], path.name),
                                                                      "page": numero}))
    return docs


def cargar_documentos(data_dir: Path | None = None) -> list[Document]:
    data_dir = data_dir or get_settings().data_dir
    sidecar_path = data_dir / "_metadata.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8")) if sidecar_path.exists() else {}
    docs: list[Document] = []
    for path in sorted(data_dir.iterdir()):
        if path.name.startswith("_"):
            continue
        if path.suffix == ".md":
            docs += cargar_markdown(path)
        elif path.suffix == ".json":
            docs += cargar_json(path)
        elif path.suffix == ".pdf":
            docs += cargar_pdf(path, sidecar)
    return docs


def _splitter() -> RecursiveCharacterTextSplitter:
    s = get_settings()
    try:
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base", chunk_size=s.chunk_size_tokens, chunk_overlap=s.chunk_overlap_tokens,
            separators=SEPARADORES)
    except Exception as exc:   # tiktoken baja su vocabulario la primera vez; sin red, se aproxima
        logger.warning("tiktoken no disponible (%s): se aproxima 1 token ≈ 4 caracteres", type(exc).__name__)
        contar: Callable[[str], int] = lambda t: max(1, len(t) // 4)
        return RecursiveCharacterTextSplitter(chunk_size=s.chunk_size_tokens, chunk_overlap=s.chunk_overlap_tokens,
                                              length_function=contar, separators=SEPARADORES)


def dividir(docs: list[Document]) -> list[Document]:
    chunks = _splitter().split_documents(docs)
    contador: dict[tuple, int] = {}
    for c in chunks:
        clave = (c.metadata["doc_id"], c.metadata["page"])
        contador[clave] = contador.get(clave, 0) + 1
        c.metadata["chunk_id"] = f"{c.metadata['doc_id']}#p{c.metadata['page']}-c{contador[clave]}"
    return chunks


@lru_cache(maxsize=1)
def cargar_chunks() -> tuple[Document, ...]:
    """Mismo proceso para la ingesta en Pinecone y para el índice BM25 local."""
    chunks = dividir(cargar_documentos())
    logger.info("Manual: %d chunks de %d archivos", len(chunks), len({c.metadata["source"] for c in chunks}))
    return tuple(chunks)
