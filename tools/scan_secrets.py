"""OPEN LAB 展示版：機敏資訊掃描。

掃專案資料夾底下全部檔案（含 local\\，跳過 .git\\），只印「檔名:行號  類型」，**不印出命中的值**。
類型：IP、UNC 路徑、碟符絕對路徑、uuid、token、帳號、內網主機名、來源檔名。
內網主機名與帳號的字詞清單放 local/scan_terms.json（不進版控；那個檔自己當然會命中，屬預期）。

site\\、web\\、data\\、dist\\ 底下有任何命中 → 退出碼 2（會公開的東西必須是 0 筆）。
--dir <專案內資料夾> 只掃那一個資料夾（例：--dir dist）。
用法：py tools\\scan_secrets.py [--out <專案內的檔>]
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS = os.path.join(ROOT, "local", "scan_terms.json")
PUBLIC_DIRS = ("site", "web", "data", "dist")   # 會公開的；dist＝上線打包結果
SKIP_DIRS = {".git", "__pycache__", "clips", "covers"}   # 影片／封面是二進位，另行處理

_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
PATTERNS = [
    ("IP", re.compile(r"(?<![\d.])" + _OCT + r"(?:\." + _OCT + r"){3}(?![\d.])")),
    ("UNC 路徑", re.compile(r"\\\\[A-Za-z0-9._$-]+\\|(?<![:\w/])//[A-Za-z0-9._-]+/\S")),
    ("碟符絕對路徑", re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("token", re.compile(r"(?i)\b(?:token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]"
                         r"|\bAKfyc[\w-]{10,}|\b[a-f0-9]{32,}\b|\b[A-Za-z0-9_\-]{40,}\b")),
    ("帳號", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("內網主機名", re.compile(r"(?i)\.ts\.net\b|\btailscale\b|\.local\b|\.lan\b|\.internal\b")),
    ("來源檔名", re.compile(r"\b(?:M\d{4}|C\d{4}|DSC_\d{4})\b|\.(?:MOV|MP4)\b")),
]


def load_terms():
    if not os.path.isfile(TERMS):
        print(f"注意：沒有 {os.path.relpath(TERMS, ROOT)}，主機名／帳號只用通用樣式", file=sys.stderr)
        return []
    with io.open(TERMS, encoding="utf-8-sig") as f:
        t = json.load(f)
    out = []
    for kind in ("內網主機名", "帳號"):
        for w in t.get(kind, []):
            out.append((kind, re.compile(re.escape(w), re.I)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="結果另存（必須在專案資料夾底下）")
    ap.add_argument("--dir", help="只掃這個資料夾（專案內）")
    args = ap.parse_args()
    pats = PATTERNS + load_terms()
    hits, nfiles, binaries = [], 0, []
    start = ROOT
    if args.dir:
        start = os.path.realpath(os.path.join(ROOT, args.dir))
        base = os.path.normcase(os.path.realpath(ROOT))
        try:
            inside = os.path.commonpath([os.path.normcase(start), base]) == base
        except ValueError:
            inside = False
        if not inside or not os.path.isdir(start) or os.path.normcase(start) == base:
            print("停止：--dir 必須是專案資料夾底下已存在的子資料夾", file=sys.stderr)
            sys.exit(2)
    for dp, dns, fns in os.walk(start):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, ROOT)
            with open(p, "rb") as f:
                raw = f.read()
            if b"\0" in raw[:4096]:
                binaries.append(rel)
                continue
            nfiles += 1
            text = raw.decode("utf-8-sig", errors="replace")
            for ln, line in enumerate(text.splitlines(), 1):
                for kind, rx in pats:
                    n = len(rx.findall(line))
                    if n:
                        hits.append((rel, ln, kind, n))
    lines = [f"掃描 {nfiles} 個文字檔（跳過 {', '.join(sorted(SKIP_DIRS))}）；二進位檔 {len(binaries)} 個"]
    for b in binaries:
        lines.append(f"  二進位（未掃內容）：{b}")
    for rel, ln, kind, n in hits:
        lines.append(f"{rel}:{ln}\t{kind}" + (f" ×{n}" if n > 1 else ""))
    pub = [h for h in hits if h[0].split(os.sep)[0] in PUBLIC_DIRS]
    by_top = {}
    for h in hits:
        top = h[0].split(os.sep)[0] if os.sep in h[0] else "(根目錄)"
        by_top[top] = by_top.get(top, 0) + 1
    lines.append("各資料夾命中行數：" + ("、".join(f"{k} {v}" for k, v in sorted(by_top.items())) or "0"))
    lines.append(f"公開資料夾（{'、'.join(PUBLIC_DIRS)}）命中：{len(pub)} 行")
    print("\n".join(lines))
    if args.out:
        o = os.path.realpath(os.path.abspath(args.out))
        r = os.path.normcase(os.path.realpath(ROOT))
        try:
            ok = os.path.commonpath([os.path.normcase(o), r]) == r
        except ValueError:      # 不同碟符 → 不在專案底下
            ok = False
        if not ok:
            print("停止：--out 不在專案資料夾底下", file=sys.stderr)
            sys.exit(2)
        with io.open(o, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    sys.exit(2 if pub else 0)


if __name__ == "__main__":
    main()
