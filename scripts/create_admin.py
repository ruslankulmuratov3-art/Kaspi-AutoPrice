import getpass
from app.core.database import init_db, db_session
from app.repositories.users import users


def main():
    init_db()
    username = input('Username: ').strip()
    email = input('Email: ').strip()
    password = getpass.getpass('Password: ')
    with db_session() as db:
        user = users.ensure_admin(db, email, username, password)
        print('Admin:', user.username, user.email)

if __name__ == '__main__':
    main()
