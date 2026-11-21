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
- Uživatel se registruje e-mailem a heslem (účty se ukládají do `data/users.sqlite`). Ve „Mém skladu“ si vede nakoupené přípravky; ty se ve výsledku zobrazí jako první a dají se zobrazit samostatně. U každého přípravku může zadat svou nákupní cenu (Kč za l nebo kg); vyhodnocení pak ukáže náklady na hektar a na celý díl a evidence postřiků náklady každého zásahu i součet za období. Veřejný ceník profesionálních přípravků v ČR neexistuje (ceny se liší podle distributora a smlouvy), proto ceny zadává každý sám.
- **Moje pole:** uživatel zadá své ID JI (číslo uživatele v LPIS), nebo klikne v mapě na kterékoli své pole a zvolí „Tohle je moje pole“. Aplikace načte z veřejného LPIS všechny jeho díly, zvýrazní je v mapě a ukáže u nich názvy. Pole si může pojmenovat (např. „Za humny“), vyhledat podle názvu nebo kódu a vybrat kliknutím. Volba „V mapě ukázat jen moje pole“ skryje cizí díly. Přihlašovat se do eAGRI není potřeba: ID JI uživatele je u každého dílu ve veřejné WFS službě LPIS. Názvy se ukládají do `data/users.sqlite` a po novém načtení z LPIS zůstanou.
- **Podpora:** v horní liště je stránka „Podpora“ s formulářem pro nahlášení chyby, návrh nové funkce nebo dotaz. Zpráva se vždy uloží do `data/users.sqlite` a zároveň se odešle e-mailem přes bezplatnou službu [Web3Forms](https://web3forms.com). K chybě se dají přiložit technické údaje (prohlížeč, vybraný pozemek a plodina). Uživatel vidí své zprávy a ty neodeslané může odeslat znovu.
- Rozhraní je průvodce ve třech krocích: 1. pozemek (velká mapa), 2. plodina a trysky, 3. výsledek.
- **Evidence postřiků:** záznam o použití přípravku se vytvoří přímo z výsledku (tlačítko „Zapsat postřik“) s předvyplněným pozemkem, plodinou, přípravkem, evidenčním číslem a dávkou, nebo ručně. Záznam obsahuje údaje podle § 11 odst. 1 vyhlášky č. 132/2018 Sb. (ve znění vyhlášky č. 200/2023 Sb.): identifikaci podniku, čtverec a kód DPB, katastrální území, výměru a datum výsevu plodiny, datum aplikace, ošetřenou výměru, název přípravku, dávku na jednotku, škodlivý organismus, samostatně/směs (S/TM), účinnost (1/0) a datum ukončení pěstování. Navíc evidenční číslo přípravku, BBCH a čas aplikace podle nařízení (EU) 2023/564. U každého záznamu aplikace ukáže, co ještě chybí doplnit. Přehled se filtruje podle roku nebo období a jde stáhnout pro Excel (CSV) nebo vytisknout či uložit jako PDF pro kontrolu.

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

**Nová verze aplikace**

1. Ukončete aplikaci (zavřete okno příkazového řádku).
2. Stáhněte z GitHubu znovu ZIP (**Code → Download ZIP**) a rozbalte ho do nové složky.
3. Ze staré složky zkopírujte celou složku `data` do nové složky (přepište). Jsou v ní vaše účty, sklad, evidence postřiků a stažený registr. Bez ní by se vše ztratilo.
4. V nové složce otevřete příkazový řádek a spusťte aplikaci jako obvykle.

Registr se mění zhruba jednou za měsíc. Aktualizujete ho příkazem `python -m app.registry_import`.

## Známá omezení

- **Vodní toky:** počítají se stálé i občasné toky a vodní plochy ze ZABAGED. Podzemní (zatrubněné) toky se nepočítají. Meliorační příkopy, které v ZABAGED nejsou, aplikace nevidí.
- **OPVZ II. stupně** v LPIS není, proto se u přípravků zobrazí jen upozornění.
- **Kódy plodin:** veřejná služba neobsahuje číselník plodin. Přiřazení kódů k názvům odhaduje `build_crops` v `app/registry_import.py` z textů v registru.
- **Načtení pozemku** trvá 15–25 s, protože se postupně dotazuje několik vrstev LPIS. Služba MZe občas požadavek odmítne, proto ho aplikace zkouší opakovaně.
- Plodina na pozemku není ve veřejném LPIS. Zadává ji uživatel.
- **Evidence:** export do formátu XML pro Jednotné úložiště dat evidence POR (povinné jen pro podniky nad 200 ha orné půdy, vinic, chmelnic a sadů) zatím není. Katastrální území veřejný LPIS nevrací, zadává se ručně (u dalšího záznamu na stejném dílu se předvyplní).

## Struktura

- `app/registry_import.py`: stažení registru a uložení do SQLite
- `app/lpis.py`: dotazy na LPIS WFS a prostorové kontroly
- `app/evaluate.py`: vyhodnocení přípravků pro pozemek a plodinu
- `app/main.py`: API (FastAPI)
- `app/records.py`: evidence postřiků, export do CSV a tisková sestava
- `app/users.py`: účty, přihlášení a sklad přípravků s cenami; ukládá se do `data/users.sqlite`. Hesla se neukládají, jen jejich otisk (scrypt se solí). Přihlašovací cookie je náhodný token a v databázi je jen jeho otisk (SHA-256). Po 5 špatných heslech za 15 minut se přihlášení na chvíli zablokuje.
- `app/prices.py`: přepočet ceny za l/kg na náklady na hektar, díl a záznam postřiku
- `app/static/index.html`: webové rozhraní (Leaflet). Fotka na přihlašovací obrazovce: James Baltz, [Unsplash](https://unsplash.com/photos/agricultural-sprayers-in-green-corn-field-jAt6cN6zl8M) (licence Unsplash).

## Můj účet a správce

- Na stránce **Můj účet** (v horní liště, nebo kliknutím na své jméno) jde změnit jméno a heslo, odhlásit se na ostatních zařízeních a **smazat účet** se všemi daty (sklad, pole, evidence, zprávy podpory). Smazání se potvrzuje heslem.
- **Správce** na stejné stránce vidí seznam všech uživatelů a může je odhlásit nebo smazat. Jen správce může nastavit odesílání zpráv z Podpory.
- Kdo je správce: e-maily v proměnné prostředí `ADMIN_EMAILS` (oddělené čárkou), nebo v `data/nastaveni.json` jako `"spravci": ["vas@email.cz"]`. Když není nastaveno nic, správcem je nejstarší účet v aplikaci. **Na veřejném webu `ADMIN_EMAILS` nastavte**, ať se správcem nestane náhodou někdo jiný.
- Po této aktualizaci se všichni musí jednou znovu přihlásit (staré přihlášení se kvůli bezpečnějšímu ukládání zruší).

## Nastavení: název aplikace a e-mail pro podporu

- **Název aplikace** je v souboru `app/config.py` (`APP_NAME`). Po změně se nový název ukáže v hlavičce, na přihlašovací stránce, v záložce prohlížeče i na tiskové sestavě.
- **Odesílání zpráv z Podpory na e-mail** se nastaví přímo v aplikaci: na stránce Podpora je karta „Nastavit odesílání na e-mail“. Na [web3forms.com](https://web3forms.com) zadáte e-mail, kam mají zprávy chodit, klíč z e-mailu vložíte do aplikace a uložíte. Klíč se uloží do `data/nastaveni.json`, tedy jen u vás na počítači (není v gitu ani na GitHubu) a při aktualizaci se přenese se složkou `data`. Dokud klíč není nastavený, zprávy se jen ukládají v aplikaci.
