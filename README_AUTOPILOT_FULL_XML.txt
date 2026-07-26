Kaspi AutoPrice — Full XML Autopilot Patch

Что добавлено:
1. После первого импорта ACTIVE.xlsx товары сохраняются в базе сайта.
2. XML-автопилот сам пересобирает XML из базы и правил лимитов.
3. При каждом открытии /kaspi-feed/{store_id}.xml сайт проверяет, не устарел ли XML.
4. Если XML старше KASPI_XML_STALE_AFTER_MINUTES, сайт пересобирает XML перед отдачей.
5. Есть фоновый автопилот каждые KASPI_AUTOPILOT_INTERVAL_MINUTES, пока Render-сервис активен.
6. Все версии XML и pull-логи остаются в /xml-history.
7. Поиск товаров по названию/SKU стал надёжнее для кириллицы: финальный поиск делает Python casefold().

Новые переменные Render:
KASPI_AUTOPILOT_ENABLED=true
KASPI_AUTOPILOT_INTERVAL_MINUTES=60
KASPI_AUTOPILOT_STARTUP_DELAY_SECONDS=25
KASPI_AUTOPILOT_MAX_PRODUCTS_PER_RUN=5000
KASPI_AUTOPILOT_WAREHOUSE_ID=PP1
KASPI_AUTOPILOT_UPDATE_LOCAL_PRICE=true
KASPI_AUTOPILOT_DELAY_SECONDS=0
KASPI_XML_REBUILD_ON_PULL=true
KASPI_XML_STALE_AFTER_MINUTES=55

Важно:
Kaspi забирает XML по своему расписанию. Сайт видит факт запроса XML, но Kaspi не отправляет callback о том, что все цены приняты. Финальный статус проверяется в кабинете Kaspi.
