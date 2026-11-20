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

## Spuštění na vlastním počítači (Windows)

Aplikace běží jen u vás na počítači a otevírá se v prohlížeči. Potřebuje připojení k internetu, protože data bere z veřejných služeb MZe a ČÚZK.

**Jednou na začátku**

1. Nainstalujte Python z [python.org/downloads](https://www.python.org/downloads/). V instalátoru zaškrtněte **Add python.exe to PATH**.
2. Na GitHubu u repozitáře klikněte na zelené **Code → Download ZIP** a ZIP rozbalte, například do `C:\farm-app`.
3. Otevřete rozbalenou složku v Průzkumníku, klikněte do řádku s cestou nahoře, napište `cmd` a stiskněte Enter. Otevře se příkazový řádek přímo v této složce.
4. Do něj postupně zadejte:

   ```
   python -m pip install -r requirements.txt
   python -m app.registry_import
   ```

   Druhý příkaz stáhne registr přípravků. Trvá asi 4 minuty.

**Pokaždé, když chcete aplikaci použít**

1. Otevřete příkazový řádek ve složce aplikace (krok 3 výše) a zadejte:

   ```
   python -m uvicorn app.main:app --port 8000
   ```

2. V prohlížeči otevřete http://localhost:8000.
3. Aplikaci ukončíte zavřením okna příkazového řádku.

Registr se mění zhruba jednou za měsíc. Aktualizujete ho příkazem `python -m app.registry_import`.

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
