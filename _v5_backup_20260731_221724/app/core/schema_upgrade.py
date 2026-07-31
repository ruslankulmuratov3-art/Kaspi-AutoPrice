from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def _dialect(engine: Engine) -> str:
    return str(engine.dialect.name or '').lower()


def _existing_columns(engine: Engine, table: str) -> set[str]:
    try:
        return {col['name'] for col in inspect(engine).get_columns(table)}
    except Exception:
        return set()


def _add_column(engine: Engine, table: str, column: str, ddl_type: str) -> None:
    existing = _existing_columns(engine, table)
    if column in existing:
        return
    dialect = _dialect(engine)
    if dialect == 'postgresql':
        sql = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}'
    else:
        sql = f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'
    with engine.begin() as conn:
        conn.execute(text(sql))


def _add_index(engine: Engine, name: str, table: str, column: str) -> None:
    dialect = _dialect(engine)
    with engine.begin() as conn:
        if dialect == 'postgresql':
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})'))
        else:
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})'))


def run_safe_schema_upgrade(engine: Engine) -> None:
    """Best-effort additive migration. Never drops tables/data."""
    if not _existing_columns(engine, 'products'):
        return
    dialect = _dialect(engine)
    dt = 'TIMESTAMP' if dialect == 'postgresql' else 'DATETIME'
    bool_type = 'BOOLEAN' if dialect == 'postgresql' else 'BOOLEAN'
    varchar_80 = 'VARCHAR(80)' if dialect == 'postgresql' else 'VARCHAR(80)'
    varchar_120 = 'VARCHAR(120)' if dialect == 'postgresql' else 'VARCHAR(120)'
    varchar_255 = 'VARCHAR(255)' if dialect == 'postgresql' else 'VARCHAR(255)'
    text_type = 'TEXT'
    float_type = 'DOUBLE PRECISION' if dialect == 'postgresql' else 'FLOAT'

    product_columns = {
        'model': varchar_255,
        'product_id': varchar_120,
        'last_imported_at': dt,
        'last_seen_import_batch': varchar_80,
        'missing_from_last_import': bool_type,
        'last_competitor_checked_at': dt,
        'last_pricing_calculated_at': dt,
        'last_competitor_price': float_type,
        'last_autopilot_error': text_type,
    }
    for column, ddl_type in product_columns.items():
        try:
            _add_column(engine, 'products', column, ddl_type)
        except Exception:
            # Additive migrations are best effort across SQLite/Postgres variations.
            pass

    user_columns = {
        'full_name': varchar_255,
        'avatar_url': text_type,
        'auth_provider': varchar_80,
        'google_sub': varchar_255,
        'email_verified': bool_type,
        'last_login_at': dt,
    }
    if _existing_columns(engine, 'users'):
        for column, ddl_type in user_columns.items():
            try:
                _add_column(engine, 'users', column, ddl_type)
            except Exception:
                pass
        try:
            _add_index(engine, 'ix_users_google_sub', 'users', 'google_sub')
            with engine.begin() as conn:
                if dialect == 'postgresql':
                    conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub_not_null ON users (google_sub) WHERE google_sub IS NOT NULL'))
                else:
                    conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_users_google_sub_not_null ON users (google_sub)'))
        except Exception:
            pass

    store_columns = {
        'description': text_type,
        'logo_url': text_type,
        'last_selected_at': dt,
    }
    if _existing_columns(engine, 'stores'):
        for column, ddl_type in store_columns.items():
            try:
                _add_column(engine, 'stores', column, ddl_type)
            except Exception:
                pass

    snapshot_columns = {
        'last_attempt_at': dt,
        'next_retry_at': dt,
        'lease_owner': varchar_120,
        'lease_token': varchar_80,
        'lease_started_at': dt,
        'lease_until': dt,
    }
    if _existing_columns(engine, 'competitor_snapshots'):
        for column, ddl_type in snapshot_columns.items():
            try:
                _add_column(engine, 'competitor_snapshots', column, ddl_type)
            except Exception:
                pass
        try:
            _add_index(engine, 'ix_competitor_snapshots_next_retry_at', 'competitor_snapshots', 'next_retry_at')
            _add_index(engine, 'ix_competitor_snapshots_lease_until', 'competitor_snapshots', 'lease_until')
        except Exception:
            pass

    try:
        _add_index(engine, 'ix_products_product_id', 'products', 'product_id')
        _add_index(engine, 'ix_products_name', 'products', 'name')
    except Exception:
        pass
