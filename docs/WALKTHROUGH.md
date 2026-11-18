# Walkthrough brief

Input for the exploratory walkthrough skill: an agent with a headless browser uses the live site
as a visitor would and reports what gets in the way. Keep the by-design list current, or the
walk re-reports the same intentional behaviour every time.

## Site

- Live: https://zapfkompass.de (canonical). `www.zapfkompass.de`, `http://` and the old
  `beermap.jakubwaller.eu` redirect there with a 301, keeping path and query.
- Languages: German, Czech, English through the "DE | CZ | EN" select in the header. `?lang=de|cs|en`
  on `/` picks one; without it the site uses the stored choice, then the browser language, then German.
- What it is: one MapLibre map of pubs, bars, restaurants, cafés and beer gardens in Germany, Austria,
  Czechia, Poland and Slovakia, filterable by beer brand and serving (Fassbier, Tankbier). Each venue
  card lists the brands with source and check date, OSM opening hours and an address. Visitors can
  report beers, corrections and missing places into a moderated queue. No login, no app.
- Pages: the map `/` (everything else on it is a modal: Statistik, Über das Projekt, Kontakt,
  "Bier melden", the venue card, the brand picker), `/impressum.html`, `/datenschutz.html`.
- Shareable URLs: only `/?lang=…`. A city, a filter, a map position or a venue has no URL.

## Personas

1. **Tankbier hunter in Hamburg, phone.** Tried tank Pilsner Urquell in Prague and wants it again tonight.
   Standing near the Reeperbahn (grant geolocation at 53.5497, 9.9636), German browser. He taps "Mein
   Standort", narrows to "Nur Tankbier" and "Jetzt geöffnet", opens the nearest venue and wants its
   name, address, closing time and how sure the site is about the tank. Done when he knows where to walk.
2. **Czech weekend planner, desktop, Czech.** Browser in `cs-CZ`, has never heard of the site, got a
   plain `https://zapfkompass.de/` link. Going to Vienna for a weekend and wants three pubs that pour
   Budvar (Budweiser Budvar), ideally from the tank, then the link to send a friend so it opens in Czech.
   Done when he has three names and a link that opens in Czech.
3. **Village regular, phone.** Her local pub in a small Franconian town (not one of the 24 cities in the
   region menu) pours a brand the card does not list, and a second pub she knows is not on the map at
   all. She searches her pub by name, opens its card and fills "Marke hinzufügen" up to the button, then
   finds the "Bier melden" form for the missing place and fills it up to "+ Ort melden". She wants to
   know what happens after sending and who runs the site. Done when she knows both, without sending.

## Key flows

- First load: map, header, serving chips with the "Jetzt geöffnet" toggle, brand chips, the count
  ("N Orte" / "N Treffer").
- Region menu "Ganze Karte" plus 24 cities (DE, AT, CZ groups): the map flies there.
- Zoom past 10: gray dots for venues without beer data appear and load per viewport.
- Serving chips: Alle Orte, Nur Zapfbier, Nur Fassbier, Nur Tankbier; "Jetzt geöffnet" combines with them.
- Brand chips (top 9) and "Alle Marken" (searchable picker of every brand); the chosen brand moves to
  the front of the bar and a second tap clears it.
- Search: venue name, brand, beer or address; suggestions, picking one flies to it, remote hits from
  the whole database merge in after a short delay.
- Venue card: address (or an approximate one filled in a moment later), opening-hours badge and week,
  beers with serving, source and date, the report forms (see out of bounds).
- "Mein Standort": granted, denied and failed (toast).
- Statistik (top 10 brands), Über das Projekt, Kontakt (mail address, Impressum, Datenschutz, Ko-fi).
- Language switch on the map with a card or picker open, then the legal pages.

App-only flows: none. Zapfkompass has no app. Dopivatel, a separate app, reports into the same queue
through a partner API; that is outside the walk.

## By design — do not report

- **Reports are moderated.** Nothing sent appears on the map at once; the form answers "Danke, wird
  geprüft!" and the "Bier melden" text says a missing place appears after review.
- **"Nur Zapfbier" means any venue with a known brand**, verified serving or not; "Nur Fassbier" and
  "Nur Tankbier" need a verified serving. So the Zapfbier count is far larger than Fass plus Tank.
- **"Jetzt geöffnet" hides venues whose hours are missing or unreadable** ("we don't know" is not a yes)
  and re-checks itself every minute, so the count can drop while the page sits still.
