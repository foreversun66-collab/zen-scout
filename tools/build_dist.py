"""OPEN LAB 展示版：上線打包。用「白名單」組出 dist/，組完自動驗證。

白名單（逐檔列舉，不是整個資料夾抓）：
  site/index.html、web/index.html、web/search.js、data/events_1025.json
  clips/<片段>.mp4、covers/<片段>.jpg —— 只收 events_1025.json 裡實際引用到的那 115 組
  index.html（dist 根目錄，本工具產生：轉到 site/index.html）
  404.html（dist 根目錄，本工具產生：Cloudflare Pages 找不到檔案時回這頁、狀態 404；沒有它會把首頁當 200 回）
  _headers（dist 根目錄，本工具產生：Cloudflare Pages 對所有路徑加 X-Robots-Tag: noindex, nofollow）
  （另寫 local/dist_manifest.json：每個檔的相對路徑、大小、sha256 —— 不進 dist）
一律不得進 dist：local/、tools/、.git、*.db、*.log、*.csv、*.bak*（另外再對 dist 全掃一次確認）。

dist/ 已存在時：只在 local/dist_manifest.json 在、而且 dist 裡每個檔都列在上面時才重建，只刪 manifest 列過的檔
（不認得的檔一個都不刪 → 停下來給人看）。

驗證（任一不符 → 退出碼 2）：
  ・檔數 < 20,000、最大單檔 < 25 MiB；印總容量
  ・dist 裡的檔＝白名單（不多不少）、沒有禁止的路徑或副檔名
  ・機敏掃描（tools/scan_secrets.py --dir dist）0 筆
  ・站內連結：HTML 的 href／src、網頁程式碼裡 BASE + '…' 讀的資料、JSON 引用的影片與封面，
    全部要對到 dist 裡實際存在的檔；外部網址也算不合格
用法：py tools\\build_dist.py
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
MANIFEST_PATH = os.path.join(ROOT, "local", "dist_manifest.json")   # 不放進 dist：sha256 長得像金鑰，掃描會命中，而且這份清單不需要公開
ALLOWED_EXTERNAL = {"https://github.com/foreversun66-collab/zen-scout"}   # 唯一允許的外部連結：交付頁的程式庫按鈕
STATIC = ["site/index.html", "web/index.html", "web/search.js", "data/events_1025.json"]
EVENTS = "data/events_1025.json"
MAX_FILES = 20000
MAX_BYTES = 25 * 1024 * 1024
BAD_TOP = {"local", "tools", ".git"}
BAD_EXT = (".db", ".log", ".csv", ".db-wal", ".db-shm", ".py", ".pyc")
COPYRIGHT = "影像素材版權屬中信特攻籃球隊，僅供本次競賽展示"
PRODUCT = "ZEN Scout 幀探"
HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow\n"
NOT_FOUND = """<!DOCTYPE html>
<html lang="zh-Hant-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<link rel="icon" href="data:,">
<title>ZEN Scout 幀探｜找不到這一頁</title>
<style>body{margin:0;background:#0f1115;color:#e8eaf0;font-family:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center}a{color:#f5c518}</style></head>
<body><div><h1>找不到這一頁</h1><p><a href="/site/">回到 ZEN Scout 幀探 首頁</a></p></div></body></html>
"""
ROOT_INDEX = """<!DOCTYPE html>
<html lang="zh-Hant-TW"><head><meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta http-equiv="refresh" content="0; url=site/index.html">
<link rel="icon" href="data:,">
<title>ZEN Scout 幀探</title></head>
<body><p><a href="site/index.html">前往作品首頁</a></p></body></html>
"""


def die(msg):
    print("停止：" + msg, file=sys.stderr)
    sys.exit(2)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def whitelist():
    with io.open(os.path.join(ROOT, EVENTS), encoding="utf-8") as f:
        d = json.load(f)
    files = list(STATIC)
    for e in d["events"]:
        for c in e["clips"]:
            for k in ("video", "cover"):
                rel = c[k]
                if not re.fullmatch(r"(clips/[A-Za-z0-9_]+\.mp4|covers/[A-Za-z0-9_]+\.jpg)", rel):
                    die(f"events_1025.json 的 {k} 路徑不合規則：{rel!r}")
                files.append(rel)
    if len(set(files)) != len(files):
        die("白名單有重複的檔")
    return files, d


def clear_old():
    if not os.path.exists(DIST):
        return
    if not os.path.isfile(MANIFEST_PATH):
        die("dist/ 已存在、但找不到 local/dist_manifest.json（認不出是不是本工具建的）—— 不動它，請人工確認")
    with io.open(MANIFEST_PATH, encoding="utf-8") as f:
        old = json.load(f)
    known = {x["path"] for x in old["files"]}
    have = set()
    for dp, dns, fns in os.walk(DIST):
        for fn in fns:
            have.add(os.path.relpath(os.path.join(dp, fn), DIST).replace("\\", "/"))
    extra = sorted(have - known)
    if extra:
        die(f"dist/ 裡有 manifest 以外的檔 {len(extra)} 個（例：{extra[:3]}）—— 不刪不認得的檔，請人工確認")
    for rel in sorted(have):
        os.remove(os.path.join(DIST, rel))
    for dp, dns, fns in sorted(os.walk(DIST), key=lambda x: -len(x[0])):
        if dp != DIST and not os.listdir(dp):
            os.rmdir(dp)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src", "poster") and v is not None:
                self.refs.append((tag, k, v))
            if tag == "meta" and k == "content" and v and "url=" in v.lower():
                self.refs.append((tag, "refresh", v.split("=", 1)[1].strip()))


def check_links(d):
    """回 (檢查的連結數, 斷連結清單, 外部連結清單)。"""
    n, broken, external, allowed_ext = 0, [], [], []

    def target_ok(from_rel, ref):
        nonlocal n
        if ref.startswith(("#", "data:", "javascript:", "mailto:")):
            return
        u = urlsplit(ref)
        if u.scheme or u.netloc:
            if ref in ALLOWED_EXTERNAL:
                allowed_ext.append((from_rel, ref))
            else:
                external.append((from_rel, ref))
            return
        n += 1
        base = "" if u.path.startswith("/") else os.path.dirname(from_rel)
        path = unquote(u.path).lstrip("/") if u.path.startswith("/") else unquote(u.path)
        if path == "" or path.endswith("/"):
            path += "index.html"          # /site/ → site/index.html（Cloudflare Pages 的簡短網址）
        tgt = os.path.normpath(os.path.join(base, path)).replace("\\", "/")
        if tgt.startswith("..") or not os.path.isfile(os.path.join(DIST, tgt)):
            broken.append((from_rel, ref))

    for rel in ("index.html", "404.html", "site/index.html", "web/index.html"):
        with io.open(os.path.join(DIST, rel), encoding="utf-8") as f:
            text = f.read()
        p = Links()
        p.feed(text)
        for _, _, ref in p.refs:
            target_ok(rel, ref)
        # 網頁程式碼讀資料的寫法：BASE + 'xxx'（BASE 在同一檔裡定義）
        m = re.search(r"var BASE = '([^']*)'", text)
        if m:
            for lit in re.findall(r"BASE \+ '([^']+)'", text):
                target_ok(rel, m.group(1) + lit)
            # JSON 裡的影片與封面：網頁用 BASE + c.video／BASE + c.cover 讀
            if "BASE + c.video" in text or "BASE + c.cover" in text or "BASE + e.clips[0].cover" in text:
                for e in d["events"]:
                    for c in e["clips"]:
                        target_ok(rel, m.group(1) + c["video"])
                        target_ok(rel, m.group(1) + c["cover"])
    return n, broken, external, allowed_ext


def main():
    verify_only = "--verify-only" in sys.argv[1:]     # 不重建，只驗證現有的 dist（驗證本身的對照測試用）
    files, d = whitelist()
    out = files + ["index.html", "404.html", "_headers"]
    if not verify_only:
        for rel in files:
            if not os.path.isfile(os.path.join(ROOT, rel)):
                die(f"白名單上的檔不存在：{rel}")
        clear_old()
        os.makedirs(DIST, exist_ok=True)
        for rel in files:
            dst = os.path.join(DIST, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(ROOT, rel), dst)        # 只複製內容，不帶原檔的時間與屬性
        with io.open(os.path.join(DIST, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(ROOT_INDEX)
        with io.open(os.path.join(DIST, "404.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(NOT_FOUND)
        with io.open(os.path.join(DIST, "_headers"), "w", encoding="utf-8", newline="\n") as f:
            f.write(HEADERS)
        man = {"files": [{"path": r, "bytes": os.path.getsize(os.path.join(DIST, r)),
                          "sha256": sha256(os.path.join(DIST, r))} for r in sorted(out)]}
        with io.open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
    elif not os.path.isdir(DIST):
        die("沒有 dist/ 可以驗證")

    # ---- 驗證 ----
    bad = []
    allf = []
    for dp, dns, fns in os.walk(DIST):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), DIST).replace("\\", "/")
            allf.append((rel, os.path.getsize(os.path.join(dp, fn))))
    names = {r for r, _ in allf}
    expect = set(out)
    if names != expect:
        bad.append(f"dist 的檔跟白名單不一致：多 {sorted(names - expect)[:5]}、少 {sorted(expect - names)[:5]}")
    for rel, _ in allf:
        top = rel.split("/", 1)[0]
        if top in BAD_TOP or rel.lower().endswith(BAD_EXT) or ".bak" in rel.lower():
            bad.append(f"禁止的檔進了 dist：{rel}")
    for rel in out:
        if rel in STATIC and os.path.isfile(os.path.join(DIST, rel)) \
                and sha256(os.path.join(DIST, rel)) != sha256(os.path.join(ROOT, rel)):
            bad.append(f"複製後內容不同：{rel}")
    try:
        with io.open(MANIFEST_PATH, encoding="utf-8") as f:
            man = {x["path"]: x for x in json.load(f)["files"]}
    except (OSError, ValueError) as e:
        man = {}
        bad.append(f"讀不到 local/dist_manifest.json（{e}）")
    for rel, size in allf:
        m = man.get(rel)
        if not m or m["bytes"] != size or m["sha256"] != sha256(os.path.join(DIST, rel)):
            bad.append(f"跟 local/dist_manifest.json 對不上：{rel}")
    try:
        with io.open(os.path.join(DIST, "_headers"), encoding="utf-8", newline="") as f:
            if f.read() != HEADERS:
                bad.append("_headers 內容不對（要對所有路徑加 X-Robots-Tag: noindex, nofollow）")
    except OSError:
        bad.append("dist 沒有 _headers")
    # 品牌檢查（第 4 輪改名 ZEN Scout 幀探）：文字檔裡「DEA」不分大小寫 0 筆；「中信特攻」只能在版權聲明那一句
    #   另外：作品名稱一律寫「ZEN Scout 幀探」；不含 Scout 的舊寫法「ZEN 幀探」0 筆
    n_dea = n_ctbc = n_ctbc_ok = n_old = n_new = 0
    for rel, _ in allf:
        if rel.endswith((".mp4", ".jpg")):
            continue
        with io.open(os.path.join(DIST, rel), encoding="utf-8", errors="replace") as f:
            txt = f.read()
        k = len(re.findall(r"(?i)dea", txt))
        if k:
            bad.append(f"品牌檢查：{rel} 有 {k} 處「DEA」")
        n_dea += k
        n_ctbc += txt.count("中信特攻")
        n_ctbc_ok += txt.count(COPYRIGHT)
        o = len(re.findall(r"ZEN\s*幀探", txt))
        if o:
            bad.append(f"品牌檢查：{rel} 有 {o} 處舊寫法「ZEN 幀探」")
        n_old += o
        n_new += txt.count(PRODUCT)
    if n_ctbc != n_ctbc_ok or n_ctbc_ok < 1:
        bad.append(f"品牌檢查：「中信特攻」出現 {n_ctbc} 次，其中在版權聲明裡 {n_ctbc_ok} 次")
    if n_new < 1:
        bad.append(f"品牌檢查：找不到作品名稱「{PRODUCT}」")
    print(f"品牌檢查：DEA {n_dea} 筆；「中信特攻」{n_ctbc} 次（版權聲明 {n_ctbc_ok} 次）；"
          f"「{PRODUCT}」{n_new} 處、舊寫法「ZEN 幀探」{n_old} 處")
    total = sum(s for _, s in allf)
    big = max(allf, key=lambda x: x[1])
    nf = len(allf)
    print(f"dist：{nf} 個檔、總容量 {total / 2**20:.1f} MiB（{total / 1e6:.1f} MB）、最大單檔 {big[0]} {big[1] / 2**20:.2f} MiB")
    by = {}
    for rel, s in allf:
        k = rel.split("/", 1)[0] if "/" in rel else "(根目錄)"
        by.setdefault(k, [0, 0])
        by[k][0] += 1
        by[k][1] += s
    print("  " + "、".join(f"{k} {v[0]} 檔 {v[1] / 2**20:.1f} MiB" for k, v in sorted(by.items())))
    if nf >= MAX_FILES:
        bad.append(f"檔數 {nf} ≥ {MAX_FILES}")
    if big[1] >= MAX_BYTES:
        bad.append(f"最大單檔 {big[0]} ≥ 25 MiB")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    sc = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "scan_secrets.py"), "--dir", "dist"],
                        capture_output=True, text=True, encoding="utf-8", env=env)
    last = [l for l in sc.stdout.splitlines() if l.startswith(("掃描", "公開資料夾"))]
    print("機敏掃描：" + "；".join(last))
    if sc.returncode != 0:
        bad.append("機敏掃描有命中：\n" + "\n".join(l for l in sc.stdout.splitlines() if "\t" in l))

    n, broken, external, allowed_ext = check_links(d)
    print(f"站內連結：檢查 {n} 條、斷連結 {len(broken)}、外部連結 {len(external)}（白名單內的外部連結 {len(allowed_ext)} 條：{sorted(set(x[1] for x in allowed_ext))}）")
    for b in broken[:10]:
        bad.append(f"斷連結：{b[0]} → {b[1]}")
    for x in external[:10]:
        bad.append(f"外部連結：{x[0]} → {x[1]}")

    for b in bad:
        print("不符：" + b)
    print("全部通過" if not bad else f"不符 {len(bad)} 項")
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
