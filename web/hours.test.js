import { test } from "node:test";
import assert from "node:assert/strict";
import { parseOpeningHours, openState, statusText, formatWeek, venueSchedule, venuesOpenNow,
         scheduleToGrid, gridToOpeningHours }
  from "./hours.js";

// Monday 2026-08-03 18:30 local, unless a test says otherwise.
const at = (iso) => new Date(iso);

test("parses weekday ranges and per-day time lists", () => {
  const s = parseOpeningHours("Mo-Fr 08:00-12:00,13:00-18:00; Sa 09:00-14:00");
  assert.deepEqual(s.days[0], [[480, 720], [780, 1080]]);
  assert.deepEqual(s.days[5], [[540, 840]]);
  assert.deepEqual(s.days[6], []);  // Sunday never mentioned -> closed
});

test("a range ending past midnight runs beyond 1440", () => {
  const s = parseOpeningHours("Mo-Su 17:00-01:00");
  assert.deepEqual(s.days[0], [[1020, 1500]]);
});

test("24/7 opens every day", () => {
  const s = parseOpeningHours("24/7");
  assert.equal(s.days.filter((d) => d.length === 1).length, 7);
  assert.deepEqual(s.days[3], [[0, 1440]]);
});

test("a later rule overrides the days it names", () => {
  const s = parseOpeningHours("Mo-Su 11:00-23:00; Tu off");
  assert.deepEqual(s.days[1], []);
  assert.deepEqual(s.days[2], [[660, 1380]]);
});

test("holiday rules are ignored, not fatal", () => {
  const s = parseOpeningHours("Mo-Fr 10:00-20:00; PH off");
  assert.deepEqual(s.days[0], [[600, 1200]]);
});

test("holidays inside a day list are dropped, the weekdays stay", () => {
  assert.deepEqual(parseOpeningHours("Mo-Su,PH 11:00-23:00").days[6], [[660, 1380]]);
  assert.deepEqual(parseOpeningHours("PH,Mo-Su 11:00-23:00").days[0], [[660, 1380]]);
  assert.deepEqual(parseOpeningHours("Mo-Fr 11:00-22:00; Sa,Su,PH 12:00-22:00").days[5],
                   [[720, 1320]]);
});

test("a comma used where the grammar wants a semicolon still parses", () => {
  const s = parseOpeningHours("Su-Th 18:00-01:00, Fr,Sa 18:00-02:00");
  assert.deepEqual(s.days[0], [[1080, 1500]]);   // Mo, from the Su-Th rule
  assert.deepEqual(s.days[4], [[1080, 1560]]);   // Fr, from the second rule
  // …while a genuine list of time ranges is left alone.
  assert.deepEqual(parseOpeningHours("Tu-Sa 12:00-15:00,17:30-22:00").days[1],
                   [[720, 900], [1050, 1320]]);
});

test("open-ended times ('18:00+') parse as a start without a close", () => {
  const s = parseOpeningHours("Mo-Sa 18:00+");
  assert.deepEqual(s.days[0], [[1080, null]]);
  assert.deepEqual(formatWeek(s), [
    { label: "Mo–Sa", text: "ab 18:00" },
    { label: "So", text: "geschlossen" },
  ]);
  assert.equal(statusText(openState(s, at("2026-08-03T19:00"))), "Jetzt geöffnet");
  assert.equal(statusText(openState(s, at("2026-08-03T09:00"))),
               "Geschlossen · öffnet heute 18:00");
});

test("German day abbreviations and a bare time range parse", () => {
  assert.deepEqual(parseOpeningHours("Mo-So 12:00-22:00").days[6], [[720, 1320]]);
  assert.deepEqual(parseOpeningHours("10:00-18:00").days[6], [[600, 1080]]);
});

test("out-of-scope syntax returns null so the raw tag can be shown", () => {
  assert.equal(parseOpeningHours("Apr-Oct Mo-Su 10:00-20:00"), null);
  assert.equal(parseOpeningHours("Mo-Fr sunrise-sunset"), null);
  assert.equal(parseOpeningHours("nach Vereinbarung"), null);
  assert.equal(parseOpeningHours(""), null);
  assert.equal(parseOpeningHours(null), null);
});

