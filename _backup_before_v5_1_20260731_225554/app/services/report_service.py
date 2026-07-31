import csv
import io
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.product import Product
from app.models.price_history import PriceHistory
from app.models.alert import Alert
from app.models.store import Store


class ReportService:
    def dashboard_metrics(self, db: Session) -> dict:
        products = db.query(Product).count()
        stores = db.query(Store).count()
        changes_24h = db.query(PriceHistory).filter(PriceHistory.created_at >= datetime.utcnow() - timedelta(days=1)).count()
        unread_alerts = db.query(Alert).filter(Alert.is_read.is_(False)).count()
        avg_price = 0
        rows = db.query(Product.current_price).all()
        if rows:
            avg_price = sum(float(row[0] or 0) for row in rows) / len(rows)
        return {
            'products': products,
            'stores': stores,
            'changes_24h': changes_24h,
            'unread_alerts': unread_alerts,
            'avg_price': round(avg_price, 0),
        }

    def export_products_csv(self, db: Session) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id', 'store_id', 'kaspi_sku', 'name', 'current_price', 'min_price', 'max_price', 'stock', 'status'])
        for p in db.query(Product).order_by(Product.id.asc()).all():
            writer.writerow([p.id, p.store_id, p.kaspi_sku, p.name, p.current_price, p.min_price, p.max_price, p.stock, p.status.value])
        return output.getvalue()

report_service = ReportService()
