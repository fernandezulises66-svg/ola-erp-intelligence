"""Logging legible en consola (UTF-8 también en Windows) y silencio para las librerías ruidosas."""

import logging
import sys

RUIDOSOS = ("httpx", "httpx2", "httpcore", "anthropic", "openai", "pinecone", "urllib3", "redisvl",
            "opentelemetry", "aiohttp", "uvicorn.access")


def setup_logging(nivel: int = logging.INFO) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=nivel, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    for nombre in RUIDOSOS:
        logging.getLogger(nombre).setLevel(logging.WARNING)
