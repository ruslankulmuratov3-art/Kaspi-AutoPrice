# Kaspi Price Manager Real API

Версия без тестовых цен и без mock-заглушек.

Что важно:

- Токен Kaspi не хранится в коде.
- Токен вставляется только в `.env` или в настройках магазина внутри админки.
- Если токен или endpoint не заполнены, проект показывает ошибку API и не имитирует успешную работу.
- `scripts/seed.py` создаёт только администратора. Демо-магазины и демо-товары не создаются.

## Быстрый запуск на Windows / PyCharm

```bash
pip install -r requirements.txt
copy .env.example .env
python scripts/seed.py
uvicorn app.main:app --reload
```

Открыть:

```text
http://127.0.0.1:8000
```

Логин по умолчанию:

```text
admin
```

Пароль:

```text
ChangeMe123!
```

## Куда вставлять Kaspi API

Открой файл `.env` и заполни:

```env
KASPI_BASE_URL=https://kaspi.kz/shop/api
KASPI_API_TOKEN=сюда_твой_токен
KASPI_MERCHANT_ID=твой_merchant_id
KASPI_STORE_ID=твой_store_id_если_нужен
KASPI_COMPANY_NAME=название_компании
```

Токен никому не отправляй и не загружай в GitHub.

`.gitignore` уже закрывает `.env`.

## Как проверить подключение

После запуска сайта зайди в браузере:

```text
http://127.0.0.1:8000/docs
```

Открой endpoint:

```text
GET /api/kaspi/test
```

Он делает настоящий безопасный запрос к Kaspi import schema. Товары не меняет.

## Как отправить цену в Kaspi

1. В админке создай магазин.
2. Вставь API Token в магазин или в `.env`.
3. Добавь товар с настоящим SKU Kaspi.
4. Укажи текущую цену.
5. Открой карточку товара.
6. Нажми `Отправить текущую цену в Kaspi`.

Проект отправит реальный запрос в Kaspi через `app/services/kaspi_client.py`.

## Формат изменения цены

По умолчанию:

```env
KASPI_PRICE_UPDATE_FORMAT=json_import
```

Если твой кабинет Kaspi принимает XML-прайс лист, можно поставить:

```env
KASPI_PRICE_UPDATE_FORMAT=xml_catalog
```

## Автоцены по конкурентам

Проект не придумывает конкурентов и не генерирует fake-офферы.

Если в твоём партнёрском API есть реальный endpoint офферов конкурентов, заполни:

```env
KASPI_OFFERS_URL_TEMPLATE=https://адрес/{sku}/offers
```

Поддерживаемые подстановки:

```text
{sku}
{product_id}
{merchant_id}
{city}
```

Если endpoint не заполнен, кнопка автоцены не будет использовать выдуманные данные.

## Главный файл Kaspi интеграции

```text
app/services/kaspi_client.py
```

В нём нет mock-логики. Только реальные HTTP-запросы.
