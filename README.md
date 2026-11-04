# cammitaly

Mappa i cammini italiani sui comuni che attraversano. Un solo comando scarica le fonti, costruisce i tracciati, elimina i cammini duplicati, li incrocia con i confini comunali ISTAT e scrive una cartella di lavoro Excel.

## Indice

- [Installazione](#installazione)
- [Uso](#uso)
- [Configurazione](#configurazione)
- [Fonti dati](#fonti-dati)
- [Test](#test)

## Installazione

Richiede Python 3.12 e almeno 1,5 GB di RAM libera (la lettura dell'estratto OpenStreetMap si ferma da sola oltre 1,25 GB). Da una copia pulita del repo:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .
```

Su Windows impostare la codifica UTF-8 prima di ogni comando (PowerShell; in Git Bash `export PYTHONUTF8=1`):

```
$env:PYTHONUTF8 = "1"
```

## Uso

### 1. Passaggio manuale: pagine del Ministero del Turismo

Le pagine di elenco del Catalogo dei Cammini religiosi sono protette da un controllo del browser e non si scaricano da script. Sono l'unico passaggio a mano.

1. Aprire `https://www.ministeroturismo.gov.it/catalogo-dei-cammini-religiosi-italiani/` nel browser.
2. Salvare la pagina (Salva con nome, pagina web completa) e ripetere per ogni pagina di elenco successiva (`.../page/2/`, `.../page/3/`, ... fino all'ultima).
3. Mettere tutti i file in `data/raw/mitur/pagine/` (cartella da creare se manca).

Il nome del file deve iniziare con `p` e finire con `.html` (ad esempio `p01.html`, `p02.html`); il numero di pagina non si legge dal nome ma dal link `canonical` dentro il file. Il programma non modifica mai questi file e, prima di usarli, controlla che siano tutte e sole le pagine dell'elenco, una volta ciascuna: se ne manca una o ce n'è una di troppo (ad esempio dell'anno precedente) si ferma e dice quali.

### 2. Catena completa

```
.venv\Scripts\python.exe -m cammitaly all
```

La cartella di lavoro viene scritta in `data/output/cammini_comuni_<data>.xlsx` (`<data>` = giorno di elaborazione, AAAA-MM-GG). Il comando si ferma al primo errore indicando il passo; dopo aver rimosso la causa basta rilanciarlo: i download già completati restano in `data/raw/` e non si ripetono.

Tempi indicativi della prima esecuzione, circa due ore in tutto:

| Passo | Tempo |
|---|---|
| `fetch webmapp` | circa 25 min |
| `fetch mitur` | circa 20 min (circa 100 zip, pausa di 10 s) |
| `fetch osm` | dipende dalla rete (circa 2 GB) |
| `build osm` | circa 7 min |
| `osm-match` | circa 20 min |
| `dedup` | 8-13 min |
| tutti gli altri | pochi minuti ciascuno |

### 3. Passi singoli (rielaborazioni parziali)

`all` esegue nell'ordine: `fetch` di tutte le fonti, `build cdi`, `build mitur`, `build osm`, `tag`, `osm-match`, `dedup`, `pairs`, `workbook`. Ogni passo si può rilanciare da solo con `.venv\Scripts\python.exe -m cammitaly <comando>`; i file prodotti stanno in `data/interim/`, salvo dove indicato.

| Comando | Cosa fa | Richiede | Uscita |
|---|---|---|---|
| `fetch camminiditalia` | catalogo dei cammini e delle tappe | | `camminiditalia_cammini.csv`, `camminiditalia_tappe.csv` |
| `fetch webmapp` | elenco e geometria delle tappe | | `webmapp_tracks.csv` |
| `fetch mitur` | legge le pagine salvate a mano, scarica ed estrae i zip | pagine del passo 1 | `mitur_cammini.csv` |
| `fetch istat` | confini comunali ISTAT | | `data/raw/istat/` |
| `fetch osm` | estratto Italia di Geofabrik, con verifica md5 | | `data/raw/osm/` |
| `build cdi` | assegna ogni traccia Webmapp al suo cammino | `fetch camminiditalia`, `fetch webmapp` | `tratte_cdi.gpkg`, `cammini_cdi.csv`, `limbo_cdi.csv` |
| `build mitur` | legge i file GPX, KML e KMZ dei zip | `fetch mitur` | `tratte_mitur.gpkg`, `cammini_mitur.csv`, `limbo_mitur.csv` |
| `build osm` | legge le relazioni di percorso dell'estratto | `fetch osm` | `tratte_osm.gpkg`, `cammini_osm.csv`, `limbo_osm.csv` |
| `tag` | etichetta tratte e cammini per mezzo e carattere religioso | i tre `build` | `etichette_tratte.csv`, `etichette_cammini.csv` |
| `osm-match` | abbina i cammini del catalogo alle relazioni OSM | i tre `build`, `fetch istat` | `osm_match/` |
| `dedup` | riconosce i cammini presenti in entrambe le fonti | `build cdi`, `build mitur`, `osm-match` | `dedup/` |
| `pairs` | comuni attraversati da ogni cammino distinto, in ordine di percorso | `tag`, `osm-match`, `dedup`, `fetch istat` | `comuni/` |
| `workbook` | scrive la cartella di lavoro Excel | `pairs`, `fetch istat` | `data/output/cammini_comuni_<data>.xlsx` |
| `calibra` | verifica la distanza `d` sui dati (facoltativo, non fa parte di `all`) | `build cdi`, `build mitur`, `fetch istat` | `calibrazione/` |

`fetch` accetta `--limit N` (webmapp: prime N tracce; mitur: primi N zip; osm: `0` = solo md5 e dimensione, senza pbf) e `--refresh` (ignora la cache e scarica di nuovo). Ogni richiesta resta registrata in `data/raw/requests_log.csv`; l'esecuzione si ferma al primo errore HTTP, reindirizzamento o timeout, senza nuovi tentativi.

### 4. Anno nuovo

Un file già presente in `data/raw/` non viene scaricato di nuovo, quindi per una nuova edizione dei dati:

1. ripetere il passaggio manuale, sostituendo le pagine dell'anno precedente;
2. lanciare `all --refresh`, che scarica di nuovo tutte le fonti.

`data/output/` conserva le cartelle di lavoro degli anni precedenti. I confini ISTAT sono quelli del 1 gennaio dell'anno in corso; se ISTAT non ha ancora pubblicato l'edizione di quest'anno, il comando si ferma subito e indica di fissare l'anno precedente con la variabile `CAMMITALY_ISTAT_YEAR` (ad esempio `$env:CAMMITALY_ISTAT_YEAR = "2025"`).

## Configurazione

I valori stanno in `src/cammitaly/config.py`; la tabella riporta quelli che si possono cambiare.

| Parametro | Dove | Valore | Significato |
|---|---|---|---|
| Distanza `d` | `CROSSING_DISTANCE_M` | 100 m | un cammino è associato a un comune se dista dal confine comunale al più `d` |
| Anno dei confini ISTAT | variabile d'ambiente `CAMMITALY_ISTAT_YEAR` | anno in corso | edizione al 1 gennaio da scaricare e usare |
| Deduplica, nome | `DEDUP_NAME_MIN_SCORE` | 95 | punteggio minimo (0-100) per dire che due nomi sono simili |
| Deduplica, fasce di sovrapposizione | `DEDUP_OVERLAP_MIN`, `DEDUP_DISTINCT_BELOW`, `DEDUP_SECTION_OVERLAP_MIN` | 0,70; 0,50; 0,95 | quota del tracciato più corto vicino all'altro: duplicato sopra la prima, distinti sotto la seconda, in mezzo "da verificare"; sezione sopra la terza |
| Deduplica, vicinanza | `DEDUP_NEAR_M` | 50 m | distanza entro cui due tracciati si considerano sovrapposti |
| Abbinamento OSM, nome | `OSM_MATCH_NAME_MIN_SCORE` | come la deduplica | punteggio minimo per il nome |
| Abbinamento OSM, fasce | `OSM_MATCH_SHARE_MIN`, `OSM_MATCH_LENGTH_RATIO_MIN`, `OSM_MATCH_LENGTH_RATIO_MAX` | 0,70; 0,5; 1,5 | quota di sovrapposizione minima e intervallo del rapporto tra lunghezza OSM e lunghezza del cammino |
| Pause e tetti di richieste | `HOSTS` | per host | pausa minima tra richieste e numero massimo per esecuzione |

Dopo aver cambiato un parametro si rilanciano il passo che lo usa e quelli successivi (tabella dei passi singoli).

## Fonti dati

| Fonte | Contenuto | Licenza e crediti |
|---|---|---|
| camminiditalia.org | cammini con scheda tecnica e tappe | licenza non dichiarata |
| Webmapp (app Cammini d'Italia) | tracce GPS delle tappe | mappa CC BY-NC-SA; tracce senza licenza dichiarata |
| Ministero del Turismo, Catalogo dei Cammini religiosi | schede e zip di tracce GPS | licenza non verificata; fonte: Ministero del Turismo |
| OpenStreetMap, estratto Geofabrik | relazioni di percorso | ODbL, © OpenStreetMap contributors |
| ISTAT | confini amministrativi non generalizzati al 1 gennaio | CC BY 4.0, fonte: ISTAT |

Regole e conteggi del metodo: `report/notes.md`.

## Test

```
.venv\Scripts\python.exe -m pytest -q
```

I test non usano la rete. Il test sulla pagina reale `p01.html` richiede il file salvato a mano e viene saltato se manca.
