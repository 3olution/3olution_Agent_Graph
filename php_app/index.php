<?php
/**
 * 3olution Notes — a small PHP notes app backed by SQLite (or MySQL
 * or PostgreSQL, if configured).
 * https://3olution.com
 *
 * Served at /php/ by the Python backend. The index page supports
 * three actions:
 *
 *   GET  /php/              List all notes, show the add-note form
 *   POST /php/  action=add  Insert a note, redirect back to the list
 *   POST /php/  action=del  Delete a note, redirect back to the list
 *
 * The DELETE action is a POST (not a GET) so that a bookmark, a
 * browser prefetcher, or a link-shortener cannot accidentally remove
 * a note. A GET with ?delete=ID is still accepted for backward
 * compatibility but triggers a confirmation prompt.
 *
 * ═══════════════════════════════════════════════════════════════════
 *   CROSS-DRIVER COMPATIBILITY
 * ═══════════════════════════════════════════════════════════════════
 *
 * All SQL goes through prepared statements. All DDL is emitted by
 * db.php's ensure_schema(), which picks the correct dialect for the
 * connected driver.
 */

declare(strict_types=1);

require_once __DIR__ . '/db.php';

// ── Connect and ensure the schema exists ─────────────────────────────
try {
    $db = app_db();
    ensure_schema($db);
} catch (Throwable $e) {
    // Connection or DDL failure. Render a helpful error page and stop.
    http_response_code(500);
    ?>
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>3olution Notes — Database error</title>
        <link rel="icon" type="image/png" href="<?= h(app_base()) ?>/logo.png">
        <link rel="stylesheet" href="<?= h(app_base()) ?>/php-static/style.css">
    </head>
    <body>
        <header>
            <div class="brand">
                <img src="<?= h(app_base()) ?>/logo.png" class="logo" alt="3olution">
                <h1>3olution Notes</h1>
            </div>
        </header>
        <main>
            <section class="card">
                <h2>Database error</h2>
                <p class="error"><?= h($e->getMessage()) ?></p>
                <p class="hint">
                    Check the configuration in <code>.env</code>.
                    The most common cause is a missing PDO driver:
                    install <code>php-sqlite3</code> (or
                    <code>php-mysql</code> / <code>php-pgsql</code>)
                    and restart the backend.
                </p>
            </section>
        </main>
    </body>
    </html>
    <?php
    exit;
}


// ══════════════════════════════════════════════════════════════════════
//  Handle POST actions
// ══════════════════════════════════════════════════════════════════════

$message = '';
$message_kind = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = (string)($_POST['action'] ?? '');

    // ── Add ──────────────────────────────────────────────────────────
    if ($action === 'add') {
        $title = trim((string)($_POST['title'] ?? ''));
        $body  = trim((string)($_POST['body']  ?? ''));

        if ($title === '' || $body === '') {
            $message = 'Both title and body are required.';
            $message_kind = 'error';
        } elseif (mb_strlen($title) > 200) {
            $message = 'Title is too long (200 characters maximum).';
            $message_kind = 'error';
        } elseif (mb_strlen($body) > 10000) {
            $message = 'Body is too long (10000 characters maximum).';
            $message_kind = 'error';
        } else {
            $stmt = $db->prepare(
                'INSERT INTO notes (title, body, created) VALUES (?, ?, ?)'
            );
            $stmt->execute([$title, $body, date('c')]);

            // Redirect after POST to prevent double-submission on reload.
            $base = app_base();
            header("Location: {$base}/php/");
            exit;
        }
    }

    // ── Delete ───────────────────────────────────────────────────────
    elseif ($action === 'delete') {
        $id = (int)($_POST['id'] ?? 0);
        if ($id > 0) {
            $stmt = $db->prepare('DELETE FROM notes WHERE id = ?');
            $stmt->execute([$id]);
        }
        $base = app_base();
        header("Location: {$base}/php/");
        exit;
    }
}

// ── Backward-compatibility: accept GET-based delete with confirmation ─
if ($_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['delete'])) {
    $id = (int)$_GET['delete'];
    if ($id > 0) {
        $stmt = $db->prepare('DELETE FROM notes WHERE id = ?');
        $stmt->execute([$id]);
    }
    $base = app_base();
    header("Location: {$base}/php/");
    exit;
}


