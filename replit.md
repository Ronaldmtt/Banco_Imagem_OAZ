# OAZ Smart Image Bank

## Overview
OAZ Smart Image Bank is a Flask-based intelligent image management system designed for fashion retail. It provides a professional image cataloging platform with AI-powered analysis, user authentication, and a modern dark-mode interface.

## Current State
The project has been successfully configured to run in the Replit environment. The application is fully functional with:
- User authentication system (login/register)
- Image upload and cataloging with smart metadata extraction
- Batch upload system for 1M+ images with parallel processing
- Brand and Collection management
- Workflow de status (Pendente → Aprovado/Rejeitado → Pendente Análise IA)
- Image editing with metadata management
- Reports with CSV export
- Premium dark-mode UI with glassmorphism effects
- PostgreSQL database with pre-configured admin user

## Project Architecture

### Tech Stack
- **Backend**: Flask (Python 3.11)
- **Database**: PostgreSQL with SQLAlchemy ORM (connection pooling)
- **Authentication**: Flask-Login with password hashing
- **AI Integration**: OpenAI GPT-4o Vision API (fallback mode)
- **Frontend**: Vanilla HTML/CSS/JavaScript with custom design system
- **Storage**: Replit Object Storage (cloud-based)

### Project Structure
```
.
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── instance/
│   └── oaz_img.db         # SQLite database
├── static/
│   ├── css/
│   │   └── style.css      # Premium dark-mode styles
│   └── uploads/           # User-uploaded images
├── templates/
│   ├── base.html          # Base template
│   ├── auth/              # Login and registration
│   ├── dashboard/         # Main dashboard
│   ├── images/            # Image catalog, upload, edit, detail
│   ├── collections/       # Collection management
│   ├── brands/            # Brand management
│   ├── reports/           # Reports and exports
│   ├── analytics/         # Analytics views
│   ├── integrations/      # Integration settings
│   └── admin/             # Admin settings
├── migrate_db.py          # Database migration utility
└── reset_admin.py         # Admin user reset utility
```

### Database Models
- **User**: User accounts with authentication
- **SystemConfig**: System-wide configuration (API keys, etc.)
- **Brand**: Product brands
- **Collection**: Image collections/groups
- **Image**: Image records with AI-extracted metadata, brand_id, photographer, shooting_date, unique_code
- **ImageItem**: Individual pieces detected in an image (supports multiple pieces per photo)
- **Produto**: Product catalog with SKU, description, color, category, technical attributes
- **ImagemProduto**: Many-to-many relationship between images and products
- **HistoricoSKU**: SKU change history with versioning and user tracking
- **CarteiraCompras**: Shopping cart/purchase order import from CSV with photo status tracking

### Key Features
1. **Smart Upload**: Drag-and-drop interface with metadata extraction from Carteira de Compras
2. **Batch Upload**: Upload 1M+ images via ZIP or multiple files with parallel processing (5 workers)
3. **SKU Matching**: Automatic match between image filename (SKU) and Carteira de Compras data
4. **AI Fallback**: Images without match are marked "Pendente Análise IA" for future OpenAI analysis
5. **Portuguese Language**: All UI text in Brazilian Portuguese
6. **Brand Management**: Create, edit, and delete brands
7. **Collection Management**: Organize images into collections
8. **Status Workflow**: Pendente → Aprovado/Rejeitado → Pendente Análise IA
9. **Image Editing**: Edit SKU, description, brand, collection, photographer, shooting date
10. **Advanced Filtering**: Filter by status, collection, brand, and search by SKU/description/tags
11. **Reports**: Metrics by status, brand, collection with CSV export
12. **Product Catalog (Produtos)**: Full CRUD for products with SKU, description, color, category
13. **Image-Product Association**: Link multiple images to products and vice-versa
14. **SKU History Tracking**: Version control for SKU changes with user audit trail
15. **Shopping Cart Import (Carteira de Compras)**: Excel/CSV import with auto-creation of brands/collections
16. **Audit Reports**: Cross-reference products vs images vs shopping cart, divergence detection

