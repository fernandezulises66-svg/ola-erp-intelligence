#!/usr/bin/env bash
# Levanta todo el sistema con una sola instrucción:  ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Creé .env a partir de .env.example: completá tus API keys y volvé a correr ./run.sh"
  exit 1
fi
docker compose up --build -d
echo -n "Esperando a la API"
for _ in $(seq 1 60); do
  if curl -fs http://localhost:8000/health >/dev/null 2>&1; then
    echo; curl -s http://localhost:8000/health; echo
    echo "API: http://localhost:8000/docs · Phoenix: http://localhost:6006"
    exit 0
  fi
  echo -n "."; sleep 2
done
echo; echo "La API no respondió: revisá 'docker compose logs api'"; exit 1
