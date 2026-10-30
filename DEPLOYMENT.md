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

## Registry upgrade

Back up the SQLite database before deploying this version. Schema initialization adds
the application tags, optional pillar, output type, tool link, effort unit, and
cost-presence fields without replacing existing rows. Existing effort values remain
hours. For old records, a positive cost is treated as provided; a zero cost is treated
as unknown until explicitly saved in the new form.

Savings calculations:

- Monthly effort hours = effort per run (converted from minutes if needed) x monthly frequency.
- Monthly hours saved = monthly effort hours x savings percentage / 100.
- Monthly cost saved = equivalent current cost per run x monthly frequency x savings percentage / 100.
- Blank cost keeps hours savings available; no monetary saving is inferred from time alone.
- Costs must use a consistent currency across the register; the app does not convert currencies.

Use Cases > Bulk Upload provides an Excel template with hierarchy IDs, allowed values,
and instructions. Import validates the entire file before saving and offers a count
preview before confirmation. CSV is also supported. Imports append new use cases;
reimporting a previously imported file creates additional records. Use normal Edit
for updates. The ordinary Excel export can also be imported as new records. Its
Tagged Pillar column preserves the explicit use-case tag separately from the inherited
hierarchy pillar displayed in Pillar. Files support up to 5000 data rows.

Contributor selections come from all saved contributor names. Choose Add new
contributor to introduce another name. Application names accept comma-separated tags.
The optional pillar selector adds a use-case pillar; leaving it blank retains any
pillar inherited from the attached hierarchy item.

Register column widths are saved in the browser. Drag a column divider or focus it
and use left/right arrows. Reset Columns restores defaults. Savings columns support
numeric sorting. Process Explorer highlights branches with use cases and offers a
With use cases only filter. Parent detail panels include descendant use cases.

Run backend regression checks with `python3 -m unittest -v test_app`.
