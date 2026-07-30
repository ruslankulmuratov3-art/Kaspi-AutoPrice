from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from urllib.parse import quote
from io import BytesIO
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.security import TOKEN_COOKIE_NAME
from app.models.product import Product, ProductStatus
from app.models.store import Store
from app.models.price_history import PriceHistory
from app.models.pricing_rule import PricingRule, PricingStrategy
from app.models.alert import Alert, AlertType
from app.models.user import User
from app.repositories.users import users
from app.services.auth_service import auth_service
from app.services.report_service import report_service
from app.services.import_service import import_service
from app.services.pricing_engine import pricing_engine
from app.services.price_list_service import price_list_service, PriceListError
from app.services.price_list_import_service import price_list_import_service, PriceListImportError
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


def is_pending_product(product: Product) -> bool:
    return (
        not product.auto_pricing_enabled
        or float(product.min_price or 0) <= 0
        or float(product.max_price or 0) <= 0
        or (float(product.max_price or 0) < float(product.min_price or 0) and float(product.max_price or 0) > 0)
    )


@web_router.get('/', response_class=HTMLResponse)
def root(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return RedirectResponse('/login', status_code=303)
    return RedirectResponse('/dashboard', status_code=303)


@web_router.get('/login', response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    users.ensure_admin(db, settings.ADMIN_EMAIL, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
    return templates.TemplateResponse('login.html', {'request': request, 'app_name': settings.APP_NAME})


@web_router.post('/login')
def login_action(response: Response, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, username, password)
    token = auth_service.token_for_user(user)
    redirect = RedirectResponse('/dashboard', status_code=303)
    redirect.set_cookie(TOKEN_COOKIE_NAME, token, httponly=True, samesite='lax')
    return redirect


@web_router.post('/logout')
def logout_action():
    redirect = RedirectResponse('/login', status_code=303)
    redirect.delete_cookie(TOKEN_COOKIE_NAME)
    return redirect


@web_router.get('/dashboard', response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    metrics = report_service.dashboard_metrics(db)
    recent_products = db.query(Product).order_by(Product.updated_at.desc()).limit(8).all()
    confirmed_sources = ['kaspi_confirmed', 'price_list_confirmed']
    history = db.query(PriceHistory).filter(PriceHistory.source.in_(confirmed_sources)).order_by(PriceHistory.created_at.desc()).limit(10).all()
    alerts = db.query(Alert).order_by(Alert.created_at.desc()).limit(8).all()
    return templates.TemplateResponse('dashboard.html', {'request': request, 'user': user, 'metrics': metrics, 'products': recent_products, 'history': history, 'alerts': alerts})


@web_router.get('/stores', response_class=HTMLResponse)
def stores_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    stores = db.query(Store).order_by(Store.id.desc()).all()
    return templates.TemplateResponse('stores.html', {'request': request, 'user': user, 'stores': stores})


@web_router.post('/stores')
def create_store_page(request: Request, name: str = Form(...), merchant_id: str = Form(...), city: str = Form('Алматы'), api_token: str = Form(''), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    db.add(Store(name=name, merchant_id=merchant_id, city=city, api_token=api_token, owner_id=user.id))
    db.commit()
    return RedirectResponse('/stores', status_code=303)


@web_router.get('/products', response_class=HTMLResponse)
def products_page(request: Request, q: str = '', db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    query = db.query(Product)
    if q:
        like = f'%{q}%'
        query = query.filter((Product.name.ilike(like)) | (Product.kaspi_sku.ilike(like)))
    products = query.order_by(Product.id.desc()).limit(300).all()
    stores = db.query(Store).order_by(Store.name.asc()).all()
    pending_products = db.query(Product).filter(
        or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0)
    ).order_by(Product.id.desc()).limit(80).all()
    pending_count = db.query(Product).filter(
        or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0)
    ).count()
    ready_count = db.query(Product).filter(Product.auto_pricing_enabled == True, Product.min_price > 0, Product.max_price > 0).count()
    return templates.TemplateResponse('products.html', {
        'request': request,
        'user': user,
        'products': products,
        'pending_products': pending_products,
        'pending_count': pending_count,
        'ready_count': ready_count,
        'stores': stores,
        'q': q,
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
        return RedirectResponse('/products?error=' + quote('Нужно выбрать Excel .xlsx из Kaspi, например ACTIVE.xlsx'), status_code=303)
    try:
        result = price_list_import_service.import_xlsx(db, store_id=store_id, data=await file.read())
        msg = f'Excel импортирован: новых {result.created}, обновлено {result.updated}, пропущено {result.skipped}. Неподтверждённых товаров: {result.pending}. Теперь укажи мин/макс/себестоимость.'
        db.add(Alert(title='Товары импортированы из Excel', body=msg, type=AlertType.SYSTEM))
        db.commit()
        return RedirectResponse('/products?message=' + quote(msg), status_code=303)
    except PriceListImportError as exc:
        return RedirectResponse('/products?error=' + quote(str(exc)[:500]), status_code=303)


@web_router.post('/products/bulk-setup')
def bulk_setup_products_page(
    request: Request,
    min_down_percent: float = Form(12),
    max_up_percent: float = Form(15),
    cost_percent: float = Form(65),
    beat_step: float = Form(10),
    max_change_percent_per_run: float = Form(20),
    min_margin_percent: float = Form(8),
    db: Session = Depends(get_db),
):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    products = db.query(Product).filter(
        or_(Product.auto_pricing_enabled == False, Product.min_price <= 0, Product.max_price <= 0)
    ).all()
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
        rule.beat_step = beat_step
        rule.max_change_percent_per_run = max_change_percent_per_run
        rule.min_margin_percent = min_margin_percent
        rule.is_enabled = True
        db.add(product)
        db.add(rule)
        changed += 1
    db.add(Alert(title='Массовые лимиты настроены', body=f'Настроено товаров: {changed}. Проверь пару товаров глазами перед массовым автопрайсом.', type=AlertType.SYSTEM))
    db.commit()
    return RedirectResponse('/products?message=' + quote(f'Настроено неподтверждённых товаров: {changed}. Проверь лимиты перед массовым автопрайсом.'), status_code=303)


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
    confirmed_sources = ['kaspi_confirmed', 'price_list_confirmed']
    confirmed_history = db.query(PriceHistory).filter(PriceHistory.product_id == product.id, PriceHistory.source.in_(confirmed_sources)).order_by(PriceHistory.created_at.desc()).limit(10).all()
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
async def run_all_page(request: Request, template: UploadFile | None = File(None), db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    products = db.query(Product).filter(
        Product.auto_pricing_enabled == True,
        Product.status == ProductStatus.ACTIVE,
        Product.min_price > 0,
        Product.max_price > 0,
    ).limit(2000).all()
    if not products:
        return RedirectResponse('/products?error=' + quote('Нет подтверждённых товаров для автопрайса. Сначала импортируй Excel и укажи лимиты.'), status_code=303)
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
        data = price_list_service.build_xlsx(products, price_by_sku, template_bytes=await optional_template_bytes(template))
        db.add(Alert(title='Excel-прайс подготовлен', body=f'Это НЕ история изменения цен. Подтверждённых товаров в расчёте: {len(products)}. Строк с новой ценой: {changed}. Без изменений/пропущено: {skipped}. Проверь Excel перед загрузкой в Kaspi.', type=AlertType.SYSTEM))
        db.commit()
        return excel_response(data, 'kaspi_auto_all_products.xlsx')
    except PriceListError as exc:
        return RedirectResponse('/products?error=' + quote(str(exc)[:350]), status_code=303)


@web_router.get('/history', response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    confirmed_sources = ['kaspi_confirmed', 'price_list_confirmed']
    history = db.query(PriceHistory).filter(PriceHistory.source.in_(confirmed_sources)).order_by(PriceHistory.created_at.desc()).limit(300).all()
    all_count = db.query(PriceHistory).count()
    return templates.TemplateResponse('history.html', {'request': request, 'user': user, 'history': history, 'all_count': all_count, 'message': request.query_params.get('message', '')})


@web_router.post('/history/clear-test')
def clear_test_history_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    deleted = db.query(PriceHistory).delete()
    db.add(Alert(title='Тестовая история очищена', body=f'Удалено записей истории цен: {deleted}. Это не трогает товары и Excel-файлы.', type=AlertType.SYSTEM))
    db.commit()
    return RedirectResponse('/history?message=' + quote('Тестовая история очищена. Товары и цены в Kaspi не трогались.'), status_code=303)


@web_router.get('/admin', response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user = ensure_user(request, db)
    if not user:
        return login_redirect()
    all_users = db.query(User).order_by(User.id.asc()).all()
    alerts = db.query(Alert).order_by(Alert.id.desc()).limit(50).all()
    return templates.TemplateResponse('admin.html', {'request': request, 'user': user, 'users': all_users, 'alerts': alerts})
