from app import app, db, User

with app.app_context():
    user = User.query.filter_by(username='admin').first()
    if user:
        print(f'User found: {user.username}')
        print(f'Email: {user.email}')
        print(f'Has password hash: {user.password_hash is not None}')
        print(f'Password check for "admin": {user.check_password("admin")}')
    else:
        print('No admin user found!')
