#!/usr/bin/env python3
"""
app.py
=======================================================================
Local web interface for vibecodedCurveSim.py. Upload a decklist .txt
file in your browser, set the same options the CLI's flags expose,
and view the full statistics summary as a real web page instead of a
monospace table -- plus download the raw CSV or the plain-text summary
if you want them.

This is a THIN WRAPPER, not a reimplementation: every number on the
results page comes from vibecodedCurveSim.py's own functions
(parse_decklist, _build_card_lookup, run_simulation,
generate_statistics_summary, and the _summary_* helpers), imported and
called directly. Nothing about the simulation engine itself is
duplicated or re-derived here -- if you trust the CLI's numbers, you
can trust this page's numbers, because it's the exact same code path.

Run it with:
    pip install -r requirements.txt
    python app.py
then open http://127.0.0.1:5000 in your browser.

Single-user, local-only design: this keeps the most recent run's
results in a plain module-level variable, not a per-visitor session.
That's a deliberate simplification appropriate for "one person running
this on their own machine," not a public multi-user web service --
if two people used the same running instance at once, they'd see
each other's most recent run. Don't deploy this as-is to the public
internet for that reason (see README).

LIVE PROGRESS: a run is executed in a background thread (see `_Job`/
`_run_job`) rather than inline in the `/run` request, so the browser
can show a live-updating progress page (fetch log lines as they
happen, then a games-simulated counter) instead of a blank tab for
however long a big decklist takes. Progress reaches the browser via
Server-Sent Events (`/progress/<job_id>`, a long-lived streaming
response) rather than polling. Each job gets its OWN `queue.Queue` for
this -- deliberately NOT `contextlib.redirect_stdout`, which patches
the one process-wide `sys.stdout` and would be unsafe to use from a
background thread (see `vibecodedCurveSim._emit`'s docstring): instead
`_build_card_lookup`/`run_simulation` are called with an explicit
`log_fn`/`progress_callback` that pushes straight into this job's
queue, so concurrent requests (however unlikely in single-user use)
can never cross-contaminate each other's log output.
"""
import io
import json
import random
import tempfile
import threading
import uuid
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

from flask import Flask, render_template, request, send_file, abort, Response, stream_with_context

import vibecodedCurveSim as sim

app = Flask(__name__)

CACHE_PATH = Path(__file__).parent / "scryfall_cache.json"

# The most recent run's full state, kept in memory so the download
# routes can re-serve it without re-running the simulation. See the
# module docstring above for why this is fine for local single-user
# use and not for a shared/public deployment.
LATEST_RUN = {}

# Every in-flight or finished job, keyed by a random id handed to the
# browser at submit time. Same single-user/single-process caveat as
# LATEST_RUN -- this is a plain dict, not a database, and jobs are
# never evicted (fine for a locally-run tool restarted every so often;
# would need real cleanup for anything longer-lived).
JOBS: dict = {}


class _Job:
    """
    One simulation run's live state, shared between the background
    worker thread (`_run_job`, which writes to it) and the Flask
    request thread serving `/progress/<id>` (which reads from it via
    `events`, a queue that IS safe to hand between threads unlike raw
    stdout redirection).
    """

    def __init__(self, job_id: str):
        self.id = job_id
        self.status = "starting"   # starting -> fetching -> simulating -> done / error
        self.events: Queue = Queue()
        self.result: Optional[dict] = None
        self.error: Optional[str] = None

    def emit(self, kind: str, **data) -> None:
        self.status = data.get("status", self.status)
        self.events.put({"kind": kind, **data})


