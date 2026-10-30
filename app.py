from __future__ import annotations

import csv
import os
import math
import sqlite3
from datetime import date, datetime
from functools import wraps
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, render_template, request, send_file, session
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from werkzeug.security import check_password_hash


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("FAST_DB_PATH", BASE_DIR / "instance" / "fast_tracker.sqlite3")).expanduser()

NODE_TYPES = {"process", "subprocess", "task"}
USE_CASE_TYPES = {"Automation", "AI", "Agentic AI"}
STATUSES = {"Planned", "WIP", "Built", "Built and Deployed"}
OUTPUT_TYPES = {"GPT", "Plugin", "Web tool", "Web app", "Web integration", "Agent", "Workflow", "Script", "Other"}

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
if os.environ.get("FLASK_DEBUG", "true").lower() != "true" and not os.environ.get("FAST_SECRET_KEY"):
    raise RuntimeError("FAST_SECRET_KEY must be set when FLASK_DEBUG is false")
app.secret_key = os.environ.get("FAST_SECRET_KEY", "dev-only-local-secret")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_DEBUG", "true").lower() != "true"


@app.after_request
def add_no_cache_headers(response):
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DB_PATH.parent.mkdir(exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def is_admin() -> bool:
    return bool(session.get("is_admin"))


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "Admin login required"}), 401
        return view(*args, **kwargs)

    return wrapped


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL CHECK(type IN ('process', 'subprocess', 'task')),
            parent_id INTEGER REFERENCES nodes(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pillars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS node_pillars (
            node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            pillar_id INTEGER NOT NULL REFERENCES pillars(id) ON DELETE CASCADE,
            PRIMARY KEY (node_id, pillar_id)
        );

        CREATE TABLE IF NOT EXISTS use_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            problem_statement TEXT NOT NULL,
            use_case_type TEXT NOT NULL,
            short_description TEXT NOT NULL,
            status TEXT NOT NULL,
            contributor TEXT NOT NULL,
            created_date TEXT NOT NULL,
            updated_date TEXT NOT NULL,
            effort_hours REAL NOT NULL DEFAULT 0,
            cost_per_run REAL NOT NULL DEFAULT 0,
            frequency_per_month REAL NOT NULL DEFAULT 0,
            savings_percent REAL NOT NULL DEFAULT 0
        );
        """
    )
    ensure_columns(
        db,
        "use_cases",
        {
            "effort_hours": "REAL NOT NULL DEFAULT 0",
            "cost_per_run": "REAL NOT NULL DEFAULT 0",
            "frequency_per_month": "REAL NOT NULL DEFAULT 0",
            "savings_percent": "REAL NOT NULL DEFAULT 0",
            "app_tags": "TEXT NOT NULL DEFAULT ''",
            "pillar_id": "INTEGER REFERENCES pillars(id) ON DELETE SET NULL",
            "effort_unit": "TEXT NOT NULL DEFAULT 'Hours'",
            "cost_provided": "INTEGER NOT NULL DEFAULT 0",
            "output_type": "TEXT NOT NULL DEFAULT ''",
            "tool_url": "TEXT NOT NULL DEFAULT ''",
        },
    )
    db.commit()


def ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ids_arg(name: str) -> list[int]:
    raw = request.form.get(name) or request.json.get(name, "") if request.is_json else request.form.get(name, "")
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).strip()]
    return [int(x) for x in str(raw).split(",") if x.strip()]


def node_path_cte() -> str:
    return """
        WITH node_path AS (
            SELECT
                n.id AS node_id,
                CASE
                    WHEN n.type = 'process' THEN n.id
                    WHEN n.type = 'subprocess' THEN parent.id
                    WHEN n.type = 'task' THEN grandparent.id
                END AS process_id,
                CASE
                    WHEN n.type = 'process' THEN n.name
                    WHEN n.type = 'subprocess' THEN COALESCE(parent.name, '')
                    WHEN n.type = 'task' THEN COALESCE(grandparent.name, '')
                END AS process,
                CASE
                    WHEN n.type = 'subprocess' THEN n.id
                    WHEN n.type = 'task' THEN parent.id
                END AS subprocess_id,
                CASE
                    WHEN n.type = 'subprocess' THEN n.name
                    WHEN n.type = 'task' THEN COALESCE(parent.name, '')
                    ELSE ''
                END AS subprocess,
                CASE WHEN n.type = 'task' THEN n.id END AS task_id,
                CASE WHEN n.type = 'task' THEN n.name ELSE '' END AS task,
                n.type AS attached_level
            FROM nodes n
            LEFT JOIN nodes parent ON n.parent_id = parent.id
            LEFT JOIN nodes grandparent ON parent.parent_id = grandparent.id
        )
    """


def filtered_use_cases() -> list[dict]:
    params = request.args
    where = []
    values: list[str] = []
    if params.get("pillar"):
        where.append(
            "(SELECT name FROM pillars WHERE id = uc.pillar_id) = ? OR (uc.pillar_id IS NULL AND EXISTS (SELECT 1 FROM node_pillars np JOIN pillars p ON p.id = np.pillar_id WHERE np.node_id = uc.node_id AND p.name = ?))"
        )
        where[-1] = "(" + where[-1] + ")"
        values.extend([params["pillar"], params["pillar"]])
    for key, column in [("process", "path.process_id"), ("subprocess", "path.subprocess_id"), ("type", "uc.use_case_type"), ("status", "uc.status"), ("contributor", "uc.contributor")]:
        if params.get(key):
            where.append(f"{column} = ?")
            values.append(params[key])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_db().execute(
        node_path_cte()
        + f"""
        SELECT
            uc.id,
            uc.node_id,
            COALESCE((SELECT name FROM pillars WHERE id = uc.pillar_id), GROUP_CONCAT(p.name, ', ')) AS pillar,
            uc.pillar_id, uc.app_tags, uc.output_type, uc.tool_url, uc.effort_unit,
            (uc.cost_provided OR uc.cost_per_run > 0) AS cost_provided,
            path.process_id,
            path.process,
            path.subprocess_id,
            path.subprocess,
            path.task_id,
            path.task,
            path.attached_level,
            uc.title,
            uc.problem_statement,
            uc.use_case_type,
            uc.short_description,
            uc.status,
            uc.contributor,
            uc.created_date,
            uc.updated_date,
            uc.effort_hours,
            uc.cost_per_run,
            uc.frequency_per_month,
            uc.savings_percent,
            ROUND(uc.effort_hours * uc.frequency_per_month, 6) AS monthly_effort_hours,
            ROUND(uc.effort_hours * uc.frequency_per_month * uc.savings_percent / 100.0, 6) AS monthly_hours_saved,
            ROUND(uc.cost_per_run * uc.frequency_per_month, 2) AS estimated_monthly_cost,
            ROUND(uc.cost_per_run * uc.frequency_per_month * uc.savings_percent / 100.0, 2) AS estimated_monthly_savings
        FROM use_cases uc
        JOIN node_path path ON path.node_id = uc.node_id
        LEFT JOIN node_pillars np ON np.node_id = uc.node_id
        LEFT JOIN pillars p ON p.id = np.pillar_id
        {clause}
        GROUP BY uc.id
        ORDER BY uc.updated_date DESC, uc.id DESC
        """,
        values,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_nodes() -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT n.*, COALESCE(GROUP_CONCAT(p.name, ', '), '') AS pillars,
               COALESCE(GROUP_CONCAT(p.id), '') AS pillar_ids
        FROM nodes n
        LEFT JOIN node_pillars np ON np.node_id = n.id
        LEFT JOIN pillars p ON p.id = np.pillar_id
        GROUP BY n.id
        ORDER BY CASE n.type WHEN 'process' THEN 1 WHEN 'subprocess' THEN 2 ELSE 3 END, n.name
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def find_or_create_node(db: sqlite3.Connection, name: str, node_type: str, parent_id: int | None) -> int:
    row = db.execute(
        "SELECT id FROM nodes WHERE lower(name) = lower(?) AND type = ? AND COALESCE(parent_id, 0) = COALESCE(?, 0)",
        (name, node_type, parent_id),
    ).fetchone()
    if row:
        return row["id"]
    cur = db.execute(
        "INSERT INTO nodes (name, type, parent_id, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (name, node_type, parent_id),
    )
    return cur.lastrowid


def normalize_header(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def worksheet_rows(worksheet) -> list[dict]:
    rows = list(worksheet.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(cell or "").strip() for cell in rows[0]]
    return [dict(zip(headers, row)) for row in rows[1:] if any(cell not in (None, "") for cell in row)]


def row_has(row: dict, aliases: set[str]) -> bool:
    headers = {normalize_header(key) for key in row}
    return bool(headers & aliases)


def parse_upload(file_storage) -> tuple[list[dict], list[dict]]:
    filename = (file_storage.filename or "").lower()
    raw = file_storage.read()
    if filename.endswith(".csv"):
        text = raw.decode("utf-8-sig")
        return list(csv.DictReader(text.splitlines(), restkey="_extra")), []
    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    hierarchy_rows: list[dict] = []
    pillar_rows: list[dict] = []
    for worksheet in workbook.worksheets:
        rows = worksheet_rows(worksheet)
        if not rows:
            continue
        sample = rows[0]
        has_process = row_has(sample, {"process", "processname", "level1"})
        has_subprocess = row_has(sample, {"subprocess", "subprocessname", "subprocesses", "level2"})
        has_pillar = row_has(sample, {"pillar", "pillars", "pillarname", "name"})
        if has_process and has_subprocess:
            hierarchy_rows.extend(rows)
        elif has_pillar:
            pillar_rows.extend(rows)
    return hierarchy_rows, pillar_rows


def value_for(row: dict, aliases: set[str]) -> str:
    normalized = {normalize_header(key): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(alias)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def task_name_for(row: dict) -> str:
    return value_for(row, {"task", "tasks", "taskname", "level3"})


def pillar_names_for(row: dict) -> list[str]:
    raw = value_for(row, {"pillar", "pillars", "pillarname"})
    return [name.strip() for name in raw.replace("\n", ",").split(",") if name.strip()]


def save_pillar(db: sqlite3.Connection, name: str, description: str = "") -> int:
    clean_name = name.strip()
    clean_description = description.strip()
    db.execute("INSERT OR IGNORE INTO pillars (name, description) VALUES (?, ?)", (clean_name, clean_description))
    if clean_description:
        db.execute("UPDATE pillars SET description = ? WHERE name = ?", (clean_description, clean_name))
    return db.execute("SELECT id FROM pillars WHERE name = ?", (clean_name,)).fetchone()["id"]


def import_pillars(db: sqlite3.Connection, rows: list[dict]) -> dict[str, int]:
    imported: dict[str, int] = {}
    for row in rows:
        name = value_for(row, {"pillar", "pillars", "pillarname", "name"})
        if not name:
            continue
        description = value_for(row, {"description", "desc", "details"})
        imported[name] = save_pillar(db, name, description)
    return imported


def hierarchy_payload() -> list[dict]:
    nodes = get_nodes()
    use_cases = filtered_use_cases()
    counts = {
        node["id"]: {"total": 0, "Planned": 0, "WIP": 0, "Built": 0, "Built and Deployed": 0}
        for node in nodes
    }
    for case in use_cases:
        current = case["node_id"]
        while current:
            bucket = counts[current]
            bucket["total"] += 1
            bucket[case["status"]] += 1
            current = next((n["parent_id"] for n in nodes if n["id"] == current), None)
    by_parent: dict[int | None, list[dict]] = {}
    for node in nodes:
        node["counts"] = counts[node["id"]]
        node["children"] = []
        by_parent.setdefault(node["parent_id"], []).append(node)
    for node in nodes:
        node["children"] = by_parent.get(node["id"], [])
    return by_parent.get(None, [])


@app.route("/")
def index():
    init_db()
    asset_version = max(path.stat().st_mtime_ns for path in (BASE_DIR / "static").glob("*.*"))
    return render_template("index.html", statuses=sorted(STATUSES), use_case_types=sorted(USE_CASE_TYPES), asset_version=asset_version)


@app.route("/healthz")
def healthz():
    init_db()
    get_db().execute("SELECT 1").fetchone()
    return jsonify({"ok": True})


@app.route("/api/bootstrap")
def bootstrap():
    init_db()
    use_cases = filtered_use_cases()
    return jsonify(
        {
            "isAdmin": is_admin(),
            "contributors": [r[0] for r in get_db().execute("SELECT DISTINCT contributor FROM use_cases WHERE contributor != '' ORDER BY contributor COLLATE NOCASE")],
            "applications": sorted({tag.strip() for r in get_db().execute("SELECT app_tags FROM use_cases") for tag in r[0].split(',') if tag.strip()}),
            "nodes": get_nodes(),
            "pillars": [row_to_dict(r) for r in get_db().execute("SELECT * FROM pillars ORDER BY name")],
            "useCases": use_cases,
            "hierarchy": hierarchy_payload(),
        }
    )


@app.route("/admin/login", methods=["POST"])
def admin_login():
    configured_id = os.environ.get("FAST_ADMIN_ID", "").strip()
    configured_hash = os.environ.get("FAST_ADMIN_PASSWORD_HASH", "").strip()
    if not configured_id or not configured_hash:
        return jsonify({"error": "Admin login is not configured"}), 503
    data = request.get_json() or {}
    admin_id = str(data.get("admin_id", "")).strip()
    password = str(data.get("password", ""))
    if admin_id != configured_id or not check_password_hash(configured_hash, password):
        return jsonify({"error": "Invalid admin ID or password"}), 401
    session["is_admin"] = True
    session["admin_id"] = admin_id
    return jsonify({"ok": True, "adminId": admin_id})


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_id", None)
    return jsonify({"ok": True})


@app.route("/api/nodes", methods=["POST"])
@admin_required
def create_node():
    init_db()
    data = request.get_json()
    node_type = data.get("type")
    if node_type not in NODE_TYPES:
        return jsonify({"error": "Invalid node type"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO nodes (name, description, type, parent_id, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (data.get("name", "").strip(), data.get("description", "").strip(), node_type, data.get("parent_id") or None),
    )
    node_id = cur.lastrowid
    save_node_pillars(db, node_id, data.get("pillar_ids", []))
    db.commit()
    return jsonify({"id": node_id})


@app.route("/api/hierarchy/import", methods=["POST"])
@admin_required
def import_hierarchy():
    init_db()
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"error": "Upload a CSV or Excel file"}), 400
    hierarchy_rows, pillar_rows = parse_upload(uploaded)
    db = get_db()
    if request.form.get("replace") == "true":
        db.execute("DELETE FROM use_cases")
        db.execute("DELETE FROM nodes")
        db.execute("DELETE FROM pillars")
    imported_pillars = import_pillars(db, pillar_rows)
    touched_processes: set[int] = set()
    touched_subprocesses: set[int] = set()
    task_count = 0
    for row in hierarchy_rows:
        process_name = value_for(row, {"process", "processname", "level1"})
        subprocess_name = value_for(row, {"subprocess", "subprocessname", "subprocesses", "level2"})
        task_name = task_name_for(row)
        if not process_name or not subprocess_name:
            continue
        process_id = find_or_create_node(db, process_name, "process", None)
        subprocess_id = find_or_create_node(db, subprocess_name, "subprocess", process_id)
        pillar_ids = [
            imported_pillars.get(name) or save_pillar(db, name)
            for name in pillar_names_for(row)
        ]
        if pillar_ids:
            save_node_pillars(db, process_id, pillar_ids)
            save_node_pillars(db, subprocess_id, pillar_ids)
        touched_processes.add(process_id)
        touched_subprocesses.add(subprocess_id)
        if task_name:
            task_id = find_or_create_node(db, task_name, "task", subprocess_id)
            if pillar_ids:
                save_node_pillars(db, task_id, pillar_ids)
            task_count += 1
    db.commit()
    return jsonify(
        {
            "ok": True,
            "processes": len(touched_processes),
            "subprocesses": len(touched_subprocesses),
            "tasks": task_count,
            "pillars": len(imported_pillars),
        }
    )


@app.route("/api/nodes/<int:node_id>", methods=["PUT", "DELETE"])
@admin_required
def node_detail(node_id: int):
    init_db()
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        db.commit()
        return jsonify({"ok": True})
    data = request.get_json()
    db.execute(
        "UPDATE nodes SET name = ?, description = ?, parent_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (data.get("name", "").strip(), data.get("description", "").strip(), data.get("parent_id") or None, node_id),
    )
    save_node_pillars(db, node_id, data.get("pillar_ids", []))
    db.commit()
    return jsonify({"ok": True})


def save_node_pillars(db: sqlite3.Connection, node_id: int, pillar_ids: list[int]) -> None:
    db.execute("DELETE FROM node_pillars WHERE node_id = ?", (node_id,))
    valid_ids = [int(pid) for pid in pillar_ids if str(pid).strip()]
    db.executemany("INSERT OR IGNORE INTO node_pillars (node_id, pillar_id) VALUES (?, ?)", [(node_id, pid) for pid in valid_ids])


@app.route("/api/pillars", methods=["POST"])
@admin_required
def create_pillar():
    init_db()
    data = request.get_json()
    cur = get_db().execute("INSERT INTO pillars (name, description) VALUES (?, ?)", (data.get("name", "").strip(), data.get("description", "").strip()))
    get_db().commit()
    return jsonify({"id": cur.lastrowid})


@app.route("/api/pillars/<int:pillar_id>", methods=["PUT", "DELETE"])
@admin_required
def pillar_detail(pillar_id: int):
    init_db()
    db = get_db()
    if request.method == "DELETE":
        db.execute("DELETE FROM pillars WHERE id = ?", (pillar_id,))
    else:
        data = request.get_json()
        db.execute("UPDATE pillars SET name = ?, description = ? WHERE id = ?", (data.get("name", "").strip(), data.get("description", "").strip(), pillar_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/use-cases", methods=["POST"])
def create_use_case():
    init_db()
    try:
        data = validated_use_case(request.get_json() or {})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    case_id = insert_use_case(data)
    get_db().commit()
    return jsonify({"id": case_id})


def validated_use_case(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Use case must be an object")
    result = {}
    for field in ("title", "problem_statement", "short_description", "contributor"):
        result[field] = str(data.get(field) or "").strip()
        if not result[field]:
            raise ValueError(f"{field.replace('_', ' ').title()} is required")
    result["node_id"] = int(data.get("node_id") or 0)
    if not get_db().execute("SELECT 1 FROM nodes WHERE id = ?", (result["node_id"],)).fetchone():
        raise ValueError("Select an existing process, sub-process or task")
    for field, allowed in (("use_case_type", USE_CASE_TYPES), ("status", STATUSES)):
        result[field] = data.get(field)
        if result[field] not in allowed:
            raise ValueError(f"Invalid {field.replace('_', ' ')}")
    unit = data.get("effort_unit") or "Hours"
    if unit not in {"Hours", "Minutes"}:
        raise ValueError("Effort unit must be Hours or Minutes")
    result["effort_unit"] = unit
    for field in ("effort_hours", "cost_per_run", "frequency_per_month", "savings_percent"):
        value = data.get("effort_value") if field == "effort_hours" and "effort_value" in data else data.get(field)
        number = float(value or 0)
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{field.replace('_', ' ').title()} must be a non-negative number")
        if field == "savings_percent" and number > 100:
            raise ValueError("Savings percentage cannot exceed 100")
        result[field] = number / 60 if field == "effort_hours" and "effort_value" in data and unit == "Minutes" else number
    result["cost_provided"] = int(data.get("cost_per_run") not in (None, ""))
    result["pillar_id"] = int(data["pillar_id"]) if data.get("pillar_id") else None
    if result["pillar_id"] and not get_db().execute("SELECT 1 FROM pillars WHERE id = ?", (result["pillar_id"],)).fetchone():
        raise ValueError("Unknown pillar")
    tags = str(data.get("app_tags") or "").replace(";", ",").split(",")
    result["app_tags"] = ", ".join(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    result["output_type"] = str(data.get("output_type") or "").strip()
    if result["output_type"] and result["output_type"] not in OUTPUT_TYPES:
        raise ValueError("Invalid output type")
    result["tool_url"] = str(data.get("tool_url") or "").strip()
    if result["tool_url"]:
        parsed = urlparse(result["tool_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Tool link must be a valid http or https URL")
    for field in ("created_date", "updated_date"):
        raw = data.get(field) or date.today().isoformat()
        result[field] = date.fromisoformat(str(raw)[:10]).isoformat()
    return result


def insert_use_case(data: dict) -> int:
    fields = list(data)
    return get_db().execute(
        f"INSERT INTO use_cases ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        list(data.values()),
    ).lastrowid


@app.route("/api/use-cases/<int:case_id>", methods=["PUT", "DELETE"])
def use_case_detail(case_id: int):
    init_db()
    db = get_db()
    if not db.execute("SELECT 1 FROM use_cases WHERE id = ?", (case_id,)).fetchone():
        return jsonify({"error": "Use case not found"}), 404
    if request.method == "DELETE":
        db.execute("DELETE FROM use_cases WHERE id = ?", (case_id,))
        db.commit()
        return jsonify({"ok": True})
    try:
        data = validated_use_case(request.get_json() or {})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    db.execute(f"UPDATE use_cases SET {', '.join(field + ' = ?' for field in data)} WHERE id = ?", [*data.values(), case_id])
    db.commit()
    return jsonify({"ok": True})


@app.route("/export")
def export_excel():
    init_db()
    rows = filtered_use_cases()
    wb = Workbook()
    ws = wb.active
    ws.title = "Use Cases"
    headers = [
        "Pillar",
        "Process",
        "Sub-process",
        "Task",
        "Use Case Title",
        "Current Problem / Opportunity",
        "Type",
        "Status",
        "Short Description",
        "Contributor",
        "Created Date",
        "Last Updated Date",
        "Attached Level",
        "Current Effort Spent / Run",
        "Equivalent Current Cost / Run",
        "Frequency / Month",
        "Savings From Automation %",
        "Current Monthly Cost",
        "Monthly Savings From Automation",
        "Applications", "Output Type", "Tool URL", "Monthly Hours Saved", "Node ID", "Effort Unit", "Tagged Pillar",
    ]
    ws.append(headers)
    for row in rows:
        ws.append(
            [
                row["pillar"],
                row["process"],
                row["subprocess"],
                row["task"],
                row["title"],
                row["problem_statement"],
                row["use_case_type"],
                row["status"],
                row["short_description"],
                row["contributor"],
                row["created_date"],
                row["updated_date"],
                row["attached_level"],
                row["effort_hours"],
                row["cost_per_run"] if row["cost_provided"] else None,
                row["frequency_per_month"],
                row["savings_percent"],
                row["estimated_monthly_cost"],
                row["estimated_monthly_savings"],
                row["app_tags"], row["output_type"], row["tool_url"], row["monthly_hours_saved"], row["node_id"], "Hours",
                get_db().execute("SELECT name FROM pillars WHERE id = ?", (row["pillar_id"],)).fetchone()[0] if row["pillar_id"] else "",
            ]
        )
    # User-entered text must remain text when opened in Excel.
    for cells in ws.iter_rows():
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="20323A")
    ws.auto_filter.ref = ws.dimensions
    table = Table(displayName="AI_Automation_Use_Cases", ref=ws.dimensions)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True, showColumnStripes=False)
    ws.add_table(table)
    for col in range(1, len(headers) + 1):
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = min(44, max(14, max(len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, ws.max_row + 1)) + 2))
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"AI_Automation_Use_Cases_{date.today().isoformat()}.xlsx"
    return send_file(buffer, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


IMPORT_FIELDS = {
    "Node ID": "node_id", "Use Case Title": "title", "Current Problem / Opportunity": "problem_statement",
    "Short Description": "short_description", "Type": "use_case_type", "Status": "status",
    "Contributor": "contributor", "Effort Per Run": "effort_value", "Effort Unit": "effort_unit",
    "Equivalent Current Cost / Run": "cost_per_run", "Frequency / Month": "frequency_per_month",
    "Savings From Automation %": "savings_percent", "Applications": "app_tags", "Pillar": "pillar",
    "Output Type": "output_type", "Tool URL": "tool_url", "Created Date": "created_date",
    "Last Updated Date": "updated_date", "Current Effort Spent / Run": "effort_hours", "Tagged Pillar": "tagged_pillar",
}


@app.route("/api/use-cases/template")
def use_case_template():
    init_db()
    wb = Workbook()
    ws = wb.active
    ws.title = "Use Cases"
    ws.append([header for header in IMPORT_FIELDS if header not in {"Current Effort Spent / Run", "Tagged Pillar"}])
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="20323A")
        ws.column_dimensions[cell.column_letter].width = 26
    nodes = wb.create_sheet("Hierarchy Reference")
    nodes.append(["Node ID", "Level", "Name", "Parent ID"])
    for node in get_nodes():
        nodes.append([node["id"], node["type"], node["name"], node["parent_id"]])
    pillars = wb.create_sheet("Pillar Reference")
    pillars.append(["Pillar"])
    for row in get_db().execute("SELECT name FROM pillars ORDER BY name"):
        pillars.append([row[0]])
    guide = wb.create_sheet("Instructions")
    for line in [
        "Fill the Use Cases sheet. Upload appends records; existing records are never replaced.",
        "Node ID must match the Hierarchy Reference sheet. Required: title, pain point, description, type, status, contributor.",
        "Type: Automation, AI, Agentic AI. Status: Planned, WIP, Built, Built and Deployed.",
        "Effort Unit: Hours or Minutes. Savings %: 0 to 100. Leave cost blank for hours-only savings.",
        "Applications: comma-separated names. Pillar is optional; use a name from Pillar Reference.",
        "Output Type: " + ", ".join(sorted(OUTPUT_TYPES)),
        "Dates are optional (YYYY-MM-DD). Tool URL must start with http:// or https://.",
    ]:
        guide.append([line])
    guide.column_dimensions["A"].width = 120
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="FAST_Use_Case_Template.xlsx")


@app.route("/api/use-cases/import", methods=["POST"])
def import_use_cases():
    init_db()
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"error": "Select an Excel or CSV file"}), 400
    try:
        if (uploaded.filename or "").lower().endswith(".csv"):
            rows = list(csv.DictReader(uploaded.read().decode("utf-8-sig").splitlines()))
        else:
            wb = load_workbook(uploaded, read_only=True, data_only=True)
            rows = worksheet_rows(wb["Use Cases"] if "Use Cases" in wb.sheetnames else wb.active)
            wb.close()
        if not rows or len(rows) > 5000:
            raise ValueError("Upload between 1 and 5000 use cases")
    except Exception as exc:
        return jsonify({"error": f"Could not read import: {exc}"}), 400
    fields = {normalize_header(header): field for header, field in IMPORT_FIELDS.items()}
    validated, errors = [], []
    for number, row in enumerate(rows, 2):
        try:
            data = {fields[normalize_header(key)]: value for key, value in row.items() if normalize_header(key) in fields}
            # Exports always express the canonical effort column in hours.
            if "effort_hours" in data and "effort_value" not in data:
                data["effort_unit"] = "Hours"
            inherited_pillar = data.pop("pillar", "")
            pillar = str(data.pop("tagged_pillar", inherited_pillar) or "").strip()
            if pillar:
                match = get_db().execute("SELECT id FROM pillars WHERE name = ? COLLATE NOCASE", (pillar,)).fetchone()
                if not match:
                    raise ValueError(f"Unknown pillar: {pillar}")
                data["pillar_id"] = match[0]
            validated.append(validated_use_case(data))
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"Row {number}: {exc}")
    if errors:
        return jsonify({"error": "No rows imported. Correct the listed rows and retry.", "errors": errors}), 400
    if request.form.get("preview") == "true":
        return jsonify({"count": len(validated), "preview": True})
    db = get_db()
    try:
        for data in validated:
            insert_use_case(data)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({"imported": len(validated)})


if __name__ == "__main__":
    with app.app_context():
        init_db()
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug, host="0.0.0.0", port=port)
