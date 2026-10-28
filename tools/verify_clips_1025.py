"""OPEN LAB 展示版：切片產出驗收（只讀產出，不碰來源碟）。

對 local/slice_plan_1025.csv 的每一列（或 --only 指定的）檢查：
  ・clips/<編號>.mp4 與 covers/<編號>.jpg 都在；目錄裡沒有計畫以外的檔
  ・影片 1280x720、h264、yuv420p、29.97 fps（30000/1001）、有 AAC 音軌、沒有資料／字幕／時間碼軌
  ・影片長度跟計畫的 dur_sec 相差不超過 1 格（1/29.97 秒）
  ・faststart：最上層 moov 在 mdat 前面
  ・中繼資料：容器與各軌的 tag 只能是白名單（品牌、編碼器名、handler、語言、vendor），
    不得有 creation_time、timecode、序號、型號、位置等；mvhd／tkhd 的建立時間必須是 0
  ・封面 jpg 寬 640、沒有 EXIF（APP1）
  ・最大單檔 < 25 MiB；印總容量、平均
不符 → 列出並退出碼 2。
用法：py tools\\verify_clips_1025.py [--dir <輸出根>] [--only E011_FX3,...]
"""
import argparse
import csv
import io
import json
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "local", "slice_plan_1025.csv")
FRAME = 1001 / 30000
MAX_BYTES = 25 * 1024 * 1024
TAG_OK = {"major_brand", "minor_version", "compatible_brands", "encoder", "handler_name",
          "vendor_id", "language"}


def ffprobe(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", p],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(r.stdout) if r.returncode == 0 else None


def top_atoms(p):
    """最上層 box 的 (type, offset) 清單。"""
    out, off = [], 0
    size_all = os.path.getsize(p)
    with open(p, "rb") as f:
        while off < size_all:
            f.seek(off)
            h = f.read(16)
            if len(h) < 8:
                break
            size, typ = struct.unpack(">I4s", h[:8])
            if size == 1:
                size = struct.unpack(">Q", h[8:16])[0]
            elif size == 0:
                size = size_all - off
            out.append((typ.decode("latin-1"), off))
            if size < 8:
                break
            off += size
    return out


_CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "udta", "edts"}


def create_times(p):
    """moov 底下 mvhd／tkhd／mdhd 的 creation／modification time。

    ★ 要照 box 結構逐層走，不能在整個檔案裡搜字串 —— 影像資料（mdat）裡碰巧會出現
      "mvhd"／"tkhd" 這幾個位元組，搜字串會把亂數當成時間（第 2 輪 115 段裡誤報 2 段）。"""
    with open(p, "rb") as f:
        data = f.read()
    vals = []

    def walk(start, end, path):
        off = start
        while off + 8 <= end:
            size, typ = struct.unpack(">I4s", data[off:off + 8])
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", data[off + 8:off + 16])[0]
                hdr = 16
            elif size == 0:
                size = end - off
            if size < 8:
                break
            t = typ.decode("latin-1")
            if t in ("mvhd", "tkhd", "mdhd"):
                b = off + hdr
                if data[b] == 1:
                    c, m = struct.unpack(">QQ", data[b + 4:b + 20])
                else:
                    c, m = struct.unpack(">II", data[b + 4:b + 12])
                vals.append((path + "/" + t, c, m))
            elif t in _CONTAINERS:
                walk(off + hdr, off + size, path + "/" + t)
            off += size

    walk(0, len(data), "")
    return vals


