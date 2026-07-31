from __future__ import annotations

from app.core.config import settings
from app.core.database import SessionLocal, engine, init_db
from app.models.autopilot import AutopilotJob, CompetitorSourceState
from app.models.product import Product
from app.models.store import Store
from app.models.xml_feed import XmlFeedVersion


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        print(f'Database: {engine.dialect.name}')
        print(f'Environment: {settings.ENVIRONMENT}')
        print(f'Public URL: {settings.PUBLIC_BASE_URL or "not set"}')
        print(f'Direct price API: {"enabled" if settings.KASPI_DIRECT_PRICE_API_ENABLED else "disabled"}')
        print(f'Stores: {db.query(Store).count()}')
        print(f'Products: {db.query(Product).count()}')
        print(f'Jobs: {db.query(AutopilotJob).count()}')
        print(f'XML versions: {db.query(XmlFeedVersion).count()}')
        state = db.query(CompetitorSourceState).first()
        print(f'Competitor source: {state.state if state else "not initialized"}')
    finally:
        db.close()


if __name__ == '__main__':
    main()
