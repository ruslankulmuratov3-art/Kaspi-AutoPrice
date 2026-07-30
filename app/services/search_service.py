from __future__ import annotations

from app.models.product import Product


def product_text_matches(product: Product, needle: str) -> bool:
    """Unicode-safe partial search by name, SKU, product_id, brand, model and URL."""
    normalized = ' '.join(str(needle or '').casefold().split())
    if not normalized:
        return True
    haystack = ' '.join([
        str(product.name or ''),
        str(product.kaspi_sku or ''),
        str(product.url or ''),
        str(getattr(product, 'product_id', '') or ''),
        str(getattr(product, 'model', '') or ''),
        str(getattr(product, 'brand', '') or ''),
        str(getattr(product, 'category', '') or ''),
    ]).casefold()
    return normalized in haystack
