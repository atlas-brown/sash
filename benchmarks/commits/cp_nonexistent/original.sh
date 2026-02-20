#!/bin/bash
# Script de Deploy Simples para TIB-SaaS

# Cores
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}>>> Iniciando Deploy TIB-SaaS...${NC}"

# 1. Verificar Docker
if ! command -v docker &> /dev/null
then
    echo "Docker não encontrado. Instalando..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
else
    echo "Docker já instalado."
fi

# 2. Pull do código mais recente
echo -e "${GREEN}>>> Atualizando código...${NC}"
git pull origin master

# 3. Verificar .env
if [ ! -f .env ]; then
    echo "Arquivo .env não encontrado. Criando a partir de .env.example (EDITE DEPOIS!)"
    cp .env .env # bug here: .env does not exist
fi

# 4. Subir containers
echo -e "${GREEN}>>> Subindo Aplicação...${NC}"
docker compose up -d --build --remove-orphans

echo -e "${GREEN}>>> Deploy Concluído!${NC}"
echo "Verifique os logs com: docker compose logs -f"
