Патч исправляет ошибку Kaspi:
Content type 'application/json' not supported

Что изменено:
app/services/kaspi_client.py теперь отправляет JSON-тело в /products/import
с Content-Type: text/plain; charset=utf-8, как в официальном примере Kaspi.

Как поставить:
1) Остановить сайт Ctrl+C
2) Скопировать папку app из патча в проект с заменой
3) Запустить сайт снова