def jpeg_has_exif(p):
    with open(p, "rb") as f:
        d = f.read(65536)
    i = 2
    while i + 4 <= len(d) and d[i] == 0xFF:
        marker = d[i + 1]
        if marker == 0xDA:      # 影像資料開始，標頭段結束
            break
        seg = struct.unpack(">H", d[i + 2:i + 4])[0]
        if marker == 0xE1:      # APP1＝EXIF／XMP
            return True
        i += 2 + seg
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=ROOT, help="輸出根（底下有 clips、covers）")
    ap.add_argument("--only")
    ap.add_argument("--json-out", help="逐檔結果另存（專案內）")
    args = ap.parse_args()
    with io.open(PLAN, encoding="utf-8-sig", newline="") as f:
        plan = list(csv.DictReader(f))
    if args.only:
        want = set(s.strip() for s in args.only.split(","))
        plan = [r for r in plan if r["clip_id"] in want]
    cdir, vdir = os.path.join(args.dir, "covers"), os.path.join(args.dir, "clips")
    bad, sizes, recs = [], [], []
    want_ids = {r["clip_id"] for r in plan}
    for d, ext in ((vdir, ".mp4"), (cdir, ".jpg")):
        have = {os.path.splitext(n)[0] for n in os.listdir(d)} if os.path.isdir(d) else set()
        extra = sorted(n for n in (os.listdir(d) if os.path.isdir(d) else [])
                       if not n.endswith(ext) or os.path.splitext(n)[0] not in want_ids)
        if extra and not args.only:
            bad.append(("(目錄)", f"{os.path.basename(d)} 有計畫以外的檔 {len(extra)} 個：{extra[:5]}"))
        miss = sorted(want_ids - have)
        if miss:
            bad.append(("(目錄)", f"{os.path.basename(d)} 少了 {len(miss)} 個：{miss[:5]}"))
    for r in plan:
        cid = r["clip_id"]
        v, c = os.path.join(vdir, cid + ".mp4"), os.path.join(cdir, cid + ".jpg")
        errs = []
        if not os.path.isfile(v) or not os.path.isfile(c):
            continue                        # 上面已列「少了」
        info = ffprobe(v)
        if not info:
            bad.append((cid, "ffprobe 讀不動"))
            continue
        vs = [s for s in info["streams"] if s["codec_type"] == "video"]
        aus = [s for s in info["streams"] if s["codec_type"] == "audio"]
        others = [s["codec_type"] for s in info["streams"] if s["codec_type"] not in ("video", "audio")]
        if len(vs) != 1:
            errs.append(f"視訊軌 {len(vs)} 條")
        else:
            s = vs[0]
            if (s.get("width"), s.get("height")) != (1280, 720):
                errs.append(f"解析度 {s.get('width')}x{s.get('height')}")
            if s.get("codec_name") != "h264":
                errs.append(f"編碼 {s.get('codec_name')}")
            if s.get("pix_fmt") != "yuv420p":
                errs.append(f"像素格式 {s.get('pix_fmt')}")
            if s.get("r_frame_rate") != "30000/1001" or s.get("avg_frame_rate") != "30000/1001":
                errs.append(f"fps {s.get('r_frame_rate')}／{s.get('avg_frame_rate')}")
            vdur = float(s.get("duration") or 0)
            if abs(vdur - float(r["dur_sec"])) > FRAME + 1e-6:
                errs.append(f"長度 {vdur:.3f} vs 計畫 {r['dur_sec']}（差 {abs(vdur - float(r['dur_sec'])):.3f}）")
        if len(aus) != 1 or aus[0].get("codec_name") != "aac":
            errs.append(f"音軌 {[a.get('codec_name') for a in aus]}")
        if others:
            errs.append(f"多餘的軌 {others}")
        tags = dict(info["format"].get("tags", {}))
        for s in info["streams"]:
            tags.update({f"{s['codec_type']}:{k}": x for k, x in (s.get("tags") or {}).items()})
        badtags = sorted(k for k in tags if k.split(":")[-1] not in TAG_OK)
        if badtags:
            errs.append(f"中繼資料 tag 不在白名單：{badtags}")
        allct = create_times(v)
        n_mvhd = sum(1 for x in allct if x[0].endswith("mvhd"))
        n_tkhd = sum(1 for x in allct if x[0].endswith("tkhd"))
        if n_mvhd != 1 or n_tkhd < 2:       # 分母：讀不到就不能說「時間都是 0」
            errs.append(f"讀不到 mvhd／tkhd（mvhd {n_mvhd}、tkhd {n_tkhd}）")
        ct = [x for x in allct if x[1] or x[2]]
        if ct:
            errs.append(f"box 建立／修改時間不是 0：{ct[:3]}")
        atoms = [a for a, _ in top_atoms(v)]
        if "moov" not in atoms or "mdat" not in atoms or atoms.index("moov") > atoms.index("mdat"):
            errs.append(f"不是 faststart（最上層 {atoms}）")
        ci = ffprobe(c)
        cw = ci["streams"][0].get("width") if ci else None
        if cw != 640:
            errs.append(f"封面寬 {cw}")
        if jpeg_has_exif(c):
            errs.append("封面有 EXIF")
        vb, cb = os.path.getsize(v), os.path.getsize(c)
        if vb >= MAX_BYTES:
            errs.append(f"影片 {vb} bytes ≥ 25 MiB")
        sizes.append((cid, vb, cb))
        recs.append({"clip_id": cid, "bytes": vb, "cover_bytes": cb,
                     "wh": f"{vs[0].get('width')}x{vs[0].get('height')}" if vs else "",
                     "codec": vs[0].get("codec_name") if vs else "", "pix_fmt": vs[0].get("pix_fmt") if vs else "",
                     "fps": vs[0].get("r_frame_rate") if vs else "", "dur": vs[0].get("duration") if vs else "",
                     "plan_dur": r["dur_sec"], "bit_rate": info["format"].get("bit_rate"),
                     "audio": aus[0].get("codec_name") if aus else "", "tags": sorted(tags)})
        if errs:
            bad.append((cid, "；".join(errs)))
    if sizes:
        tv = sum(s[1] for s in sizes)
        tc = sum(s[2] for s in sizes)
        mx = max(sizes, key=lambda s: s[1])
        print(f"驗收 {len(sizes)} 段：影片合計 {tv / 1e6:.1f} MB（{tv / 2**20:.1f} MiB）、平均 {tv / len(sizes) / 1e6:.2f} MB、"
              f"最大 {mx[0]} {mx[1] / 2**20:.2f} MiB；封面合計 {tc / 1e6:.2f} MB")
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=1)
    for cid, m in bad:
        print(f"不符：{cid}：{m}")
    print("全部通過" if not bad else f"不符 {len(bad)} 項")
    sys.exit(2 if bad else 0)


if __name__ == "__main__":
    main()