def _run_job(job: _Job, decklist_text: str, decklist_name: str, num_simulations: int,
             max_turns: int, verbose: bool) -> None:
    """
    The actual pipeline, run on a background thread so `/run` can
    return immediately with a progress page. Identical computation to
    the old synchronous `_run_simulation` (same vibecodedCurveSim.py
    functions, same call order) -- the only difference is that
    progress is pushed live into `job.events` instead of being
    captured/returned all at once at the end.
    """
    try:
        job.emit("status", status="fetching", message="Looking up cards on Scryfall...")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                          encoding="utf-8") as tmp:
            tmp.write(decklist_text)
            tmp_path = Path(tmp.name)
        try:
            commander_names, library_counts = sim.parse_decklist(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        # A fake Path carrying the ORIGINAL uploaded filename, purely so
        # generate_statistics_summary's title/"Source decklist" line shows
        # the name the user actually uploaded rather than a temp filename.
        display_path = Path(decklist_name)

        all_names = set(library_counts) | set(commander_names)
        log_lines = []

        def log_fn(line: str) -> None:
            log_lines.append(line)
            job.emit("log", line=line)

        card_lookup = sim._build_card_lookup(all_names, CACHE_PATH, verbose=verbose, log_fn=log_fn)

        missing = sorted(all_names - card_lookup.keys())
        for name in list(library_counts):
            if name not in card_lookup:
                library_counts.pop(name)

        if not library_counts:
            raise ValueError("No library cards could be resolved from this decklist -- nothing to simulate.")

        job.emit("status", status="simulating",
                  message=f"Simulating {num_simulations:,} games...")

        def progress_cb(done: int, total: int) -> None:
            job.emit("progress", done=done, total=total)

        results, mana_consistency = sim.run_simulation(
            card_lookup, library_counts, num_simulations, max_turns,
            progress_callback=progress_cb,
        )

        job.emit("status", status="reporting", message="Building the report...")

        summary_text = sim.generate_statistics_summary(
            display_path, commander_names, library_counts, card_lookup,
            results, num_simulations, max_turns, mana_consistency,
        )

        offsets = list(range(sim.MANA_AVAILABILITY_LOOKAHEAD + 1))
        csv_rows = [["card_name", "cmc"] + [f"turn_cmc+{o}" for o in offsets]]
        for name, per_turn in sorted(results.items(), key=lambda kv: (card_lookup[kv[0]].cmc, kv[0])):
            cmc = card_lookup[name].cmc
            row = [name, cmc]
            for o in offsets:
                turn = cmc + o
                row.append(f"{per_turn[turn]:.4f}" if turn in per_turn else "")
            csv_rows.append(row)

        # Pick one random nonland card's art for the results page's
        # background -- purely cosmetic (see Card.art_crop_url), so a
        # card missing art (rare, but possible for very old printings)
        # just narrows the pool rather than breaking anything. Drawn
        # from `results` specifically (the cards actually reported in
        # the table), not the whole card_lookup, so the featured art is
        # always something the viewer can see numbers for.
        art_candidates = [card_lookup[name].art_crop_url for name in results
                           if card_lookup[name].art_crop_url]
        background_art_url = random.choice(art_candidates) if art_candidates else None

        job.result = {
            "decklist_name": decklist_name,
            "commander_names": commander_names,
            "library_counts": library_counts,
            "card_lookup": card_lookup,
            "results": results,
            "mana_consistency": mana_consistency,
            "background_art_url": background_art_url,
            "missing": missing,
            "fetch_log": "\n".join(log_lines),
            "num_simulations": num_simulations,
            "max_turns": max_turns,
            "summary_text": summary_text,
            "csv_rows": csv_rows,
        }
        job.emit("done", status="done", redirect=f"/results/{job.id}")
    except Exception as exc:
        job.error = str(exc)
        job.emit("error", status="error", message=str(exc))


def _build_view_model(run: dict) -> dict:
    """Turn a `_run_simulation` result into the plain data structures
    results.html renders -- HTML tables/bars instead of the CLI's
    monospace ASCII, but every underlying number computed by the same
    vibecodedCurveSim.py helpers the CLI's --summary flag uses."""
    card_lookup = run["card_lookup"]
    library_counts = run["library_counts"]
    results = run["results"]

    mana = sim._summary_mana_base(library_counts, card_lookup)
    curve_hist = sim._summary_curve_histogram(card_lookup, library_counts)
    color_rel = sim._summary_color_reliability(card_lookup, results)
    deck_avg = sim._summary_deck_average(library_counts, results, card_lookup)
    deck_stddev = sim._summary_deck_stddev(library_counts, results, card_lookup, deck_avg)
    mana_consistency = run["mana_consistency"]

    total_cards = sum(library_counts.values())
    nonland_count = len(results)

    color_rows = []
    if color_rel:
        max_avg = max(color_rel.values())
        for col, avg in sorted(color_rel.items(), key=lambda x: -x[1]):
            color_rows.append({
                "name": sim._SUMMARY_COLOR_NAMES[col], "pct": avg,
                "bar_pct": (avg / max_avg * 100) if max_avg else 0,
            })

    combined = {col: mana["land_color_copies"].get(col, 0) + mana["rock_dork_color_copies"].get(col, 0)
                for col in sim._SUMMARY_COLOR_ORDER}
    max_combined = max(combined.values()) if combined else 1
    mana_rows = []
    for col in sim._SUMMARY_COLOR_ORDER:
        land_n = mana["land_color_copies"].get(col, 0)
        rd_n = mana["rock_dork_color_copies"].get(col, 0)
        if land_n == 0 and rd_n == 0:
            continue
        mana_rows.append({
            "name": sim._SUMMARY_COLOR_NAMES[col], "land": land_n, "rock_dork": rd_n,
            "total": combined[col], "bar_pct": (combined[col] / max_combined * 100) if max_combined else 0,
        })

    max_curve = max(curve_hist.values()) if curve_hist else 1
    curve_rows = [
        {"cmc": cmc, "count": curve_hist[cmc], "bar_pct": curve_hist[cmc] / max_curve * 100}
        for cmc in sorted(curve_hist)
    ]

    card_rows = []
    for name, per_turn in sorted(results.items(), key=lambda kv: (card_lookup[kv[0]].cmc, kv[0])):
        card = card_lookup[name]
        cells = [per_turn.get(card.cmc + o) for o in range(sim.MANA_AVAILABILITY_LOOKAHEAD + 1)]
        card_rows.append({"cmc": card.cmc, "name": name, "cost": card.mana_cost or "—", "cells": cells})

    def on_curve_val(name, card, per_turn):
        return per_turn.get(card.cmc)

    def gap_val(name, card, per_turn):
        oc, c3 = per_turn.get(card.cmc), per_turn.get(card.cmc + sim.MANA_AVAILABILITY_LOOKAHEAD)
        return None if oc is None or c3 is None else c3 - oc

    lowest_early = sim._summary_top_n(results, card_lookup, 5, on_curve_val, reverse=False,
                                       filter_fn=lambda n, c, p: c.cmc <= 4)
    steepest = sim._summary_top_n(results, card_lookup, 5, gap_val, reverse=True)
    most_reliable = sim._summary_top_n(results, card_lookup, 5, on_curve_val, reverse=True)

    def outlier_rows(items):
        return [{"name": name, "cost": card.mana_cost or "land", "cmc": card.cmc, "value": v}
                for v, name, card, _ in items]

    takeaways = sim._summary_takeaways(mana, curve_hist, color_rel, results, card_lookup,
                                        total_cards, nonland_count, run["max_turns"])

    return {
        "decklist_name": run["decklist_name"],
        "commander_names": run["commander_names"],
        "background_art_url": run.get("background_art_url"),
        "missing": run["missing"],
        "fetch_log": run["fetch_log"],
        "num_simulations": run["num_simulations"],
        "max_turns": run["max_turns"],
        "total_cards": total_cards,
        "total_lands": mana["total_lands"],
        "nonland_count": nonland_count,
        "deck_avg": deck_avg,
        "deck_stddev": deck_stddev,
        "mana_consistency": mana_consistency,
        "screw_check_turn": sim.SCREW_CHECK_TURN,
        "screw_land_threshold": sim.SCREW_LAND_THRESHOLD,
        "flood_check_turn": sim.FLOOD_CHECK_TURN,
        "flood_excess_threshold": sim.FLOOD_EXCESS_LAND_THRESHOLD,
        "mana": mana,
        "mana_rows": mana_rows,
        "curve_rows": curve_rows,
        "color_rows": color_rows,
        "card_rows": card_rows,
        "lowest_early": outlier_rows(lowest_early),
        "steepest": [{"name": name, "cost": card.mana_cost or "land",
                      "from_pct": per_turn.get(card.cmc), "to_pct": per_turn.get(card.cmc + sim.MANA_AVAILABILITY_LOOKAHEAD),
                      "gap": v} for v, name, card, per_turn in steepest],
        "most_reliable": outlier_rows(most_reliable),
        "takeaways": takeaways,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/assumptions", methods=["GET"])
def assumptions():
    return render_template("assumptions.html")


@app.route("/run", methods=["POST"])
def run():
    upload = request.files.get("decklist")
    if upload is None or upload.filename == "":
        return render_template("index.html", error="Please choose a decklist .txt file to upload.")

    try:
        num_simulations = int(request.form.get("simulations", 10000))
        max_turns = int(request.form.get("max_turns", sim.MAX_SIMULATED_TURNS))
    except ValueError:
        return render_template("index.html", error="Simulations and Max Turns must be whole numbers.")

    verbose = request.form.get("verbose") == "on"
    decklist_text = upload.stream.read().decode("utf-8", errors="replace")

    job = _Job(uuid.uuid4().hex)
    JOBS[job.id] = job
    threading.Thread(
        target=_run_job,
        args=(job, decklist_text, upload.filename, num_simulations, max_turns, verbose),
        daemon=True,
    ).start()

    return render_template("progress.html", job_id=job.id, decklist_name=upload.filename)


@app.route("/progress/<job_id>")
def progress_stream(job_id):
    """
    Server-Sent Events stream: one `data: {...}\\n\\n` line per event
    (log line, progress tick, or the final done/error), read live by
    progress.html's `EventSource`. Blocks on `job.events.get()` between
    events -- cheap for a single background job, and exactly why this
    app must run with `threaded=True` (see bottom of file): a
    long-lived streaming response like this one must not tie up the
    only worker thread the dev server has.

    Reconnect safety: if a job already finished (or errored) before
    this connection opens -- e.g. the browser tab reconnects after a
    dropped connection -- the terminal event may have already been
    permanently consumed off `job.events` by the earlier, now-dead
    connection (a `Queue.get()` removes what it reads). Without the
    explicit check below, a reconnect would just hang: nothing new is
    ever going to arrive on a finished job's queue. Checking
    `job.result`/`job.error` up front lets a reconnect see the outcome
    immediately instead of waiting forever for an event that already
    happened.
    """
    job = JOBS.get(job_id)
    if job is None:
        abort(404)

    def gen():
        if job.result is not None:
            yield f"data: {json.dumps({'kind': 'done', 'redirect': f'/results/{job.id}'})}\n\n"
            return
        if job.error is not None:
            yield f"data: {json.dumps({'kind': 'error', 'message': job.error})}\n\n"
            return

        while True:
            try:
                event = job.events.get(timeout=20)
            except Empty:
                yield ": keep-alive\n\n"  # SSE comment line -- resets client/proxy idle timeouts
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event["kind"] in ("done", "error"):
                return

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/results/<job_id>")
def results(job_id):
    job = JOBS.get(job_id)
    if job is None:
        abort(404)
    if job.result is None:
        # Not finished (or failed) -- send them back to the progress
        # page rather than a broken/empty results page. Covers a
        # refresh or a shared link hitting this route mid-run.
        return render_template("progress.html", job_id=job.id, decklist_name="")

    LATEST_RUN.clear()
    LATEST_RUN.update(job.result)

    view = _build_view_model(job.result)
    return render_template("results.html", **view)


@app.route("/download/csv")
def download_csv():
    if not LATEST_RUN:
        abort(404)
    buf = io.StringIO()
    import csv as csv_module
    writer = csv_module.writer(buf)
    writer.writerows(LATEST_RUN["csv_rows"])
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    name = Path(LATEST_RUN["decklist_name"]).stem + "-results.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=name)


@app.route("/download/summary")
def download_summary():
    if not LATEST_RUN:
        abort(404)
    mem = io.BytesIO(LATEST_RUN["summary_text"].encode("utf-8"))
    name = Path(LATEST_RUN["decklist_name"]).stem + " statistics summary.txt"
    return send_file(mem, mimetype="text/plain", as_attachment=True, download_name=name)


if __name__ == "__main__":
    # threaded=True is required, not just faster: /progress/<id> is a
    # long-lived streaming connection that blocks for the entire run,
    # and the actual work happens on its own background thread anyway
    # -- without threaded=True, Flask's single worker would be stuck
    # serving that one open connection and unable to handle anything
    # else (even the initial page load) until the job finished.
    #
    # debug=False deliberately: Flask's debug mode ships the Werkzeug
    # interactive debugger, which lets anyone who can reach an
    # unhandled-exception page execute arbitrary Python in the browser.
    # This app binds to localhost by default, but the README has
    # brand-new users run this file directly as their normal way of
    # using the tool -- it shouldn't default to a mode that exists for
    # active development, not end use. Set it to True locally if you're
    # working on app.py itself and want the auto-reloader/debugger back.
    app.run(debug=False, threaded=True)