- **Gray dots** (venues with no beer data) show only on "Alle Orte" with no brand chosen, and only from
  zoom 10; below that they are gone on purpose. They load per viewport, and the count includes only the
  gray dots loaded so far, so it grows as you pan into new areas. "Alle N Treffer auf der Karte zeigen"
  counts gray hits too; when the fitted view is below zoom 10, a toast says how many appear on zooming in.
- **A tap opens the nearest dot**, not only the one under the finger: a near miss opens a venue. A tap
  while the map is still gliding from a swipe opens nothing (it stops the glide), and a near-miss tap
  waits a moment so a double tap can zoom instead. Rotate and tilt gestures are off.
- **Address search matches word starts only** (so "astra" does not find every "Koreastraße");
  names, brands and beers match anywhere in the word.
- **Approximate addresses.** About a third of venues have no address in OSM; the card fills one in a
  moment after opening, marked "(ungefähr)", and leaves the "Ort korrigieren" address field empty.
- **Opening hours come from OSM only**, marked "ohne Gewähr". A tag the site cannot read is printed as
  is; hours too complex for the editing grid are not prefilled and a warning says a suggestion replaces them.
- **Staged rows stay.** Beers added with "+ Weiteres Bier" but not sent stay in this browser for 24 hours
  and come back when the card is reopened.
- **One "Augustiner" chip** covers Munich's and Salzburg's Augustiner; the OSM tags cannot tell them apart.
- **No Polish or Slovak UI**, and no PL or SK city in the region menu. The 24 menu cities are the ones
  refreshed nightly in full.
- **Language rules.** `?lang=` outranks the stored choice; the switcher writes `?lang=` into the address
  bar so a copied URL keeps the language, and so does a language taken from storage or the browser (a
  German browser gets `?lang=de`; one in a language the site lacks keeps the bare URL); `/?lang=cs|en` also serves a Czech or English page title and
  link preview. The preview image is the same in every language. On a phone the about button reads
  "Über" / "Info" / "About" instead of the long label.
- **The site asks for location only when "Mein Standort" is tapped.** Mobile browsers may ask again on
  every visit; that is browser behaviour.
- **Impressum and Datenschutz are German only.** In Czech and English the links say so ("(německy)",
  "(in German)").
- **Duplicates are merged in Zapfkompass's data only**, never in OpenStreetMap, one reported pair at a
  time (`python -m pipeline.rekey --merge`); other same-address twins can still show until someone folds
  them. A venue whose OSM element has neither `addr:city` nor `addr:place` shows an address without a town.
- **The Impressum's contact form is on buergerwecker.de** (`/kontakt?projekt=zapfkompass`), a sister
  site of the same operator; the privacy page says so.
- **No cookies, no tracking, no third-party scripts**; map tiles come from tile.openstreetmap.org and
  Cloudflare may set an anti-bot cookie (both on the privacy page).

## Out of bounds

The site is production. Every report goes to a real moderation queue, and approved hours, missing places
and closures are carried on to OpenStreetMap. The walk must never send one.

- **No POST to `/api/submit`, in any form.** Fill fields if you like and stop before the button. Watch out:
  - the ✓ ("Ausschank korrigieren") and ✕ ("Bier entfernen") buttons beside each listed beer send at
    once with one click, no confirmation. Never click them;
  - "+ Bier melden" in the venue card, "Adresse speichern", "Als geschlossen melden" (if a confirm dialog
    ever shows, dismiss it), "Zeiten vorschlagen" and "+ Ort melden" in the "Bier melden" modal;
  - pressing Enter in any field of those forms submits them. Enter in the map's search box is fine.
  - "+ Weiteres Bier" only stages a row in the browser and may be used.
- **No `/admin`, `/admin/login`, `/api/admin/…` or `/api/partner/submit`**, and not `/docs` or
  `/openapi.json` (its "Try it out" can send real requests).
- **Contact:** do not mail beermap@jakubwaller.eu. Open the buergerwecker.de contact form and read it;
  do not fill or submit it.
- **Ko-fi:** open the page, no payment, no sign-in. **OpenStreetMap links:** read only, no login, edit or note.
- **Load:** move like a person, a page or action every few seconds. Opening a venue card without an
  address makes the server ask Nominatim once, so open cards one at a time, not dozens; no scripted
  sweeps of `/api/gray` tiles or `/api/search`, no rapid reloads.
- Never use a real name, e-mail or address of a person; if you fill a field, use synthetic text
  ("Testkneipe", `test@example.com`) and do not send it.
