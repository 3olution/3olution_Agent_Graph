Extensive Documentation
The PHP notes app in one diagram
text
Browser
   │
   │  GET /php/
   ▼
FastAPI backend  (backend/routes/php_routes.py)
   │
   │  1. resolve path safely under php_app/
   │  2. build CGI environment (REQUEST_METHOD, QUERY_STRING, …)
   │  3. spawn php-cgi with the script as SCRIPT_FILENAME
   │  4. pass the request body on stdin
   │  5. capture stdout + stderr
   ▼
php-cgi subprocess
   │
   │  reads SCRIPT_FILENAME, executes the PHP file
   │  includes db.php via require_once
   │  db.php opens a PDO connection to:
   │      - SQLite at php_app/data/app.db  (default)
   │      - MySQL / PostgreSQL if configured
   ▼
app_db() → PDO connection
   │
   │  ensure_schema() emits driver-specific CREATE TABLE
   │  the page reads/inserts/deletes rows
   ▼
HTML response back up the chain
Cross-driver support — what actually works
The original db.php claimed MySQL and PostgreSQL support but the
index.php schema used SQLite-only AUTOINCREMENT syntax. That is
now fixed: ensure_schema() in db.php picks the correct DDL per
driver.

Feature	SQLite	MySQL	PostgreSQL
Auto-increment PK	INTEGER PRIMARY KEY AUTOINCREMENT	INT AUTO_INCREMENT PRIMARY KEY	SERIAL PRIMARY KEY
Version query	SELECT sqlite_version()	SELECT VERSION()	SELECT version()
Table listing	sqlite_master query	SHOW TABLES	pg_tables query
WAL mode	PRAGMA journal_mode = WAL	n/a	n/a
Foreign keys	PRAGMA foreign_keys = ON	on by default (InnoDB)	on by default
The /php-static route
This was one of the issues identified in the earlier review:
index.php references /php-static/style.css but no such route
existed in php_routes.py, so the CSS was a 404 on every platform.

The fix has two parts:

backend/routes/php_routes.py now registers a route:

python
@router.get("/php-static/{asset:path}", include_in_schema=False)
async def php_static(asset: str):
    ...
The CSS file now lives at php_app/static/style.css — the
directory the route serves from.

backend/main.py's _ensure_php_dirs() creates php_app/static/
automatically on startup, so the folder is always present even on a
fresh clone.

The old php_app/style.css is kept as a stub with a comment pointing
at the new location. This is defensive: any cached browser reference
to /php/style.css gets a harmless stub instead of a 404.

The POST-not-GET delete
The delete action is a POST form:

html
<form method="post" action="/php/">
    <input type="hidden" name="action" value="delete" />
    <input type="hidden" name="id" value="42" />
    <button type="submit" ...>×</button>
</form>
The original code used <a href="/php/?delete=42">, which:

Can be triggered by a browser prefetcher or a link scanner

Can be bookmarked, causing a delete on revisit

Can be embedded in a malicious image URL on another site

A POST is not automatically triggered by any of those. The current
implementation still accepts GET ?delete=ID for backward
compatibility, but the primary path is a POST.

Data directory permissions
The db.php mkdir call now uses 0755, not the original 0777:

php
mkdir($dir, 0755, true);
0755 means:

Owner: read, write, execute

Group: read, execute

Other: read, execute

0777 would add write permission for group and other, letting any
user on a shared host drop a malicious file into the data directory.
The change is a no-op on Windows (permissions are ignored there) and
a security improvement on Unix.

SQLite WAL mode
The SQLite connection now enables Write-Ahead Logging:

php
$pdo->exec('PRAGMA journal_mode = WAL');
WAL mode changes SQLite's journaling behaviour in a way that:

Allows concurrent readers while a writer is active

Reduces the file-sync cost of each commit

Creates two sidecar files alongside the database:

app.db-wal — the write-ahead log

app.db-shm — the shared-memory index

The sidecar files are cleaned up automatically when the last
connection closes. They are listed in .gitignore alongside *.db.

The 0755 mkdir and file creation
The SQLite database file itself is created by PDO when the first
connection is established. Its permissions depend on the process
umask:

Umask	Effective file mode
022 (typical)	0644 — owner rw, others r
002 (group-writable)	0664 — owner rw, group rw
077 (private)	0600 — owner rw only
For the default single-user scenario, 0644 is fine. On a
multi-user host where other users should not read the notes, set the
umask or use a restricted user for the PHP-CGI subprocess.

