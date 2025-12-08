# OAZ Smart Image Bank

## Overview
Sistema inteligente de gerenciamento de banco de imagens para varejo de moda. Suporta catalogação de até 1 milhão de imagens com processamento paralelo, integração com Carteira de Compras e análise via IA como fallback.

## Features
- **User Management**: Login e registro seguros
- **Dashboard**: Visão geral com estatísticas e gráficos
- **Image Catalog**: Catálogo em grid com filtros avançados
- **Smart Upload**: Interface drag & drop com extração de metadados
- **Batch Upload**: Upload em lote com processamento paralelo (5 workers)
- **Carteira de Compras**: Importação de Excel/CSV com auto-criação de entidades
- **SKU Matching**: Match automático entre imagens e Carteira de Compras
- **Produtos**: Cadastro completo de produtos com histórico de SKU
- **Auditoria**: Relatórios de cruzamento e divergências
- **Premium UI**: Dark mode com efeitos glassmorphism

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   python app.py
   ```
   The app will run on `http://0.0.0.0:5000`.

3. **Login**:
   - **Username**: `admin`
   - **Password**: `admin`
   (Or register a new account)

## Project Structure
- `app.py`: Main application logic and database models
- `batch_processor.py`: Parallel batch processing engine
- `object_storage.py`: Replit Object Storage integration
- `templates/`: HTML templates
- `static/`: CSS, JS, and legacy uploaded images

## Technologies
- Flask (Python 3.11)
- PostgreSQL (Neon-backed via Replit)
- SQLAlchemy ORM
- Replit Object Storage
- Chart.js
- Vanilla CSS (Custom Design System)

## Database
O sistema utiliza PostgreSQL (Replit Database) para armazenamento persistente. A conexão é configurada automaticamente via variável de ambiente `DATABASE_URL`.