## Configuration

### Environment Setup
The application is configured to run on:
- **Host**: 0.0.0.0 (required for Replit)
- **Port**: 5000 (frontend)
- **Debug Mode**: Enabled in development

### Default Credentials
- **Username**: admin
- **Password**: admin

⚠️ Change these credentials in production!

### OpenAI Integration
To enable AI-powered image analysis:
1. Log in to the application
2. Navigate to Settings (Configurações)
3. Enter your OpenAI API Key
4. The key is stored in the database for persistence

Without an API key, images will still upload but won't have automatic analysis.

## Deployment
The project is configured with:
- **Deployment Type**: Autoscale (stateless web application)
- **Run Command**: `python app.py`
- Port 5000 is automatically exposed for web traffic

## Development Workflow

### Running Locally
The workflow "Flask App" is configured to run the application automatically. It will:
1. Start the Flask development server
2. Initialize the database if needed
3. Create the admin user if it doesn't exist
4. Listen on port 5000

### Database Management
- Database auto-initializes on first run
- Use `migrate_db.py` for schema migrations
- Use `reset_admin.py` to reset admin credentials

### Static Files
- Uploaded images are stored in `static/uploads/` (local storage) or Object Storage (cloud)
- Filenames are timestamped to prevent conflicts
- Supported formats: PNG, JPG, JPEG, GIF

### Object Storage (Always Active)
All image uploads are stored in Replit Object Storage (cloud) automatically:

1. **How it works**:
   - All new uploads go directly to cloud storage (no local files)
   - Images are served via `/storage/<object_path>` route
   - Existing images in `static/uploads/` continue to work (legacy fallback)
   - Files stored in bucket under `images/` prefix

2. **File Structure in Bucket**:
   ```
   images/
     20251204123456_abc12345.jpg
     20251204123457_def67890.png
   ```

3. **Benefits**:
   - Scalable cloud storage
   - No disk space limitations
   - Automatic CDN caching
   - Persistent across deployments

## Recent Changes
- **2025-12-04**: Smart Batch Upload System with SKU Matching
  - Primary: Match SKU (filename) with CarteiraCompras data
  - Secondary: Images without match marked "Pendente Análise IA" for future AI fallback
  - No automatic API calls during batch processing (API is fallback only)
  - Metadata extraction: description, color, category, subcategory from Carteira
  - Auto-update CarteiraCompras status_foto to "Com Foto" on match
  - ThreadPoolExecutor with 5 parallel workers
  - Real-time progress dashboard with polling
  - Optimized indexes for 1M+ records
  - New routes: /batch, /batch/new, /batch/<id>, /batch/<id>/status

- **2025-12-04**: PostgreSQL Migration & Object Storage
  - Migrated database from SQLite to PostgreSQL for scalability
  - New BatchUpload and BatchItem models for tracking batch processing
  - All uploads go directly to Replit Object Storage (no local files)
  - Route /storage/<path> serves images from cloud storage

- **2025-12-04**: Object Storage Integration (Mandatory)
  - All uploads now go directly to Replit Object Storage (no local files)
  - Added replit-object-storage SDK for cloud image storage
  - New object_storage.py service with upload/download/delete methods
  - Route /storage/<path> serves images from Object Storage
  - Image model has storage_path column for cloud storage paths
  - Templates use image.image_url for transparent local/cloud URLs
  - Temporary files are deleted after upload to cloud


- **2025-12-02**: Auto-criação de Entidades na Importação
  - Auto-criação de Coleções: "INVERNO 2026" → cria coleção com ano=2026 e estação=Inverno
  - Auto-criação de Marcas: detecta coluna MARCA/BRAND e cria automaticamente
  - Auto-criação de Produtos: cria produto com SKU, descrição, cor, categoria do Excel
  - Extração inteligente de ano (2024-2029) e estação (Inverno, Verão, Primavera, Outono, Resort)
  - Novas colunas: colecao_id e marca_id em CarteiraCompras para associação
  - Flash messages detalhadas: "Criados automaticamente: 3 coleção(ões), 2 marca(s), 150 produto(s)"
  - Funções auxiliares: obter_ou_criar_colecao(), obter_ou_criar_marca(), obter_ou_criar_produto()

