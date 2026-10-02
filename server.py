"""
server.py -- Flask app: serves the dashboard and exposes the JSON API it calls.

    GET  /                  dashboard page
    GET  /api/templates     preset tables for the "Load Preset" dropdown
    POST /api/validate      {table}          -> validation result
    POST /api/optimize      {table, params}  -> input_handler -> SearchRunner -> results
    POST /api/export-csv    {table}          -> CSV file
    POST /api/import-csv    multipart file   -> {table}
    GET  /health
"""
import logging
import os
import time
from flask import Flask, Response, jsonify, render_template, request

from input_handler import (csv_to_table, run_optimization_pipeline, table_to_csv, validate_table)
from presets import PRESETS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="static")
app.config["TEMPLATES_AUTO_RELOAD"] = True

#open the dashboard
@app.route("/")
def index():
    return render_template("index.html")


#Give the dashboard the predefined example tables.
@app.route("/api/templates")
def templates():
    return jsonify({"status": "success", "templates": PRESETS})

#Check whether the table entered by the user is valid. 
@app.route("/api/validate", methods=["POST"])
def validate():
    payload = request.get_json(silent=True) or {}
    return jsonify(validate_table(payload.get("table")))

#Receives the user's optimization request.
@app.route("/api/optimize", methods=["POST"])
def optimize():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "errors": ["No JSON payload provided."]}), 400
    t_start = time.perf_counter()                       # request received
    result = run_optimization_pipeline(payload)
    result["total_time_sec"] = round(time.perf_counter() - t_start, 2)   # result ready
    return jsonify(result), (200 if result["status"] == "success" else 400)

#Let the user download the current table as a CSV file.
@app.route("/api/export-csv", methods=["POST"])
def export_csv():
    payload = request.get_json(silent=True) or {}
    text = table_to_csv(payload.get("table") or [])
    return Response(text, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=assembly_line.csv"})

#gets the CSV file uploaded by the user converts it into your application's table structure sends the table back to the dashboard
@app.route("/api/import-csv", methods=["POST"])
def import_csv():
    f = request.files.get("file")
    if f is None:
        return jsonify({"status": "error", "errors": ["No file uploaded."]}), 400
    try:
        text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"status": "error", "errors": ["CSV must be UTF-8 encoded."]}), 400
    table, errors = csv_to_table(text)
    if errors:
        return jsonify({"status": "error", "errors": errors}), 400
    return jsonify({"status": "success", "table": table})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
