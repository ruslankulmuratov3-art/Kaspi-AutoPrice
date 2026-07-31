"""Trusted local competitor worker for Kaspi AutoPrice.

The agent does not expose the computer to the Internet. It pulls product tasks from Render,
reads the public offers endpoint from the user's ordinary connection, and pushes only the
validated JSON result back to Render through a per-device token. The first launch uses a
one-time pairing code created by the administrator.

Fast-safe mode is sequential: it starts faster than the old agent, but automatically slows
down after network/API failures and fully respects Retry-After on HTTP 429.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class AgentConfig:
    render_base_url: str
    token: str
    agent_id: str
    store_id: int | None
    batch_size: int
    delay_seconds: float
    min_delay_seconds: float
    max_delay_seconds: float
    speedup_after_successes: int
    poll_seconds: int
    timeout_seconds: int
    city_id: str
    offers_base_url: str
    offers_limit: int
    sort_option: str
    method: str
    flush_every: int


@dataclass(slots=True)
class AdaptivePacer:
    current_delay: float
    min_delay: float
    max_delay: float
    speedup_after_successes: int = 15
    success_streak: int = 0
    last_started_at: float | None = None

    def wait_for_slot(self) -> None:
        if self.last_started_at is not None:
            elapsed = time.monotonic() - self.last_started_at
            remaining = self.current_delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self.last_started_at = time.monotonic()

    def on_success(self) -> bool:
        self.success_streak += 1
        if self.success_streak < self.speedup_after_successes:
            return False
        self.success_streak = 0
        old = self.current_delay
        self.current_delay = max(self.min_delay, round(self.current_delay * 0.9, 2))
        return self.current_delay != old

    def on_soft_failure(self) -> bool:
        self.success_streak = 0
        old = self.current_delay
        self.current_delay = min(self.max_delay, max(self.current_delay + 2.0, self.current_delay * 1.5))
        return self.current_delay != old

    def on_hard_failure(self) -> bool:
        self.success_streak = 0
        old = self.current_delay
        self.current_delay = self.max_delay
        return self.current_delay != old


def credentials_path() -> Path:
    custom = os.getenv('KASPI_AGENT_CONFIG', '').strip()
    return Path(custom).expanduser() if custom else Path.home() / '.kaspi_autoprice_agent.json'


def load_saved_credentials() -> dict[str, Any]:
    path = credentials_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_credentials(data: dict[str, Any]) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def pair_device(base_url: str, code: str, device_name: str, platform_name: str, timeout_seconds: int = 45) -> dict[str, Any]:
    if not base_url.startswith(('http://', 'https://')):
        raise SystemExit('Ошибка: укажи RENDER_BASE_URL, например https://kaspi-autoprice.onrender.com')
    payload = {
        'code': code.strip(),
        'device_name': device_name.strip() or platform.node() or 'Новое устройство',
        'platform': platform_name.strip() or f'{platform.system()} {platform.release()}',
    }
    try:
        response = httpx.post(
            f'{base_url.rstrip("/")}/api/local-agent/pair',
            json=payload,
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:300]
        raise SystemExit(f'Не удалось подключить устройство: HTTP {exc.response.status_code} · {detail}') from exc
    except httpx.HTTPError as exc:
        raise SystemExit(f'Не удалось связаться с Render: {exc}') from exc
    data = response.json()
    credentials = {
        'render_base_url': base_url.rstrip('/'),
        'token': str(data['token']),
        'agent_id': str(data.get('device_key') or data.get('device_name') or 'paired-device'),
        'device_id': data.get('device_id'),
        'device_name': data.get('device_name') or payload['device_name'],
    }
    save_credentials(credentials)
    return credentials


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def load_config(args: argparse.Namespace) -> AgentConfig:
    saved = load_saved_credentials()
    base_url = (args.url or os.getenv('RENDER_BASE_URL', '') or saved.get('render_base_url', '')).strip().rstrip('/')
    token = (args.token or os.getenv('LOCAL_AGENT_TOKEN', '') or saved.get('token', '')).strip()
    agent_id = (args.agent_id or os.getenv('LOCAL_AGENT_ID', '') or saved.get('agent_id', '')).strip()
    agent_id = agent_id or f'device-{os.getenv("COMPUTERNAME") or os.getenv("HOSTNAME") or "local"}'
    raw_store = args.store_id if args.store_id is not None else os.getenv('LOCAL_AGENT_STORE_ID', '').strip()
    store_id = int(raw_store) if str(raw_store).isdigit() and int(raw_store) > 0 else None
    if not base_url.startswith(('http://', 'https://')):
        raise SystemExit('Ошибка: укажи RENDER_BASE_URL, например https://kaspi-autoprice.onrender.com')
    if not token:
        raise SystemExit('Ошибка: устройство не подключено. Запусти агент с --pair-code DEV-...')

    default_delay = 4.0 if args.fast else 6.0
    configured_delay = args.delay or _float_env('AGENT_DELAY_SECONDS', default_delay)
    min_delay = _float_env('AGENT_MIN_DELAY_SECONDS', 3.0 if args.fast else 4.0)
    max_delay = _float_env('AGENT_MAX_DELAY_SECONDS', 20.0)
    min_delay = max(2.0, min(min_delay, 60.0))
    max_delay = max(min_delay, min(max_delay, 300.0))

    default_batch = 50 if args.fast else 30
    return AgentConfig(
        render_base_url=base_url,
        token=token,
        agent_id=agent_id[:80],
        store_id=store_id,
        batch_size=max(1, min(args.batch_size or _int_env('LOCAL_AGENT_BATCH_SIZE', default_batch), 100)),
        delay_seconds=max(min_delay, min(float(configured_delay), max_delay)),
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
        speedup_after_successes=max(5, min(_int_env('AGENT_SPEEDUP_AFTER_SUCCESSES', 15), 100)),
        poll_seconds=max(10, args.poll or _int_env('AGENT_POLL_SECONDS', 60)),
        timeout_seconds=max(10, _int_env('AGENT_HTTP_TIMEOUT_SECONDS', 45)),
        city_id=os.getenv('KASPI_PUBLIC_CITY_ID', '750000000').strip() or '750000000',
        offers_base_url=os.getenv('KASPI_PUBLIC_OFFERS_BASE_URL', 'https://kaspi.kz/yml/offer-view/offers').strip().rstrip('/'),
        offers_limit=max(1, min(_int_env('KASPI_PUBLIC_OFFERS_LIMIT', 10), 50)),
        sort_option=os.getenv('KASPI_PUBLIC_SORT_OPTION', 'PRICE').strip() or 'PRICE',
        method=os.getenv('KASPI_PUBLIC_OFFERS_METHOD', 'POST').strip().upper() or 'POST',
        flush_every=max(1, min(_int_env('LOCAL_AGENT_FLUSH_EVERY', 25), 100)),
    )


def agent_headers(config: AgentConfig) -> dict[str, str]:
    return {
        'X-Agent-Token': config.token,
        'X-Agent-ID': config.agent_id,
        'Accept': 'application/json',
    }


def fetch_tasks(client: httpx.Client, config: AgentConfig) -> list[dict[str, Any]]:
    params: dict[str, Any] = {'limit': config.batch_size}
    if config.store_id:
        params['store_id'] = config.store_id
    response = client.get(
        f'{config.render_base_url}/api/local-agent/tasks',
        params=params,
        headers=agent_headers(config),
    )
    response.raise_for_status()
    data = response.json()
    return list(data.get('items') or [])


def retry_after_seconds(response: httpx.Response, default: int = 3600) -> int:
    raw = response.headers.get('Retry-After', '').strip()
    try:
        return max(60, min(int(float(raw)), 86400)) if raw else default
    except ValueError:
        return default


def fetch_kaspi_payload(client: httpx.Client, config: AgentConfig, task: dict[str, Any]) -> tuple[str, Any | None, int | None, str, int | None]:
    public_product_id = str(task['public_product_id'])
    url = f'{config.offers_base_url}/{public_product_id}'
    body = {
        'cityId': config.city_id,
        'id': public_product_id,
        'page': 0,
        'limit': config.offers_limit,
        'sortOption': config.sort_option,
    }
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://kaspi.kz',
        'Referer': str(task.get('url') or f'https://kaspi.kz/shop/p/-{public_product_id}/'),
        'User-Agent': 'Kaspi-AutoPrice-Local-Agent/1.1',
    }
    try:
        if config.method == 'GET':
            response = client.get(url, params=body, headers=headers)
        else:
            response = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return 'error', None, None, f'Сетевая ошибка: {exc}', 300

    if response.status_code == 429:
        wait = retry_after_seconds(response)
        return 'error', None, 429, 'Kaspi временно ограничил запросы: HTTP 429', wait
    if response.status_code in (403, 405):
        return 'error', None, response.status_code, f'Kaspi временно отклонил запрос: HTTP {response.status_code}', 3600
    if response.status_code >= 500:
        return 'error', None, response.status_code, f'Kaspi временно недоступен: HTTP {response.status_code}', 300
    if response.status_code >= 400:
        return 'error', None, response.status_code, f'Kaspi вернул HTTP {response.status_code}', 1800

    text = response.text.lstrip()
    content_type = response.headers.get('content-type', '').lower()
    if text.startswith('<') and 'json' not in content_type:
        return 'error', None, response.status_code, 'Kaspi вернул HTML вместо JSON', 3600
    try:
        return 'ok', response.json(), response.status_code, '', None
    except ValueError:
        return 'error', None, response.status_code, 'Kaspi вернул некорректный JSON', 1800


def submit_result(
    client: httpx.Client,
    config: AgentConfig,
    task: dict[str, Any],
    status: str,
    payload: Any | None,
    http_status: int | None,
    error: str,
    retry_after: int | None,
) -> dict[str, Any]:
    body = {
        'product_id': int(task['product_id']),
        'public_product_id': str(task['public_product_id']),
        'lease_token': str(task['lease_token']),
        'status': status,
        'payload': payload,
        'http_status': http_status,
        'error': error,
        'retry_after_seconds': retry_after,
    }
    response = client.post(
        f'{config.render_base_url}/api/local-agent/result',
        json=body,
        headers=agent_headers(config),
    )
    response.raise_for_status()
    return response.json()


def enqueue_autopilot(client: httpx.Client, config: AgentConfig, store_id: int) -> None:
    response = client.post(
        f'{config.render_base_url}/api/local-agent/run-autopilot',
        json={'store_id': store_id},
        headers=agent_headers(config),
    )
    response.raise_for_status()
    data = response.json()
    print(f'Render: автопилот магазина {store_id} поставлен в очередь, job #{data.get("job_id")}.')


def _format_eta(processed: int, started_at: float, batch_remaining: int) -> str:
    elapsed = max(0.001, time.monotonic() - started_at)
    rate = processed / elapsed if processed > 0 else 0.0
    if rate <= 0 or batch_remaining <= 0:
        return ''
    seconds = int(round(batch_remaining / rate))
    minutes, sec = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f' · ETA партии {hours}ч {minutes}м'
    return f' · ETA партии {minutes}м {sec}с'


def run(config: AgentConfig, *, once: bool = False) -> int:
    timeout = httpx.Timeout(config.timeout_seconds)
    synced_since_run: set[int] = set()
    successes_since_flush: dict[int, int] = {}
    pacer = AdaptivePacer(
        current_delay=config.delay_seconds,
        min_delay=config.min_delay_seconds,
        max_delay=config.max_delay_seconds,
        speedup_after_successes=config.speedup_after_successes,
    )
    print('Локальный агент запущен — быстрый безопасный режим.')
    print(f'Render: {config.render_base_url}')
    print(f'Устройство: {config.agent_id}')
    print(f'Задержка: {pacer.current_delay:g} сек. · минимум {pacer.min_delay:g} · максимум {pacer.max_delay:g}')
    print(f'Партия: до {config.batch_size} товаров. Остановить: Ctrl+C')

    with httpx.Client(timeout=timeout, follow_redirects=True) as render_client, httpx.Client(timeout=timeout, follow_redirects=True) as kaspi_client:
        while True:
            try:
                tasks = fetch_tasks(render_client, config)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                print(f'Render API вернул HTTP {code}: {exc.response.text[:200]}')
                if code in (401, 403):
                    return 2
                time.sleep(config.poll_seconds)
                continue
            except httpx.HTTPError as exc:
                print(f'Render временно недоступен: {exc}')
                time.sleep(config.poll_seconds)
                continue

            if not tasks:
                if synced_since_run:
                    for store_id in sorted(synced_since_run):
                        if successes_since_flush.get(store_id, 0) <= 0:
                            continue
                        try:
                            enqueue_autopilot(render_client, config, store_id)
                            successes_since_flush[store_id] = 0
                        except httpx.HTTPError as exc:
                            print(f'Не удалось запустить автопилот магазина {store_id}: {exc}')
                    synced_since_run.clear()
                if once:
                    print('Готово: свежих заданий больше нет.')
                    return 0
                print(f'Новых заданий нет. Следующая проверка через {config.poll_seconds} сек.')
                time.sleep(config.poll_seconds)
                continue

            stop_for_cooldown = False
            batch_started_at = time.monotonic()
            processed_in_batch = 0
            for index, task in enumerate(tasks, start=1):
                pacer.wait_for_slot()
                name = str(task.get('name') or '')[:55]
                public_id = task.get('public_product_id')
                print(f'[{index}/{len(tasks)}] {public_id} · {name}')
                status, payload, http_status, error, retry_after = fetch_kaspi_payload(kaspi_client, config, task)
                try:
                    result = submit_result(render_client, config, task, status, payload, http_status, error, retry_after)
                except httpx.HTTPError as exc:
                    print(f'  Не удалось отправить результат в Render: {exc}')
                    if pacer.on_soft_failure():
                        print(f'  Темп снижен до {pacer.current_delay:g} сек.')
                    continue

                processed_in_batch += 1
                eta = _format_eta(processed_in_batch, batch_started_at, len(tasks) - index)
                if status == 'ok':
                    store_id = int(task['store_id'])
                    synced_since_run.add(store_id)
                    successes_since_flush[store_id] = successes_since_flush.get(store_id, 0) + 1
                    if pacer.on_success():
                        print(f'  Темп ускорен до {pacer.current_delay:g} сек.')
                    print(f'  OK: предложений {result.get("offers", 0)}, минимум {result.get("minimum_price", 0):g} ₸{eta}')
                    if successes_since_flush.get(store_id, 0) >= config.flush_every:
                        try:
                            enqueue_autopilot(render_client, config, store_id)
                            successes_since_flush[store_id] = 0
                        except httpx.HTTPError as exc:
                            print(f'  Не удалось передать партию в автопилот: {exc}')
                    continue

                print(f'  Ошибка: {error}{eta}')
                hard_block = http_status in (403, 405, 429) or 'HTML вместо JSON' in error
                if hard_block:
                    pacer.on_hard_failure()
                    wait = int(retry_after or 3600)
                    print(f'  Массовые запросы остановлены. Пауза {max(1, wait // 60)} мин.')
                    time.sleep(wait)
                    stop_for_cooldown = True
                    break
                if pacer.on_soft_failure():
                    print(f'  Темп снижен до {pacer.current_delay:g} сек.')

            # Не ждём окончания всей очереди: после каждой партии Render быстро
            # пересчитывает только новые данные и публикует полный безопасный XML.
            for store_id in sorted(synced_since_run):
                if successes_since_flush.get(store_id, 0) <= 0:
                    continue
                try:
                    enqueue_autopilot(render_client, config, store_id)
                    successes_since_flush[store_id] = 0
                except httpx.HTTPError as exc:
                    print(f'Не удалось передать партию магазина {store_id}: {exc}')

            if once and stop_for_cooldown:
                return 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Local Kaspi competitor agent')
    parser.add_argument('--url', help='Render base URL')
    parser.add_argument('--token', help='Existing per-device token (normally loaded automatically)')
    parser.add_argument('--agent-id', help='Optional local label')
    parser.add_argument('--pair-code', help='One-time DEV code created by the administrator')
    parser.add_argument('--device-name', default='', help='Name shown in the admin panel')
    parser.add_argument('--platform', default='', help='Optional platform name')
    parser.add_argument('--reset-config', action='store_true', help='Delete saved device credentials and exit')
    parser.add_argument('--store-id', type=int, help='Only one Render store id')
    parser.add_argument('--batch-size', type=int, default=0)
    parser.add_argument('--delay', type=float, default=0)
    parser.add_argument('--poll', type=int, default=0)
    parser.add_argument('--fast', action='store_true', help='Start in safe fast mode (4 seconds, adaptive backoff)')
    parser.add_argument('--once', action='store_true', help='Process all currently due products and exit')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.reset_config:
        path = credentials_path()
        path.unlink(missing_ok=True)
        print(f'Сохранённые данные устройства удалены: {path}')
        return 0
    saved = load_saved_credentials()
    base_url = (args.url or os.getenv('RENDER_BASE_URL', '') or saved.get('render_base_url', '')).strip().rstrip('/')
    if args.pair_code:
        name = args.device_name or platform.node() or 'Новое устройство'
        platform_name = args.platform or f'{platform.system()} {platform.release()}'
        credentials = pair_device(base_url, args.pair_code, name, platform_name)
        print(f'Устройство подключено: {credentials.get("device_name")}')
        print(f'Данные сохранены: {credentials_path()}')
    try:
        config = load_config(args)
        return run(config, once=args.once)
    except KeyboardInterrupt:
        print('\nАгент остановлен.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