test("openState reports open with a closing time", () => {
  const s = parseOpeningHours("Mo-Fr 17:00-01:00");
  const st = openState(s, at("2026-08-03T18:30"));  // Monday evening
  assert.equal(st.open, true);
  assert.equal(statusText(st), "Jetzt geöffnet · bis 01:00");
});

test("openState counts yesterday's after-midnight tail as open", () => {
  const s = parseOpeningHours("Mo-Fr 17:00-01:00");
  const st = openState(s, at("2026-08-04T00:30"));  // Tuesday 00:30 = Monday's tail
  assert.equal(st.open, true);
  assert.equal(st.until, 60);
});

test("openState names the next opening", () => {
  const s = parseOpeningHours("Mo-Fr 17:00-23:00");
  assert.equal(statusText(openState(s, at("2026-08-03T09:00"))),
               "Geschlossen · öffnet heute 17:00");
  assert.equal(statusText(openState(s, at("2026-08-03T23:30"))),
               "Geschlossen · öffnet morgen 17:00");
  // Saturday 12:00 -> shut all weekend, next opening is Monday.
  assert.equal(statusText(openState(s, at("2026-08-08T12:00"))),
               "Geschlossen · öffnet Mo 17:00");
});

test("openState survives a schedule that never opens", () => {
  const st = openState(parseOpeningHours("Mo-Su off"), at("2026-08-03T18:30"));
  assert.deepEqual(st, { open: false });
  assert.equal(statusText(st), "Geschlossen");
});

test("formatWeek folds consecutive identical days", () => {
  const s = parseOpeningHours("Mo-Th 17:00-01:00; Fr-Sa 17:00-03:00; Su off");
  assert.deepEqual(formatWeek(s), [
    { label: "Mo–Do", text: "17:00–01:00" },
    { label: "Fr, Sa", text: "17:00–03:00" },
    { label: "So", text: "geschlossen" },
  ]);
});

test("formatWeek spells out a round-the-clock day", () => {
  assert.deepEqual(formatWeek(parseOpeningHours("24/7")),
                   [{ label: "Mo–So", text: "durchgehend geöffnet" }]);
});

test("statusText and formatWeek speak Czech and English on request", () => {
  const s = parseOpeningHours("Mo-Fr 17:00-01:00");
  assert.equal(statusText(openState(s, at("2026-08-03T18:30")), "cs"),
               "Nyní otevřeno · do 01:00");
  assert.equal(statusText(openState(s, at("2026-08-03T18:30")), "en"),
               "Open now · until 01:00");
  assert.equal(statusText(openState(s, at("2026-08-08T12:00")), "cs"),
               "Zavřeno · otevírá Po 17:00");
  assert.equal(statusText(openState(s, at("2026-08-08T12:00")), "en"),
               "Closed · opens Mon 17:00");
  assert.deepEqual(formatWeek(s, "cs"), [
    { label: "Po–Pá", text: "17:00–01:00" },
    { label: "So, Ne", text: "zavřeno" },
  ]);
  assert.deepEqual(formatWeek(s, "en"), [
    { label: "Mon–Fri", text: "17:00–01:00" },
    { label: "Sat, Sun", text: "closed" },
  ]);
  // An unknown language falls back to German rather than crashing.
  assert.equal(statusText(openState(s, at("2026-08-03T18:30")), "fr"),
               "Jetzt geöffnet · bis 01:00");
});

test("venuesOpenNow keeps the open venues and drops unknown hours", () => {
  const venues = [
    { name: "Open", opening_hours: "Mo-Su 17:00-01:00" },
    { name: "Closed today", opening_hours: "Tu-Su 17:00-01:00" },
    { name: "Untagged", opening_hours: null },
    { name: "Unparseable", opening_hours: "nach Absprache" },
    { name: "Always", opening_hours: "24/7" },
  ];
  assert.deepEqual(
    venuesOpenNow(venues, at("2026-08-03T18:30")).map((v) => v.name),
    ["Open", "Always"]);
});

