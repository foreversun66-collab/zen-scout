"""OPEN LAB 展示版：匯出 1025（例行賽02 vs 國王）事件。

讀「中央索引快照」的 annotations.db / catalog.db（**請先複製到暫存夾再給路徑**，不直接開雲端硬碟上的檔）。
產出兩份：
  data/events_1025.json        會公開：事件＋機位片段編號（不含碟符、uuid、絕對路徑、來源檔名）
  local/slice_plan_1025.csv    不公開：每列一個機位片段的來源相對路徑與入出點（給 slice_1025.py）

核對（不符就停、不寫檔）：事件 49、片段 115、修正／排除 0 筆（有的話本程式沒處理，要先改程式）。

用法：py tools\\export_1025.py --db-dir <快照複本資料夾>
"""
import argparse
import csv
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 專案根（tools 的上一層）

GAME_ID = "2627_20261025_國王"
MEDIA_DIR = "例行賽/1025例行賽2vs國王"
EXPECT_EVENTS = 49
EXPECT_CLIPS = 115
PRE_SEC = 5.0      # 入點＝錨點前 5 秒
POST_SEC = 3.0     # 出點＝錨點後 3 秒

# 機位：資料庫的 camera 代碼 → (公開名稱, 片段編號尾碼, 該機位應在的資料夾)
# 資料夾也要對得上，對不上就停（代碼和資料夾不一致＝這張表的前提錯了）
CAMS = {
    "M":   ("FX3",   "FX3",  "CLIP"),
    "C":   ("A7R V", "A7RV", "A7R5"),
    "DSC": ("ZR",    "ZR",   "101NC_ZR"),
}
CAM_ORDER = ["M", "C", "DSC"]
PERIOD = {"1st": "Q1", "2nd": "Q2", "3rd": "Q3", "4th": "Q4"}


def die(msg):
    print("停止：" + msg, file=sys.stderr)
    sys.exit(2)


def inside_root(p):
    p = os.path.normcase(os.path.realpath(p))
    r = os.path.normcase(os.path.realpath(ROOT))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:          # 不同碟符 → 不在專案底下
        return False


