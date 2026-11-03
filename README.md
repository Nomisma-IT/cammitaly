# cammitaly

Acquisizione dei dati per mappare i cammini italiani sui comuni che attraversano. Questa fase scarica e ordina le fonti; geometrie, deduplica, incrocio con i comuni ed esportazione xlsx sono fasi successive.

## Indice

- [Installazione](#installazione)
- [Uso](#uso)
- [Configurazione](#configurazione)
- [Fonti dati](#fonti-dati)
- [Test](#test)

## Installazione

Richiede Python 3.12. Il pacchetto si installa in modalità editabile (scelta unica; non serve `PYTHONPATH`).

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .
```

## Uso

```
.venv\Scripts\python.exe -m cammitaly fetch <fonte> [--limit N] [--refresh]
```

| Fonte | Cosa fa | `--limit N` | Richieste (circa) |
|---|---|---|---|
| `camminiditalia` | catalogo dei cammini e tappe (CSV in `data/interim/`) | ignorato | 3 (una HEAD per il numero di pagine, poi 2 pagine) |
| `webmapp` | configurazione, elenco e geometria delle tappe | prime N tracce | 2 + numero di tracce |
| `mitur` | legge le pagine salvate a mano, scarica ed estrae i zip (file piatti `<NNNN>_<nome>` e `manifest.csv` per cartella) | primi N zip | uno per zip (99 zip distinti) |
| `istat` | confini comunali al 1 gennaio 2026 | ignorato | 2 |
| `osm` | estratto Italia di Geofabrik, con verifica md5 | `0` = solo md5 e dimensione, senza pbf | 2 o 3 |

- Un file già presente in `data/raw/` non viene scaricato di nuovo; `--refresh` forza il nuovo download.
- L'esecuzione si ferma al primo errore HTTP (qualunque codice 4xx o 5xx), al primo reindirizzamento 3xx, al primo timeout o errore di rete, a un download più corto del `Content-Length` dichiarato o al tetto di richieste per host. Nessun nuovo tentativo automatico; i redirect non si seguono.
- Ogni richiesta resta registrata in `data/raw/requests_log.csv`.

### Geometrie

```
.venv\Scripts\python.exe -m cammitaly build <fonte>
```

| Fonte | Cosa fa | Uscite in `data/interim/` |
|---|---|---|
| `cdi` | assegna ogni traccia Webmapp al suo cammino, pulisce e proietta in EPSG:32632 | `tratte_cdi.gpkg` (layer `tratte`), `cammini_cdi.csv`, `limbo_cdi.csv` |
| `mitur` | legge i file GPX, KML e KMZ dei zip del ministero, classifica ogni traccia, scarta i duplicati interni | `tratte_mitur.gpkg` (layer `tratte`), `cammini_mitur.csv`, `limbo_mitur.csv` |
| `osm` | legge le relazioni di percorso (a piedi o pellegrinaggio) dell'estratto Italia, una tratta per relazione | `tratte_osm.gpkg` (layer `tratte`), `cammini_osm.csv`, `limbo_osm.csv` |

`cdi` richiede `fetch camminiditalia` e `fetch webmapp` già eseguiti; `mitur` richiede `fetch mitur`; `osm` richiede `fetch osm` (il pbf) e circa 7 minuti con picco di memoria sotto 1,25 GB: si ferma da solo oltre quel limite, quindi servono almeno 1,5 GB di RAM libera. Le regole e i conteggi sono in `report/notes.md`.

### Pagine del Ministero del Turismo (passaggio manuale)

Le pagine di elenco del Catalogo dei Cammini religiosi sono protette da un controllo del browser e non si scaricano da script. Vanno salvate a mano:

1. aprire il catalogo nel browser e salvare ogni pagina di elenco (Salva con nome, pagina web completa) come `p01.html`, `p02.html`, ... in `data/raw/mitur/pagine/`;
2. lanciare `.venv\Scripts\python.exe -m cammitaly fetch mitur`.

Il numero di pagina si legge dal link `canonical` dentro il file, non dal nome del file. I file in quella cartella non vengono mai modificati dal programma. Si scaricano solo link sotto `https://www.ministeroturismo.gov.it/wp-content/uploads/` che finiscono in `.zip`; ogni altro link di "Scarica percorso" viene ignorato e segnalato in una riga di avviso.

## Configurazione

Tutte le costanti (percorsi, URL, pause tra richieste, tetti per esecuzione, user agent) stanno in `src/cammitaly/config.py`.

| Host | Pausa minima | Tetto per esecuzione |
|---|---|---|
| camminiditalia.org | 11 s | 6 |
| camminiditalia.maphub.it | 6 s | 5 |
| wmfe.s3.eu-central-1.amazonaws.com | 1 s | 1400 |
| www.ministeroturismo.gov.it (solo zip) | 10 s | 150 |
| www.istat.it | 5 s | 5 |
| download.geofabrik.de | 5 s | 3 |

## Fonti dati

| Fonte | Contenuto | Licenza |
|---|---|---|
| camminiditalia.org | 136 cammini con scheda tecnica e tappe | non dichiarata |
| Webmapp (app Cammini d'Italia) | tracce GPS delle tappe, una per tappa | mappa CC BY-NC-SA; tracce senza licenza dichiarata |
| Ministero del Turismo | Catalogo dei Cammini religiosi, zip di tracce GPS | non verificata |
| ISTAT | confini amministrativi al 1 gennaio 2026, versione non generalizzata | CC BY 4.0 |
| OpenStreetMap (Geofabrik) | estratto Italia | ODbL |

Note sul metodo e sui conteggi: `report/notes.md`.

## Test

```
.venv\Scripts\python.exe -m pytest -q
```

I test non usano la rete. Il test sulla pagina reale `p01.html` richiede il file salvato a mano e viene saltato se manca.
