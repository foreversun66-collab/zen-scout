"""OPEN LAB 展示版：照 local/slice_plan_1025.csv 切 1025 的機位片段與封面。

預設**乾跑**：只認碟（認得到的話）、組好每一條 ffmpeg 指令印出來、算預估總容量；不執行 ffmpeg 切片。
加 --apply 才真的切。

認碟：--drive <卷標> 指定來源碟；卷標必須在 local/drive_expect.json 的白名單（卷標 → uuid）裡，
Windows 卷標相符，而且該碟 _MAM/drive_id.json 的 drive_uuid＝白名單上那個 uuid（都對才算）。不寫死碟符。
  白名單外的卷標 → 一律停（乾跑也停）。
  乾跑：認不到碟 → 印「未接碟，跳過」，指令裡的來源用 <卷標> 代位，照樣驗 csv 與組指令。
  --apply：認不到碟、或 uuid 不符 → 停。
試編：--only <片段編號,…> 只處理指定的片段；--out-dir <專案內資料夾> 改輸出位置（底下一樣是 clips\、covers\）。

輸出（一律在專案資料夾底下，否則停）：
  clips/<片段編號>.mp4   1280x720、H.264 8-bit 4:2:0、AAC 雙聲道、faststart、去掉來源中繼資料
  covers/<片段編號>.jpg  錨點那一格，寬 640
來源碟只讀（ffmpeg 只當輸入讀）。已存在的輸出不覆蓋（-n）。

用法：
  py tools\\slice_1025.py --drive DEA_SSD1                  乾跑
  py tools\\slice_1025.py --drive DEA_SSD1 --encoder cpu    乾跑、指定 CPU 編碼
  py tools\\slice_1025.py --drive DEA_SSD1 --apply          真的切（預設 NVIDIA 顯卡編碼，不可用時自動改 CPU）
"""
import time
import argparse
import csv
import ctypes
import io
import json
import os
import shutil
import string
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 專案根（tools 的上一層）
PLAN = os.path.join(ROOT, "local", "slice_plan_1025.csv")
EXPECT = os.path.join(ROOT, "local", "drive_expect.json")
CLIPS_DIR = os.path.join(ROOT, "clips")
COVERS_DIR = os.path.join(ROOT, "covers")

EXPECT_ROWS = 115
PLAN_COLS = ["clip_id", "src_rel_path", "anchor_sec", "in_sec", "out_sec", "dur_sec", "truncated", "output"]

V_BITRATE_K = 3000      # 目標視訊位元率（kbps）
V_MAXRATE_K = 4500      # 上限
A_BITRATE_K = 128
COVER_EST_KB = 60       # 封面每張估計（640 寬 jpg）

# out_range=tv：ZR 的來源是全範圍（yuvj420p），不轉的話輸出也會是全範圍、被標成 yuvj420p
#（第 2 輪試編實測）；網頁播放一律用標準範圍 yuv420p。
VF = ("scale=1280:720:force_original_aspect_ratio=decrease:out_range=tv,"
      "pad=1280:720:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p")
ENC_ARGS = {
    "nvidia": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "23",
               "-b:v", f"{V_BITRATE_K}k", "-maxrate", f"{V_MAXRATE_K}k", "-bufsize", f"{V_MAXRATE_K * 2}k",
               "-profile:v", "high", "-pix_fmt", "yuv420p"],
    "cpu": ["-c:v", "libx264", "-preset", "medium", "-crf", "22",
            "-maxrate", f"{V_MAXRATE_K}k", "-bufsize", f"{V_MAXRATE_K * 2}k",
            "-profile:v", "high", "-pix_fmt", "yuv420p"],
}


def die(msg):
    print("停止：" + msg, file=sys.stderr)
    sys.exit(2)


def inside_root(p):
    p = os.path.normcase(os.path.realpath(p))
    r = os.path.normcase(os.path.realpath(ROOT))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:          # 不同碟符（來源碟與專案在不同顆碟）→ 當然不在專案底下
        return False