test("venuesOpenNow counts yesterday's after-midnight tail as open", () => {
  const venues = [{ name: "Late", opening_hours: "Mo 17:00-03:00" }];
  // Tuesday 01:30 — still inside Monday's run.
  assert.equal(venuesOpenNow(venues, at("2026-08-04T01:30")).length, 1);
  assert.equal(venuesOpenNow(venues, at("2026-08-04T04:00")).length, 0);
});

test("venueSchedule memoizes the parse on the venue object", () => {
  const v = { opening_hours: "Mo-Su 10:00-22:00" };
  const first = venueSchedule(v);
  assert.equal(venueSchedule(v), first);   // same object, not a re-parse
  assert.equal(v._schedule, first);
  const bad = { opening_hours: "on request" };
  assert.equal(venueSchedule(bad), null);
  assert.equal(bad._schedule, null);       // null is cached, undefined is "not tried yet"
});

test("gridToOpeningHours merges consecutive identical days", () => {
  const grid = scheduleToGrid(parseOpeningHours("Mo-Fr 10:00-22:00; Sa,Su 12:00-23:00"));
  assert.equal(gridToOpeningHours(grid), "Mo-Fr 10:00-22:00; Sa-Su 12:00-23:00");
});

test("the grid round-trips the values it is offered for", () => {
  for (const raw of [
    "Mo-Fr 10:00-22:00; Sa-Su 12:00-23:00",
    "Mo-Su 16:00-01:00",
    "Mo-Fr 11:00-14:00,17:00-23:00; Sa-Su off",
    "Mo-Sa 11:00-24:00; Su off",
  ]) {
    const again = gridToOpeningHours(scheduleToGrid(parseOpeningHours(raw)));
    assert.equal(again, raw, `round trip of ${raw}`);
    // and the result stays inside the subset the parser reads
    assert.ok(parseOpeningHours(again), `${again} must parse`);
  }
});

test("24/7 survives the round trip as 24/7", () => {
  assert.equal(gridToOpeningHours(scheduleToGrid(parseOpeningHours("24/7"))), "24/7");
});

test("a midnight end reaches the grid as 00:00 and comes back out as 24:00", () => {
  // The grid feeds <input type="time">, which silently blanks any value whose
  // hour is not 00-23 — a "24:00" there would render empty and be dropped on
  // save. OSM spells the end of the day 24:00, so the round trip restores it.
  const grid = scheduleToGrid(parseOpeningHours("Mo-Su 18:00-24:00"));
  assert.deepEqual(grid[0].ranges, [["18:00", "00:00"]]);
  assert.equal(gridToOpeningHours(grid), "Mo-Su 18:00-24:00");
});

test("every value the grid produces is one <input type=\"time\"> accepts", () => {
  // The HTML value sanitisation for type=time keeps only a valid time string
  // (hour 00-23, minute 00-59) and replaces anything else with "". Whatever a
  // venue is tagged, the grid must never hand the DOM a value it will discard.
  const tags = [
    "Mo-Su 11:00-24:00", "Mo-Su 11:00-00:00", "Mo 16:00-01:00",
    "24/7", "Mo-Fr 08:00-12:00,17:00-24:00; Sa,Su 10:00-23:30",
  ];
  for (const tag of tags) {
    for (const day of scheduleToGrid(parseOpeningHours(tag))) {
      for (const value of day.ranges.flat()) {
        assert.match(value, /^(?:[01]\d|2[0-3]):[0-5]\d$/, `${tag} -> ${value}`);
      }
    }
  }
});

test("gridToOpeningHours refuses a grid that says nothing usable", () => {
  const allClosed = Array.from({ length: 7 }, () => ({ closed: true, ranges: [] }));
  assert.equal(gridToOpeningHours(allClosed), null);   // that is a closure report
  const openButBlank = Array.from({ length: 7 }, () => ({ closed: false, ranges: [["", ""]] }));
  assert.equal(gridToOpeningHours(openButBlank), null);
});

test("a single differing day does not collapse into its neighbours", () => {
  const grid = scheduleToGrid(parseOpeningHours("Mo-Tu 10:00-20:00; We off; Th-Su 10:00-20:00"));
  assert.equal(gridToOpeningHours(grid), "Mo-Tu 10:00-20:00; We off; Th-Su 10:00-20:00");
});
