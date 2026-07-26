Kaspi public offers patch

Что делает:
- добавляет загрузку конкурентов с публичной витрины Kaspi через https://kaspi.kz/yml/offer-view/offers/{productId}
- добавляет поле URL товара Kaspi
- добавляет безопасную кнопку "Загрузить конкурентов" — она только читает, цену не меняет
- скрывает глобальную кнопку "Запустить автоцены" сверху, чтобы случайно не пройтись по всем товарам
- оставляет изменение твоей цены только через официальный Kaspi API

Как поставить:
1) Останови сервер Ctrl+C.
2) Скопируй эти файлы с заменой в свою папку kaspi_saas_real.
3) В .env добавь, если нет:
   KASPI_PUBLIC_OFFERS_ENABLED=true
   KASPI_PUBLIC_OFFERS_BASE_URL=https://kaspi.kz/yml/offer-view/offers
   KASPI_PUBLIC_CITY_ID=750000000
   KASPI_PUBLIC_OFFERS_LIMIT=30
   KASPI_PUBLIC_SORT_OPTION=PRICE
4) Запусти сервер:
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

Важно:
- Твой .env не отправляй никому.
- Сначала нажимай только "Загрузить конкурентов".
- "Рассчитать и отправить автоцену" меняет цену в Kaspi.
