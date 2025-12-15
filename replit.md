# OAZ Smart Image Bank

## Overview
OAZ Smart Image Bank is a Flask-based intelligent image management system tailored for fashion retail. Its primary purpose is to provide a professional image cataloging platform with AI-powered analysis, robust user authentication, and a modern, intuitive dark-mode interface. The system aims to streamline image workflows, enhance metadata management, and provide valuable insights for fashion businesses.

## User Preferences
- Interface em português brasileiro

## System Architecture

### UI/UX Decisions
The application features a premium dark-mode UI with glassmorphism effects, designed for a modern and professional aesthetic. All UI text is in Brazilian Portuguese.

### Technical Implementations
-   **Backend**: Flask (Python 3.11)
-   **Database**: PostgreSQL with SQLAlchemy ORM, utilizing connection pooling for efficiency.
-   **Authentication**: Flask-Login for secure user management and password hashing.
-   **AI Integration**: Utilizes OpenAI GPT-4o Vision API for intelligent image analysis, with a fallback mechanism.
-   **Frontend**: Built with Vanilla HTML, CSS, and JavaScript, employing a custom design system.
-   **Storage**: Replit Object Storage is used for scalable cloud-based image storage.
-   **Image Upload**: Supports drag-and-drop, and batch uploads of 1M+ images with parallel processing (5 workers). Images are automatically matched with SKU data from purchase orders (Carteira de Compras).
-   **SKU Grouping**: Intelligent extraction of SKU base from filenames with suffixes (_01, _02, -A, -B, _FRENTE, _COSTAS, etc.). Multiple images of the same product are grouped by `sku_base` while maintaining individual `sequencia` identifiers for each angle/variation.
-   **AI Fallback**: Images without an automatic match are marked for future AI analysis, with contextualized analysis leveraging similar products.
-   **Field Separation**: Clear separation between Carteira data (nome_peca, categoria, subcategoria, tipo_peca, origem) and AI-generated data (description, tags, cor, material). Carteira fields are never overwritten by AI analysis.
-   **Reconciliation System**: Button to re-match images with Carteira when new products are imported. Updates Carteira-derived fields while preserving AI-generated observations.
-   **Resilient Upload System**: Upload direto para Object Storage com reprocessamento:
    - **Upload Imediato ao Bucket**: Arquivos vão DIRETO para o Object Storage durante recepção (sem arquivos temporários locais)
    - **Persistência Garantida**: `storage_path` é salvo imediatamente após upload ao bucket
    - **Processamento Desacoplado**: Match com Carteira, thumbnails e análise IA usam imagem já salva no bucket
    - **Reprocessamento**: Endpoint `/batch/{id}/reprocess` permite reprocessar itens que falharam (imagem já está no bucket)
    - **Recuperação de Falhas**: Se o processamento falhar, não perde a imagem - basta reprocessar
    - Suporte legado para arquivos locais durante transição
-   **Multi-Batch Queue System**: Upload multiple collections at once with centralized processing:
    - Create multiple batches in a single interface (/batch/queue)
    - Each batch can have different collection and brand assignments
    - Upload files for each batch in sequence (one batch at a time)
    - "Processar Todos" button to start processing all uploaded batches together
    - Sequential processing with cleanup only after all batches complete
-   **Thumbnail System**: Automatic thumbnail generation using Pillow:
    - Thumbnails stored in PostgreSQL (BYTEA) for fast retrieval
    - 300px max width, JPEG quality 75% (~5-50KB each)
    - Endpoint `/thumbnail/<image_id>` serves thumbnails with fallback generation
    - Catalog listing uses thumbnails for fast page loads
-   **Logging System**: Sistema de logging centralizado com duas camadas:
    - **Local (`oaz_logger.py`)**: Logging no console do servidor
        - Formato estruturado: `[TIMESTAMP] [LEVEL] [MODULE] [ACTION] mensagem | key=value`
        - Módulos: AUTH, BATCH, UPLOAD, CARTEIRA, CATALOG, CRUD, DASHBOARD, SYSTEM
        - Logs especializados: auth_log, batch_log, upload_log, carteira_log, catalog_log, crud_log, nav_log
        - Timestamps com milissegundos para debugging
        - Cores no console para fácil identificação visual
    - **Externo (`rpa_monitor_client`)**: Monitoramento remoto via WebSocket
        - Conexão ao servidor RPA Monitor (wss://app-in-sight.replit.app/ws)
        - Funções helper: `rpa_info()`, `rpa_warn()`, `rpa_error()`, `rpa_screenshot()`
        - Screenshot automático em erros para debugging visual
        - Configuração via secrets: RPA_MONITOR_ID, RPA_MONITOR_HOST, RPA_MONITOR_PORT, RPA_MONITOR_REGION, RPA_MONITOR_TRANSPORT
        - Integrado em todas as rotas principais: Login, Dashboard, Catálogo, Carteira, Batch
-   **Data Models**: Includes models for Users, Brands, Collections, Images, ImageItems (for multi-piece detection), Products, SKU History, and Shopping Cart (CarteiraCompras) imports.
-   **Workflow**: Implements a status workflow for images: Pendente → Aprovado/Rejeitado → Pendente Análise IA.
-   **Product Management**: Comprehensive CRUD operations for products, linking images to products, and tracking SKU changes with an audit trail.
-   **Import System**: Robust Excel/CSV import for purchase orders, including automatic creation of brands, collections, and products, with flexible column normalization and multi-sheet import capabilities.
-   **Reporting**: Provides metrics by status, brand, and collection, with CSV export functionality.
-   **SKUs Sem Foto Report**: Dashboard card showing photography coverage by collection, with progress bars and detailed page listing pending SKUs with CSV export. Reports correctly consider collection scope - a SKU is only marked as "with photo" if there's an image in the SAME collection.
-   **Collection-Scoped Matching**: Batch processing now filters SKU matches by the collection selected in the batch dropdown, preventing cross-collection matches.
-   **Multi-Piece Detection**: AI can detect and analyze up to four individual pieces within a single image, each with its own metadata.
-   **Batch AI Analysis from Catalog**: Select multiple SKUs in the catalog view and generate AI descriptions for all at once:
    - Checkboxes on each thumbnail card for multi-selection
    - Floating action bar appears when items are selected
    - Quick AI button on each thumbnail for single-click analysis
    - Modal with field selection (description, tags, color, type, material)
    - Real-time progress bar with per-image status log
    - Sequential processing with error resilience (partial success handling)
    - Endpoint `/api/analyze-single` for individual image analysis via AJAX

### System Design Choices
The application is designed for scalability and performance, particularly for handling large volumes of images and data. It leverages a PostgreSQL database with optimized indexes for efficient querying of millions of records. The use of Replit Object Storage ensures persistent, scalable cloud storage for images, accessible via a dedicated `/storage/<path>` route. The batch processing system employs `ThreadPoolExecutor` for parallel execution, and the application is configured for Autoscale deployment on Replit.

## External Dependencies

-   **PostgreSQL**: Primary database for all application data.
-   **OpenAI GPT-4o Vision API**: Used for AI-powered image analysis and metadata extraction.
-   **Replit Object Storage**: Cloud-based storage solution for all uploaded images.
-   **Flask-Login**: For user authentication and session management.
-   **SQLAlchemy**: Python SQL toolkit and Object-Relational Mapper (ORM).