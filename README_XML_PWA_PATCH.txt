Kaspi XML + Android PWA patch

Что добавлено:
1) /automation — страница XML-автозагрузки.
2) /automation/rebuild-xml — пересобирает XML по конкурентам и лимитам.
3) /kaspi-feed/{store_id}.xml — публичная ссылка XML для Kaspi.
4) /android — страница установки на Android.
5) PWA manifest + service worker.

Важно:
- 127.0.0.1 Kaspi и Android из интернета не увидят. Нужен Render/VPS/домен HTTPS.
- Прямой официальный API endpoint “изменить цену по токену” в Kaspi Гиде не найден. Официальный авто-путь — XML автозагрузка.
- Для XML нужен настоящий код склада/точки из Kaspi: раздел Склады и магазины.
