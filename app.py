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
"""
import contextlib
import io
import tempfile
from pathlib import Path

from flask import Flask, render_template, request, send_file, abort

import vibecodedCurveSim as sim

app = Flask(__name__)

CACHE_PATH = Path(__file__).parent / "scryfall_cache.json"

# The most recent run's full state, kept in memory so the download
# routes can re-serve it without re-running the simulation. See the
# module docstring above for why this is fine for local single-user
# use and not for a shared/public deployment.
LATEST_RUN = {}


def _run_simulation(decklist_text: str, decklist_name: str, num_simulations: int,
                     max_turns: int, verbose: bool) -> dict:
    """
    Run the full pipeline against decklist text already read into
    memory (from the uploaded file), using vibecodedCurveSim.py's own
    functions throughout. Returns a dict with everything the results
    template needs, plus everything the download routes need to
    regenerate the CSV/summary files on demand.
    """
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

    fetch_log = io.StringIO()
    with contextlib.redirect_stdout(fetch_log):
        card_lookup = sim._build_card_lookup(all_names, CACHE_PATH, verbose=verbose)

    missing = sorted(all_names - card_lookup.keys())
    for name in list(library_counts):
        if name not in card_lookup:
            library_counts.pop(name)

    if not library_counts:
        raise ValueError("No library cards could be resolved from this decklist -- nothing to simulate.")

    results = sim.run_simulation(card_lookup, library_counts, num_simulations, max_turns)

    summary_text = sim.generate_statistics_summary(
        display_path, commander_names, library_counts, card_lookup,
        results, num_simulations, max_turns,
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

    return {
        "decklist_name": decklist_name,
        "commander_names": commander_names,
        "library_counts": library_counts,
        "card_lookup": card_lookup,
        "results": results,
        "missing": missing,
        "fetch_log": fetch_log.getvalue(),
        "num_simulations": num_simulations,
        "max_turns": max_turns,
        "summary_text": summary_text,
        "csv_rows": csv_rows,
    }


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
        "missing": run["missing"],
        "fetch_log": run["fetch_log"],
        "num_simulations": run["num_simulations"],
        "max_turns": run["max_turns"],
        "total_cards": total_cards,
        "total_lands": mana["total_lands"],
        "nonland_count": nonland_count,
        "deck_avg": deck_avg,
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

    try:
        run_data = _run_simulation(decklist_text, upload.filename, num_simulations, max_turns, verbose)
    except ValueError as exc:
        return render_template("index.html", error=str(exc))

    LATEST_RUN.clear()
    LATEST_RUN.update(run_data)

    view = _build_view_model(run_data)
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
    app.run(debug=True)
