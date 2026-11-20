# Farm App: přípravky na pozemek

Webová aplikace, která pro díl půdního bloku z LPIS a zvolenou plodinu vypíše přípravky na ochranu rostlin povolené v ČR. U každého přípravku ukáže omezení platná pro daný pozemek.

> Aplikace je pomůcka pro rozhodování. Závazné jsou etiketa přípravku a údaje v registru ÚKZÚZ.

## Co umí

- Načte díl půdního bloku z veřejné WFS služby LPIS, a to podle čtverce a kódu dílu nebo kliknutím do mapy.
- Spočítá vzdálenost dílu od nejbližší povrchové vody (vodní toky a plochy ze ZABAGED, ČÚZK) a u každého přípravku porovná požadovanou ochrannou vzdálenost se skutečností. Když nestačí, ukáže, kolik hektarů pásu podél vody se nesmí ošetřit.
- Zjistí, zda díl zasahuje do zóny svažitých pozemků u vody (vrstva `SVAZITE_100_WFS`) nebo do 100m okolí ochranného pásma vodního zdroje I. stupně. Dále ukáže erozní ohroženost (SEO/MEO) a počet registrovaných stanovišť včelstev do 5 km.
- Pro zvolenou plodinu vypíše povolené přípravky a ke každému:
  - dávky, ochrannou lhůtu, fázi BBCH a maximální počet aplikací,
  - ochrannou vzdálenost od povrchové vody pro zvolenou redukci úletu (0/50/75/90 %),
  - podmínky pro svažité pozemky u vody (ochranná vzdálenost nebo vegetační pás, případně „nelze“),
  - vyloučení v OPVZ II. stupně, rizika pro včely, omezení kvůli podzemní vodě,
  - konec povolení a lhůtu pro spotřebování zásob.
- Výsledek rozdělí na „lze použít“, „s podmínkou“ a „nelze“.

## Zdroje dat

| Data | Zdroj | Přístup |
|---|---|---|
| Registr přípravků | MZe/ÚKZÚZ, webová služba `EPH_GCP01H` (SOAP) | veřejná, bez přihlášení |
| Díly půdních bloků, eroze, svahy u vody, OPVZ I, včely | LPIS WFS `https://mze.gov.cz/public/app/wms/plpis_wfs.fcgi` | veřejná |
| Vodní toky a vodní plochy | ČÚZK ZABAGED, ArcGIS REST `ags.cuzk.gov.cz/.../ZABAGED_POLOHOPIS/MapServer` (vrstvy 93 a 132) | veřejná |
| Podkladová mapa | OpenStreetMap + WMS LPIS | veřejná |

## Spuštění

```bash
pip install -r requirements.txt
python -m app.registry_import        # stáhne registr do data/registry.sqlite (asi 4 minuty, ~45 MB XML)
uvicorn app.main:app --port 8000     # pak otevřete http://localhost:8000
```

Registr se mění zhruba jednou za měsíc. Import stačí spouštět jednou denně.

## Nasazení na internet (Render)

Repozitář obsahuje `render.yaml`, takže aplikaci jde nasadit zdarma na [Render](https://render.com):

1. Na render.com se zaregistrujte přes účet GitHub.
2. Klikněte na **New → Blueprint**, vyberte repozitář `Farm-app` a potvrďte.
3. Render se zeptá na `APP_PASSWORD`. To je heslo, které prohlížeč chce při otevření stránky (jméno může být libovolné). Když pole necháte prázdné, stránka bude bez hesla.
4. První sestavení trvá asi 6–8 minut, protože se stahuje registr přípravků. Pak je aplikace na adrese `https://farm-app-….onrender.com`.

Bezplatná verze aplikaci po 15 minutách nečinnosti uspí. První otevření potom trvá asi minutu. Registr se obnoví při každém novém nasazení (v Renderu **Manual Deploy → Deploy latest commit**).

## Známá omezení

- **Vodní toky:** počítají se stálé i občasné toky a vodní plochy ze ZABAGED. Podzemní (zatrubněné) toky se nepočítají. Meliorační příkopy, které v ZABAGED nejsou, aplikace nevidí.
- **OPVZ II. stupně** v LPIS není, proto se u přípravků zobrazí jen upozornění.
- **Kódy plodin:** veřejná služba neobsahuje číselník plodin. Přiřazení kódů k názvům odhaduje `build_crops` v `app/registry_import.py` z textů v registru.
- **Načtení pozemku** trvá 15–25 s, protože se postupně dotazuje několik vrstev LPIS. Služba MZe občas požadavek odmítne, proto ho aplikace zkouší opakovaně.
- Plodina na pozemku není ve veřejném LPIS. Zadává ji uživatel.

## Struktura

- `app/registry_import.py`: stažení registru a uložení do SQLite
- `app/lpis.py`: dotazy na LPIS WFS a prostorové kontroly
- `app/evaluate.py`: vyhodnocení přípravků pro pozemek a plodinu
- `app/main.py`: API (FastAPI)
- `app/static/index.html`: webové rozhraní (Leaflet)
