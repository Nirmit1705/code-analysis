"""Registration/login business logic."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User


class AuthError(Exception):
    """Raised for auth failures the API layer should turn into 4xx responses."""


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def register_user(db: Session, email: str, password: str) -> User:
    if get_user_by_email(db, email) is not None:
        raise AuthError("A user with this email already exists")

    user = User(email=email, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = get_user_by_email(db, email)
    if user is None or user.hashed_password is None:
        raise AuthError("Incorrect email or password")
    if not verify_password(password, user.hashed_password):
        raise AuthError("Incorrect email or password")
    return user
