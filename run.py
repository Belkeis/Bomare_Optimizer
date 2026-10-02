"""Start the Bomare dashboard:  python run.py   ->  http://localhost:5000"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # project root on the path

from server import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Bomare dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("DEBUG") == "1")