def must_inside_root(p):
    if not inside_root(p):
        die(f"寫入目標不在專案資料夾底下：{p}")
    return p


# ---------------- 切片計畫 ----------------
def load_plan():
    if not os.path.isfile(PLAN):
        die(f"找不到切片計畫 {PLAN}（先跑 export_1025.py）")
    with io.open(PLAN, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        if rd.fieldnames != PLAN_COLS:
            die(f"切片計畫欄位不符：{rd.fieldnames}")
        rows = list(rd)
    if len(rows) != EXPECT_ROWS:
        die(f"切片計畫 {len(rows)} 列，應為 {EXPECT_ROWS}")
    seen = set()
    for i, r in enumerate(rows, 2):
        cid = r["clip_id"]
        if not cid or cid in seen:
            die(f"第 {i} 行片段編號空白或重複：{cid!r}")
        seen.add(cid)
        rel = r["src_rel_path"]
        if (not rel or rel.startswith(("/", "\\")) or ":" in rel or ".." in rel.replace("\\", "/").split("/")):
            die(f"第 {i} 行來源路徑不是碟根相對路徑：{rel!r}")
        try:
            a, tin, tout, d = (float(r[k]) for k in ("anchor_sec", "in_sec", "out_sec", "dur_sec"))
        except ValueError:
            die(f"第 {i} 行秒數不是數字")
        if not (0 <= tin <= a <= tout) or abs((tout - tin) - d) > 0.002 or d <= 0 or d > 8.0005:
            die(f"第 {i} 行入出點不合理：in={tin} anchor={a} out={tout} dur={d}")
        if r["truncated"] not in ("Y", "N"):
            die(f"第 {i} 行 truncated 不是 Y/N")
        if r["output"] != f"clips/{cid}.mp4":
            die(f"第 {i} 行輸出檔名不符規則：{r['output']!r}")
        r["_a"], r["_in"], r["_dur"] = a, tin, d
    return rows


# ---------------- 認碟 ----------------
def volume_label(root):
    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), buf, 261,
                                                      None, None, None, None, 0)
    return buf.value if ok else None


def whitelist_uuid(expect, label):
    """白名單：local/drive_expect.json 的 drives {卷標: uuid}。白名單外 → 停（不論乾跑或 --apply）。"""
    wl = expect.get("drives") or {}
    if not isinstance(wl, dict) or not wl:
        die("drive_expect.json 沒有 drives 白名單")
    if label not in wl:
        die(f"卷標 {label!r} 不在白名單（{'、'.join(sorted(wl))}）")
    return str(wl[label])


def find_drive(label, want_uuid):
    """回 (碟根 或 None, 說明)。只看本機固定碟與可移除碟（不碰網路碟）；
    先比 Windows 卷標，卷標對了才去讀那一顆的 _MAM/drive_id.json。"""
    if os.name != "nt":
        return None, "不是 Windows，不認碟"
    want_label = label
    hits = []
    for L in string.ascii_uppercase:
        root = f"{L}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) not in (2, 3):
            continue
        if volume_label(root) == want_label:
            hits.append(root)
    if not hits:
        return None, f"未接碟（沒有卷標 {want_label} 的本機碟）"
    if len(hits) > 1:
        return None, f"卷標 {want_label} 有 {len(hits)} 顆，不猜"
    return verify_root(hits[0], want_uuid)


def verify_root(root, want_uuid):
    """卷標已對上的那顆：讀 <碟根>/_MAM/drive_id.json，uuid 要相符。回 (碟根 或 None, 說明)。"""
    want_uuid = want_uuid.lower()
    p = os.path.join(root, "_MAM", "drive_id.json")
    try:
        with io.open(p, encoding="utf-8-sig") as f:
            got = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"卷標對、但讀不到 drive_id.json（{e}）"
    if str(got.get("drive_uuid", "")).lower() != want_uuid:
        return None, "卷標對、但 drive_id.json 的 uuid 不符"
    return root, "卷標與 uuid 都相符"


