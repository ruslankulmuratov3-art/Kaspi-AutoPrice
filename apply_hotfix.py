
from pathlib import Path
import sys

path = Path("app/services/autopilot_service.py")
if not path.exists():
    raise SystemExit("ERROR: app/services/autopilot_service.py not found. Run this from project root.")

text = path.read_text(encoding="utf-8")

old = """delay = 0.0 if job.mode == 'local_agent_sync' else max(0.0, float(settings.KASPI_AUTOPILOT_DELAY_SECONDS or 0))"""

new = """# Read mode in a live SQLAlchemy session. The original `job` object may already
        # be detached/expired after commit or session close on PostgreSQL/Render.
        with SessionLocal() as mode_db:
            job_mode = (
                mode_db.query(AutopilotJob.mode)
                .filter(AutopilotJob.id == job_id)
                .scalar()
            )
        delay = 0.0 if job_mode == 'local_agent_sync' else max(
            0.0,
            float(settings.KASPI_AUTOPILOT_DELAY_SECONDS or 0),
        )"""

if old not in text:
    if "job_mode = (" in text and "AutopilotJob.mode" in text:
        print("OK: hotfix already applied.")
        raise SystemExit(0)
    raise SystemExit(
        "ERROR: target line not found. Your file differs from expected version. "
        "Send app/services/autopilot_service.py for a precise patch."
    )

backup = path.with_suffix(".py.detached_backup")
backup.write_text(text, encoding="utf-8")

path.write_text(text.replace(old, new, 1), encoding="utf-8")
print(f"OK: patched {path}")
print(f"Backup: {backup}")
