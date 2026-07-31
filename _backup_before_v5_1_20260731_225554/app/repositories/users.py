from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from app.repositories.base import BaseRepository
from app.core.security import hash_password


class UserRepository(BaseRepository[User]):
    def __init__(self):
        super().__init__(User)

    def by_username(self, db: Session, username: str) -> User | None:
        return db.query(User).filter(User.username == username).first()

    def by_email(self, db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    def ensure_admin(self, db: Session, email: str, username: str, password: str) -> User:
        user = self.by_username(db, username)
        if user:
            return user
        user = User(email=email, username=username, password_hash=hash_password(password), role=UserRole.OWNER)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

users = UserRepository()
