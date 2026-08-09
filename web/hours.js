// OpenStreetMap `opening_hours` — parsing, "open now", and a localized week
// view (German default, Czech and English via the `lang` parameter).
//
// The real opening_hours grammar is huge (holidays, month and week ranges,
// "sunset+01:00", year selectors). This handles the subset venues actually use —
// weekday selectors plus clock ranges — and returns null for everything else, so
// the UI can fall back to printing the raw tag. A wrong "Jetzt geöffnet" that
// sends someone across town costs more than an uninterpreted string.

const DAY_INDEX = {
  mo: 0, tu: 1, we: 2, th: 3, fr: 4, sa: 5, su: 6,
  di: 1, mi: 2, do: 3, so: 6,   // German abbreviations turn up in German tags
};
export const DAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const DAY = 1440;  // minutes

// Display strings per UI language. Kept here (not in i18n.js) so this module
// stays dependency-free and its tests run without the rest of the app.
const LOCALES = {
  de: { days: DAY_LABELS,
        openNow: "Jetzt geöffnet", until: "bis", closed: "Geschlossen", opens: "öffnet",
        today: "heute", tomorrow: "morgen",
        dayClosed: "geschlossen", allDay: "durchgehend geöffnet", from: "ab" },
  cs: { days: ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"],
        openNow: "Nyní otevřeno", until: "do", closed: "Zavřeno", opens: "otevírá",
        today: "dnes", tomorrow: "zítra",
        dayClosed: "zavřeno", allDay: "otevřeno nonstop", from: "od" },
  en: { days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        openNow: "Open now", until: "until", closed: "Closed", opens: "opens",
        today: "today", tomorrow: "tomorrow",
        dayClosed: "closed", allDay: "open 24 hours", from: "from" },
};
const locale = (lang) => LOCALES[lang] || LOCALES.de;

const pad = (n) => String(n).padStart(2, "0");
export const formatMinutes = (m) => `${pad(Math.floor((m % DAY) / 60))}:${pad(m % 60)}`;

// "" -> every day. "Mo-Fr,Su" -> [0,1,2,3,4,6]. Wrapping ranges (Sa-Mo) work.
// Holiday selectors are dropped from the list ("Mo-Su,PH" is just Mo-Su), so a
// rule that names *only* holidays comes back empty and gets skipped.
function parseDays(sel) {
  if (!sel) return [0, 1, 2, 3, 4, 5, 6];
  const out = new Set();
  for (const part of sel.split(",")) {
    const p = part.trim().toLowerCase();
    if (!p || p === "ph" || p === "sh") continue;
    const m = p.match(/^([a-z]{2})(?:\s*-\s*([a-z]{2}))?$/);
    if (!m) return null;
    const from = DAY_INDEX[m[1]];
    const to = m[2] === undefined ? from : DAY_INDEX[m[2]];
    if (from === undefined || to === undefined) return null;
    for (let i = from; ; i = (i + 1) % 7) {
      out.add(i);
      if (i === to) break;
    }
  }
  return [...out];
}

