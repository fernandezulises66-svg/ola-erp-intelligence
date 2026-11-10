"""
Fábrica de chat models (la capa de "cliente" del Módulo 1, ahora sobre los SDK asíncronos oficiales).

- Proveedor y modelo por variables de entorno: nada hardcodeado.
- Reintentos con backoff ante errores de red/429/5xx (`max_retries` del SDK) y `timeout` por llamada.
- Todas las llamadas del sistema usan `ainvoke`/`astream`: el SDK async de Anthropic u OpenAI (httpx)
  nunca bloquea el event loop.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel

from app.core.config import get_settings


def crear_llm(max_tokens: int | None = None) -> BaseChatModel:
    s = get_settings()
    if s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=s.openai_chat_model, max_retries=s.llm_max_retries, timeout=s.llm_timeout_s,
                          max_tokens=max_tokens or s.llm_max_tokens)

    from langchain_anthropic import ChatAnthropic

    params: dict[str, Any] = {"model": s.anthropic_model, "max_tokens": max_tokens or s.llm_max_tokens,
                              "max_retries": s.llm_max_retries, "timeout": s.llm_timeout_s}
    if s.anthropic_workspace_id:          # keys no asociadas a un workspace
        params["default_headers"] = {"anthropic-workspace-id": s.anthropic_workspace_id}
    return ChatAnthropic(**params)        # los modelos Claude 5.x no aceptan `temperature`: no se envía