def ro(path):
    if not os.path.isfile(path):
        die(f"找不到 {path}")
    return sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", required=True, help="快照複本資料夾（含 annotations.db、catalog.db）")
    args = ap.parse_args()

    ann = ro(os.path.join(args.db_dir, "annotations.db"))
    cat = ro(os.path.join(args.db_dir, "catalog.db"))

    g = ann.execute("select dea_side, opponent, game_date, media_dir from games where game_id=?",
                    (GAME_ID,)).fetchone()
    if not g:
        die(f"快照裡沒有場次 {GAME_ID}")
    dea_side, opponent, game_date, media_dir = g
    if media_dir != MEDIA_DIR:
        die(f"場次資料夾不符：{media_dir!r}")
    if dea_side not in ("L", "R"):
        die(f"dea_side 不是 L/R：{dea_side!r}")

    for t in ("game_event_fix", "game_event_excluded"):
        n = ann.execute(f"select count(*) from {t} where game_id=?", (GAME_ID,)).fetchone()[0]
        if n:
            die(f"{t} 有 {n} 筆 —— 本程式沒有套用修正／排除，先改程式再匯出")

    events = ann.execute(
        "select pbp_row, period, pbp_clock, action, player, assist_by, score_l, score_r, screen_clock "
        "from game_events where game_id=? order by pbp_row", (GAME_ID,)).fetchall()
    clips = ann.execute(
        "select pbp_row, camera, rel_path, offset_in_clip from game_event_clips where game_id=?",
        (GAME_ID,)).fetchall()
    if len(events) != EXPECT_EVENTS:
        die(f"事件數 {len(events)}，應為 {EXPECT_EVENTS}")
    if len(clips) != EXPECT_CLIPS:
        die(f"片段數 {len(clips)}，應為 {EXPECT_CLIPS}")

    # 來源檔長度：catalog 的 path 用反斜線
    dur = {}
    for p, d in cat.execute("select path, duration from clips where folder like ?",
                            (MEDIA_DIR.replace("/", "\\") + "%",)):
        dur[p.replace("\\", "/")] = d

    by_ev = {}
    for row, cam, rel, off in clips:
        if cam not in CAMS:
            die(f"未知機位代碼 {cam!r}（事件 {row}）")
        parts = rel.split("/")
        if "/".join(parts[:-2]) != MEDIA_DIR or parts[-2] != CAMS[cam][2]:
            die(f"機位 {cam} 的來源資料夾不符：{rel}")
        if rel not in dur or not dur[rel]:
            die(f"catalog 找不到來源檔長度：{rel}")
        if off is None or off < 0 or off > dur[rel]:
            die(f"錨點秒數超出來源檔：{rel} @ {off}")
        by_ev.setdefault(row, {})
        if cam in by_ev[row]:
            die(f"事件 {row} 同一機位有兩個片段")
        by_ev[row][cam] = (rel, off)

    ev_out, plan = [], []
    for row, period, pclock, action, player, assist, sl, sr, sclock in events:
        if row not in by_ev:
            die(f"事件 {row} 沒有任何機位片段")
        eid = f"E{row:03d}"
        dea, opp = (sr, sl) if dea_side == "R" else (sl, sr)
        item = {
            "id": eid,
            "type": action,
            "player": player,
            "assist": assist or "",
            "period": PERIOD.get(period, period),
            # 顯示用時鐘＝畫面時鐘優先、沒有才用 PBP 時鐘（與中央索引 snapshot_tool 的 game_rows 同一規則）
            "clock": sclock or pclock,
            "pbp_clock": pclock,
            # 比分欄：team＝我方、opp＝對手（公開檔不出現隊名縮寫，第 4 輪改名）
            "score": None if dea is None or opp is None else {"team": dea, "opp": opp},
            "clips": [],
        }
        for cam in CAM_ORDER:
            if cam not in by_ev[row]:
                continue
            rel, off = by_ev[row][cam]
            name, suffix, _ = CAMS[cam]
            cid = f"{eid}_{suffix}"
            d = dur[rel]
            tin = max(0.0, off - PRE_SEC)
            tout = min(d, off + POST_SEC)
            # t_event：事件瞬間在片段裡的第幾秒（一般＝5；截在檔頭的會比較早）。網頁切換機位時對齊用。
            item["clips"].append({"id": cid, "camera": name, "t_event": round(off - tin, 3),
                                  "video": f"clips/{cid}.mp4", "cover": f"covers/{cid}.jpg"})
            trunc = (off - PRE_SEC < 0) or (off + POST_SEC > d)
            plan.append({
                "clip_id": cid, "src_rel_path": rel, "anchor_sec": f"{off:.3f}",
                "in_sec": f"{tin:.3f}", "out_sec": f"{tout:.3f}", "dur_sec": f"{tout - tin:.3f}",
                "truncated": "Y" if trunc else "N", "output": f"clips/{cid}.mp4",
            })
        ev_out.append(item)

    out_json = os.path.join(ROOT, "data", "events_1025.json")
    out_csv = os.path.join(ROOT, "local", "slice_plan_1025.csv")
    for p in (out_json, out_csv):
        if not inside_root(p):
            die(f"寫入目標不在專案資料夾底下：{p}")

    doc = {
        "game": {"id": "1025", "title": "2026-27 例行賽02 vs 國王", "date": game_date,
                 "opponent": opponent},
        "cameras": [CAMS[c][0] for c in CAM_ORDER],
        "event_count": len(ev_out),
        "clip_count": len(plan),
        "events": ev_out,
    }
    with open(out_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(plan[0].keys()))
        w.writeheader()
        w.writerows(plan)

    ntr = sum(1 for p in plan if p["truncated"] == "Y")
    print(f"事件 {len(ev_out)}／片段 {len(plan)}／截短 {ntr}")
    print("寫出：data/events_1025.json、local/slice_plan_1025.csv")


if __name__ == "__main__":
    main()
