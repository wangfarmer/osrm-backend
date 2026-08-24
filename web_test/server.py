from http.server import SimpleHTTPRequestHandler, HTTPServer
from urllib.request import urlopen
from urllib.parse import urlparse
import json

OSRM_BASE = "http://localhost:5000"

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/osrm/"):
            osrm_path = self.path.replace("/osrm", "", 1)
            url = OSRM_BASE + osrm_path

            try:
                with urlopen(url) as response:
                    data = response.read()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
        else:
            super().do_GET()

if __name__ == "__main__":
    server = HTTPServer(("localhost", 8088), Handler)
    print("Open http://localhost:8088")
    server.serve_forever()