The driver preflight check
db.php now checks PDO::getAvailableDrivers() before attempting a
connection:

php
$installed = PDO::getAvailableDrivers();
if (!in_array($driver, $installed, true)) {
    throw new RuntimeException(
        "PDO driver '$driver' is not installed. ..."
    );
}
This produces an actionable error message instead of a generic
"could not find driver". The message includes the platform-specific
install command, so a user on Debian sees:

text
PDO driver 'sqlite' is not installed. Available drivers: mysql, pgsql.
On Debian/Ubuntu: sudo apt install php-sqlite3
The /api/php/drivers endpoint in backend/routes/php_routes.py
exposes the same information programmatically.

Common configurations
Single-user SQLite (default)

env
AGENT_PHP_ENABLED=true
AGENT_PHP_DB_DRIVER=sqlite
AGENT_PHP_SQLITE_PATH=data/app.db
The database file is created at php_app/data/app.db on first
request. Nothing else to configure.

Local MySQL

env
AGENT_PHP_ENABLED=true
AGENT_PHP_DB_DRIVER=mysql
AGENT_PHP_DB_HOST=127.0.0.1
AGENT_PHP_DB_PORT=3306
AGENT_PHP_DB_NAME=notes
AGENT_PHP_DB_USER=notes_user
AGENT_PHP_DB_PASSWORD=secret
The database and user must exist before the first request. The
notes table is created automatically.

PostgreSQL on another host

env
AGENT_PHP_ENABLED=true
AGENT_PHP_DB_DRIVER=pgsql
AGENT_PHP_DB_HOST=db.example.com
AGENT_PHP_DB_PORT=5432
AGENT_PHP_DB_NAME=notes
AGENT_PHP_DB_USER=notes_user
AGENT_PHP_DB_PASSWORD=secret
Requires the pdo_pgsql extension and network access from the
backend host to the database server.

Testing the PHP app
With the backend running, verify each part step by step:

bash
# 1. Check PHP is available
curl http://127.0.0.1:8000/api/php/status

# 2. Check the driver is available
curl http://127.0.0.1:8000/api/php/drivers

# 3. Load the notes page
curl http://127.0.0.1:8000/php/

# 4. Add a note (form-encoded POST)
curl -X POST http://127.0.0.1:8000/php/ \
    -d 'action=add' \
    -d 'title=Test note' \
    -d 'body=Created via curl'

# 5. Verify the note was saved
curl http://127.0.0.1:8000/php/ | grep "Test note"

# 6. Check the JSON demo endpoint
curl http://127.0.0.1:8000/php/demo.php

# 7. Inspect the SQLite database directly
runtime/sqlite/sqlite3 php_app/data/app.db "SELECT * FROM notes;"
Cleanup and reset
To reset the notes app to a blank state:

bash
# SQLite
rm php_app/data/app.db php_app/data/app.db-wal php_app/data/app.db-shm

# MySQL / PostgreSQL
# Drop and recreate the database, or just delete the notes table.
The next request to /php/ recreates the schema automatically.

Models folder — how the backend uses it
backend/models_setup.py performs three operations on this folder:

ensure_models_dir() — called during the FastAPI lifespan and
by the vendor download step. Creates the folder if missing and
writes the README you see above on first creation.

list_gguf_files() — called by /api/llama/runtime/models
and /api/llama/all-models. Globs *.gguf at the top level of
the folder, returning name, path, size, and size_gb for each file.

resolve_model_path(name) — called by the llama-cpp manager
when starting or swapping models. Checks:

Is name an absolute path? Use it directly.

Is models/name a file? Use that.

Is <project_root>/name a file? Use that.

Otherwise return None and produce a clear error.

The folder is not recursed. If you have subfolders, reference the
relative path explicitly:

env
AGENT_LLAMA_CPP_MODEL=chat/llama-3.2-3b.Q4_K_M.gguf
The .gitkeep convention
Empty directories do not survive in git — git tracks files, not
directories. The .gitkeep files in logs/ and php_app/data/
exist only so those folders appear in a fresh clone.

.gitignore uses the inverse pattern to keep only the placeholder:

gitignore
logs/
!logs/.gitkeep

models/
!models/README.md

php_app/data/
!php_app/data/.gitkeep
Note that models/ whitelists README.md rather than a .gitkeep,
because the README serves double duty as both the placeholder and
useful documentation.