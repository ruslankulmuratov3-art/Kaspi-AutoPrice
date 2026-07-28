Kaspi AutoPrice — Professional UI + XML Autopilot cleanup

Что делает патч:
- Убирает лишние тексты, повторы и технический мусор из интерфейса.
- Делает меню простым: Обзор, Магазины, Товары, Автопилот, XML, История, Архив, Настройки.
- Чинит визуал на телефоне: карточки товаров, крупные кнопки, формы в одну колонку.
- Убирает опасный акцент на ручной Excel; основной режим — XML автопилот.
- Оставляет Excel только как резервный инструмент.
- Упрощает страницы: dashboard, stores, products, automation, xml-history, archive, settings.
- Оставляет PostgreSQL/DATABASE_URL, seed.py и стабильную логику автопилота из stable patch.
- Поиск работает по названию, SKU, product_id, brand, model, url.

Важно:
- Kaspi не даёт callback о финальном применении цен.
- Приложение показывает создание XML и факт запроса XML-ссылки.
- Финальную обработку смотрите в кабинете продавца Kaspi.
- Для Render обязательно используйте DATABASE_URL от PostgreSQL.

После установки:
1) git add .
2) git commit -m "Professional UI and XML autopilot cleanup"
3) git push
4) Render сам сделает deploy
5) Проверить /dashboard, /products, /automation, /xml-history, /price-lists
