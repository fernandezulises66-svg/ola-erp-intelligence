"""Agentes: supervisor, investigador (RAG), analista (cálculos) y sintetizador (cadena LCEL)."""

import warnings

# `create_react_agent` (langgraph.prebuilt) sigue funcionando en LangGraph 1.x; su sucesor es
# `langchain.agents.create_agent`. Silenciamos el aviso para que los logs queden limpios.
warnings.filterwarnings("ignore", message="create_react_agent has been moved")
