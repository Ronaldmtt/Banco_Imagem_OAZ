# OAZ Smart Image Bank

## Overview
OAZ Smart Image Bank is a Flask-based intelligent image management system designed for fashion retail. It provides a professional image cataloging platform with AI-powered analysis, user authentication, and a modern dark-mode interface.

## Current State
The project has been successfully configured to run in the Replit environment. The application is fully functional with:
- User authentication system (login/register)
- Image upload and cataloging with AI-powered analysis
- Brand and Collection management
- Workflow de status (Pendente → Aprovado/Rejeitado)
- Image editing with metadata management
- Reports with CSV export
- Premium dark-mode UI with glassmorphism effects
- SQLite database with pre-configured admin user

## Project Architecture

### Tech Stack
- **Backend**: Flask (Python 3.11)
- **Database**: SQLite with SQLAlchemy ORM
- **Authentication**: Flask-Login with password hashing
- **AI Integration**: OpenAI GPT-4o Vision API
- **Frontend**: Vanilla HTML/CSS/JavaScript with custom design system

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
1. **Smart Upload**: Drag-and-drop interface with automatic AI analysis
2. **AI Image Analysis**: Extracts product attributes (type, color, material, pattern, style)
3. **Portuguese Language**: All AI descriptions and UI text in Brazilian Portuguese
4. **Brand Management**: Create, edit, and delete brands
5. **Collection Management**: Organize images into collections
6. **Status Workflow**: Approve or reject images (Pendente → Aprovado/Rejeitado)
7. **Image Editing**: Edit SKU, description, brand, collection, photographer, shooting date
8. **Advanced Filtering**: Filter by status, collection, brand, and search by SKU/description/tags
9. **Reports**: Metrics by status, brand, collection with CSV export
10. **SEO-Ready**: AI-generated descriptions and keywords
11. **Product Catalog (Produtos)**: Full CRUD for products with SKU, description, color, category, technical attributes
12. **Image-Product Association**: Link multiple images to products and vice-versa
13. **SKU History Tracking**: Version control for SKU changes with user audit trail
14. **Shopping Cart Import (Carteira de Compras)**: Periodic CSV import with automatic photo status matching
15. **Audit Reports**: Cross-reference products vs images vs shopping cart, divergence detection

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
- Uploaded images are stored in `static/uploads/`
- Filenames are timestamped to prevent conflicts
- Supported formats: PNG, JPG, JPEG, GIF

## Recent Changes
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

## Notes
- The application uses Flask's built-in development server
- For production, consider using a production WSGI server like Gunicorn
- Database is SQLite (suitable for development; consider PostgreSQL for production)
- All file paths are relative to the project root
