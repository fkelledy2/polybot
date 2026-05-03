# web/app.py
# ─────────────────────────────────────────────────────────────
# Flask web dashboard for Polybot.
# Runs on http://localhost:8080 in a background thread.
# ─────────────────────────────────────────────────────────────

import json
import logging
import os
import threading
import time
from collections import deque
from functools import wraps

from flask import Flask, Response, jsonify, render_template, request, session, redirect, url_for

import db
from config import STARTING_BALANCE
from web.costs import get_all_costs_summary
from notifications import alert_deployment

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── Authentication ────────────────────────────────────────────
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "change-me-in-production")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "authenticated" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# ── Shared state (updated each scan by main.py) ───────────────
shared_state: dict = {
    "scan_count":      0,
    "last_scan":       "—",
    "is_halted":       False,
    "balance":         STARTING_BALANCE,
    "portfolio_value": STARTING_BALANCE,
    "model":           "—",
    "edges_found":     0,
    "wallets_tracked": 0,
}

# ── Signal buffer (all signals from last scan) ────────────────
recent_signals: list = []          # Updated atomically by main.py
recent_markets: list = []          # Raw market list from last scan
_state_lock = threading.Lock()

# ── In-memory log buffer ──────────────────────────────────────
_log_buffer: deque = deque(maxlen=500)
_log_lock   = threading.Lock()
_log_counter = 0


class WebLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _log_counter
        try:
            msg = self.format(record)
            with _log_lock:
                _log_counter += 1
                _log_buffer.append({
                    "id":     _log_counter,
                    "level":  record.levelname,
                    "logger": record.name,
                    "msg":    msg,
                })
        except Exception:
            pass


def install_log_handler() -> None:
    handler = WebLogHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                          datefmt="%H:%M:%S")
    )
    logging.getLogger().addHandler(handler)


def update_signals(all_signals: list, markets: list, wallets_tracked: int) -> None:
    """Called by main.py each scan to push latest signal data."""
    global recent_signals, recent_markets
    with _state_lock:
        recent_signals = [_signal_to_dict(s, markets) for s in all_signals]
        recent_markets = markets[:]
        shared_state["edges_found"]     = sum(1 for s in all_signals if s.should_trade)
        shared_state["wallets_tracked"] = wallets_tracked


def _signal_to_dict(signal, markets: list) -> dict:
    """Serialize a TradeSignal dataclass to a JSON-safe dict."""
    # Find volume from the market list
    volume = 0.0
    for m in markets:
        if m.get("market_id") == signal.market_id:
            volume = m.get("volume_usd", 0.0)
            break
    return {
        "market_id":             signal.market_id,
        "question":              signal.question,
        "market_yes_price":      round(signal.market_yes_price, 4),
        "claude_yes_probability": round(signal.claude_yes_probability, 4),
        "edge":                  round(signal.edge, 4),
        "direction":             signal.direction,
        "confidence":            signal.confidence,
        "reasoning":             signal.reasoning,
        "wallet_alignment":      signal.wallet_alignment,
        "should_trade":          signal.should_trade,
        "volume_usd":            volume,
        "days_to_resolve":       next(
            (m.get("days_to_resolve") for m in markets if m.get("market_id") == signal.market_id),
            None
        ),
    }


# ── Database helpers ──────────────────────────────────────────

def _db():
    return db.get_connection()


