# OAZ Smart Image Bank

## Overview
OAZ Smart Image Bank is a Flask-based intelligent image management system designed for fashion retail. It provides a professional image cataloging platform with AI-powered analysis, user authentication, and a modern dark-mode interface.

## Current State
The project has been successfully configured to run in the Replit environment. The application is fully functional with:
- User authentication system (login/register)
- Image upload and cataloging
- AI-powered image analysis using OpenAI GPT-4o Vision
- Collection management
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
│   ├── images/            # Image catalog and upload
│   ├── collections/       # Collection management
│   ├── analytics/         # Analytics views
│   ├── integrations/      # Integration settings
│   └── admin/             # Admin settings
├── migrate_db.py          # Database migration utility
└── reset_admin.py         # Admin user reset utility
```

### Database Models
- **User**: User accounts with authentication
- **SystemConfig**: System-wide configuration (API keys, etc.)
- **Collection**: Image collections/groups
- **Image**: Image records with AI-extracted metadata

### Key Features
1. **Smart Upload**: Drag-and-drop interface with automatic AI analysis
2. **AI Image Analysis**: Extracts product attributes (type, color, material, pattern, style)
3. **Portuguese Language**: All AI descriptions and UI text in Brazilian Portuguese
4. **Collection Management**: Organize images into collections
5. **Advanced Filtering**: Filter by status, collection, and attributes
6. **SEO-Ready**: AI-generated descriptions and keywords

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
- **2025-12-01**: Initial Replit setup
  - Moved project files to root directory
  - Updated Flask configuration for Replit (host 0.0.0.0, port 5000)
  - Installed Python 3.11 and dependencies
  - Created .gitignore for Python projects
  - Configured deployment as autoscale
  - Initialized database with admin user

## User Preferences
None specified yet.

## Notes
- The application uses Flask's built-in development server
- For production, consider using a production WSGI server like Gunicorn
- Database is SQLite (suitable for development; consider PostgreSQL for production)
- All file paths are relative to the project root
