// UI languages: German (default), Czech, English. Pure module — no DOM work
// here; app.js applies translations. localStorage/navigator access is guarded
// so the module (and its tests) also run under plain Node.

export const LANGS = ["de", "cs", "en"];
const STORAGE_KEY = "beermap.lang";

export const MESSAGES = {
  de: {
    "title": "Zapfkompass – Bier vom Fass & Tank",

    "a11y.zoomIn": "Hineinzoomen",
    "a11y.zoomOut": "Herauszoomen",
    "a11y.locate": "Meinen Standort anzeigen",
    "a11y.locateShort": "Mein Standort",
    "a11y.region": "Region wählen",
    "a11y.clearSearch": "Suche löschen",
    "a11y.searchResults": "Suchergebnisse",
    "a11y.close": "Schließen",
    "a11y.serving": "Ausschank",
    "a11y.lang": "Sprache wählen",

    "nav.stats": "Statistik",
    "nav.about": "Über",
    "nav.aboutLong": " das Projekt",
    "nav.contact": "Kontakt",
    "nav.cta": "Bier melden",

    "search.placeholder": "Kneipe, Adresse oder Marke suchen…",

    "city.map": "Ganze Karte",
    "country.de": "Deutschland",
    "country.at": "Österreich",
    "country.cz": "Tschechien",
    "city.berlin": "Berlin",
    "city.bremen": "Bremen",
    "city.dresden": "Dresden",
    "city.duesseldorf": "Düsseldorf",
    "city.frankfurt": "Frankfurt am Main",
    "city.hamburg": "Hamburg",
    "city.hannover": "Hannover",
    "city.koeln": "Köln",
    "city.leipzig": "Leipzig",
    "city.muenchen": "München",
    "city.nuernberg": "Nürnberg",
    "city.stuttgart": "Stuttgart",
    "city.graz": "Graz",
    "city.innsbruck": "Innsbruck",
    "city.klagenfurt": "Klagenfurt",
    "city.linz": "Linz",
    "city.salzburg": "Salzburg",
    "city.wien": "Wien",
    "city.brno": "Brünn (Brno)",
    "city.budejovice": "Budweis (České Budějovice)",
    "city.boleslav": "Mladá Boleslav",
    "city.ostrava": "Ostrava",
    "city.plzen": "Pilsen (Plzeň)",
    "city.praha": "Prag (Praha)",

    "serving.all": "Alle Orte",
    "serving.draught": "Nur Zapfbier",
    "serving.fass": "Nur Fassbier",
    "serving.tank": "Nur Tankbier",
    "serving.openNow": "Jetzt geöffnet",
    "serving.openNowHint": "Zeigt nur Orte, für die Öffnungszeiten bekannt sind und die gerade offen haben.",

    "brands.all": "Alle Marken",
    "brands.title": "Marke wählen",
    "brands.search": "Marke suchen…",
    "brands.none": "Keine Marke gefunden für „{q}“",
    "brands.reset": "Markenfilter zurücksetzen",
    "brands.more.one": "… und eine weitere Marke — tippe zum Suchen",
    "brands.more.other": "… und {n} weitere Marken — tippe zum Suchen",

    "count.places.one": "{n} Ort",
    "count.places.other": "{n} Orte",
    "count.hits.one": "{n} Treffer",
    "count.hits.other": "{n} Treffer",

    "badge.tank": "Tankbier",
    "badge.fass": "Fassbier",
    "source.manual": "✓ verifiziert",
    "source.community": "✓ geprüft",

    "beer.fass": "Fass",
    "beer.tank": "Tank",
    "beer.correct": "Ausschank korrigieren",
    "beer.remove": "Bier entfernen",

    "venue.noBeers": "Noch keine Biere erfasst.",
    "venue.addBrand": "Marke hinzufügen",
    "venue.beerOptional": "Sorte (optional)",
    "venue.addAnother": "+ Weiteres Bier",
    "venue.rejectedRow": "abgelehnt",
    "venue.removeStaged": "Entfernen",
    "venue.submitBeer": "+ Bier melden",
    "venue.fix": "Ort korrigieren",
    "venue.address": "Adresse",
    "venue.saveAddress": "Adresse speichern",
    "venue.reportClosed": "Als geschlossen melden",
    "venue.fallbackName": "Kneipe",

    "venue.fixHours": "Zeiten korrigieren",
    "hours.closedDay": "geschlossen",
    "hours.addBreak": "Pause",
    "hours.save": "Zeiten vorschlagen",
    "hours.gridInvalid": " Bitte für jeden offenen Tag eine Zeit angeben.",
    "hours.complex": "Die aktuellen Zeiten sind zu komplex für dieses Raster. Ein Vorschlag ersetzt sie vollständig.",
    "hours.title": "Öffnungszeiten",
    "hours.note": "Zeiten aus OpenStreetMap — ohne Gewähr.",

    "modal.stats": "Statistik",
    "modal.about": "Über das Projekt",
    "modal.contact": "Kontakt",
    "modal.add": "Bier melden",
    "stats.empty": "Noch keine Daten.",

    "about.body": "Zapfkompass zeigt, wo es in Deutschland, Österreich und Tschechien Bier vom Fass oder Tank gibt — Marke für Marke. Vierundzwanzig Großstädte — Berlin, Bremen, Dresden, Düsseldorf, Frankfurt am Main, Hamburg, Hannover, Köln, Leipzig, München, Nürnberg und Stuttgart, dazu Wien, Graz, Linz, Salzburg, Innsbruck und Klagenfurt sowie Prag, Brünn, Pilsen, Ostrava, Budweis und Mladá Boleslav — sind vollständig erfasst (dort ist jede Kneipe anklickbar), im Rest aller drei Länder alle Orte mit bekannter Biermarke. Die Basis bilden von Hand geprüfte Einträge, ergänzt um OpenStreetMap-Daten und die „Wo gibt's das?“-Seiten der Brauereien — darunter die bekannten Prager Tankovnas mit Tankbier von Pilsner Urquell und Budvar. Jede Verknüpfung trägt eine Quelle und ein Prüfdatum.",

    "contact.intro": "Fehler entdeckt oder eine Kneipe fehlt? Schreib uns:",
    "contact.legal": "Rechtliches",
    "contact.imprint": "Impressum",
    "contact.privacy": "Datenschutz",
    "kofi.short": "☕ Kaffee",
    "kofi.aria": "Kaffee spendieren (Ko-fi)",
    "kofi.cta": "☕ Kaffee spendieren",
    "kofi.free": "Entstanden aus der Suche nach Tankbier: frisch und unpasteurisiert schmeckt es wie in der Brauerei, in Tschechien gibt es das fast überall, anderswo muss man es suchen. Zapfkompass ist kostenlos und werbefrei.",

    "add.p1": "Klick auf eine Kneipe direkt auf der Karte — dort kannst du eine Marke und Ausschankart (Fass/Tank) melden.",
    "add.p2strong": "Fehlt ein Ort komplett?",
    "add.p2rest": "Trag ihn hier ein — nach Prüfung erscheint er auf der Karte.",
    "add.venueName": "Name des Lokals",
    "add.addressPh": "Straße Nr., PLZ Stadt",
    "add.brandOptional": "Marke (optional)",
    "add.submitVenue": "+ Ort melden",

    "form.thanks": " Danke, wird geprüft!",
    "form.sending": " Wird gesendet…",
    "form.sent": " {ok} von {n} gesendet.",
    "form.retry": " {failed} nicht angekommen, bitte später nochmal.",
    "form.rejected": " {rejected} abgelehnt (ungültige Angabe).",
    "form.needBrand": " Bitte auch die Marke angeben.",
    "form.error": " Fehler",
    "confirm.close": "Diesen Ort wirklich als dauerhaft geschlossen melden?",

    "toast.noGeo": "Standortbestimmung wird von diesem Browser nicht unterstützt.",
    "toast.denied": "Standortfreigabe abgelehnt — bitte in den Browser-Einstellungen erlauben.",
    "toast.failed": "Standort konnte nicht ermittelt werden.",

    "suggest.open": "geöffnet",
    "suggest.closed": "geschlossen",
    "suggest.unnamed": "Ohne Namen",
    "suggest.showAll.one": "Den einen Treffer auf der Karte zeigen",
    "suggest.showAll.other": "Alle {n} Treffer auf der Karte zeigen",
    "suggest.none": "Keine Treffer für „{q}“",
  },

  cs: {
    "title": "Zapfkompass – pivo ze sudu a z tanku",

    "a11y.zoomIn": "Přiblížit",
    "a11y.zoomOut": "Oddálit",
    "a11y.locate": "Zobrazit moji polohu",
    "a11y.locateShort": "Moje poloha",
    "a11y.region": "Vybrat region",
    "a11y.clearSearch": "Vymazat hledání",
    "a11y.searchResults": "Výsledky hledání",
    "a11y.close": "Zavřít",
    "a11y.serving": "Výčep",
    "a11y.lang": "Zvolit jazyk",

    "nav.stats": "Statistika",
    "nav.about": "O projektu",
    "nav.aboutLong": "",
    "nav.contact": "Kontakt",
    "nav.cta": "Nahlásit pivo",

    "search.placeholder": "Hledat hospodu, adresu nebo značku…",

    "city.map": "Celá mapa",
    "country.de": "Německo",
    "country.at": "Rakousko",
    "country.cz": "Česko",
    "city.berlin": "Berlín",
    "city.bremen": "Brémy",
    "city.dresden": "Drážďany",
    "city.duesseldorf": "Düsseldorf",
    "city.frankfurt": "Frankfurt nad Mohanem",
    "city.hamburg": "Hamburk",
    "city.hannover": "Hannover",
    "city.koeln": "Kolín nad Rýnem",
    "city.leipzig": "Lipsko",
    "city.muenchen": "Mnichov",
    "city.nuernberg": "Norimberk",
    "city.stuttgart": "Stuttgart",
    "city.graz": "Štýrský Hradec (Graz)",
    "city.innsbruck": "Innsbruck",
    "city.klagenfurt": "Klagenfurt",
    "city.linz": "Linec",
    "city.salzburg": "Salcburk",
    "city.wien": "Vídeň",
    "city.brno": "Brno",
    "city.budejovice": "České Budějovice",
    "city.boleslav": "Mladá Boleslav",
    "city.ostrava": "Ostrava",
    "city.plzen": "Plzeň",
    "city.praha": "Praha",

    "serving.all": "Všechna místa",
    "serving.draught": "Jen čepované",
    "serving.fass": "Jen sudové",
    "serving.tank": "Jen tankové",
    "serving.openNow": "Teď otevřeno",
    "serving.openNowHint": "Zobrazí jen podniky se známou otevírací dobou, které mají právě otevřeno.",

    "brands.all": "Všechny značky",
    "brands.title": "Vybrat značku",
    "brands.search": "Hledat značku…",
    "brands.none": "Pro „{q}“ nic nenalezeno",
    "brands.reset": "Zrušit filtr značek",
    "brands.more.one": "… a ještě jedna značka — pište pro hledání",
    "brands.more.few": "… a další {n} značky — pište pro hledání",
    "brands.more.other": "… a dalších {n} značek — pište pro hledání",

    "count.places.one": "{n} místo",
    "count.places.few": "{n} místa",
    "count.places.other": "{n} míst",
    "count.hits.one": "{n} výsledek",
    "count.hits.few": "{n} výsledky",
    "count.hits.other": "{n} výsledků",

    "badge.tank": "tankové pivo",
    "badge.fass": "sudové pivo",
    "source.manual": "✓ ověřeno",
    "source.community": "✓ zkontrolováno",

    "beer.fass": "Sud",
    "beer.tank": "Tank",
    "beer.correct": "Opravit výčep",
    "beer.remove": "Odebrat pivo",

    "venue.noBeers": "Zatím žádná piva.",
    "venue.addBrand": "Přidat značku",
    "venue.beerOptional": "Druh (nepovinné)",
    "venue.addAnother": "+ Další pivo",
    "venue.rejectedRow": "zamítnuto",
    "venue.removeStaged": "Odebrat",
    "venue.submitBeer": "+ Nahlásit pivo",
    "venue.fix": "Opravit místo",
    "venue.address": "Adresa",
    "venue.saveAddress": "Uložit adresu",
    "venue.reportClosed": "Nahlásit jako zavřené",
    "venue.fallbackName": "Hospoda",

    "venue.fixHours": "Opravit otevírací dobu",
    "hours.closedDay": "zavřeno",
    "hours.addBreak": "Přestávka",
    "hours.save": "Navrhnout dobu",
    "hours.gridInvalid": " U každého otevřeného dne zadejte prosím čas.",
    "hours.complex": "Současná otevírací doba je pro tuto mřížku příliš složitá. Návrh ji zcela nahradí.",
    "hours.title": "Otevírací doba",
    "hours.note": "Časy z OpenStreetMap — bez záruky.",

    "modal.stats": "Statistika",
    "modal.about": "O projektu",
    "modal.contact": "Kontakt",
    "modal.add": "Nahlásit pivo",
    "stats.empty": "Zatím žádná data.",

    "about.body": "Zapfkompass ukazuje, kde v Německu, Rakousku a Česku najdete pivo ze sudu nebo z tanku — značku po značce. Dvacet čtyři velkých měst — Berlín, Brémy, Drážďany, Düsseldorf, Frankfurt nad Mohanem, Hamburk, Hannover, Kolín nad Rýnem, Lipsko, Mnichov, Norimberk a Stuttgart, k tomu Vídeň, Štýrský Hradec, Linec, Salcburk, Innsbruck a Klagenfurt, spolu s Prahou, Brnem, Plzní, Ostravou, Českými Budějovicemi a Mladou Boleslaví — je zmapováno kompletně (každá hospoda je tam klikací), ve zbytku všech tří zemí najdete všechna místa se známou pivní značkou. Základem jsou ručně ověřené záznamy, doplněné o data z OpenStreetMap a stránky pivovarů „kde načepují“ — včetně známých pražských tankoven s tankovým pivem Pilsner Urquell a Budvar. Každý záznam nese zdroj a datum ověření.",

    "contact.intro": "Našli jste chybu, nebo chybí hospoda? Napište nám:",
    "contact.legal": "Právní informace",
    "contact.imprint": "Impressum",
    "contact.privacy": "Ochrana údajů",
    "kofi.short": "☕ Káva",
    "kofi.aria": "Kup mi kávu (Ko-fi)",
    "kofi.cta": "☕ Kup mi kávu",
    "kofi.free": "Vznikl z hledání tankového piva: čerstvé a nepasterované chutná jako v pivovaru, v Česku je skoro všude, v cizině se hledá těžko. Zapfkompass je zdarma a bez reklam.",

    "add.p1": "Klikněte na hospodu přímo na mapě — tam můžete nahlásit značku a způsob výčepu (sud/tank).",
    "add.p2strong": "Chybí místo úplně?",
    "add.p2rest": "Zadejte ho zde — po kontrole se objeví na mapě.",
    "add.venueName": "Název podniku",
    "add.addressPh": "Ulice č., PSČ město",
    "add.brandOptional": "Značka (nepovinné)",
    "add.submitVenue": "+ Nahlásit místo",

    "form.thanks": " Díky, zkontrolujeme!",
    "form.sending": " Odesílá se…",
    "form.sent": " Odesláno {ok} z {n}.",
    "form.retry": " {failed} se nepodařilo odeslat, zkuste to prosím později.",
    "form.rejected": " {rejected} zamítnuto (neplatný údaj).",
    "form.needBrand": " Uveďte prosím také značku.",
    "form.error": " Chyba",
    "confirm.close": "Opravdu nahlásit toto místo jako trvale zavřené?",

    "toast.noGeo": "Tento prohlížeč nepodporuje určení polohy.",
    "toast.denied": "Přístup k poloze byl zamítnut — povolte ho v nastavení prohlížeče.",
    "toast.failed": "Polohu se nepodařilo zjistit.",

    "suggest.open": "otevřeno",
    "suggest.closed": "zavřeno",
    "suggest.unnamed": "Bez názvu",
    "suggest.showAll.one": "Zobrazit jediný výsledek na mapě",
    "suggest.showAll.few": "Zobrazit všechny {n} výsledky na mapě",
    "suggest.showAll.other": "Zobrazit všech {n} výsledků na mapě",
    "suggest.none": "Žádné výsledky pro „{q}“",
  },

  en: {
    "title": "Zapfkompass – keg & tank beer",

    "a11y.zoomIn": "Zoom in",
    "a11y.zoomOut": "Zoom out",
    "a11y.locate": "Show my location",
    "a11y.locateShort": "My location",
    "a11y.region": "Choose region",
    "a11y.clearSearch": "Clear search",
    "a11y.searchResults": "Search results",
    "a11y.close": "Close",
    "a11y.serving": "Serving",
    "a11y.lang": "Choose language",

    "nav.stats": "Stats",
    "nav.about": "About",
    "nav.aboutLong": " the project",
    "nav.contact": "Contact",
    "nav.cta": "Report beer",

    "search.placeholder": "Search pub, address or brand…",

    "city.map": "Whole map",
    "country.de": "Germany",
    "country.at": "Austria",
    "country.cz": "Czechia",
    "city.berlin": "Berlin",
    "city.bremen": "Bremen",
    "city.dresden": "Dresden",
    "city.duesseldorf": "Düsseldorf",
    "city.frankfurt": "Frankfurt am Main",
    "city.hamburg": "Hamburg",
    "city.hannover": "Hanover",
    "city.koeln": "Cologne",
    "city.leipzig": "Leipzig",
    "city.muenchen": "Munich",
    "city.nuernberg": "Nuremberg",
    "city.stuttgart": "Stuttgart",
    "city.graz": "Graz",
    "city.innsbruck": "Innsbruck",
    "city.klagenfurt": "Klagenfurt",
    "city.linz": "Linz",
    "city.salzburg": "Salzburg",
    "city.wien": "Vienna",
    "city.brno": "Brno",
    "city.budejovice": "České Budějovice (Budweis)",
    "city.boleslav": "Mladá Boleslav",
    "city.ostrava": "Ostrava",
    "city.plzen": "Pilsen (Plzeň)",
    "city.praha": "Prague",

    "serving.all": "All places",
    "serving.draught": "Draught only",
    "serving.fass": "Keg only",
    "serving.tank": "Tank only",
    "serving.openNow": "Open now",
    "serving.openNowHint": "Shows only places with known opening hours that are open right now.",

    "brands.all": "All brands",
    "brands.title": "Choose a brand",
    "brands.search": "Search brands…",
    "brands.none": "No brand found for “{q}”",
    "brands.reset": "Clear brand filter",
    "brands.more.one": "… and one more brand — type to search",
    "brands.more.other": "… and {n} more brands — type to search",

    "count.places.one": "{n} place",
    "count.places.other": "{n} places",
    "count.hits.one": "{n} result",
    "count.hits.other": "{n} results",

    "badge.tank": "tank beer",
    "badge.fass": "keg beer",
    "source.manual": "✓ verified",
    "source.community": "✓ reviewed",

    "beer.fass": "Keg",
    "beer.tank": "Tank",
    "beer.correct": "Correct serving type",
    "beer.remove": "Remove beer",

    "venue.noBeers": "No beers recorded yet.",
    "venue.addBrand": "Add a brand",
    "venue.beerOptional": "Beer (optional)",
    "venue.addAnother": "+ Another beer",
    "venue.rejectedRow": "rejected",
    "venue.removeStaged": "Remove",
    "venue.submitBeer": "+ Report beer",
    "venue.fix": "Correct this place",
    "venue.address": "Address",
    "venue.saveAddress": "Save address",
    "venue.reportClosed": "Report as closed",
    "venue.fallbackName": "Pub",

    "venue.fixHours": "Correct opening hours",
    "hours.closedDay": "closed",
    "hours.addBreak": "Break",
    "hours.save": "Suggest hours",
    "hours.gridInvalid": " Please give a time for every open day.",
    "hours.complex": "The current hours are too complex for this grid. A suggestion replaces them entirely.",
    "hours.title": "Opening hours",
    "hours.note": "Hours from OpenStreetMap — no guarantee.",

    "modal.stats": "Statistics",
    "modal.about": "About the project",
    "modal.contact": "Contact",
    "modal.add": "Report beer",
    "stats.empty": "No data yet.",

    "about.body": "Zapfkompass shows where to find keg or tank beer in Germany, Austria and Czechia — brand by brand. Twenty-four major cities — Berlin, Bremen, Dresden, Düsseldorf, Frankfurt am Main, Hamburg, Hanover, Cologne, Leipzig, Munich, Nuremberg and Stuttgart, plus Vienna, Graz, Linz, Salzburg, Innsbruck and Klagenfurt, and Prague, Brno, Pilsen, Ostrava, České Budějovice and Mladá Boleslav — are covered in full (every pub there is clickable); across the rest of all three countries you'll find every place with a known beer brand. The base is hand-checked entries, extended with OpenStreetMap data and the breweries' own “where to drink” pages — including Prague's famous tankovnas serving tank beer from Pilsner Urquell and Budvar. Every link carries a source and a verification date.",

    "contact.intro": "Spotted an error, or is a pub missing? Write to us:",
    "contact.legal": "Legal",
    "contact.imprint": "Imprint",
    "contact.privacy": "Privacy",
    "kofi.short": "☕ Coffee",
    "kofi.aria": "Buy me a coffee (Ko-fi)",
    "kofi.cta": "☕ Buy me a coffee",
    "kofi.free": "Born from hunting for tank beer: fresh and unpasteurised, it tastes the way it does at the brewery, it is almost everywhere in Czechia and hard to find anywhere else. Zapfkompass is free and ad-free.",

    "add.p1": "Click a pub directly on the map — there you can report a brand and serving type (keg/tank).",
    "add.p2strong": "Is a place missing entirely?",
    "add.p2rest": "Add it here — after review it appears on the map.",
    "add.venueName": "Venue name",
    "add.addressPh": "Street no., postcode city",
    "add.brandOptional": "Brand (optional)",
    "add.submitVenue": "+ Report a place",

    "form.thanks": " Thanks, we'll review it!",
    "form.sending": " Sending…",
    "form.sent": " {ok} of {n} sent.",
    "form.retry": " {failed} didn't go through, please retry later.",
    "form.rejected": " {rejected} rejected (invalid entry).",
    "form.needBrand": " Please add the brand as well.",
    "form.error": " Error",
    "confirm.close": "Really report this place as permanently closed?",

    "toast.noGeo": "This browser does not support geolocation.",
    "toast.denied": "Location access denied — please allow it in your browser settings.",
    "toast.failed": "Could not determine your location.",

    "suggest.open": "open",
    "suggest.closed": "closed",
    "suggest.unnamed": "Unnamed",
    "suggest.showAll.one": "Show the one result on the map",
    "suggest.showAll.other": "Show all {n} results on the map",
    "suggest.none": "No results for “{q}”",
  },
};

