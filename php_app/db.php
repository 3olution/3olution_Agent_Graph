<?php
/**
 * SQL helper for the 3olution PHP app.
 * https://3olution.com
 *
 * Returns a PDO connection configured from environment variables
 * that the Python backend passes through the CGI environment.
 *
 * ═══════════════════════════════════════════════════════════════════
 *   SUPPORTED DRIVERS
 * ═══════════════════════════════════════════════════════════════════
 *
 *   sqlite   Zero-config. The database file is created on first use.
 *   mysql    Requires pdo_mysql extension and a running MySQL server.
 *   pgsql    Requires pdo_pgsql extension and a running PostgreSQL.
 *
 * The driver is chosen by AGENT_PHP_DB_DRIVER. When it's 'sqlite', the
 * file lives at AGENT_PHP_SQLITE_PATH relative to php_app/. When it's
 * mysql or pgsql, the connection parameters come from AGENT_PHP_DB_*.
 *
 * ═══════════════════════════════════════════════════════════════════
 *   CROSS-DRIVER SCHEMA
 * ═══════════════════════════════════════════════════════════════════
 *
 * The `notes` table is defined per-driver in ensure_schema(). SQLite,
 * MySQL, and PostgreSQL each have their own dialect for auto-increment
 * primary keys:
 *
 *     SQLite:       INTEGER PRIMARY KEY AUTOINCREMENT
 *     MySQL:        INT AUTO_INCREMENT PRIMARY KEY
 *     PostgreSQL:   SERIAL PRIMARY KEY
 *
 * Trying to run one dialect's CREATE TABLE against a different driver
 * raises a syntax error. The ensure_schema() function switches on the
 * connected driver and emits the correct DDL.
 *
 * ═══════════════════════════════════════════════════════════════════
 *   TIMESTAMPS
 * ═══════════════════════════════════════════════════════════════════
 *
 * Timestamps are stored as ISO 8601 strings ('2025-01-15T12:34:56+00:00')
 * rather than native DATETIME types. This is deliberate:
 *
 *   - ISO 8601 sorts correctly as a string in every driver.
 *   - No timezone conversion is performed on read.
 *   - The format is portable and human-readable.
 *
 * The trade-off is a small storage overhead and the loss of native
 * date-arithmetic functions, neither of which matter for a notes app.
 */

declare(strict_types=1);


// ══════════════════════════════════════════════════════════════════════
//  Connection
// ══════════════════════════════════════════════════════════════════════

/**
 * Return a singleton PDO connection to the configured database.
 *
 * The connection is created on first call and cached in a static
 * variable for the lifetime of the request. Each PHP-CGI request is a
 * fresh process, so "the lifetime of the request" is effectively the
 * lifetime of this function's execution.
 *
 * The returned PDO has three important attributes set:
 *
 *   ERRMODE_EXCEPTION      Errors raise PDOException rather than
 *                          returning false. This makes error handling
 *                          uniform and forces callers to think about
 *                          failure.
 *
 *   DEFAULT_FETCH_MODE     Every fetch returns an associative array
 *                          (column name → value) instead of the default
 *                          numeric-indexed array.
 *
 *   EMULATE_PREPARES=false MySQL only — uses real server-side prepared
 *                          statements, which is both safer (correct
 *                          quoting even in edge cases) and faster for
 *                          repeated queries.
 *
 * @throws RuntimeException if the driver is unavailable or the
 *         connection fails.
 */
