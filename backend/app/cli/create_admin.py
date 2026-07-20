import argparse
import asyncio
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db import create_engine, create_session_factory
from app.models.auth import User, UserRole
from app.schemas.auth import normalize_email
from app.security.passwords import PasswordManager


async def create_admin(email: str, display_name: str, password: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            if await session.scalar(select(User.id).where(User.email == email)) is not None:
                raise ValueError("A user with this email already exists.")
            session.add(
                User(
                    email=email,
                    display_name=display_name,
                    role=UserRole.SUPER_ADMIN.value,
                    password_hash=PasswordManager().hash(password),
                    is_active=True,
                    version=1,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("A user with this email already exists.") from exc
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the initial SUPER_ADMIN account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    email = normalize_email(args.email)
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        parser.error("password must contain at least 12 characters")
    if password != confirmation:
        parser.error("passwords do not match")
    try:
        asyncio.run(create_admin(email, args.display_name.strip(), password))
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Created SUPER_ADMIN account for {email}.")


if __name__ == "__main__":
    main()
