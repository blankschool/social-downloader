#!/bin/bash

# Social Media Transcription — script de inicialização
# Uso local (com ngrok): ./start.sh
# Uso VPS (sem ngrok):   VPS_MODE=true ./start.sh
#                        ou simplesmente: ./start.sh (ngrok é pulado se sem NGROK_AUTH_TOKEN)

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Cores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Social Media Transcription${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# Carregar variáveis de ambiente do .env
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
fi

# Verificar API_KEY obrigatória
if [ -z "${API_KEY:-}" ]; then
    echo -e "${RED}Erro: API_KEY não configurada${NC}"
    echo "Adicione ao .env:  API_KEY=sua_chave_aqui"
    exit 1
fi

# Verificar venv
if [ ! -d "venv" ]; then
    echo -e "${RED}Erro: venv não encontrado${NC}"
    echo "Execute: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Modo VPS: escuta em 0.0.0.0 para ser acessível externamente
VPS_MODE="${VPS_MODE:-false}"
BIND_HOST="127.0.0.1"
if [ "$VPS_MODE" = "true" ]; then
    BIND_HOST="0.0.0.0"
    echo -e "${YELLOW}Modo VPS: backend escutando em 0.0.0.0:8000${NC}"
fi

# Matar processos anteriores na porta 8000
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Parando processo anterior na porta 8000...${NC}"
    kill -9 $(lsof -t -i:8000) 2>/dev/null || true
    sleep 1
fi

echo -e "${GREEN}1. Iniciando API Backend (porta 8000)...${NC}"
source venv/bin/activate
python3 -m uvicorn backend.main:app --host "$BIND_HOST" --port 8000 &
API_PID=$!
sleep 2

# Verificar se API iniciou
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${RED}Erro: API não iniciou corretamente${NC}"
    kill $API_PID 2>/dev/null || true
    exit 1
fi
echo -e "${GREEN}   ✓ API rodando${NC}"

# Ngrok: opcional — só inicia se NGROK_AUTH_TOKEN estiver definido e VPS_MODE=false
NGROK_PID=""
if [ "${VPS_MODE}" != "true" ] && [ -n "${NGROK_AUTH_TOKEN:-}" ]; then
    echo ""
    echo -e "${GREEN}2. Iniciando ngrok tunnel...${NC}"
    # Injeta o token no ngrok antes de subir
    ngrok config add-authtoken "$NGROK_AUTH_TOKEN" --config="$ROOT_DIR/ngrok.yml" > /dev/null 2>&1 || true
    ngrok start --config="$ROOT_DIR/ngrok.yml" backend &
    NGROK_PID=$!
    sleep 3
    PUBLIC_URL="https://savedown.ngrok.app"
elif [ "${VPS_MODE}" = "true" ]; then
    PUBLIC_URL="http://$(curl -s ifconfig.me 2>/dev/null || echo 'SEU_IP'):8000"
else
    PUBLIC_URL="http://localhost:8000"
fi

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}Sistema iniciado!${NC}"
echo ""
echo -e "  ${CYAN}URL Pública:${NC} $PUBLIC_URL"
echo -e "  ${CYAN}API Local:${NC}   http://localhost:8000"
echo -e "  ${CYAN}Frontend:${NC}    http://localhost:8000/ui"
echo ""
echo -e "${YELLOW}Pressione CTRL+C para parar${NC}"
echo -e "${CYAN}========================================${NC}"

cleanup() {
    echo ""
    echo -e "${YELLOW}Parando serviços...${NC}"
    kill $API_PID 2>/dev/null || true
    [ -n "$NGROK_PID" ] && kill $NGROK_PID 2>/dev/null || true
    echo -e "${GREEN}Serviços parados.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

wait