let lang = "de";

/** Pick the language: stored choice first, then the browser's language list,
 *  German as the site default. Pure — inputs are injected for testability. */
export function detectLang(stored, browserLangs) {
  if (LANGS.includes(stored)) return stored;
  for (const l of browserLangs || []) {
    const base = String(l).toLowerCase().slice(0, 2);
    if (LANGS.includes(base)) return base;
  }
  return "de";
}

export function getLang() {
  return lang;
}

export function setLang(l) {
  if (!LANGS.includes(l)) return;
  lang = l;
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(STORAGE_KEY, l);
  } catch { /* storage blocked (private mode) — the choice just won't persist */ }
}

/** Language from a shared link's `?lang=` (case-insensitive), or null. Pure —
 *  the caller passes location.search. */
export function langFromQuery(search) {
  let l = null;
  try { l = new URLSearchParams(search || "").get("lang"); } catch { return null; }
  l = l ? String(l).toLowerCase() : null;
  return LANGS.includes(l) ? l : null;
}

export function initLang() {
  // A shared link's ?lang= outranks the visitor's stored choice: whoever sent
  // the link chose the language on purpose, and the switcher is one tap away.
  const fromUrl = typeof location !== "undefined" ? langFromQuery(location.search) : null;
  let stored = null;
  try {
    if (typeof localStorage !== "undefined") stored = localStorage.getItem(STORAGE_KEY);
  } catch { /* ditto */ }
  const browserLangs = typeof navigator !== "undefined"
    ? (navigator.languages || [navigator.language]) : [];
  lang = fromUrl || detectLang(stored, browserLangs);
  return lang;
}

const interpolate = (msg, vars) =>
  vars ? msg.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : msg;

/** Translate `key`, with `{name}` placeholders filled from `vars`.
 *  Falls back to German, then to the key itself, so a missing translation
 *  degrades to readable text instead of a blank. */
export function t(key, vars) {
  const msg = MESSAGES[lang][key] ?? MESSAGES.de[key] ?? key;
  return interpolate(msg, vars);
}

// Czech needs three plural forms (1 / 2–4 / rest); German and English two.
const pluralForm = (l, n) =>
  l === "cs" ? (n === 1 ? "one" : n >= 2 && n <= 4 ? "few" : "other")
             : (n === 1 ? "one" : "other");

/** Translate a count: `tn("count.places", 3)` looks up
 *  `count.places.<one|few|other>` and fills `{n}`. */
export function tn(key, n) {
  const dict = MESSAGES[lang];
  const form = pluralForm(lang, n);
  const msg = dict[`${key}.${form}`] ?? dict[`${key}.other`]
    ?? MESSAGES.de[`${key}.${pluralForm("de", n)}`] ?? MESSAGES.de[`${key}.other`] ?? key;
  return interpolate(msg, { n });
}
