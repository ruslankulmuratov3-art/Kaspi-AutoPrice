"""Idempotent additive migration entry point for local and Render deploys."""
from app.core.database import init_db


def main() -> None:
    init_db()
    print('OK: additive database migration completed; no tables were dropped.')


if __name__ == '__main__':
    main()