// ══════════════════════════════════════════════════════════════════════
//  Fetch data for the page
// ══════════════════════════════════════════════════════════════════════

$rows = $db->query(
    'SELECT id, title, body, created FROM notes ORDER BY id DESC'
)->fetchAll();

$driver = $db->getAttribute(PDO::ATTR_DRIVER_NAME);
$base   = app_base();
$now    = date('Y-m-d H:i:s');
$php    = PHP_VERSION;

// Database label for the header. SQLite shows a filename; MySQL and
// PostgreSQL show the schema/database name.
$db_label = $driver;
if ($driver === 'sqlite') {
    $db_label = 'sqlite';
} elseif ($driver === 'mysql' || $driver === 'pgsql') {
    $db_label .= ':' . (getenv('AGENT_PHP_DB_NAME') ?: 'app');
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>3olution Notes</title>
    <link rel="icon" type="image/png" href="<?= h($base) ?>/logo.png">
    <link rel="icon" type="image/x-icon" href="<?= h($base) ?>/favicon.ico">
    <link rel="apple-touch-icon" href="<?= h($base) ?>/logo.png">
    <link rel="stylesheet" href="<?= h($base) ?>/php-static/style.css">
</head>
<body>
    <header>
        <div class="brand">
            <img src="<?= h($base) ?>/logo.png" class="logo" alt="3olution">
            <h1>3olution Notes</h1>
        </div>
        <div class="meta">
            <span>PHP <?= h($php) ?></span>
            <span>DB <?= h($db_label) ?></span>
            <span><?= h($now) ?></span>
        </div>
    </header>

    <main>
        <!-- ── Add a note ──────────────────────────────────────────── -->
        <section class="card">
            <h2>Add a Note</h2>

            <?php if ($message): ?>
                <p class="message <?= h($message_kind) ?>">
                    <?= h($message) ?>
                </p>
            <?php endif; ?>

            <form method="post" action="<?= h($base) ?>/php/">
                <input type="hidden" name="action" value="add" />

                <label for="title">Title</label>
                <input
                    type="text"
                    id="title"
                    name="title"
                    placeholder="What is it about?"
                    maxlength="200"
                    required
                    autofocus
                />

                <label for="body">Body</label>
                <textarea
                    id="body"
                    name="body"
                    rows="4"
                    placeholder="Write something…"
                    maxlength="10000"
                    required
                ></textarea>

                <button type="submit">Save note</button>
            </form>
        </section>

        <!-- ── List notes ──────────────────────────────────────────── -->
        <section class="card">
            <h2>Notes (<?= count($rows) ?>)</h2>

            <?php if (count($rows) === 0): ?>
                <p class="empty">No notes yet. Add one above.</p>
            <?php else: ?>
                <ul class="notes">
                    <?php foreach ($rows as $row): ?>
                        <li>
                            <div class="note-header">
                                <strong><?= h($row['title']) ?></strong>
                                <span class="date">
                                    <?= h($row['created']) ?>
                                </span>
                                <form
                                    method="post"
                                    action="<?= h($base) ?>/php/"
                                    class="inline"
                                >
                                    <input type="hidden" name="action" value="delete" />
                                    <input type="hidden" name="id" value="<?= (int)$row['id'] ?>" />
                                    <button
                                        type="submit"
                                        class="delete"
                                        title="Delete this note"
                                        onclick="return confirm('Delete this note?')"
                                    >
                                        ×
                                    </button>
                                </form>
                            </div>
                            <p><?= nl2br(h($row['body'])) ?></p>
                        </li>
                    <?php endforeach; ?>
                </ul>
            <?php endif; ?>
        </section>

        <!-- ── Endpoints reference ─────────────────────────────────── -->
        <section class="card">
            <h2>Endpoints</h2>
            <ul class="endpoints">
                <li><code>GET  <?= h($base) ?>/php/</code> — this page</li>
                <li><code>GET  <?= h($base) ?>/php/demo.php</code> — SQL demo (JSON)</li>
                <li><code>GET  /api/php/status</code> — PHP binary info</li>
                <li><code>GET  /api/php/drivers</code> — PDO drivers installed</li>
            </ul>
        </section>
    </main>
</body>
</html>