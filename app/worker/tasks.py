import asyncio
from app.worker.celery_app import celery_app
from app.core.database import db_session
from app.models.product import Product
from app.models.task_log import TaskLog, TaskStatus
from app.services.pricing_engine import pricing_engine


@celery_app.task(name='app.worker.tasks.run_auto_pricing')
def run_auto_pricing():
    async def runner():
        changed = 0
        checked = 0
        with db_session() as db:
            log = TaskLog(task_name='run_auto_pricing', status=TaskStatus.RUNNING)
            db.add(log)
            db.flush()
            for product in db.query(Product).limit(500).all():
                decision = await pricing_engine.apply_product(db, product)
                checked += 1
                if decision.can_apply:
                    changed += 1
            log.status = TaskStatus.SUCCESS
            log.message = f'checked={checked}, changed={changed}'
            db.add(log)
        return {'checked': checked, 'changed': changed}
    return asyncio.run(runner())


@celery_app.task(name='app.worker.tasks.sync_products')
def sync_products():
    with db_session() as db:
        log = TaskLog(task_name='sync_products', status=TaskStatus.SUCCESS, message='sync placeholder: integrate real Kaspi catalog endpoint')
        db.add(log)
    return {'status': 'ok'}