function app_db(): PDO
{
    static $pdo = null;
    if ($pdo !== null) {
        return $pdo;
    }

    $driver = strtolower(trim((string)(getenv('AGENT_PHP_DB_DRIVER') ?: 'sqlite')));

    // ── Preflight: check the driver is actually installed ────────────
    //
    // Some distributions ship PHP with no PDO drivers by default. The
    // user installs `php-cli` but not `php-sqlite3`, then wonders why
    // nothing works. This check produces a clear, actionable message
    // instead of "could not find driver".
    $installed = PDO::getAvailableDrivers();
    if (!in_array($driver, $installed, true)) {
        $install_hints = [
            'sqlite' => 'On Debian/Ubuntu: sudo apt install php-sqlite3
                         On Fedora: sudo dnf install php-pdo
                         On macOS: brew reinstall php',
            'mysql'  => 'On Debian/Ubuntu: sudo apt install php-mysql
                         On Fedora: sudo dnf install php-mysqlnd',
            'pgsql'  => 'On Debian/Ubuntu: sudo apt install php-pgsql
                         On Fedora: sudo dnf install php-pgsql',
        ];
        $hint = $install_hints[$driver]
            ?? "Install the pdo_$driver extension for PHP.";
        throw new RuntimeException(
            "PDO driver '$driver' is not installed. "
            . "Available drivers: " . (empty($installed) ? '(none)' : implode(', ', $installed))
            . ". " . $hint
        );
    }

    // ── Dispatch to the correct DSN builder ──────────────────────────
    switch ($driver) {

        // ── MySQL ────────────────────────────────────────────────────
        case 'mysql':
            $host = getenv('AGENT_PHP_DB_HOST')     ?: '127.0.0.1';
            $port = getenv('AGENT_PHP_DB_PORT')     ?: '3306';
            $name = getenv('AGENT_PHP_DB_NAME')     ?: 'app';
            $user = getenv('AGENT_PHP_DB_USER')     ?: 'app';
            $pass = getenv('AGENT_PHP_DB_PASSWORD') ?: '';

            $dsn = "mysql:host={$host};port={$port};dbname={$name};charset=utf8mb4";
            $pdo = new PDO($dsn, $user, $pass, [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_EMULATE_PREPARES   => false,
            ]);
            break;

        // ── PostgreSQL ───────────────────────────────────────────────
        case 'pgsql':
        case 'postgres':
            $host = getenv('AGENT_PHP_DB_HOST')     ?: '127.0.0.1';
            $port = getenv('AGENT_PHP_DB_PORT')     ?: '5432';
            $name = getenv('AGENT_PHP_DB_NAME')     ?: 'app';
            $user = getenv('AGENT_PHP_DB_USER')     ?: 'app';
            $pass = getenv('AGENT_PHP_DB_PASSWORD') ?: '';

            $dsn = "pgsql:host={$host};port={$port};dbname={$name}";
            $pdo = new PDO($dsn, $user, $pass, [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            ]);
            break;

        // ── SQLite (default) ─────────────────────────────────────────
        case 'sqlite':
        default:
            $rel = (string)(getenv('AGENT_PHP_SQLITE_PATH') ?: 'data/app.db');

            // Absolute paths pass through. Relative paths resolve
            // against php_app/.
            $is_absolute =
                substr($rel, 0, 1) === '/'
                || (strlen($rel) > 1
                    && ctype_alpha($rel[0])
                    && $rel[1] === ':')
                || substr($rel, 0, 2) === '\\\\';

            if ($is_absolute) {
                $path = $rel;
            } else {
                $path = __DIR__ . DIRECTORY_SEPARATOR . ltrim($rel, '/\\');
            }

            $dir = dirname($path);
            if (!is_dir($dir)) {
                // 0755 is owner-writable, group/other-readable.
                // Do NOT use 0777 — that is world-writable and a
                // needless security exposure on shared hosts.
                mkdir($dir, 0755, true);
            }

            $pdo = new PDO("sqlite:{$path}", null, null, [
                PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            ]);
            // Enable foreign key enforcement (off by default in SQLite).
            $pdo->exec('PRAGMA foreign_keys = ON');
            // Use WAL mode for better concurrent read performance.
            // WAL persists across connections because it is a database
            // property, not a per-connection setting.
            $pdo->exec('PRAGMA journal_mode = WAL');
            break;
    }

    return $pdo;
}


// ══════════════════════════════════════════════════════════════════════
//  Schema
// ══════════════════════════════════════════════════════════════════════

/**
 * Create the `notes` table if it does not exist.
 *
 * Emits driver-specific DDL because the three supported databases
 * disagree on how auto-increment primary keys are declared:
 *
 *     SQLite:     INTEGER PRIMARY KEY AUTOINCREMENT
 *     MySQL:      INT AUTO_INCREMENT PRIMARY KEY
 *     PostgreSQL: SERIAL PRIMARY KEY
 *
 * Safe to call on every request. CREATE TABLE IF NOT EXISTS is a no-op
 * when the table already exists.
 *
 * @param PDO $db The database connection.
 * @return void
 * @throws PDOException on DDL failure.
 */
function ensure_schema(PDO $db): void
{
    $driver = $db->getAttribute(PDO::ATTR_DRIVER_NAME);

    switch ($driver) {
        case 'mysql':
            $sql = "
                CREATE TABLE IF NOT EXISTS notes (
                    id      INT AUTO_INCREMENT PRIMARY KEY,
                    title   VARCHAR(255) NOT NULL,
                    body    TEXT NOT NULL,
                    created VARCHAR(40) NOT NULL,
                    INDEX idx_notes_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            ";
            break;

        case 'pgsql':
            $sql = "
                CREATE TABLE IF NOT EXISTS notes (
                    id      SERIAL PRIMARY KEY,
                    title   VARCHAR(255) NOT NULL,
                    body    TEXT NOT NULL,
                    created VARCHAR(40) NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notes_created ON notes (created)
            ";
            break;

        case 'sqlite':
        default:
            $sql = "
                CREATE TABLE IF NOT EXISTS notes (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title   TEXT NOT NULL,
                    body    TEXT NOT NULL,
                    created TEXT NOT NULL
                )
            ";
            break;
    }

    $db->exec($sql);
}


// ══════════════════════════════════════════════════════════════════════
//  Utility
// ══════════════════════════════════════════════════════════════════════

/**
 * HTML-escape a string for safe output.
 *
 * Convenience wrapper around htmlspecialchars() with the flags most
 * commonly needed: ENT_QUOTES (encode both double and single quotes)
 * and ENT_SUBSTITUTE (replace invalid UTF-8 sequences with U+FFFD
 * rather than returning an empty string).
 */
function h(?string $s): string
{
    return htmlspecialchars(
        (string)($s ?? ''),
        ENT_QUOTES | ENT_SUBSTITUTE,
        'UTF-8'
    );
}


/**
 * Return the URL prefix the app is mounted at.
 *
 * The Python backend injects X-Forwarded-Prefix: /php into the CGI
 * environment. PHP sees it as $_SERVER['HTTP_X_FORWARDED_PREFIX'].
 * Relative links and form actions prepend this prefix so they work
 * whether the app is mounted at /php/ or at the root.
 */
function app_base(): string
{
    $prefix = $_SERVER['HTTP_X_FORWARDED_PREFIX'] ?? '';
    if (!is_string($prefix)) {
        return '';
    }
    // Strip a trailing slash so concatenating "/path" always works.
    return rtrim($prefix, '/');
}