def _get_stats() -> dict:
    try:
        conn = _db()
        c = db.get_cursor(conn)
        c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS s FROM trades WHERE status='won'")
        r = c.fetchone(); won_count, won_pnl = r["n"], r["s"]
        c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS s FROM trades WHERE status='lost'")
        r = c.fetchone(); lost_count, lost_pnl = r["n"], r["s"]
        c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size_usd),0) AS s FROM trades WHERE status='open'")
        r = c.fetchone(); open_count, open_cost = r["n"], r["s"]
        c.execute("SELECT balance FROM balance_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        balance = row["balance"] if row else STARTING_BALANCE
        conn.close()
    except Exception:
        won_count = won_pnl = lost_count = lost_pnl = open_count = open_cost = 0
        balance = STARTING_BALANCE

    won_count  = int(won_count  or 0)
    lost_count = int(lost_count or 0)
    won_pnl    = float(won_pnl   or 0)
    lost_pnl   = float(lost_pnl  or 0)
    open_cost  = float(open_cost or 0)

    total_closed = won_count + lost_count
    win_rate     = won_count / total_closed if total_closed > 0 else 0
    total_pnl    = won_pnl + lost_pnl
    portfolio    = float(balance) + open_cost

    return {
        "balance":          round(float(balance), 2),
        "portfolio_value":  round(portfolio, 2),
        "starting_balance": STARTING_BALANCE,
        "total_pnl":        round(total_pnl, 2),
        "total_pnl_pct":    round(total_pnl / STARTING_BALANCE * 100, 2),
        "win_rate":         round(win_rate * 100, 1),
        "won_count":        won_count,
        "lost_count":       lost_count,
        "open_count":       int(open_count or 0),
        **shared_state,
    }


# ── Routes ────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == DASHBOARD_USERNAME and password == DASHBOARD_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/mobile")
@login_required
def mobile():
    return render_template("mobile.html")


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(_get_stats())


@app.route("/api/signals")
@login_required
def api_signals():
    with _state_lock:
        data = recent_signals[:]
    # Sort by abs edge descending
    data.sort(key=lambda s: abs(s["edge"]), reverse=True)
    return jsonify(data)


@app.route("/api/markets")
@login_required
def api_markets():
    with _state_lock:
        data = recent_markets[:]
    return jsonify(data)


@app.route("/api/trades")
@login_required
def api_trades():
    try:
        conn = _db()
        c = db.get_cursor(conn)
        c.execute("""
            SELECT id, market_id, question, direction,
                   entry_price, size_usd, shares,
                   timestamp, status, exit_price, pnl
            FROM trades ORDER BY id DESC LIMIT 100
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception:
        rows = []
    return jsonify(rows)


@app.route("/api/positions")
@login_required
def api_positions():
    try:
        conn = _db()
        c = db.get_cursor(conn)
        c.execute("""
            SELECT id, market_id, question, direction,
                   entry_price, size_usd, shares, timestamp
            FROM trades WHERE status = 'open'
            ORDER BY timestamp DESC
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception:
        rows = []
    return jsonify(rows)


@app.route("/api/pnl-history")
@login_required
def api_pnl_history():
    try:
        conn = _db()
        c = db.get_cursor(conn)
        # Fetch all balance history — chart will display all available data
        # This gives a complete view of portfolio changes over time
        c.execute("SELECT timestamp, balance FROM balance_log ORDER BY id")
        rows = [{"t": r["timestamp"], "b": round(r["balance"], 2)} for r in c.fetchall()]
        conn.close()
    except Exception:
        rows = []
    return jsonify(rows)


@app.route("/api/logs/stream")
@login_required
def log_stream():
    def generate():
        with _log_lock:
            snapshot = list(_log_buffer)
        last_id = 0
        for entry in snapshot:
            last_id = entry["id"]
            yield f"id: {last_id}\ndata: {json.dumps(entry)}\n\n"
        while True:
            with _log_lock:
                new_entries = [e for e in _log_buffer if e["id"] > last_id]
            for entry in new_entries:
                last_id = entry["id"]
                yield f"id: {last_id}\ndata: {json.dumps(entry)}\n\n"
            time.sleep(0.4)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/trade-timeline")
@login_required
def api_trade_timeline():
    try:
        conn = _db()
        c = db.get_cursor(conn)
        c.execute("""
            SELECT id, question, direction, entry_price, size_usd,
                   timestamp, closed_at, status, pnl
            FROM trades ORDER BY timestamp ASC
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception:
        rows = []
    return jsonify(rows)


@app.route("/api/backtest/latest")
@login_required
def api_backtest_latest():
    """Return the most recent historical backtest run."""
    try:
        conn = _db()
        c = db.get_cursor(conn)
        c.execute("""
            SELECT id, run_at, markets_n, directional_accuracy,
                   best_threshold, best_ev, summary_json
            FROM backtest_runs ORDER BY id DESC LIMIT 1
        """)
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify(None)
        d = dict(row)
        d["summary"] = json.loads(d.pop("summary_json") or "{}")
        return jsonify(d)
    except Exception:
        return jsonify(None)


@app.route("/api/backtest/tracker")
@login_required
def api_backtest_tracker():
    """Return forward-tracker stats and recent predictions."""
    try:
        from backtest.tracker import get_tracker_stats, get_recent_predictions
        return jsonify({
            "stats":       get_tracker_stats(),
            "predictions": get_recent_predictions(50),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/costs")
@login_required
def api_costs():
    """Return SaaS service costs and usage."""
    return jsonify(get_all_costs_summary())


@app.route("/.claude/analysis_report.json")
@login_required
def api_analysis_report():
    """Return the latest trading system analysis report."""
    try:
        report_path = os.path.join(os.getcwd(), ".claude", "analysis_report.json")
        if os.path.exists(report_path):
            with open(report_path, "r") as f:
                return jsonify(json.load(f))
        else:
            return jsonify({"error": "No analysis report available"}), 404
    except Exception as e:
        logger.error(f"Error loading analysis report: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/webhooks/deploy", methods=["POST"])
def webhook_deploy():
    """Webhook endpoint for deployment notifications (GitHub/Heroku)."""
    try:
        data = request.get_json() or {}

        # GitHub push event
        if "repository" in data and "ref" in data:
            commits = data.get("commits", [])
            if commits:
                latest = commits[-1]
                commit_sha = latest.get("id", "unknown")
                message = latest.get("message", "")
                alert_deployment(commit_sha, message)

        # Heroku release webhook
        elif "release" in data:
            release = data["release"]
            alert_deployment(
                release.get("commit", "unknown"),
                release.get("description", "Heroku deployment")
            )

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"error": str(e)}), 400


def run_server(host: str = "0.0.0.0", port: int = None) -> None:
    port = port or int(os.environ.get("PORT", 8080))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
