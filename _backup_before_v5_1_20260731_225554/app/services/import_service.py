import csv
import io
from sqlalchemy.orm import Session
from app.models.product import Product
from app.models.pricing_rule import PricingRule


class ImportService:
    required_columns = {'store_id', 'kaspi_sku', 'name', 'current_price', 'min_price', 'max_price'}

    def import_products_csv(self, db: Session, csv_text: str) -> dict:
        reader = csv.DictReader(io.StringIO(csv_text))
        missing = self.required_columns - set(reader.fieldnames or [])
        if missing:
            return {'created': 0, 'errors': [f'Нет колонок: {", ".join(sorted(missing))}']}
        created = 0
        errors = []
        for index, row in enumerate(reader, start=2):
            try:
                product = Product(
                    store_id=int(row['store_id']),
                    kaspi_sku=row['kaspi_sku'].strip(),
                    name=row['name'].strip(),
                    url=row.get('url', '').strip(),
                    category=row.get('category', ''),
                    brand=row.get('brand', ''),
                    current_price=float(row.get('current_price') or 0),
                    min_price=float(row.get('min_price') or 0),
                    max_price=float(row.get('max_price') or 0),
                    cost_price=float(row.get('cost_price') or 0),
                    stock=int(row.get('stock') or 0),
                )
                db.add(product)
                db.flush()
                db.add(PricingRule(product_id=product.id))
                created += 1
            except Exception as exc:
                errors.append(f'Строка {index}: {exc}')
        db.commit()
        return {'created': created, 'errors': errors}

import_service = ImportService()
