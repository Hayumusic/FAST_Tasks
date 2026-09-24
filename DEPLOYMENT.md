# FAST Deployment Notes

This app is intentionally lightweight for low monthly data volume.

## Cost-effective AWS path

- Run the Flask app on a small AWS Lightsail instance.
- Use Gunicorn behind Nginx for production traffic.
- Keep SQLite on a persistent disk by setting `FAST_DB_PATH`.
- Enable Lightsail snapshots so the database is backed up.

Example environment:

```sh
FAST_DB_PATH=/opt/fast/data/fast_tracker.sqlite3
FLASK_DEBUG=false
PORT=8000
```

Start command:

```sh
gunicorn app:app --bind 0.0.0.0:${PORT:-8000}
```

Health check:

```text
GET /healthz
```

## Backend deployment checklist

- Install dependencies with `pip install -r requirements.txt`.
- Set `FLASK_DEBUG=false` in hosted environments.
- Set `PORT` to the platform-provided port, or default to `8000` with Gunicorn.
- Set `FAST_DB_PATH` to a persistent disk path if using SQLite.
- Do not commit or deploy `instance/fast_tracker.sqlite3`; the app creates an empty database on first start.
- Back up the SQLite file via Lightsail snapshots or a scheduled copy from the persistent disk.
- Use `/healthz` for load balancer or uptime checks.

## More managed AWS path

For stricter production requirements, move the database to PostgreSQL on RDS and keep the Flask app on App Runner or Elastic Beanstalk. The current low-cost build keeps persistence isolated through `get_db()`, so the database layer can be migrated without changing the frontend workflows.
