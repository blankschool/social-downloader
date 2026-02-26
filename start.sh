#!/bin/bash

# Script para iniciar o Social Media Transcription localmente com ngrok
# Uso: ./start.sh

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

# Carregar variáveis de ambiente do .env (incluindo NGROK_AUTH_TOKEN)
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
fi

if [ -z "${NGROK_AUTH_TOKEN:-}" ]; then
    echo -e "${RED}Erro: NGROK_AUTH_TOKEN não configurado${NC}"
    echo ""
    echo "Configure seu token do ngrok:"
    echo "  1. Acesse https://dashboard.ngrok.com/get-started/your-authtoken"
    echo "  2. Copie seu authtoken"
    echo "  3. Execute: echo 'NGROK_AUTH_TOKEN=seu_token' >> .env"
    echo ""
    echo "Ou execute diretamente:"
    echo "  NGROK_AUTH_TOKEN=seu_token ./start.sh"
    exit 1
fi

# Verificar venv
if [ ! -d "venv" ]; then
    echo -e "${RED}Erro: venv não encontrado${NC}"
    echo "Execute: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Matar processos anteriores na porta 8000
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}Parando processo anterior na porta 8000...${NC}"
    kill -9 $(lsof -t -i:8000) 2>/dev/null || true
    sleep 1
fi

# Carregar variáveis de ambiente
set -a
[ -f ".env" ] && source .env
set +a

echo -e "${GREEN}1. Iniciando API Backend (porta 8000)...${NC}"
source venv/bin/activate
python -m backend.main &
API_PID=$!
sleep 2

# Verificar se API iniciou
if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${RED}Erro: API não iniciou corretamente${NC}"
    kill $API_PID 2>/dev/null || true
    exit 1
fi
echo -e "${GREEN}   ✓ API rodando${NC}"

echo ""
echo -e "${GREEN}2. Iniciando ngrok tunnel...${NC}"
ngrok start --config="$ROOT_DIR/ngrok.yml" backend &
NGROK_PID=$!
sleep 3

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${GREEN}Sistema iniciado!${NC}"
echo ""
echo -e "  ${CYAN}URL Pública:${NC} https://savedown.ngrok.app"
echo -e "  ${CYAN}API Local:${NC}   http://localhost:8000"
echo -e "  ${CYAN}Frontend:${NC}    frontend/dist/index.html"
echo ""
echo -e "${YELLOW}Pressione CTRL+C para parar${NC}"
echo -e "${CYAN}========================================${NC}"

# Função para limpar ao sair
cleanup() {
    echo ""
    echo -e "${YELLOW}Parando serviços...${NC}"
    kill $API_PID 2>/dev/null || true
    kill $NGROK_PID 2>/dev/null || true
    echo -e "${GREEN}Serviços parados.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Aguardar
wait