# ---------------- 編碼器 ----------------
def pick_encoder(ffmpeg, want, probe):
    """回 (實際用的編碼器, 說明)。probe=True 時實際以 1 秒測試畫面試編一次（輸出丟棄，不寫檔）。"""
    if want == "cpu":
        return "cpu", "指定 CPU（libx264）"
    if not ffmpeg:
        return "nvidia", "找不到 ffmpeg，無法檢查（乾跑照組 NVIDIA 指令）"
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return "cpu", f"查不到 ffmpeg 編碼器清單（{e}）→ 改用 CPU"
    if "h264_nvenc" not in out:
        return "cpu", "ffmpeg 沒有 h264_nvenc → 改用 CPU"
    if not probe:
        return "nvidia", "ffmpeg 有 h264_nvenc（乾跑不做試編）"
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-f", "lavfi", "-i", "color=c=black:s=1280x720:d=1",
                        "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode != 0:
        last = (r.stderr.strip().splitlines() or ["?"])[-1]
        return "cpu", f"h264_nvenc 試編失敗（{last}）→ 改用 CPU"
    return "nvidia", "h264_nvenc 試編成功"


# ---------------- 指令 ----------------
def build_cmds(ffmpeg, enc, fps, src_root, label, out_root, r):
    rel = r["src_rel_path"].replace("/", os.sep)
    src = os.path.join(src_root, rel) if src_root else os.path.join(f"<{label}>", rel)
    out_v = must_inside_root(os.path.join(out_root, "clips", r["clip_id"] + ".mp4"))
    out_c = must_inside_root(os.path.join(out_root, "covers", r["clip_id"] + ".jpg"))
    exe = ffmpeg or "ffmpeg"
    v = [exe, "-hide_banner", "-nostdin", "-n",
         "-ss", f"{r['_in']:.3f}", "-i", src, "-t", f"{r['_dur']:.3f}",
         "-map", "0:v:0", "-map", "0:a:0?", "-dn", "-sn",
         "-map_metadata", "-1", "-map_chapters", "-1",
         "-vf", VF.format(fps=fps), "-color_range", "tv"] + ENC_ARGS[enc] + [
         "-c:a", "aac", "-b:a", f"{A_BITRATE_K}k", "-ac", "2", "-ar", "48000",
         "-movflags", "+faststart", out_v]
    c = [exe, "-hide_banner", "-nostdin", "-n",
         "-ss", f"{r['_a']:.3f}", "-i", src, "-frames:v", "1",
         "-map_metadata", "-1", "-vf", "scale=640:-2,format=yuvj420p", "-q:v", "3", out_c]
    return src, v, c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的執行切片（預設乾跑）")
    ap.add_argument("--encoder", choices=["cpu", "nvidia"], default="nvidia")
    ap.add_argument("--fps", default="30000/1001", help="輸出影格率（預設 29.97）")
    ap.add_argument("--log", help="乾跑輸出另存一份（必須在專案資料夾底下）")
    ap.add_argument("--drive", required=True, help="來源碟卷標（必須在 drive_expect.json 白名單內）")
    ap.add_argument("--only", help="只處理這些片段編號（逗號分隔；試編用）")
    ap.add_argument("--out-dir", help="輸出根（必須在專案資料夾底下；預設＝專案根）")
    args = ap.parse_args()
    out_root = must_inside_root(os.path.abspath(args.out_dir)) if args.out_dir else ROOT

    lines = []

    def say(s=""):
        print(s)
        lines.append(s)

    rows = load_plan()
    if args.only:
        want = [s.strip() for s in args.only.split(",") if s.strip()]
        known = {r["clip_id"] for r in rows}
        bad = [w for w in want if w not in known]
        if bad:
            die(f"--only 有計畫裡沒有的片段編號：{bad}")
        rows = [r for r in rows if r["clip_id"] in want]
        say(f"只處理 {len(rows)} 段：{'、'.join(r['clip_id'] for r in rows)}")
    ntr = sum(1 for r in rows if r["truncated"] == "Y")
    total = sum(r["_dur"] for r in rows)
    say(f"切片計畫：{len(rows)} 列、截短 {ntr} 列、總長 {total:.1f} 秒")

    if not os.path.isfile(EXPECT):
        die(f"找不到認碟設定 {EXPECT}")
    with io.open(EXPECT, encoding="utf-8-sig") as f:
        expect = json.load(f)
    want_uuid = whitelist_uuid(expect, args.drive)
    src_root, why = find_drive(args.drive, want_uuid)
    if src_root:
        say(f"認碟：{args.drive} → {src_root}（{why}；uuid {want_uuid[:8]}…）")
    elif args.apply:
        die(f"認碟失敗：{why}")
    elif why.startswith("未接碟"):
        say(f"認碟：{why} → 未接碟，跳過（指令來源以 <{args.drive}> 代位）")
    else:
        die(f"認碟失敗：{why}")       # 碟在、但 uuid 不符／讀不到 → 乾跑也停
    if src_root and inside_root(src_root):
        die("來源碟在專案資料夾底下？不合理，停")

    ffmpeg = shutil.which("ffmpeg")
    enc, enc_why = pick_encoder(ffmpeg, args.encoder, probe=args.apply)
    say(f"編碼器：{enc}（{enc_why}）；輸出 1280x720 {args.fps} fps、H.264 4:2:0、AAC {A_BITRATE_K}k、faststart")

    cmds = []
    for r in rows:
        src, v, c = build_cmds(ffmpeg, enc, args.fps, src_root, args.drive, out_root, r)
        if src_root and not os.path.isfile(src):
            die(f"來源檔不在碟上：{r['src_rel_path']}")
        cmds.append((r, v, c))

    est_mb = total * (V_BITRATE_K + A_BITRATE_K) / 8 / 1000
    hi_mb = total * (V_MAXRATE_K + A_BITRATE_K) / 8 / 1000
    cov_mb = len(rows) * COVER_EST_KB / 1000
    say(f"預估容量：影片約 {est_mb:.0f} MB（上限約 {hi_mb:.0f} MB）＋封面約 {cov_mb:.0f} MB")

    if not args.apply:
        say("")
        say("── 乾跑：以下指令不執行 ──")
        for r, v, c in cmds:
            say(f"[{r['clip_id']}] 截短={r['truncated']}")
            say("  " + subprocess.list2cmdline(v))
            say("  " + subprocess.list2cmdline(c))
        say(f"── 共 {len(cmds)} 組（影片 {len(cmds)} 條＋封面 {len(cmds)} 條）──")
        if args.log:
            p = must_inside_root(os.path.abspath(args.log))
            with io.open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        return

    if not ffmpeg:
        die("找不到 ffmpeg")
    os.makedirs(must_inside_root(os.path.join(out_root, "clips")), exist_ok=True)
    os.makedirs(must_inside_root(os.path.join(out_root, "covers")), exist_ok=True)
    made = skipped = failed = 0
    t_all = time.time()
    for r, v, c in cmds:
        t0 = time.time()
        for cmd in (v, c):
            out = cmd[-1]
            must_inside_root(out)
            if os.path.exists(out):
                skipped += 1
                continue
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
                failed += 1
                if os.path.isfile(out):
                    os.remove(out)          # 半成品只會在專案資料夾內（上面已檢查）
                last = (p.stderr.strip().splitlines() or ["?"])[-1]
                print(f"失敗 [{r['clip_id']}] {os.path.basename(out)}：{last}", file=sys.stderr)
            else:
                made += 1
        print(f"[{r['clip_id']}] 完成 {time.time() - t0:.1f} 秒", flush=True)
    print(f"產出 {made}、已存在略過 {skipped}、失敗 {failed}；總耗時 {time.time() - t_all:.1f} 秒")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
