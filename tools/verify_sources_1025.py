"""OPEN LAB 展示版：切片前的來源比對（唯讀）。

對 local/slice_plan_1025.csv 用到的每一支來源檔：
  (a) 來源碟上同一個碟根相對路徑是否存在
  (b) 跟參考索引（中央索引快照的 catalog.db 複本）比：
        大小：round(位元組 / 2^20, 2) 要等於 catalog 的 size_mb（scanner 就是這樣算的；精度約 10 KB）
        長度：ffprobe 的 format 長度跟 catalog 的 duration 相差不超過 1 格（1/fps）
  (c) 來源碟自己的 catalog.db 複本若有這支的紀錄，一樣比大小與長度
另外把每支的大小（位元組）與修改時間（ns）寫進 --state-out，給「切片前後來源沒被動過」比對用。

只讀來源碟；資料庫一律開複本（呼叫端先複製到暫存夾）。任何一支不符 → 退出碼 2。
用法：py tools\\verify_sources_1025.py --drive DEA_SSD1 --ref-catalog <複本> --drive-catalog <複本> --state-out local\\src_state_before.csv
"""
import argparse
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
from fractions import Fraction

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slice_1025 as sl   # noqa: E402  認碟、切片計畫、寫入守門共用同一份

EXPECT_SOURCES = 77


def ro_catalog(p):
    if not p:
        return {}
    if not os.path.isfile(p):
        sl.die(f"找不到 catalog 複本 {p}")
    con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
    out = {}
    for path, size_mb, dur, fps in con.execute("select path, size_mb, duration, fps from clips"):
        out[path.replace("\\", "/")] = (size_mb, dur, fps)
    con.close()
    return out


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=index,codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate",
                        "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drive", required=True)
    ap.add_argument("--ref-catalog", required=True, help="參考索引 catalog.db 的複本（中央索引快照）")
    ap.add_argument("--drive-catalog", help="來源碟自己的 catalog.db 複本（可省略）")
    ap.add_argument("--state-out", required=True, help="大小／修改時間紀錄（專案內 csv）")
    args = ap.parse_args()

    with io.open(sl.EXPECT, encoding="utf-8-sig") as f:
        expect = json.load(f)
    root, why = sl.find_drive(args.drive, sl.whitelist_uuid(expect, args.drive))
    if not root:
        sl.die(f"認碟失敗：{why}")
    print(f"認碟：{args.drive} → {root}（{why}）")
    rows = sl.load_plan()
    srcs = sorted({r["src_rel_path"] for r in rows})
    print(f"切片計畫用到的來源檔：{len(srcs)} 支（預期 {EXPECT_SOURCES}）")
    if len(srcs) != EXPECT_SOURCES:
        sl.die(f"來源檔數 {len(srcs)}，應為 {EXPECT_SOURCES}")

    ref = ro_catalog(args.ref_catalog)
    own = ro_catalog(args.drive_catalog)
    state_out = sl.must_inside_root(os.path.abspath(args.state_out))

    n_exist = n_ok = 0
    bad, state = [], []
    for rel in srcs:
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            bad.append((rel, "(a) 來源碟上沒有這支"))
            continue
        n_exist += 1
        st = os.stat(p)
        mb = round(st.st_size / (1024 * 1024), 2)
        info = ffprobe(p)
        if info is None:
            bad.append((rel, "ffprobe 讀不動"))
            continue
        dur = float(info["format"]["duration"])
        v = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
        a = [s for s in info["streams"] if s.get("codec_type") == "audio"]
        fps = float(Fraction(v.get("r_frame_rate", "0/1"))) if v else 0
        frame = 1 / fps if fps else 0.05
        errs = []
        if rel not in ref:
            errs.append("(b) 參考索引沒有這支")
        else:
            r_mb, r_dur, _ = ref[rel]
            if r_mb is None or abs(mb - r_mb) > 1e-9:
                errs.append(f"(b) 大小 {mb} MiB ≠ 參考 {r_mb}")
            if r_dur is None or abs(dur - r_dur) > frame + 1e-6:
                errs.append(f"(b) 長度 {dur:.3f} ≠ 參考 {r_dur}（差 {abs(dur - (r_dur or 0)):.3f} 秒，1 格 {frame:.4f}）")
        own_note = "無紀錄"
        if rel in own:
            o_mb, o_dur, _ = own[rel]
            own_note = "相符"
            if o_mb is None or abs(mb - o_mb) > 1e-9:
                errs.append(f"(c) 大小 {mb} MiB ≠ 碟上索引 {o_mb}")
                own_note = "不符"
            if o_dur is None or abs(dur - o_dur) > frame + 1e-6:
                errs.append(f"(c) 長度 {dur:.3f} ≠ 碟上索引 {o_dur}")
                own_note = "不符"
        if errs:
            bad.append((rel, "；".join(errs)))
        else:
            n_ok += 1
        state.append({"src_rel_path": rel, "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                      "duration": f"{dur:.6f}", "vcodec": v.get("codec_name", ""), "profile": v.get("profile", ""),
                      "pix_fmt": v.get("pix_fmt", ""), "wh": f"{v.get('width')}x{v.get('height')}",
                      "fps": v.get("r_frame_rate", ""), "audio": "/".join(s.get("codec_name", "") for s in a),
                      "own_catalog": own_note})

    with io.open(state_out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(state[0].keys()) if state else ["src_rel_path"])
        w.writeheader()
        w.writerows(state)
    n_own = sum(1 for s in state if s["own_catalog"] != "無紀錄")
    print(f"存在 {n_exist}/{len(srcs)}；相符 {n_ok}/{len(srcs)}；碟上索引有紀錄 {n_own} 支")
    for rel, msg in bad:
        print(f"不符：{rel}：{msg}")
    print(f"狀態紀錄：{os.path.relpath(state_out, sl.ROOT)}")
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
