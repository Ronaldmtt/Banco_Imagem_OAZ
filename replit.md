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
-   **Resilient Upload System**: Two-phase crash-proof upload architecture:
    - Phase 1 (Reception): Files received via streaming to disk with SHA256 hash
    - Phase 2 (Processing): Batches of 20 images processed in parallel with persistent state
    - Watchdog thread detects stuck items (> 5 min) and resets for retry
    - Resume capability after browser close or server restart via /batch/{id}/resume endpoint
-   **Data Models**: Includes models for Users, Brands, Collections, Images, ImageItems (for multi-piece detection), Products, SKU History, and Shopping Cart (CarteiraCompras) imports.
-   **Workflow**: Implements a status workflow for images: Pendente → Aprovado/Rejeitado → Pendente Análise IA.
-   **Product Management**: Comprehensive CRUD operations for products, linking images to products, and tracking SKU changes with an audit trail.
-   **Import System**: Robust Excel/CSV import for purchase orders, including automatic creation of brands, collections, and products, with flexible column normalization and multi-sheet import capabilities.
-   **Reporting**: Provides metrics by status, brand, and collection, with CSV export functionality.
-   **Multi-Piece Detection**: AI can detect and analyze up to four individual pieces within a single image, each with its own metadata.

### System Design Choices
The application is designed for scalability and performance, particularly for handling large volumes of images and data. It leverages a PostgreSQL database with optimized indexes for efficient querying of millions of records. The use of Replit Object Storage ensures persistent, scalable cloud storage for images, accessible via a dedicated `/storage/<path>` route. The batch processing system employs `ThreadPoolExecutor` for parallel execution, and the application is configured for Autoscale deployment on Replit.

## External Dependencies

-   **PostgreSQL**: Primary database for all application data.
-   **OpenAI GPT-4o Vision API**: Used for AI-powered image analysis and metadata extraction.
-   **Replit Object Storage**: Cloud-based storage solution for all uploaded images.
-   **Flask-Login**: For user authentication and session management.
-   **SQLAlchemy**: Python SQL toolkit and Object-Relational Mapper (ORM).