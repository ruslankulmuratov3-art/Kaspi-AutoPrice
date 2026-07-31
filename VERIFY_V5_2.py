from pathlib import Path

from app.main import app

required = {
    ('/products/bulk-action', 'POST'),
    ('/products/{product_id}/archive', 'POST'),
    ('/products/{product_id}/restore', 'POST'),
    ('/products/{product_id}/recalculate', 'POST'),
    ('/automation/results', 'GET'),
}

routes = []
for route in app.routes:
    path = getattr(route, 'path', '')
    methods = set(getattr(route, 'methods', set()) or set())
    routes.append((path, methods))

missing = [f'{method} {path}' for path, method in required if not any(p == path and method in m for p, m in routes)]
if missing:
    raise SystemExit('ERROR: отсутствуют маршруты:\n' + '\n'.join(missing))

for name in ('products.html', 'product_detail.html', 'automation_results.html'):
    path = Path('app/templates') / name
    if not path.exists():
        raise SystemExit(f'ERROR: отсутствует {path}')

print('OK: v5.2 маршруты и шаблоны на месте.')
