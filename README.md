# OAZ Smart Image Bank

## Overview
Sistema inteligente de gerenciamento de banco de imagens para varejo de moda. Suporta catalogação em larga escala, integração com Carteira de Compras e análise via IA. As imagens hi-res ficam no SharePoint e são servidas sob demanda pelo app.

## Features
- **User Management**: Login e registro seguros
- **Dashboard**: Visão geral com estatísticas e gráficos
- **Image Catalog**: Catálogo em grid com filtros avançados
- **SharePoint Streaming**: Imagens hi-res por Microsoft Graph com streaming no app
- **Carteira de Compras**: Importação de Excel/CSV com auto-criação de entidades
- **SKU Matching**: Match automático entre imagens e Carteira de Compras
- **Produtos**: Cadastro completo de produtos com histórico de SKU
- **Auditoria**: Relatórios de cruzamento e divergências
- **Premium UI**: Dark mode com efeitos glassmorphism

## Setup

1. **Configure o ambiente**:
   ```bash
   cp .env.example .env
   ```
   Preencha as variáveis do SharePoint e credenciais:
   - `SHAREPOINT_TENANT_ID`
   - `SHAREPOINT_CLIENT_ID`
   - `SHAREPOINT_CLIENT_SECRET`
   - `SHAREPOINT_HOSTNAME`
   - `SHAREPOINT_SITE_PATH`
   - `SHAREPOINT_DRIVE_NAME`
   - `SHAREPOINT_ROOT_FOLDER`
   - `SHAREPOINT_INDEX_TTL_MINUTES` (opcional, padrão 30)
   - `SHAREPOINT_ECOMMERCE_FOLDER` (opcional, padrão `E-commerce`)
   - `SHAREPOINT_BRAND_PARENT_SEGMENT` (opcional, padrão `Design - Cria`)

2. **Install Dependencies**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Smoke test SharePoint** (na raiz do projeto, com venv ativa):
   ```bash
   python -m scripts.sp_index_smoke_test
   python -m scripts.sp_render_smoke_test
   ```

4. **Reindexar SharePoint (full)**:
   - Na tela de Carteira, use o botão "Reindexar SharePoint", ou
   - Faça um POST para `/sharepoint/reindex`

Fluxo principal:
- Importar carteira → cruzar com SharePoint → Biblioteca de Imagens e Coleções atualizadas.

4. **Run the Application**:
   ```bash
   python app.py
   ```
   The app will run on `http://0.0.0.0:5000`.

5. **Login**:
   - **Username**: `admin`
   - **Password**: `admin`
   (Or register a new account)

## Testando a rota SharePoint
- Importe uma carteira com SKUs presentes no SharePoint.
- Abra o detalhe da imagem e valide o link do app:
  - `http://<host>:5000/sp/image/<image_id>`

## Deploy na VM GCP
```bash
git pull
sudo systemctl restart banco-imagens.service
```

## Project Structure
- `app.py`: Main application logic and database models
- `batch_processor.py`: Parallel batch processing engine
- `object_storage.py`: Replit Object Storage integration
- `sharepoint_client.py`: Integração com Microsoft Graph para imagens
- `scripts/sp_index_smoke_test.py`: Smoke test de indexação SharePoint
- `scripts/sp_render_smoke_test.py`: Smoke test de download SharePoint
- `templates/`: HTML templates
- `static/`: CSS, JS, and legacy uploaded images

## Technologies
- Flask (Python 3.11)
- PostgreSQL / SQLite
- SQLAlchemy ORM
- Microsoft Graph (SharePoint)
- Chart.js
- Vanilla CSS (Custom Design System)

## Database
O sistema utiliza PostgreSQL (Replit Database) para armazenamento persistente. A conexão é configurada automaticamente via variável de ambiente `DATABASE_URL`.
