import { test } from "node:test";
import assert from "node:assert/strict";
import { LANGS, MESSAGES, detectLang, setLang, getLang, t, tn } from "./i18n.js";

// Plural variants differ per language (Czech has "few", German/English don't),
// so parity is checked on the base keys.
const baseKeys = (lang) =>
  new Set(Object.keys(MESSAGES[lang]).map((k) => k.replace(/\.(one|few|other)$/, "")));

test("every language covers the same set of keys", () => {
  const de = baseKeys("de");
  for (const lang of LANGS) {
    assert.deepEqual(baseKeys(lang), de, `key mismatch in "${lang}"`);
  }
});

test("plural keys always have a .other fallback form", () => {
  for (const lang of LANGS) {
    for (const key of Object.keys(MESSAGES[lang])) {
      const m = key.match(/^(.*)\.(one|few)$/);
      if (m) assert.ok(MESSAGES[lang][`${m[1]}.other`],
        `"${m[1]}" in "${lang}" has .${m[2]} but no .other`);
    }
  }
});

test("detectLang: stored choice wins, then browser language, then German", () => {
  assert.equal(detectLang("cs", ["en-US"]), "cs");
  assert.equal(detectLang(null, ["en-US", "de"]), "en");
  assert.equal(detectLang(null, ["cs-CZ"]), "cs");
  assert.equal(detectLang(null, ["fr-FR", "it"]), "de");
  assert.equal(detectLang("xx", []), "de");
  assert.equal(detectLang(null, undefined), "de");
});

test("t translates in the active language and interpolates placeholders", () => {
  setLang("en");
  assert.equal(getLang(), "en");
  assert.equal(t("serving.tank"), "Tank only");
  assert.equal(t("suggest.none", { q: "abc" }), "No results for “abc”");
  setLang("cs");
  assert.equal(t("serving.tank"), "Jen tankové");
  setLang("de");
  assert.equal(t("serving.tank"), "Nur Tankbier");
});

test("t falls back to German, then to the key itself", () => {
  setLang("en");
  assert.equal(t("no.such.key"), "no.such.key");
  setLang("de");
});

test("setLang ignores unknown languages", () => {
  setLang("de");
  setLang("xx");
  assert.equal(getLang(), "de");
});

test("tn picks German/English plurals by one vs. other", () => {
  setLang("de");
  assert.equal(tn("count.places", 1), "1 Ort");
  assert.equal(tn("count.places", 5), "5 Orte");
  setLang("en");
  assert.equal(tn("count.places", 1), "1 place");
  assert.equal(tn("count.places", 2), "2 places");
  setLang("de");
});

test("tn handles the three Czech plural forms", () => {
  setLang("cs");
  assert.equal(tn("count.places", 1), "1 místo");
  assert.equal(tn("count.places", 3), "3 místa");
  assert.equal(tn("count.places", 11), "11 míst");
  assert.equal(tn("count.hits", 2), "2 výsledky");
  assert.equal(tn("count.hits", 7), "7 výsledků");
  setLang("de");
});
