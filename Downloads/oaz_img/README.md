# OAZ Smart Image Bank

## Overview
This is a Flask-based Intelligent Image Bank application designed for OAZ. It features a premium dark mode design, user authentication, image cataloging, and mock AI integration for automatic description generation.

## Features
- **User Management**: Secure Login and Registration.
- **Dashboard**: Overview of stats and charts.
- **Image Catalog**: Grid view with filtering options.
- **Smart Upload**: Drag & drop interface with AI-powered description generation (mocked).
- **Collections**: Manage image collections.
- **Premium UI**: Dark mode with glassmorphism effects.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Application**:
   ```bash
   python3 app.py
   ```
   The app will run on `http://127.0.0.1:5001`.

3. **Login**:
   - **Username**: `admin`
   - **Password**: `admin`
   (Or register a new account)

## Project Structure
- `app.py`: Main application logic and database models.
- `templates/`: HTML templates.
- `static/`: CSS, JS, and uploaded images.
- `instance/`: Database file (SQLite).

## Technologies
- Flask
- SQLite (SQLAlchemy)
- Chart.js
- Vanilla CSS (Custom Design System)