- **2025-12-02**: Excel Import Enhancement for Carteira de Compras
  - New `normalizar_carteira_dataframe()` function with robust column normalization
  - Case-insensitive, accent-insensitive, whitespace-tolerant column matching
  - Support for importing ALL sheets at once from Excel files
  - Column auto-mapping: REFERÊNCIA E COR → SKU, NOME → descrição, GRUPO → categoria, etc.
  - Mapeamento de MARCA/BRAND para criação automática de marcas
  - Clear error messages when SKU column not found
  - Enhanced template with "Import all sheets" checkbox
  - Detailed instructions on expected Excel format
  - New fields: subcategoria, colecao_nome, estilista, shooting, observacoes, origem, okr, aba_origem

- **2025-12-02**: Full Product Management & Audit System
  - New Produto model for product catalog (SKU, description, color, category, technical attributes)
  - ImagemProduto model for many-to-many image-product relationships
  - HistoricoSKU model for SKU change versioning with user tracking
  - CarteiraCompras model for shopping cart CSV import with photo status tracking
  - Full CRUD routes for products (/produtos, /produtos/new, /produtos/{id}/edit)
  - Shopping cart management (/carteira, /carteira/importar)
  - Audit dashboard (/auditoria) with SKU history, divergences, pending SKUs reports
  - CSV export for all audit reports
  - Updated sidebar with 3 new sections: Produtos, Carteira de Compras, Auditoria
  - Database migration with 5 new tables and 8 indexes

- **2025-12-02**: Multi-Piece Detection Feature
  - New ImageItem model for storing individual pieces detected in images
  - AI prompt updated to detect and analyze multiple pieces per image (up to 4)
  - Position reference for each piece (Peça Superior, Peça Inferior, Peça Única, Acessório, Calçado)
  - Separate metadata, tags, and descriptions for each detected piece
  - Updated detail page UI to display multiple pieces with cards
  - Re-analyze button updates all pieces with new detection
  - Backward compatible with existing single-piece images

- **2025-12-02**: AI Analysis Major Upgrade
  - Comprehensive material identification (30+ fabric types with visual characteristics)
  - Added transparent fabrics: Tule, Organza, Chiffon, Voal, Renda
  - Added structured fabrics: Crepe, Gabardine, Sarja, Cetim, Veludo
  - Added knits: Tricô, Moletom, Ribana/Canelada
  - Generic tag filtering (removes "casual", "moda casual", etc.)
  - Ultra-detailed descriptions for SKU verification
  - Specific style categories (avoiding generic "casual")

- **2025-12-01**: Full feature implementation
  - Added image editing page (/image/{id}/edit)
  - Created reports page with metrics and statistics
  - Implemented CSV export for all report types
  - Added brand management system (CRUD)
  - Added status workflow (Pendente → Aprovado/Rejeitado)
  - Implemented advanced filtering (by brand, collection, status, search)
  - Updated dashboard with real database data
  - Added new fields to images (brand_id, photographer, shooting_date, unique_code)

- **2025-12-01**: Initial Replit setup
  - Moved project files to root directory
  - Updated Flask configuration for Replit (host 0.0.0.0, port 5000)
  - Installed Python 3.11 and dependencies
  - Created .gitignore for Python projects
  - Configured deployment as autoscale
  - Initialized database with admin user

## User Preferences
- Interface em português brasileiro

## Tutorial Completo
Consulte o arquivo **TUTORIAL_COMPLETO.md** para um guia passo a passo detalhado de todas as funcionalidades do sistema, incluindo:
- Fluxo de trabalho completo
- Exemplos de dados para teste
- Formato de arquivos CSV para importação
- Solução de problemas

## Notes
- The application uses Flask's built-in development server
- For production, consider using a production WSGI server like Gunicorn
- Database is SQLite (suitable for development; consider PostgreSQL for production)
- All file paths are relative to the project root
