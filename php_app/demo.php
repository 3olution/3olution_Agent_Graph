<?php
/**
 * SQL demo endpoint — returns JSON describing the database connection
 * and a small query result.
 * https://3olution.com
 *
 * Endpoint:
 *     GET /php/demo.php
 *
 * Response shape:
 *     {
 *       "ok": true,
 *       "driver": "sqlite",
 *       "version": "3.46.0",
 *       "note_count": 4,
 *       "tables": ["notes"],
 *       "php": "8.4.15",
 *       "server": "3olution-Python-PHP",
 *       "time": "2025-01-15T12:34:56+00:00"
 *     }
 *
 * On error, returns HTTP 500 with:
 *     {"ok": false, "error": "..."}
 *
 * ═══════════════════════════════════════════════════════════════════
 *   CROSS-DRIVER NOTES
 * ═══════════════════════════════════════════════════════════════════
 *
 * The database version is fetched with a driver-specific query:
 *
 *     SQLite:     SELECT sqlite_version()
 *     MySQL:      SELECT VERSION()
 *     PostgreSQL: SELECT version()
 *
 * PostgreSQL has a SHOW server_version command whose result column is
 * named differently across releases. Using SELECT version() instead
 * returns a single, consistently-named `version` column on every
 * PostgreSQL build, so no special-casing is needed.
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

require_once __DIR__ . '/db.php';

try {
    $db = app_db();
    ensure_schema($db);

    $driver = (string)$db->getAttribute(PDO::ATTR_DRIVER_NAME);

    // ── Database version ─────────────────────────────────────────────
    $version = 'unknown';
    switch ($driver) {
        case 'sqlite':
            $row = $db->query('SELECT sqlite_version() AS v')->fetch();
            $version = $row['v'] ?? 'unknown';
            break;

        case 'mysql':
            $row = $db->query('SELECT VERSION() AS v')->fetch();
            $version = $row['v'] ?? 'unknown';
            break;

        case 'pgsql':
            $row = $db->query('SELECT version() AS v')->fetch();
            // PostgreSQL's version() returns a long descriptive string
            // like "PostgreSQL 15.3 on x86_64-pc-linux-gnu, ..." —
            // extract just the version number portion.
            $full = $row['v'] ?? 'unknown';
            if (preg_match('/PostgreSQL\s+([0-9.]+)/', $full, $m)) {
                $version = $m[1];
            } else {
                $version = $full;
            }
            break;
    }

    // ── Note count ───────────────────────────────────────────────────
    $note_count = 0;
    try {
        $row = $db->query('SELECT COUNT(*) AS c FROM notes')->fetch();
        $note_count = (int)($row['c'] ?? 0);
    } catch (Throwable $e) {
        // Table does not exist yet — treat as zero notes.
        $note_count = 0;
    }

    // ── Table listing (driver-specific) ──────────────────────────────
    $tables = [];
    try {
        if ($driver === 'sqlite') {
            $stmt = $db->query(
                "SELECT name FROM sqlite_master
                 WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                 ORDER BY name"
            );
        } elseif ($driver === 'mysql') {
            $stmt = $db->query('SHOW TABLES');
        } elseif ($driver === 'pgsql') {
            $stmt = $db->query(
                "SELECT tablename FROM pg_tables
                 WHERE schemaname = 'public'
                 ORDER BY tablename"
            );
        } else {
            $stmt = false;
        }

        if ($stmt !== false) {
            foreach ($stmt->fetchAll(PDO::FETCH_NUM) as $r) {
                $tables[] = (string)$r[0];
            }
        }
    } catch (Throwable $e) {
        // Table listing is cosmetic — ignore failures.
        $tables = [];
    }

    // ── Response ─────────────────────────────────────────────────────
    echo json_encode(
        [
            'ok'         => true,
            'driver'     => $driver,
            'version'    => $version,
            'note_count' => $note_count,
            'tables'     => $tables,
            'php'        => PHP_VERSION,
            'server'     => $_SERVER['SERVER_SOFTWARE'] ?? 'unknown',
            'time'       => date('c'),
        ],
        JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
    );

} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(
        [
            'ok'    => false,
            'error' => $e->getMessage(),
        ],
        JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES
    );
}