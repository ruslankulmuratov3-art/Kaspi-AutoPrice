from app.core.database import init_db, db_session
from app.core.config import settings
from app.repositories.users import users


def main():
    init_db()
    with db_session() as db:
        users.ensure_admin(db, settings.ADMIN_EMAIL, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    print('OK: база создана, admin пользователь создан. Демо-магазины и демо-товары НЕ создаются.')


if __name__ == '__main__':
    main()
