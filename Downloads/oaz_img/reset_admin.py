from app import app, db, User

with app.app_context():
    # Delete existing admin user
    admin = User.query.filter_by(username='admin').first()
    if admin:
        db.session.delete(admin)
        db.session.commit()
        print("Deleted existing admin user")
    
    # Create new admin user
    new_admin = User(username='admin', email='admin@oaz.com')
    new_admin.set_password('admin')
    db.session.add(new_admin)
    db.session.commit()
    print("Created new admin user")
    
    # Verify
    test_admin = User.query.filter_by(username='admin').first()
    if test_admin:
        print(f"Verification - User: {test_admin.username}")
        print(f"Password check: {test_admin.check_password('admin')}")
