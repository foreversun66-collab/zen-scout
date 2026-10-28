# OPEN LAB 展示版

範例場次：2026-27 例行賽02 vs 國王（2026-10-25），只用球團自拍機位 FX3／A7R V／ZR，不用轉播畫面。

這是一個**獨立的專案**：程式是從 MAM 改寫過來的，正式 MAM 與它的 git 倉庫都不會被動到。

## 資料夾

| 資料夾 | 內容 | 公開 | 進版控 |
|---|---|---|---|
| `web/` | `index.html`（搜尋＋播放頁）、`search.js`（事件搜尋邏輯） | 是 | 是 |
| `data/` | `events_1025.json`：49 個事件、115 個機位片段 | 是 | 是 |
| `clips/`、`covers/` | 720p 片段 115 支、封面 115 張（由 `slice_1025.py --apply` 產生） | 是 | 否（約 550 MB） |
| `tools/` | 匯出、切片、驗收、掃描、本機預覽 | 否 | 是 |
| `local/` | 切片計畫（含來源路徑）、認碟白名單、掃描詞表、測試與證據 | 否 | **否** |

## 流程

1. `py tools\export_1025.py --db-dir <快照複本資料夾>`：從中央索引快照的**複本**產出 `data/events_1025.json` 和 `local/slice_plan_1025.csv`
   - 核對事件數 49、片段數 115
2. `py tools\verify_sources_1025.py --drive DEA_SSD1 --ref-catalog <複本> --drive-catalog <複本> --state-out local\src_state_before.csv`：切片前比對 77 支來源檔（唯讀）
3. `py tools\slice_1025.py --drive DEA_SSD1`：乾跑，認碟並印出指令
   - 加 `--apply` 才真的切
   - 認碟用白名單（卷標 → uuid），不寫死碟符
4. `py tools\verify_clips_1025.py`：驗收產出（規格、長度、faststart、中繼資料、大小）
5. `py tools\scan_secrets.py`：機敏掃描，`web/`、`data/` 必須 0 筆
6. `py tools\serve_local.py`：本機預覽，只綁 127.0.0.1，網址 `http://127.0.0.1:4780/web/index.html`
   - 不提供 `local/`、`tools/`、`.git/`
