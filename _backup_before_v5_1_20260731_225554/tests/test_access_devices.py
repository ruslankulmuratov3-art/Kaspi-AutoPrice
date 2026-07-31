from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.security import hash_password
from app.models.access import InviteCode, InviteKind
from app.models.user import User, UserRole
from app.services.access_service import access_service


def make_db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "access.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def create_admin(db):
    user = User(
        email='admin@example.com',
        username='admin',
        password_hash=hash_password('StrongPass123!'),
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_account_invite_registers_viewer_once(tmp_path):
    db = make_db(tmp_path)
    admin = create_admin(db)
    invite, plain = access_service.create_invite(
        db,
        kind=InviteKind.ACCOUNT,
        created_by_id=admin.id,
        expires_hours=24,
        max_uses=1,
    )
    user = access_service.register_password_user(
        db,
        email='worker@example.com',
        username='worker',
        password='WorkerPass123!',
        invite_code=plain,
    )
    assert user.role == UserRole.VIEWER
    assert user.is_active is True
    saved = db.query(InviteCode).filter(InviteCode.id == invite.id).one()
    assert saved.used_count == 1
    assert saved.is_active is False
    db.close()


def test_device_pairing_creates_unique_revocable_token(tmp_path):
    db = make_db(tmp_path)
    admin = create_admin(db)
    worker = User(
        email='worker@example.com',
        username='worker',
        password_hash=hash_password('WorkerPass123!'),
        role=UserRole.VIEWER,
        is_active=True,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    _, plain = access_service.create_invite(
        db,
        kind=InviteKind.DEVICE,
        created_by_id=admin.id,
        assigned_user_id=worker.id,
        expires_hours=24,
        max_uses=1,
    )
    paired = access_service.pair_device(db, code=plain, name='Office PC', platform='Windows')
    assert paired.token.startswith('kat_')
    assert paired.device.user_id == worker.id
    assert access_service.authenticate_device(db, paired.token).id == paired.device.id
    access_service.revoke_device(db, paired.device)
    assert access_service.authenticate_device(db, paired.token) is None
    db.close()
