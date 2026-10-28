"""OPEN LAB 展示版：本機預覽用的靜態伺服器（只綁 127.0.0.1）。

只提供會公開的東西：web/、data/、clips/、covers/。local/、tools/、.git 等一律 404。
支援 Range（影片拖曳、切換機位對時要用）。網址：http://127.0.0.1:<埠>/web/index.html
用法：py tools\\serve_local.py [--port 4780]
（8740 不能用：落在 Windows 保留的 TCP 埠範圍 8712–8811，綁定會被拒絕 10013；第 2 輪實測）
"""
import argparse
import http.server
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOW = ("web", "data", "clips", "covers")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def _allowed(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/", ""):
            return True
        top = path.lstrip("/").split("/", 1)[0]
        return top in ALLOW and ".." not in path

    def send_head(self):
        if self.path.split("?", 1)[0] in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/web/index.html")
            self.end_headers()
            return None
        if not self._allowed():
            self.send_error(404)
            return None
        rng = self.headers.get("Range")
        fpath = self.translate_path(self.path)
        if not rng or not os.path.isfile(fpath):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        size = os.path.getsize(fpath)
        if not m or (m.group(1) == "" and m.group(2) == ""):
            self.send_error(416)
            return None
        if m.group(1) == "":
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(fpath, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(fpath))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remain = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remain = getattr(self, "_remain", None)
        if remain is None:
            return super().copyfile(source, outputfile)
        while remain > 0:
            buf = source.read(min(65536, remain))
            if not buf:
                break
            outputfile.write(buf)
            remain -= len(buf)
        self._remain = None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        if not self.headers.get("Range"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4780)
    args = ap.parse_args()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"預覽：http://127.0.0.1:{args.port}/web/index.html（只綁 127.0.0.1；Ctrl+C 關閉）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
