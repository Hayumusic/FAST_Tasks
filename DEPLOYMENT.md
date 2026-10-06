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
FAST_SECRET_KEY=<long-random-session-secret>
FAST_ADMIN_ID=<admin-id>
FAST_ADMIN_PASSWORD_HASH=<werkzeug-password-hash>
```

Start command:

```sh
gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000}
```

Health check:

```text
GET /healthz
```

## Backend deployment checklist

- Install dependencies with `pip install -r requirements.txt`.
- Run the start command from the repository root, where `app.py`, `wsgi.py`, `static/`, and `templates/` exist.
- Set `FLASK_DEBUG=false` in hosted environments.
- Set `PORT` to the platform-provided port, or default to `8000` with Gunicorn.
- Set `FAST_DB_PATH` to a persistent disk path if using SQLite.
- Set `FAST_SECRET_KEY` to a long random value so admin sessions remain secure. The app will not start with `FLASK_DEBUG=false` unless this is set.
- Set `FAST_ADMIN_ID` and `FAST_ADMIN_PASSWORD_HASH` to enable Setup access.
- Do not commit or deploy `instance/fast_tracker.sqlite3`; the app creates an empty database on first start.
- Back up the SQLite file via Lightsail snapshots or a scheduled copy from the persistent disk.
- Use `/healthz` for load balancer or uptime checks.

Generate an admin password hash locally:

```sh
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('replace-with-admin-password'))"
```

Generate a session secret locally:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Setup is admin-only. Dashboard, Process Explorer, Use Cases, and export remain open without login.

If starting from another directory, pass the app folder explicitly:

```sh
gunicorn --chdir /path/to/FAST_Tasks wsgi:app --bind 0.0.0.0:${PORT:-8000}
```

## More managed AWS path

For stricter production requirements, move the database to PostgreSQL on RDS and keep the Flask app on App Runner or Elastic Beanstalk. The current low-cost build keeps persistence isolated through `get_db()`, so the database layer can be migrated without changing the frontend workflows.
