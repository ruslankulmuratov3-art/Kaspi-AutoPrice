from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from urllib.parse import quote
from pathlib import Path
from io import BytesIO
from datetime import datetime, timedelta
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.security import TOKEN_COOKIE_NAME
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.models.price_history import PriceHistory
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.alert import Alert, AlertType
from app.models.user import User, UserRole
from app.models.helper import HelperSession
from app.models.autopilot import AutopilotJob, AutopilotJobItem, AutopilotJobStatus, CompetitorSourceState
from app.repositories.users import users
from app.services.auth_service import auth_service
from app.services.report_service import report_service
from app.services.import_service import import_service
from app.services.pricing_engine import pricing_engine
from app.services.price_list_service import price_list_service, PriceListError
from app.services.price_list_import_service import price_list_import_service, PriceListImportError
from app.services.generated_price_list_service import generated_price_list_service
from app.services.xml_feed_service import xml_feed_service, XmlFeedError
from app.services.autopilot_service import autopilot_service
from app.services.price_change_limiter import price_change_limiter
from app.services.competitor_service import competitor_service
from app.services.search_service import product_text_matches
from app.services.helper_session_service import helper_session_service
from app.services.incremental_pricing_service import incremental_pricing_service
from app.web.templating import templates
from app.web.deps import current_user_optional

web_router = APIRouter()


def excel_response(data: bytes, filename: str) -> StreamingResponse:
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(
        BytesIO(data),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers=headers,
    )


async def optional_template_bytes(template: UploadFile | None) -> bytes | None:
    if not template or not template.filename:
        return None
    return await template.read()


def ensure_user(request: Request, db: Session) -> User | None:
    return current_user_optional(request, db)


def login_redirect():
    return RedirectResponse('/login', status_code=303)


def _cookie_secure() -> bool:
    # Local HTTP must remain usable. Render should set ENVIRONMENT=production.
    return settings.ENVIRONMENT.lower() in {'production', 'prod'}


