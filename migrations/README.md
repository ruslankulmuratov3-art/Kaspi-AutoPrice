# Additive migrations

The project deliberately avoids destructive startup migrations. Run:

```powershell
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe scripts\migrate.py
```

`Base.metadata.create_all()` creates only missing tables, while `schema_upgrade.py` adds known columns without dropping data. New v3 tables are:

- `autopilot_jobs`
- `competitor_snapshots`
- `competitor_source_states`
- `xml_feed_versions`
- `xml_feed_pulls`
- `price_change_events`
- `pending_price_changes`