// "17:00-01:00,12:00-14:00" -> [[1020,1500],[720,840]] sorted by start. A range
// ending at or before its start closes after midnight and runs past 1440; the
// open-ended form ("18:00+", "no closing time given") ends in null.
function parseRanges(str) {
  const out = [];
  for (const part of str.split(",")) {
    const p = part.trim();
    const open = p.match(/^(\d{1,2}):(\d{2})(?:\s*-\s*\d{1,2}:\d{2})?\s*\+$/);
    if (open) {
      const start = +open[1] * 60 + +open[2];
      if (start >= DAY) return null;
      out.push([start, null]);
      continue;
    }
    const m = p.match(/^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$/);
    if (!m) return null;
    const start = +m[1] * 60 + +m[2];
    let end = +m[3] * 60 + +m[4];
    if (start >= DAY || end > DAY) return null;
    if (end <= start) end += DAY;
    out.push([start, end]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}

// Mappers routinely write ',' where the grammar wants ';'
// ("Su-Th 18:00-01:00, Fr,Sa 18:00-02:00"). A comma only starts a new rule when
// what came before is already complete (it contains a time) and what follows
// names a day — which leaves genuine lists, "12:00-15:00,17:30-22:00" and
// "Mo-Su,PH 11:00-23:00", intact.
function splitRules(text) {
  const rules = [];
  for (const chunk of text.split(";")) {
    let buf = "";
    for (const part of chunk.split(",")) {
      if (buf && /\d/.test(buf) && /^\s*[A-Za-z]{2}\b/.test(part)) {
        rules.push(buf);
        buf = part;
      } else {
        buf = buf ? `${buf},${part}` : part;
      }
    }
    if (buf.trim()) rules.push(buf);
  }
  return rules;
}

/** Parse a raw tag into `{ days: [[ [start,end], … ] × 7], raw }`, Monday
 *  first, minutes from midnight. Returns null when the syntax is out of scope. */
export function parseOpeningHours(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  const days = [[], [], [], [], [], [], []];
  let parsed = false;
  for (const rule of splitRules(text)) {
    const r = rule.trim();
    if (!r) continue;
    if (/^24\/7$/i.test(r)) {
      for (let i = 0; i < 7; i++) days[i] = [[0, DAY]];
      parsed = true;
      continue;
    }
    let sel, ranges;
    const off = r.match(/^([^\d]*?)\s*(?:off|closed|geschlossen)$/i);
    if (off) {
      sel = off[1];
      ranges = [];
    } else {
      const firstDigit = r.search(/\d/);
      if (firstDigit < 0) return null;  // neither a time range nor a closure
      sel = r.slice(0, firstDigit);
      ranges = parseRanges(r.slice(firstDigit));
      if (!ranges) return null;
    }
    const idx = parseDays(sel.trim().replace(/,$/, ""));
    if (!idx) return null;
    // Holiday-only rules ("PH off", "PH 12:00-18:00") leave the weekly grid
    // alone rather than failing the whole tag.
    if (!idx.length) continue;
    for (const d of idx) days[d] = ranges;  // a later rule overrides those days
    parsed = true;
  }
  return parsed ? { days, raw: text } : null;
}

/** Where the venue stands right now: `{ open, until }` (until = null when the
 *  tag gives no closing time) or `{ open: false, at, nextDay, nextIn }`
 *  (nextIn = days ahead). Local device time — the map is German and so are its
 *  users' clocks. */
export function openState(schedule, now = new Date()) {
  if (!schedule) return null;
  const today = (now.getDay() + 6) % 7;  // JS weeks start on Sunday, ours on Monday
  const min = now.getHours() * 60 + now.getMinutes();
  for (const [start, end] of schedule.days[today])
    if (min >= start && (end === null || min < end))
      return { open: true, until: end === null ? null : end % DAY };
  // Yesterday's after-midnight tail still counts as open.
  for (const [, end] of schedule.days[(today + 6) % 7])
    if (end !== null && end > DAY && min < end - DAY) return { open: true, until: end % DAY };
  for (let ahead = 0; ahead < 8; ahead++) {
    const d = (today + ahead) % 7;
    for (const [start] of schedule.days[d])
      if (ahead > 0 || start > min)
        return { open: false, at: start, nextDay: d, nextIn: ahead };
  }
  return { open: false };  // never open on any day
}

export function statusText(state, lang = "de") {
  if (!state) return "";
  const L = locale(lang);
  if (state.open)
    return state.until === null
      ? L.openNow
      : `${L.openNow} · ${L.until} ${formatMinutes(state.until)}`;
  if (state.at === undefined) return L.closed;
  const when = state.nextIn === 0 ? L.today
    : state.nextIn === 1 ? L.tomorrow
    : L.days[state.nextDay];
  return `${L.closed} · ${L.opens} ${when} ${formatMinutes(state.at)}`;
}

const dayText = (ranges, L) => {
  if (!ranges.length) return L.dayClosed;
  if (ranges.some(([s, e]) => e !== null && e - s >= DAY)) return L.allDay;
  return ranges
    .map(([s, e]) => (e === null ? `${L.from} ${formatMinutes(s)}` : `${formatMinutes(s)}–${formatMinutes(e)}`))
    .join(", ");
};

/** The week as display rows, consecutive identical days folded together:
 *  `[{ label: "Mo–Do", text: "17:00–01:00" }, { label: "So", text: "geschlossen" }]` */
export function formatWeek(schedule, lang = "de") {
  if (!schedule) return [];
  const L = locale(lang);
  const groups = [];
  for (let i = 0; i < 7; i++) {
    const text = dayText(schedule.days[i], L);
    const last = groups[groups.length - 1];
    if (last && last.text === text) last.end = i;
    else groups.push({ start: i, end: i, text });
  }
  return groups.map((g) => ({
    label: g.start === g.end ? L.days[g.start]
      : g.end === g.start + 1 ? `${L.days[g.start]}, ${L.days[g.end]}`
      : `${L.days[g.start]}–${L.days[g.end]}`,
    text: g.text,
  }));
}