def login_user_redirect(user: User, path: str | None = None) -> RedirectResponse:
    target = path or '/dashboard'
    redirect = RedirectResponse(target, status_code=303)
    redirect.set_cookie(
        TOKEN_COOKIE_NAME,
        auth_service.token_for_user(user),
        httponly=True,
        samesite='lax',
        secure=_cookie_secure(),
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return redirect


def require_web_admin(request: Request, db: Session) -> User | None:
    user = ensure_user(request, db)
    if not user or user.role not in (UserRole.OWNER, UserRole.ADMIN):
        return None
    return user


def set_flash(request: Request, message: str, level: str = 'success', code: str = '') -> None:
    request.session['flash'] = {'message': message, 'level': level, 'code': code}


def pop_flash(request: Request) -> dict:
    value = request.session.pop('flash', None)
    return value if isinstance(value, dict) else {}


def public_base_info(request: Request) -> dict:
    configured = settings.PUBLIC_BASE_URL.strip().rstrip('/')
    is_local_request = request.url.hostname in {'127.0.0.1', 'localhost'}
    valid_public = bool(configured.startswith('https://') and '127.0.0.1' not in configured and 'localhost' not in configured)
    production_base = configured if valid_public else 'https://kaspi-autoprice.onrender.com'
    return {
        'is_local': is_local_request,
        'production_base': production_base,
        'local_base': str(request.base_url).rstrip('/'),
        'public_base_valid': valid_public,
    }


def is_pending_product(product: Product) -> bool:
    return (
        not product.auto_pricing_enabled
        or float(product.min_price or 0) <= 0
        or float(product.max_price or 0) <= 0
        or (float(product.max_price or 0) < float(product.min_price or 0) and float(product.max_price or 0) > 0)
    )


def resolve_selected_store_id(request: Request, db: Session, stores: list[Store], requested_store_id: int | None) -> int | None:
    """Remember the selected store and never default to an empty accidental store."""
    valid_ids = {int(store.id) for store in stores}
    if requested_store_id and int(requested_store_id) in valid_ids:
        selected = int(requested_store_id)
        request.session['selected_store_id'] = selected
        return selected

    saved = request.session.get('selected_store_id')
    try:
        saved_id = int(saved) if saved is not None else None
    except (TypeError, ValueError):
        saved_id = None
    if saved_id in valid_ids:
        return saved_id

    counts = dict(
        db.query(Product.store_id, func.count(Product.id))
        .group_by(Product.store_id)
        .all()
    )
    populated = [store for store in stores if int(counts.get(store.id, 0)) > 0]
    if populated:
        selected_store = max(populated, key=lambda store: (int(counts.get(store.id, 0)), int(store.id)))
    else:
        selected_store = next((store for store in stores if store.is_active), stores[0] if stores else None)
    selected = int(selected_store.id) if selected_store else None
    if selected:
        request.session['selected_store_id'] = selected
    return selected


@web_router.get('/', response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return RedirectResponse('/login', status_code=303)
    return RedirectResponse('/dashboard', status_code=303)


@web_router.get('/login', response_class=HTMLResponse)
def login_page(request: Request, error: str = '', message: str = '', db: Session = Depends(get_db)):
    users.ensure_admin(db, settings.ADMIN_EMAIL, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    return templates.TemplateResponse('login.html', {
        'request': request,
        'app_name': settings.APP_NAME,
        'google_enabled': False,
        'registration_enabled': False,
        'error': error,
        'message': message,
    })


@web_router.post('/login')
def login_action(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    try:
        user = auth_service.authenticate(db, username, password)
        user.last_login_at = datetime.utcnow()
        db.add(user)
        db.commit()
        return login_user_redirect(user)
    except HTTPException as exc:
        return templates.TemplateResponse('login.html', {
            'request': request,
            'app_name': settings.APP_NAME,
            'google_enabled': False,
            'registration_enabled': False,
            'error': str(exc.detail),
            'message': '',
        }, status_code=exc.status_code)


@web_router.post('/logout')
def logout_action():
    redirect = RedirectResponse('/login', status_code=303)
    redirect.delete_cookie(TOKEN_COOKIE_NAME)
    return redirect


@web_router.get('/dashboard', response_class=HTMLResponse)
def dashboard(request: Request, store_id: int | None = None, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    metrics = report_service.dashboard_metrics(db)
    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    selected_store = next((s for s in stores if s.id == selected_store_id), None) if selected_store_id else None

    product_query = db.query(Product)
    if selected_store_id:
        product_query = product_query.filter(Product.store_id == selected_store_id)
    recent_products = product_query.order_by(Product.updated_at.desc()).limit(8).all()

    ready_query = db.query(Product).filter(Product.auto_pricing_enabled == True, Product.min_price > 0, Product.max_price > 0)
    pending_query = db.query(Product).filter(or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0))
    if selected_store_id:
        ready_query = ready_query.filter(Product.store_id == selected_store_id)
        pending_query = pending_query.filter(Product.store_id == selected_store_id)
    ready_count = ready_query.count()
    pending_count = pending_query.count()

    confirmed_sources = ['kaspi_confirmed', 'price_list_confirmed']
    history = db.query(PriceHistory).filter(PriceHistory.source.in_(confirmed_sources)).order_by(PriceHistory.created_at.desc()).limit(10).all()
    alerts = db.query(Alert).order_by(Alert.created_at.desc()).limit(8).all()
    recent_price_lists = generated_price_list_service.list_records(store_id=selected_store_id, limit=5)
    return templates.TemplateResponse('dashboard.html', {
        'request': request, 'user': user, 'metrics': metrics, 'products': recent_products,
        'history': history, 'alerts': alerts, 'stores': stores, 'selected_store_id': selected_store_id,
        'selected_store': selected_store, 'ready_count': ready_count, 'pending_count': pending_count,
        'recent_price_lists': recent_price_lists,
    })


@web_router.get('/stores', response_class=HTMLResponse)
def stores_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    rows = []
    for store in stores:
        total = db.query(Product).filter(Product.store_id == store.id).count()
        ready = db.query(Product).filter(
            Product.store_id == store.id,
            Product.auto_pricing_enabled == True,
            Product.min_price > 0,
            Product.max_price > 0,
        ).count()
        pending = db.query(Product).filter(
            Product.store_id == store.id,
            or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0),
        ).count()
        rows.append({'store': store, 'total': total, 'ready': ready, 'pending': pending})
    return templates.TemplateResponse('stores.html', {
        'request': request,
        'user': user,
        'stores': stores,
        'store_rows': rows,
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    })


@web_router.post('/stores')
def create_store_page(request: Request, name: str = Form(...), merchant_id: str = Form(...), city: str = Form('Алматы'), api_token: str = Form(''), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    db.add(Store(name=name.strip(), merchant_id=merchant_id.strip(), city=city.strip() or 'Алматы', api_token=api_token.strip(), owner_id=user.id))
    db.commit()
    return RedirectResponse('/stores?message=' + quote('Магазин добавлен'), status_code=303)


@web_router.post('/stores/{store_id}/update')
def update_store_page(
    request: Request,
    store_id: int,
    name: str = Form(...),
    merchant_id: str = Form(...),
    city: str = Form('Алматы'),
    api_token: str = Form(''),
    is_active: str = Form(''),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        return RedirectResponse('/stores?error=' + quote('Магазин не найден'), status_code=303)
    store.name = name.strip() or store.name
    store.merchant_id = merchant_id.strip() or store.merchant_id
    store.city = city.strip() or 'Алматы'
    if api_token.strip():
        store.api_token = api_token.strip()
    store.is_active = is_active == 'on'
    db.add(store)
    db.commit()
    return RedirectResponse('/stores?message=' + quote('Магазин обновлён'), status_code=303)


@web_router.get('/products', response_class=HTMLResponse)
def products_page(
    request: Request,
    q: str = '',
    store_id: int | None = None,
    view: str = 'all',
    show_limit: int = 500,
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()

    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    selected_store = next((s for s in stores if s.id == selected_store_id), None) if selected_store_id else None

    query = db.query(Product)
    if selected_store_id:
        query = query.filter(Product.store_id == selected_store_id)
    if view == 'ready':
        query = query.filter(Product.auto_pricing_enabled == True, Product.min_price > 0, Product.max_price > 0)
    elif view == 'pending':
        query = query.filter(or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0))
    elif view == 'active':
        query = query.filter(Product.status == ProductStatus.ACTIVE)
    try:
        show_limit = int(show_limit or 500)
    except (TypeError, ValueError):
        show_limit = 500
    show_limit = max(50, min(show_limit, 2000))
    # Для поиска по названию используем Python casefold(), чтобы кириллица искалась стабильнее.
    source_limit = 10000 if q.strip() else show_limit
    source_products = query.order_by(Product.id.desc()).limit(source_limit).all()
    if q.strip():
        products = [p for p in source_products if product_text_matches(p, q)][:show_limit]
    else:
        products = source_products

    pending_query = db.query(Product).filter(
        or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0)
    )
    ready_query = db.query(Product).filter(Product.auto_pricing_enabled == True, Product.min_price > 0, Product.max_price > 0)
    if selected_store_id:
        pending_query = pending_query.filter(Product.store_id == selected_store_id)
        ready_query = ready_query.filter(Product.store_id == selected_store_id)

    pending_products = pending_query.order_by(Product.id.desc()).limit(120).all()
    pending_count = pending_query.count()
    ready_count = ready_query.count()
    try:
        recent_price_lists = generated_price_list_service.list_records(store_id=selected_store_id, limit=8)
    except Exception:
        recent_price_lists = []

    return templates.TemplateResponse('products.html', {
        'request': request,
        'user': user,
        'products': products,
        'pending_products': pending_products,
        'pending_count': pending_count,
        'ready_count': ready_count,
        'stores': stores,
        'selected_store_id': selected_store_id,
        'selected_store': selected_store,
        'q': q,
        'view': view,
        'show_limit': show_limit,
        'recent_price_lists': recent_price_lists,
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    })


@web_router.post('/products')
def create_product_page(
    request: Request,
    store_id: int = Form(...),
    kaspi_sku: str = Form(...),
    url: str = Form(''),
    name: str = Form(...),
    current_price: float = Form(0),
    min_price: float = Form(0),
    max_price: float = Form(0),
    cost_price: float = Form(0),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = Product(
        store_id=store_id,
        kaspi_sku=kaspi_sku.strip(),
        url=url.strip(),
        name=name.strip(),
        current_price=current_price,
        min_price=min_price,
        max_price=max_price,
        cost_price=cost_price,
        status=ProductStatus.ACTIVE if min_price > 0 and max_price > 0 else ProductStatus.PAUSED,
        auto_pricing_enabled=True if min_price > 0 and max_price > 0 else False,
    )
    db.add(product)
    db.flush()
    db.add(PricingRule(product_id=product.id))
    db.commit()
    return RedirectResponse('/products?message=' + quote('Товар добавлен'), status_code=303)


@web_router.post('/products/import-kaspi-excel')
async def import_kaspi_excel_page(
    request: Request,
    store_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    filename = (file.filename or '').lower()
    if not filename.endswith('.xlsx'):
        return RedirectResponse(f'/products?store_id={store_id}&error=' + quote('Нужно выбрать Excel .xlsx из Kaspi, например ACTIVE.xlsx'), status_code=303)
    try:
        result = price_list_import_service.import_xlsx(db, store_id=store_id, data=await file.read())
        msg = f'ACTIVE.xlsx импортирован: новых {result.created}, обновлено {result.updated}, пропущено {result.skipped}, не найдено в последнем файле {result.missing}. Неподтверждённых товаров: {result.pending}. Дальше настрой лимиты и запусти автопилот.'
        db.add(Alert(title='Товары импортированы из Excel', body=msg, type=AlertType.SYSTEM))
        db.commit()
        return RedirectResponse(f'/products?store_id={store_id}&message=' + quote(msg), status_code=303)
    except PriceListImportError as exc:
        return RedirectResponse(f'/products?store_id={store_id}&error=' + quote(str(exc)[:500]), status_code=303)


@web_router.post('/products/bulk-setup')
def bulk_setup_products_page(
    request: Request,
    store_id: int = Form(0),
    min_down_percent: float = Form(12),
    max_up_percent: float = Form(15),
    cost_percent: float = Form(65),
    beat_step: float = Form(2),
    max_change_percent_per_run: float = Form(20),
    min_margin_percent: float = Form(8),
    limit_count: int = Form(0),
    q_filter: str = Form(''),
    product_ids: list[int] | None = Form(None),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    selected_ids = [int(x) for x in (product_ids or []) if int(x) > 0]
    if selected_ids:
        products_query = db.query(Product).filter(Product.id.in_(selected_ids))
    else:
        products_query = db.query(Product).filter(
            or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0)
        )
    if store_id:
        products_query = products_query.filter(Product.store_id == store_id)
    if q_filter.strip():
        like = f'%{q_filter.strip()}%'
        products_query = products_query.filter(or_(
            Product.name.ilike(like),
            Product.kaspi_sku.ilike(like),
            Product.product_id.ilike(like),
            Product.brand.ilike(like),
            Product.model.ilike(like),
            Product.url.ilike(like),
        ))
    try:
        limit_count = int(limit_count or 0)
    except (TypeError, ValueError):
        limit_count = 0
    if limit_count > 0 and not selected_ids:
        products_query = products_query.order_by(Product.id.desc()).limit(min(limit_count, 5000))
    products = products_query.all()
    changed = 0
    for product in products:
        price = float(product.current_price or 0)
        if price <= 0:
            continue
        product.min_price = round(max(1, price * (1 - min_down_percent / 100)), 0)
        product.max_price = round(max(product.min_price, price * (1 + max_up_percent / 100)), 0)
        product.cost_price = round(max(0, price * (cost_percent / 100)), 0)
        product.auto_pricing_enabled = True
        product.status = ProductStatus.ACTIVE
        if not product.pricing_rule:
            db.add(PricingRule(product_id=product.id))
            db.flush()
        rule = product.pricing_rule
        rule.strategy = PricingStrategy.BEAT_BY_STEP
        rule.beat_step = beat_step
        rule.max_change_percent_per_run = max_change_percent_per_run
        rule.min_margin_percent = min_margin_percent
        rule.is_enabled = True
        db.add(product)
        db.add(rule)
        changed += 1
    scope_text = 'выбранных товаров' if selected_ids else ('товаров по лимиту' if limit_count > 0 else 'неподтверждённых товаров')
    db.add(Alert(title='Массовые лимиты настроены', body=f'Настроено {scope_text}: {changed}. Проверь пару товаров глазами перед массовым автопрайсом.', type=AlertType.SYSTEM))
    db.commit()
    return RedirectResponse(f'/products?store_id={store_id}&message=' + quote(f'Настроено {scope_text}: {changed}. Теперь можно запускать автопилот.'), status_code=303)


@web_router.get('/products/{product_id}', response_class=HTMLResponse)
def product_detail(request: Request, product_id: int, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse('/products', status_code=303)
    if not product.pricing_rule:
        db.add(PricingRule(product_id=product.id))
        db.commit()
        db.refresh(product)
    visible_sources = ['kaspi_confirmed', 'price_list_confirmed', 'xml_prepared', 'manual']
    confirmed_history = db.query(PriceHistory).filter(PriceHistory.product_id == product.id, PriceHistory.source.in_(visible_sources)).order_by(PriceHistory.created_at.desc()).limit(20).all()
    return templates.TemplateResponse('product_detail.html', {'request': request, 'user': user, 'product': product, 'confirmed_history': confirmed_history, 'strategies': PricingStrategy, 'message': request.query_params.get('message', ''), 'error': request.query_params.get('error', '')})


@web_router.post('/products/{product_id}/settings')
def update_product_settings_page(
    request: Request,
    product_id: int,
    kaspi_sku: str = Form(...),
    url: str = Form(''),
    name: str = Form(...),
    current_price: float = Form(0),
    min_price: float = Form(0),
    max_price: float = Form(0),
    cost_price: float = Form(0),
    stock: int = Form(0),
    auto_pricing_enabled: str = Form('off'),
    status: str = Form('active'),
    strategy: str = Form('beat_by_step'),
    beat_step: float = Form(10),
    max_change_percent_per_run: float = Form(20),
    min_margin_percent: float = Form(8),
    ignore_sellers: str = Form(''),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse('/products', status_code=303)
    if min_price > 0 and max_price > 0 and min_price > max_price:
        return RedirectResponse(f'/products/{product_id}?error=' + quote('Минимальная цена не может быть выше максимальной'), status_code=303)
    product.kaspi_sku = kaspi_sku.strip()
    product.url = url.strip()
    product.name = name.strip() or product.kaspi_sku
    product.current_price = current_price
    product.min_price = min_price
    product.max_price = max_price
    product.cost_price = cost_price
    product.stock = stock
    product.auto_pricing_enabled = auto_pricing_enabled == 'on'
    try:
        product.status = ProductStatus(status)
    except ValueError:
        product.status = ProductStatus.ACTIVE
    if not product.pricing_rule:
        db.add(PricingRule(product_id=product.id))
        db.flush()
    rule = product.pricing_rule
    try:
        rule.strategy = PricingStrategy(strategy)
    except ValueError:
        rule.strategy = PricingStrategy.BEAT_BY_STEP
    rule.beat_step = beat_step
    rule.max_change_percent_per_run = max_change_percent_per_run
    rule.min_margin_percent = min_margin_percent
    rule.ignore_sellers = ignore_sellers.strip()
    rule.is_enabled = product.auto_pricing_enabled
    db.add(product)
    db.add(rule)
    db.commit()
    return RedirectResponse(f'/products/{product_id}?message=' + quote('Настройки товара сохранены'), status_code=303)


@web_router.post('/products/import')
async def import_products_page(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    filename = (file.filename or '').lower()
    if not filename.endswith('.csv'):
        return RedirectResponse('/products?error=' + quote('Сюда можно загружать только CSV. Excel Kaspi загружай в блок «Импорт товаров из Kaspi Excel». Цена в Kaspi не менялась.'), status_code=303)
    try:
        content = (await file.read()).decode('utf-8-sig')
    except UnicodeDecodeError:
        return RedirectResponse('/products?error=' + quote('Файл не похож на CSV. ACTIVE.xlsx из Kaspi загружай в блок «Импорт товаров из Kaspi Excel». Цена в Kaspi не менялась.'), status_code=303)
    import_service.import_products_csv(db, content)
    return RedirectResponse('/products?message=' + quote('CSV импортирован'), status_code=303)


@web_router.post('/pricing/refresh/{product_id}')
async def refresh_competitors_page(request: Request, product_id: int, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        try:
            await pricing_engine.refresh_competitors(db, product)
            return RedirectResponse(f'/products/{product_id}?message=' + quote('Конкуренты загружены. Цена Kaspi не менялась.'), status_code=303)
        except Exception as exc:
            error_text = str(exc)[:1000]
            db.add(Alert(title='Конкуренты не загружены', body=error_text, type=AlertType.API_ERROR))
            db.commit()
            return RedirectResponse(f'/products/{product_id}?error=' + quote(error_text[:350]), status_code=303)
    return RedirectResponse(f'/products/{product_id}', status_code=303)


@web_router.post('/pricing/push/{product_id}')
async def push_price_page(request: Request, product_id: int, template: UploadFile | None = File(None), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse('/products', status_code=303)
    try:
        price = int(round(float(product.current_price or 0)))
        data = price_list_service.build_xlsx([product], {product.kaspi_sku: price}, template_bytes=await optional_template_bytes(template))
        db.add(Alert(title='Excel-прайс подготовлен', body=f'Это НЕ история изменения цены. В Excel для товара {product.name}: текущая цена {price} тг. Проверь файл и загрузи его в Kaspi вручную.', type=AlertType.SYSTEM))
        db.commit()
        return excel_response(data, f'kaspi_current_product_{product.id}.xlsx')
    except PriceListError as exc:
        return RedirectResponse(f'/products/{product_id}?error=' + quote(str(exc)[:350]), status_code=303)


@web_router.post('/pricing/apply/{product_id}')
async def apply_price_page(request: Request, product_id: int, template: UploadFile | None = File(None), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return RedirectResponse('/products', status_code=303)
    if is_pending_product(product):
        return RedirectResponse(f'/products/{product_id}?error=' + quote('Сначала подтверди товар: укажи минимум, максимум и включи автоцену.'), status_code=303)
    decision = await pricing_engine.preview_product(db, product)
    if not decision.can_apply:
        return RedirectResponse(f'/products/{product_id}?error=' + quote(decision.reason[:350]), status_code=303)
    try:
        price = int(round(float(decision.suggested_price)))
        data = price_list_service.build_xlsx([product], {product.kaspi_sku: price}, template_bytes=await optional_template_bytes(template))
        db.add(Alert(title='Excel-прайс подготовлен', body=f'Это НЕ история изменения цены. В Excel для товара {product.name}: {decision.old_price:.0f} → {price} тг. Проверь файл и загрузи его в Kaspi вручную.', type=AlertType.SYSTEM))
        db.commit()
        return excel_response(data, f'kaspi_auto_product_{product.id}_{price}.xlsx')
    except PriceListError as exc:
        return RedirectResponse(f'/products/{product_id}?error=' + quote(str(exc)[:350]), status_code=303)


@web_router.post('/pricing/run-all')
async def run_all_page(
    request: Request,
    store_id: int = Form(0),
    template: UploadFile = File(...),
    limit_count: int = Form(0),
    q_filter: str = Form(''),
    product_ids: list[int] | None = Form(None),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    filename = (template.filename or '').lower()
    source_filename = template.filename or 'ACTIVE.xlsx'
    if not filename.endswith('.xlsx'):
        return RedirectResponse(f'/products?store_id={store_id}&error=' + quote('Выбери полный ACTIVE.xlsx из Kaspi. Минимальный файл без остальных товаров больше не создаём — это небезопасно.'), status_code=303)
    template_bytes = await template.read()
    template_total_rows = generated_price_list_service.count_sku_rows(template_bytes)

    selected_ids = [int(x) for x in (product_ids or []) if int(x) > 0]
    products_query = db.query(Product).filter(
        Product.auto_pricing_enabled == True,
        Product.status == ProductStatus.ACTIVE,
        Product.min_price > 0,
        Product.max_price > 0,
    )
    if selected_ids:
        products_query = products_query.filter(Product.id.in_(selected_ids))
    if store_id:
        products_query = products_query.filter(Product.store_id == store_id)
    if q_filter.strip():
        like = f'%{q_filter.strip()}%'
        products_query = products_query.filter(or_(
            Product.name.ilike(like),
            Product.kaspi_sku.ilike(like),
            Product.product_id.ilike(like),
            Product.brand.ilike(like),
            Product.model.ilike(like),
            Product.url.ilike(like),
        ))
    try:
        limit_count = int(limit_count or 0)
    except (TypeError, ValueError):
        limit_count = 0
    if limit_count > 0 and not selected_ids:
        products_query = products_query.order_by(Product.id.desc()).limit(min(limit_count, 5000))
    else:
        products_query = products_query.order_by(Product.id.desc()).limit(5000)
    products = products_query.all()
    if not products:
        return RedirectResponse(f'/products?store_id={store_id}&error=' + quote('Для выбранного магазина нет подтверждённых товаров. Сначала импортируй Excel и укажи лимиты.'), status_code=303)

    price_by_sku: dict[str, int] = {}
    changed = 0
    skipped = 0
    for product in products:
        decision = await pricing_engine.preview_product(db, product)
        if decision.can_apply:
            price_by_sku[product.kaspi_sku] = int(round(float(decision.suggested_price)))
            changed += 1
        else:
            price_by_sku[product.kaspi_sku] = int(round(float(product.current_price or 0)))
            skipped += 1
    try:
        data = price_list_service.build_xlsx(products, price_by_sku, template_bytes=template_bytes)
        store = db.query(Store).filter(Store.id == store_id).first() if store_id else None
        store_name = store.name if store else (f'Магазин {store_id}' if store_id else 'Все магазины')
        store_text = f'{store_name}. ' if store_id else ''
        scope_text = 'выбранные товары' if selected_ids else (f'первые {limit_count} товаров' if limit_count > 0 else 'все готовые товары')
        archive = generated_price_list_service.save_excel(
            data,
            store_id=store_id or None,
            store_name=store_name,
            source_filename=source_filename,
            total_rows=template_total_rows,
            processed_products=len(products),
            changed=changed,
            skipped=skipped,
            scope=scope_text,
            selected_count=len(selected_ids),
            q_filter=q_filter,
        )
        db.add(Alert(title='Полный Excel сохранён в архив', body=f'{store_text}Файл: {archive["filename"]}. Размер: {archive["size_label"]}. В полном ACTIVE.xlsx строк: {template_total_rows or "не определено"}. В расчёте: {len(products)}. Строк с новой ценой: {changed}. Без изменений/пропущено: {skipped}. Его можно скачать снова в разделе “Прайс-листы”.', type=AlertType.SYSTEM))
        db.commit()
        return excel_response(data, archive['filename'])
    except PriceListError as exc:
        return RedirectResponse(f'/products?store_id={store_id}&error=' + quote(str(exc)[:350]), status_code=303)


@web_router.get('/price-lists', response_class=HTMLResponse)
def price_lists_page(request: Request, store_id: int | None = None, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    records = generated_price_list_service.list_records(store_id=selected_store_id, limit=80)
    return templates.TemplateResponse('price_lists.html', {
        'request': request,
        'user': user,
        'stores': stores,
        'selected_store_id': selected_store_id,
        'records': records,
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    })


@web_router.get('/price-lists/download/{record_id}')
def download_price_list_page(request: Request, record_id: str, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    record = generated_price_list_service.get_record(record_id)
    if not record:
        return RedirectResponse('/price-lists?error=' + quote('Файл не найден в архиве'), status_code=303)
    data = generated_price_list_service.get_file_bytes(record_id)
    if data is None:
        return RedirectResponse('/price-lists?error=' + quote('Файл есть в истории, но сам Excel не найден. Для Render включи PostgreSQL и пересоздай файл.'), status_code=303)
    filename = record.get('filename') or 'kaspi_price_list.xlsx'
    return StreamingResponse(
        BytesIO(data),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )





@web_router.post('/automation/helper-session/create')
def create_helper_session_page(request: Request, store_id: int = Form(...), db: Session = Depends(get_db)):
    user = require_web_admin(request, db)
    if not user:
        return login_redirect()
    store = db.query(Store).filter(Store.id == int(store_id), Store.is_active == True).first()
    if not store:
        return RedirectResponse('/automation?error=' + quote('Магазин не найден.'), status_code=303)
    created = helper_session_service.create(db, store_id=store.id, user_id=user.id)
    base = public_base_info(request)['production_base']
    helper_url = f'{base}/helper/session/{created.token}'
    request.session['new_helper_url'] = helper_url
    return RedirectResponse(f'/automation?store_id={store.id}&message=' + quote('Временная ссылка для телефона создана.'), status_code=303)


@web_router.post('/automation/helper-session/{session_id}/revoke')
def revoke_helper_session_page(request: Request, session_id: int, store_id: int = Form(...), db: Session = Depends(get_db)):
    user = require_web_admin(request, db)
    if not user:
        return login_redirect()
    row = db.query(HelperSession).filter(HelperSession.id == int(session_id), HelperSession.store_id == int(store_id)).first()
    if row:
        row.status = 'revoked'
        row.revoked_at = datetime.utcnow()
        db.add(row)
        db.commit()
    return RedirectResponse(f'/automation?store_id={store_id}&message=' + quote('Ссылка отключена.'), status_code=303)


@web_router.get('/helper/session/{token}', response_class=HTMLResponse)
def helper_session_page(token: str, request: Request, db: Session = Depends(get_db)):
    try:
        session = helper_session_service.get(db, token, require_active=False)
        store = db.query(Store).filter(Store.id == session.store_id).first()
        error = ''
    except HTTPException as exc:
        session = None
        store = None
        error = str(exc.detail)
    return templates.TemplateResponse('helper.html', {
        'request': request,
        'user': None,
        'token': token,
        'helper_session': session,
        'store': store,
        'error': error,
    })


@web_router.get('/automation/results', response_class=HTMLResponse)
def automation_results_page(
    request: Request,
    store_id: int,
    status_filter: str = 'all',
    job_id: int | None = None,
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    job = db.query(AutopilotJob).filter(AutopilotJob.id == int(job_id)).first() if job_id else (
        db.query(AutopilotJob).filter(AutopilotJob.store_id == int(store_id)).order_by(AutopilotJob.id.desc()).first()
    )
    rows = []
    if job:
        query = db.query(AutopilotJobItem).filter(AutopilotJobItem.job_id == int(job.id))
        if status_filter == 'changed':
            query = query.filter(AutopilotJobItem.changed == True, AutopilotJobItem.status == 'changed')
        elif status_filter == 'unchanged':
            query = query.filter(AutopilotJobItem.status == 'unchanged')
        elif status_filter == 'skipped':
            query = query.filter(AutopilotJobItem.status == 'safe_skipped')
        elif status_filter == 'error':
            query = query.filter(AutopilotJobItem.status == 'error')
        elif status_filter == 'queued':
            query = query.filter(AutopilotJobItem.status == 'queued')
        rows = query.order_by(AutopilotJobItem.updated_at.desc()).limit(1000).all()
    return templates.TemplateResponse('automation_results.html', {
        'request': request,
        'user': user,
        'job': job,
        'rows': rows,
        'store_id': store_id,
        'status_filter': status_filter,
    })


@web_router.post('/xml-history/create')
def xml_history_create(request: Request, store_id: int = Form(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    try:
        record = incremental_pricing_service.rebuild_xml_now(int(store_id), finish_job=False)
        return RedirectResponse(f'/xml-history?store_id={store_id}&feed_id={record["feed_id"]}&message=' + quote('Полный XML создан и проверен.'), status_code=303)
    except Exception as exc:
        return RedirectResponse(f'/xml-history?store_id={store_id}&error=' + quote(autopilot_service._friendly_error(exc)), status_code=303)


@web_router.get('/automation', response_class=HTMLResponse)
def automation_page(request: Request, store_id: int | None = None, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    selected_store = next((store for store in stores if store.id == selected_store_id), None) if selected_store_id else None
    feed_record = xml_feed_service.get_record(selected_store_id) if selected_store_id else None
    feed_versions = xml_feed_service.list_versions(selected_store_id, limit=8) if selected_store_id else []
    feed_pulls = xml_feed_service.list_pulls(selected_store_id, limit=8) if selected_store_id else []
    autopilot_status = autopilot_service.last_status(selected_store_id) if selected_store_id else None
    latest_job = autopilot_service.latest_job(db, selected_store_id) if selected_store_id else None
    price_change_budget = price_change_limiter.usage(db, selected_store_id) if selected_store_id else price_change_limiter.usage(db, None)
    competitor_state = competitor_service.state_info(db)
    ready_count = 0
    active_count = 0
    if selected_store_id:
        ready_count = db.query(Product).filter(Product.store_id == selected_store_id, Product.auto_pricing_enabled == True, Product.status == ProductStatus.ACTIVE, Product.min_price > 0, Product.max_price > 0).count()
        active_count = db.query(Product).filter(Product.store_id == selected_store_id, Product.status == ProductStatus.ACTIVE, Product.current_price > 0).count()
    base_info = public_base_info(request)
    production_feed_url = f"{base_info['production_base']}/kaspi-feed/{selected_store_id}.xml" if selected_store_id else ''
    local_feed_url = f"{base_info['local_base']}/kaspi-feed/{selected_store_id}.xml" if selected_store_id else ''
    feed_url = production_feed_url
    fresh_snapshots = 0
    helper_sessions = []
    if selected_store_id:
        from app.models.autopilot import CompetitorSnapshot
        cutoff = datetime.utcnow() - timedelta(minutes=max(1, int(settings.LOCAL_AGENT_CACHE_TTL_MINUTES or 360)))
        fresh_snapshots = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.store_id == selected_store_id, CompetitorSnapshot.fetched_at >= cutoff).count()
        helper_sessions = db.query(HelperSession).filter(HelperSession.store_id == selected_store_id).order_by(HelperSession.id.desc()).limit(5).all()
    # A GET page must not write to SQLite while the background worker is committing.
    # Remember that the notice was shown in the browser session instead of updating the job.
    recovery_key = f'recovery_notice_seen_{latest_job.id}' if latest_job else ''
    recovery_notice = bool(
        latest_job
        and latest_job.recovery_notice_pending
        and not request.session.get(recovery_key, False)
    )
    if recovery_notice and recovery_key:
        request.session[recovery_key] = True
    new_helper_url = request.session.pop('new_helper_url', '')
    return templates.TemplateResponse('automation.html', {
        'request': request,
        'user': user,
        'stores': stores,
        'selected_store_id': selected_store_id,
        'selected_store': selected_store,
        'feed_record': feed_record,
        'feed_versions': feed_versions,
        'feed_pulls': feed_pulls,
        'autopilot_status': autopilot_status,
        'latest_job': latest_job,
        'price_change_budget': price_change_budget,
        'competitor_state': competitor_state,
        'ready_count': ready_count,
        'active_count': active_count,
        'feed_url': feed_url,
        'production_feed_url': production_feed_url,
        'local_feed_url': local_feed_url,
        'is_local': base_info['is_local'],
        'public_base_valid': base_info['public_base_valid'],
        'fresh_snapshots': fresh_snapshots,
        'helper_sessions': helper_sessions,
        'new_helper_url': new_helper_url,
        'recovery_notice': recovery_notice,
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    })


@web_router.get('/automation/status')
def automation_status_page(request: Request, store_id: int, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
    from app.models.autopilot import CompetitorSnapshot
    cutoff = datetime.utcnow() - timedelta(minutes=max(1, int(settings.LOCAL_AGENT_CACHE_TTL_MINUTES or 360)))
    fresh_snapshots = db.query(CompetitorSnapshot).filter(CompetitorSnapshot.store_id == int(store_id), CompetitorSnapshot.fetched_at >= cutoff).count()
    return JSONResponse({
        'job': autopilot_service.last_status(store_id),
        'budget': price_change_limiter.usage(db, store_id),
        'competitors': competitor_service.state_info(db),
        'feed': xml_feed_service.get_record(store_id),
        'fresh_snapshots': fresh_snapshots,
    })


@web_router.post('/automation/run-now')
def autopilot_run_now_page(request: Request, store_id: int = Form(...), warehouse_id: str = Form(''), limit_count: int = Form(0), q_filter: str = Form(''), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    job = autopilot_service.enqueue(db, store_id, mode='manual', query_filter=q_filter, requested_limit=limit_count, warehouse_id=warehouse_id)
    return RedirectResponse(f'/automation?store_id={store_id}&message=' + quote(f'Задание №{job.id} поставлено в очередь.'), status_code=303)


@web_router.post('/automation/stop')
def autopilot_stop_page(request: Request, store_id: int = Form(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    autopilot_service.request_stop(store_id)
    return RedirectResponse(f'/automation?store_id={store_id}&message=' + quote('Остановка запрошена. Текущий товар завершится безопасно.'), status_code=303)



@web_router.post('/automation/cancel-active')
def autopilot_cancel_active_page(request: Request, store_id: int = Form(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=max(1, int(settings.KASPI_AUTOPILOT_HEARTBEAT_TIMEOUT_MINUTES or 15)))
    candidates = db.query(AutopilotJob).filter(
        AutopilotJob.store_id == int(store_id),
        AutopilotJob.status.in_([AutopilotJobStatus.QUEUED, AutopilotJobStatus.RUNNING, AutopilotJobStatus.PAUSED, AutopilotJobStatus.ERROR]),
    ).all()
    jobs = [
        job for job in candidates
        if job.status in (AutopilotJobStatus.ERROR, AutopilotJobStatus.PAUSED)
        or not job.heartbeat_at
        or job.heartbeat_at < cutoff
    ]
    if not jobs:
        return RedirectResponse(
            f'/automation?store_id={store_id}&error=' + quote('Задание не зависло. Сначала нажмите «Остановить безопасно» или дождитесь обновления heartbeat.'),
            status_code=303,
        )
    for job in jobs:
        job.status = AutopilotJobStatus.CANCELLED
        job.stop_requested = True
        job.finished_at = now
        job.updated_at = now
        job.error_message = job.error_message or 'Зависшее задание отменено без удаления кэша, цен и XML.'
        db.add(job)
    db.commit()
    return RedirectResponse(
        f'/automation?store_id={store_id}&message=' + quote(f'Зависшие задания сброшены: {len(jobs)}. Данные и XML не удалены.'),
        status_code=303,
    )


@web_router.post('/automation/resume')
def autopilot_resume_page(request: Request, job_id: int = Form(...), store_id: int = Form(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    job = autopilot_service.resume(db, job_id)
    if not job:
        return RedirectResponse(f'/automation?store_id={store_id}&error=' + quote('Задание не найдено.'), status_code=303)
    return RedirectResponse(f'/automation?store_id={store_id}&message=' + quote(f'Задание №{job.id} продолжено.'), status_code=303)


@web_router.post('/automation/rebuild-xml')
def rebuild_xml_feed_page(request: Request, store_id: int = Form(...), warehouse_id: str = Form(''), limit_count: int = Form(0), q_filter: str = Form(''), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    job = autopilot_service.enqueue(db, store_id, mode='manual_xml', query_filter=q_filter, requested_limit=limit_count, warehouse_id=warehouse_id)
    return RedirectResponse(f'/automation?store_id={store_id}&message=' + quote(f'Пересборка XML поставлена в очередь: №{job.id}.'), status_code=303)


@web_router.get('/kaspi-feed/{store_id}.xml')
def kaspi_xml_feed(request: Request, store_id: int):
    xml_text = xml_feed_service.get_xml_text(store_id)
    if not xml_text:
        xml_feed_service.log_pull(store_id, request, response_status=503)
        return Response('<?xml version="1.0" encoding="utf-8"?><error>XML ещё не создан.</error>', media_type='application/xml', status_code=503)
    xml_feed_service.log_pull(store_id, request, response_status=200)
    return Response(xml_text, media_type='application/xml', headers={'Content-Disposition': f'inline; filename="kaspi_store_{store_id}.xml"', 'Cache-Control': 'no-cache, no-store, must-revalidate'})


@web_router.get('/xml-history', response_class=HTMLResponse)
def xml_history_page(request: Request, store_id: int | None = None, feed_id: str | None = None, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    selected_store = next((store for store in stores if store.id == selected_store_id), None) if selected_store_id else None
    versions = xml_feed_service.list_versions(selected_store_id, limit=50) if selected_store_id else []
    pulls = xml_feed_service.list_pulls(selected_store_id, limit=100) if selected_store_id else []
    selected_feed_id = feed_id or (versions[0].get('feed_id') if versions else None)
    selected_version = xml_feed_service.get_version(selected_store_id, selected_feed_id) if selected_feed_id else None
    details = (selected_version or {}).get('details', [])
    base_info = public_base_info(request)
    feed_url = f"{base_info['production_base']}/kaspi-feed/{selected_store_id}.xml" if selected_store_id else ''
    local_feed_url = f"{base_info['local_base']}/kaspi-feed/{selected_store_id}.xml" if selected_store_id else ''
    comparison = xml_feed_service.compare_versions(selected_store_id, selected_feed_id) if selected_store_id and selected_feed_id else {'changed': [], 'added': [], 'removed': []}
    return templates.TemplateResponse('xml_history.html', {
        'request': request, 'user': user, 'stores': stores, 'selected_store_id': selected_store_id,
        'selected_store': selected_store, 'versions': versions, 'pulls': pulls,
        'selected_version': selected_version, 'selected_feed_id': selected_feed_id,
        'details': details, 'feed_url': feed_url, 'local_feed_url': local_feed_url, 'is_local': base_info['is_local'], 'comparison': comparison,
        'message': request.query_params.get('message', ''), 'error': request.query_params.get('error', ''),
    })


@web_router.get('/xml-history/download/{store_id}/{feed_id}')
def xml_history_download(request: Request, store_id: int, feed_id: str, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    version = xml_feed_service.get_version(store_id, feed_id)
    if not version:
        return RedirectResponse(f'/xml-history?store_id={store_id}&error=' + quote('Версия XML не найдена.'), status_code=303)
    return Response(version['xml_text'], media_type='application/xml', headers={'Content-Disposition': f'attachment; filename="kaspi_store_{store_id}_{feed_id}.xml"'})


@web_router.post('/xml-history/activate')
def xml_history_activate(request: Request, store_id: int = Form(...), feed_id: str = Form(...), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    try:
        xml_feed_service.activate_version(store_id, feed_id)
        return RedirectResponse(f'/xml-history?store_id={store_id}&feed_id={feed_id}&message=' + quote('Версия XML стала активной.'), status_code=303)
    except XmlFeedError as exc:
        return RedirectResponse(f'/xml-history?store_id={store_id}&error=' + quote(autopilot_service._friendly_error(exc)), status_code=303)


@web_router.get('/android', response_class=HTMLResponse)
def android_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    base_url = str(request.base_url).rstrip('/')
    return templates.TemplateResponse('android.html', {
        'request': request,
        'user': user,
        'base_url': base_url,
        'message': request.query_params.get('message', ''),
        'error': request.query_params.get('error', ''),
    })



@web_router.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request, store_id: int | None = None, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    selected_store_id = resolve_selected_store_id(request, db, stores, store_id)
    base = db.query(Product).filter(Product.store_id == selected_store_id) if selected_store_id else db.query(Product).filter(Product.id < 0)
    total = base.count()
    active = base.filter(Product.status == ProductStatus.ACTIVE).count()
    archived = base.filter(Product.status != ProductStatus.ACTIVE).count()
    no_sku = base.filter(or_(Product.kaspi_sku.is_(None), func.trim(Product.kaspi_sku) == '')).count()
    no_public_id = base.filter(or_(Product.product_id.is_(None), func.trim(Product.product_id) == '')).count()
    no_limits = base.filter(or_(Product.min_price <= 0, Product.max_price <= 0, Product.max_price < Product.min_price)).count()
    out_of_stock = base.filter(Product.stock <= 0).count()
    ready = base.filter(
        Product.status == ProductStatus.ACTIVE,
        Product.auto_pricing_enabled == True,
        Product.min_price > 0,
        Product.max_price >= Product.min_price,
        Product.stock > 0,
    ).count()
    duplicate_skus = []
    duplicate_product_ids = []
    if selected_store_id:
        duplicate_skus = (
            db.query(Product.kaspi_sku, func.count(Product.id).label('count'))
            .filter(Product.store_id == selected_store_id, Product.kaspi_sku.isnot(None), func.trim(Product.kaspi_sku) != '')
            .group_by(Product.kaspi_sku).having(func.count(Product.id) > 1).order_by(func.count(Product.id).desc()).limit(100).all()
        )
        duplicate_product_ids = (
            db.query(Product.product_id, func.count(Product.id).label('count'))
            .filter(Product.store_id == selected_store_id, Product.product_id.isnot(None), func.trim(Product.product_id) != '')
            .group_by(Product.product_id).having(func.count(Product.id) > 1).order_by(func.count(Product.id).desc()).limit(100).all()
        )
    base_info = public_base_info(request)
    is_production = settings.ENVIRONMENT.lower() in {'production', 'prod'}
    return templates.TemplateResponse('settings.html', {
        'request': request,
        'user': user,
        'stores': stores,
        'selected_store_id': selected_store_id,
        'stats': {
            'total': total, 'active': active, 'archived': archived, 'no_sku': no_sku,
            'no_public_id': no_public_id, 'no_limits': no_limits, 'out_of_stock': out_of_stock, 'ready': ready,
            'duplicate_skus': len(duplicate_skus), 'duplicate_product_ids': len(duplicate_product_ids),
        },
        'duplicate_skus': duplicate_skus,
        'duplicate_product_ids': duplicate_product_ids,
        'environment_label': 'Production · PostgreSQL' if is_production else 'Режим разработки · локальная база',
        'is_production': is_production,
        'production_base': base_info['production_base'],
        'database_kind': 'PostgreSQL' if settings.DATABASE_URL.lower().startswith('postgres') else 'SQLite',
    })


@web_router.get('/history', response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    visible_sources = ['kaspi_confirmed', 'price_list_confirmed', 'xml_prepared', 'manual']
    history = db.query(PriceHistory).filter(PriceHistory.source.in_(visible_sources)).order_by(PriceHistory.created_at.desc()).limit(500).all()
    all_count = db.query(PriceHistory).count()
    return templates.TemplateResponse('history.html', {'request': request, 'user': user, 'history': history, 'all_count': all_count, 'message': request.query_params.get('message', '')})


@web_router.post('/history/clear-test')
def clear_test_history_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    deleted = db.query(PriceHistory).filter(PriceHistory.source.in_(['test', 'auto', 'xml_autopilot_prepared'])).delete(synchronize_session=False)
    db.add(Alert(title='Тестовая история очищена', body=f'Удалено записей истории цен: {deleted}. Это не трогает товары и Excel-файлы.', type=AlertType.SYSTEM))
    db.commit()
    return RedirectResponse('/history?message=' + quote('Тестовая история очищена. Товары и цены в Kaspi не трогались.'), status_code=303)
