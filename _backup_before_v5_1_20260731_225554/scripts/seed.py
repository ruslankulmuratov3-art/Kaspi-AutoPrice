from app.core.database import init_db, db_session
from app.core.config import settings
from app.repositories.users import users
from app.models.store import Store


def ensure_default_store(db):
    """Создаёт магазин из Render env, только если магазинов нет.

    Не удаляет товары/магазины. Это просто страховка для нового PostgreSQL/чистой базы.
    Главная защита от исчезновения данных — DATABASE_URL от Render PostgreSQL.
    """
    if not getattr(settings, 'KASPI_DEFAULT_STORE_AUTO_CREATE', True):
        return
    if db.query(Store).count() > 0:
        return
    merchant_id = (settings.KASPI_MERCHANT_ID or settings.KASPI_STORE_ID or '').strip()
    name = (settings.KASPI_COMPANY_NAME or 'EXCLUSIVE_KZ').strip()
    if not merchant_id:
        return
    db.add(Store(
        name=name,
        merchant_id=merchant_id,
        city='Алматы',
        api_token='',  # токен берётся из Environment Variables, не сохраняем его в базу
        is_active=True,
    ))


def main():
    init_db()
    with db_session() as db:
        users.ensure_admin(db, settings.ADMIN_EMAIL, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
        ensure_default_store(db)
    print('OK: база готова. Admin создан/проверен. Магазины и товары НЕ удаляются.')


if __name__ == '__main__':
    main()
