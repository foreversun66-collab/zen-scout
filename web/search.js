/* OPEN LAB 展示版：球賽事件搜尋（純前端 JavaScript）
 *
 * 來源：ClaudeCode repo 的 MAM_Central/gas/Code.gs（MAM 中央索引「手機雲端查詢」的 Apps Script，
 *       2026-09-09 那一版）。
 * 改寫範圍：
 *   - 只取 getMeta() 與 searchGames() 的搜尋規則；Apps Script 專屬的部分
 *     （doGet、HtmlService、DriveApp／PropertiesService 找資料檔）全部拿掉，
 *     資料改由呼叫端傳入（data/events_1025.json 讀進來的物件）。
 *   - 資料格式從 search_export.json 的 game_rows（欄位陣列）改成 events_1025.json 的事件物件。
 *   - 保留的規則：
 *       ・球員清單＝得分者 ∪ 助攻者；動作清單從資料推導，有助攻者時多一個虛擬動作「助攻」
 *       ・動作選「助攻」時，球員欄比對助攻者（「某人 ＋ 助攻」＝某人助攻的那些）
 *       ・關鍵字也搜得到「助攻」兩個字（有助攻者的事件）
 *   - 拿掉的規則：碟別篩選、人工排除（展示資料只有一場、沒有排除列）、依日期排序
 *     （改成依事件編號＝比賽發生順序）。
 *   - 新增：節次篩選（period）。
 * 不含任何資料夾 ID、試算表 ID、網址或 token。
 */
(function (root) {
  'use strict';

  var ACTION_ASSIST = '助攻';

  function eventTitle(e) {
    return e.player + ' ' + e.type + (e.assist ? '（' + e.assist + '助攻）' : '');
  }

  /** 頁面載入時：球員、動作、節次的選項與總數（從資料本身推導，不維護第二份清單） */
  function getMeta(data) {
    var ev = (data && data.events) || [];
    var players = {}, actions = {}, periods = {}, anyAssist = false;
    for (var i = 0; i < ev.length; i++) {
      if (ev[i].player) players[ev[i].player] = 1;
      if (ev[i].assist) { players[ev[i].assist] = 1; anyAssist = true; }
      if (ev[i].type) actions[ev[i].type] = 1;
      if (ev[i].period) periods[ev[i].period] = 1;
    }
    var acts = Object.keys(actions).sort();
    if (anyAssist) acts.push(ACTION_ASSIST);
    return {
      game: data ? data.game : null,
      total: ev.length,
      players: Object.keys(players).sort(),
      actions: acts,
      periods: Object.keys(periods).sort()
    };
  }

  /** 事件搜尋。p = { q, player, action, period, limit } */
  function searchEvents(data, p) {
    p = p || {};
    var ev = (data && data.events) || [];
    var q = String(p.q || '').toLowerCase();
    var player = p.player || '';
    var action = p.action || '';
    var period = p.period || '';
    var limit = Math.min(Number(p.limit || 100), 300);
    var assistMode = (action === ACTION_ASSIST);

    var hits = [];
    for (var i = 0; i < ev.length; i++) {
      var e = ev[i];
      if (player && (assistMode ? e.assist : e.player) !== player) continue;
      if (action) {
        if (assistMode) { if (!e.assist) continue; }
        else if (e.type !== action) continue;
      }
      if (period && e.period !== period) continue;
      if (q) {
        // 有助攻者時把「助攻」也放進可搜尋的字串，否則搜「助攻」會 0 筆
        var hay = (eventTitle(e) + ' ' + e.period + ' ' + e.clock + (e.assist ? ' 助攻' : '')).toLowerCase();
        if (hay.indexOf(q) < 0) continue;
      }
      hits.push(e);
    }
    hits.sort(function (a, b) { return a.id < b.id ? -1 : (a.id > b.id ? 1 : 0); });
    return { total: hits.length, items: hits.slice(0, limit) };
  }

  var api = { getMeta: getMeta, searchEvents: searchEvents, eventTitle: eventTitle, ACTION_ASSIST: ACTION_ASSIST };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OpenLabSearch = api;
})(typeof window !== 'undefined' ? window : this);
