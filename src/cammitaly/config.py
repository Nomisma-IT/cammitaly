"""Unica sede di tutte le costanti: percorsi, URL, ritmo delle richieste, tetti."""
import os
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

# Radice del repo: src/cammitaly/config.py -> due livelli sopra la cartella del pacchetto
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
REQUESTS_LOG = RAW_DIR / "requests_log.csv"
REQUESTS_LOG_FIELDS = ("utc_iso", "host_key", "url", "status", "bytes", "seconds")

# User agent esplicito: chi gestisce i server vede subito chi sta leggendo
USER_AGENT = "Mozilla/5.0 (compatible; cammitaly/0.1; studio Nomisma, accesso con cache e pause)"
REQUEST_TIMEOUT_S = 60
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class HostPolicy:
    min_interval_s: float  # pausa minima tra due richieste allo stesso host
    max_requests: int  # tetto di richieste per singola esecuzione


# Chiavi host usate in fetch() e nel registro delle richieste
HOST_CDI = "camminiditalia"
HOST_MAPHUB = "maphub"
HOST_S3 = "s3"
HOST_MITUR = "mitur"
HOST_ISTAT = "istat"
HOST_GEOFABRIK = "geofabrik"
HOSTS: dict[str, HostPolicy] = {
    HOST_CDI: HostPolicy(11.0, 6),  # robots.txt: Crawl-delay 10
    HOST_MAPHUB: HostPolicy(6.0, 5),
    HOST_S3: HostPolicy(1.0, 1400),  # hosting statico S3, 1 richiesta/s e' molto cortese; webmapp lo adegua all'elenco (sotto)
    HOST_MITUR: HostPolicy(10.0, 150),  # solo zip, mai pagine di elenco (controllo Radware)
    HOST_ISTAT: HostPolicy(5.0, 5),  # file statici su www.istat.it, non il servizio SDMX
    HOST_GEOFABRIK: HostPolicy(5.0, 3),
}

# --- camminiditalia.org (WordPress REST) ---
CDI_RAW_DIR = RAW_DIR / "camminiditalia"
CDI_PAGE_SIZE = 100
CDI_FIELDS = "id,slug,link,title,modified,acf,class_list,lang"
CDI_TOTAL_PAGES_HEADER = "x-wp-totalpages"  # in minuscolo, come le intestazioni di http.fetch
CDI_PAGE_URL = (
    "https://camminiditalia.org/wp-json/wp/v2/cammini"
    f"?per_page={CDI_PAGE_SIZE}&page={{page}}&_fields={CDI_FIELDS}&lang=it"
)
CDI_REGION_CLASS_PREFIX = "regioni-"
CDI_LAYER_PATTERN = r"layer=(\d+)"
CDI_CAMMINI_CSV = INTERIM_DIR / "camminiditalia_cammini.csv"
CDI_TAPPE_CSV = INTERIM_DIR / "camminiditalia_tappe.csv"

# --- Webmapp (app Cammini d'Italia) ---
WEBMAPP_RAW_DIR = RAW_DIR / "webmapp"
WEBMAPP_TRACKS_DIR = WEBMAPP_RAW_DIR / "tracks"
WEBMAPP_CONFIG_URL = "https://wmfe.s3.eu-central-1.amazonaws.com/camminiditalia/1/config.json"
WEBMAPP_LISTING_URL = "https://camminiditalia.maphub.it/api/v2/elasticsearch/?app=geohub_app_1"
WEBMAPP_TRACK_URL = "https://wmfe.s3.eu-central-1.amazonaws.com/camminiditalia/tracks/{track_id}.json"
WEBMAPP_TRACKS_CSV = INTERIM_DIR / "webmapp_tracks.csv"
# Tetto di richieste a S3 in una esecuzione = richieste gia' fatte + tracce dell'elenco + questo margine: il catalogo
# cresce, ma un ciclo fuori controllo si ferma comunque dopo al piu' MARGIN richieste in piu' del necessario
WEBMAPP_REQUEST_MARGIN = 100

# --- Ministero del Turismo, Catalogo dei Cammini religiosi ---
MITUR_RAW_DIR = RAW_DIR / "mitur"
MITUR_PAGES_DIR = MITUR_RAW_DIR / "pagine"  # salvate a mano dal proprietario: sola lettura
MITUR_PAGES_GLOB = "p*.html"
MITUR_ZIP_DIR = MITUR_RAW_DIR / "zip"
MITUR_TRACKS_DIR = MITUR_RAW_DIR / "tracce"
MITUR_CAMMINI_CSV = INTERIM_DIR / "mitur_cammini.csv"
# Unico prefisso da cui si scarica: ogni altro href di "Scarica percorso" viene ignorato e segnalato
MITUR_ZIP_PREFIX = "https://www.ministeroturismo.gov.it/wp-content/uploads/"
# Nome del zip in cache = percorso dopo il prefisso, con ogni carattere non sicuro (anche "/") reso "_"
MITUR_ZIP_NAME_UNSAFE_PATTERN = r"[^A-Za-z0-9._%-]"
MITUR_DISABLED_HREF_SUFFIX = "#"  # il pulsante disattivato punta a "#"
MITUR_NO_EXTENSION = "senza-estensione"
# Zip condiviso da due schede: lo prende la scheda il cui nome somiglia di piu' al nome del file (token_set_ratio 0-100).
# Serve un punteggio minimo e un vantaggio sulla seconda, altrimenti l'attribuzione e' incerta
MITUR_SHARED_ZIP_MIN_SCORE = 80
MITUR_SHARED_ZIP_MARGIN = 10
# Marcatura delle schede nella pagina di elenco
MITUR_CARD_TAG, MITUR_CARD_CLASS = "article", "cr-card"
MITUR_TITLE_TAG, MITUR_TITLE_CLASS = "h3", "cr-card__title"
MITUR_META_CLASS = "cr-meta-line"
MITUR_NOTE_CLASS = "cr-card__note"
MITUR_LOCATION_LABEL = "Localizzazione"
# Distanza della scheda in formato italiano: punto delle migliaia, virgola decimale ("1.050 km", "121,5 km")
MITUR_DISTANCE_LABEL = "Distanza"
MITUR_DISTANCE_PATTERN = r"^(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?\s*km$"
MITUR_SITE_LABEL = "Visita sito web"
MITUR_DOWNLOAD_LABEL = "Scarica percorso"
SLUG_STRIP_PATTERN = r"[^a-z0-9]+"
# Il numero di pagina si legge dal link canonical, non dal nome del file salvato a mano
MITUR_CANONICAL_PATTERN = r'<link\s+rel="canonical"\s+href="([^"]+)"'
MITUR_PAGE_NUMBER_PATTERN = r"/page/(\d+)/?$"
# Paginazione WordPress: ogni numero e' un elemento con classe page-numbers (link o "current"); il piu' alto e' l'ultima pagina
MITUR_PAGINATION_PATTERN = r'class="page-numbers[^"]*"[^>]*>\s*(\d+)\s*<'
MITUR_CATALOG_DATE_PATTERN = r"dal\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*(?:\((.*)\))?"
MESI_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
ZIP_SUFFIX = ".zip"
# Estrazione piatta: file "<NNNN>_<nome>" in una sola cartella, struttura originale nel manifesto.
# Il tetto e' sul percorso completo del file: Windows rifiuta oltre 260 caratteri
ZIP_MAX_STORED_PATH_CHARS = 200
ZIP_UNSAFE_NAME_PATTERN = r'[<>:"/\\|?*\x00-\x1f]'
ZIP_MAX_EXTENSION_CHARS = 10
ZIP_MANIFEST_FILE = "manifest.csv"
ZIP_MANIFEST_FIELDS = ["stored_name", "original_member_path", "zip_name", "nesting_level", "bytes"]
# Nei zip compaiono cartelle di metadati macOS che non sono tracce
ZIP_IGNORED_PARTS = ("__MACOSX",)
# Alcuni zip del ministero contengono altri zip (esportazioni da archivi condivisi)
ZIP_MAX_NESTING = 2
# Tetti larghi contro zip bomb: un zip reale del ministero ha poche decine di file
ZIP_MAX_MEMBERS = 20_000
ZIP_MAX_UNCOMPRESSED_BYTES = 2 * 1024**3

# --- ISTAT, confini amministrativi (file statici) ---
ISTAT_RAW_DIR = RAW_DIR / "istat"
ISTAT_INDEX_URL = (
    "https://www.istat.it/notizia/"
    "confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/"
)
ISTAT_INDEX_FILE = ISTAT_RAW_DIR / "indice.html"
ISTAT_EXTRACT_DIR = ISTAT_RAW_DIR / "estratto"
ISTAT_SOURCE_FILE = ISTAT_RAW_DIR / "SOURCE.txt"
# Anno dell'edizione dei confini: quello in corso, a meno che CAMMITALY_ISTAT_YEAR non lo fissi (letto all'importazione)
ISTAT_YEAR_ENV = "CAMMITALY_ISTAT_YEAR"
ISTAT_EDITION_YEAR = os.environ.get(ISTAT_YEAR_ENV) or str(date.today().year)
# Edizione al 1 gennaio, versione non generalizzata (la generalizzata ha "_g" e cartella generalizzati)
ISTAT_ZIP_LINK_PATTERN = r'href="([^"]*/non_generalizzati/{year}/Limiti0101{year}\.zip)"'

# --- OpenStreetMap, estratto Geofabrik ---
OSM_RAW_DIR = RAW_DIR / "osm"
OSM_PBF_URL = "https://download.geofabrik.de/europe/italy-latest.osm.pbf"
OSM_MD5_URL = OSM_PBF_URL + ".md5"
OSM_PBF_FILE = OSM_RAW_DIR / "italy-latest.osm.pbf"
OSM_MD5_FILE = OSM_RAW_DIR / "italy-latest.osm.pbf.md5"

# --- Geometrie: schema condiviso dai builder delle tratte (cdi, mitur, osm) ---
CRS_SOURCE = "EPSG:4326"  # coordinate di Webmapp, GPX e KML
CRS_WORK = "EPSG:32632"  # UTM 32N: unico sistema metrico del progetto, come i confini ISTAT
FONTI_GEOMETRIA = ("cdi", "mitur", "osm")
TRATTE_LAYER = "tratte"
TRATTE_GPKG_TEMPLATE = str(INTERIM_DIR / "tratte_{fonte}.gpkg")
CAMMINI_CSV_TEMPLATE = str(INTERIM_DIR / "cammini_{fonte}.csv")
LIMBO_CSV_TEMPLATE = str(INTERIM_DIR / "limbo_{fonte}.csv")
TRATTE_ATTRIBUTES = (
    "tratta_id", "cammino_id", "fonte", "nome", "ordine", "tipo",
    "n_vertici", "lunghezza_m", "spaziatura_media_m", "file_origine",
)
CAMMINI_FIELDS = [
    "cammino_id", "fonte", "nome", "regioni", "link", "lunghezza_dichiarata_km",
    "n_tappe_dichiarate", "n_tratte", "lunghezza_m", "stato_geometria", "note",
]
LIMBO_FIELDS = ["oggetto", "motivo", "dettaglio"]
TIPO_TAPPA, TIPO_VARIANTE, TIPO_BICI, TIPO_ALTRO = "tappa", "variante", "bici", "altro"
STATO_OK, STATO_PARZIALE, STATO_ASSENTE = "ok", "parziale", "assente"
METERS_DECIMALS = 2
# Un salto tra due vertici consecutivi piu' lungo di cosi' e' un errore di tracciamento: la parte si spezza
GAP_SPLIT_M = 10_000

# --- Geometrie camminiditalia (cdi): assegnazione delle tracce Webmapp ai cammini ---
CDI_ID_PREFIX = "cdi:"
# Punteggio token_set_ratio (0-100) tra il nome della traccia prima di "Tappa" e il titolo del cammino.
# Sui dati reali dei livelli condivisi il migliore vale almeno 94.3, i pari merito osservati sono a scarto 0
# e la distanza minima dal secondo nei casi non pari e' 4.7 (Rimini - La Verna): la soglia di accettazione
# 85 e il margine di pari merito 4 stanno dentro questi intervalli con spazio da entrambi i lati.
CDI_MATCH_MIN_SCORE = 85
CDI_MATCH_TIE_MARGIN = 4  # candidati a meno di questa distanza dal migliore sono pari merito
# Senza livello utile si cerca tra tutti i cammini con una soglia piu' severa
CDI_FALLBACK_MIN_SCORE = 95
MOTIVO_MULTIPLA = "assegnazione multipla"
MOTIVO_NON_ASSEGNATA = "non assegnata"
MOTIVO_LAYER_ORFANO = "layer senza cammino"
MOTIVO_TRACCIA_SENZA_GEOMETRIA = "traccia senza geometria"
MOTIVO_CAMMINO_SENZA_GEOMETRIA = "cammino senza geometria"
MOTIVO_DUPLICATO_SENZA_LAYER = "duplicato senza layer"
MOTIVO_GEOMETRIA_ILLEGGIBILE = "geometria illeggibile"
MOTIVO_LUNGHEZZA_ANOMALA = "lunghezza anomala"
CDI_FONTE = "cdi"
LAYER_SEPARATOR = "|"  # separatore degli strati in webmapp_tracks.csv e in cammini
# Trattini e apostrofi tipografici ricondotti ad ASCII prima dei confronti tra nomi
NAME_DASHES_PATTERN = "[\u2010-\u2015\u2212]"
NAME_APOSTROPHES_PATTERN = "[\u2018\u2019\u02bc`]"
# Pattern applicati al testo normalizzato (minuscolo, senza accenti, punteggiatura ridotta a spazi)
CDI_STAGE_NUMBER_PATTERN = r"\btappa\s+(?:comune\s+)?(\d+)"
# Dopo il numero di tappa e fino a ':' o '(': un suffisso e' una sola lettera ("03a", "04 N", "05/A") o bis/ter
CDI_STAGE_HEAD_PATTERN = r"\btappa\s+(?:comune\s+)?\d+\s*([^:(]*)"
CDI_STAGE_SUFFIX_PATTERN = r"[a-z]|bis|ter"
CDI_STAGE_LABEL_PATTERN = r"\btappa\s+(?:comune\s+)?\d+\s*([^:]*)"  # tutto cio' che segue il numero fino a ':'
CDI_STAGE_COMUNE_PATTERN = r"\btappa\s+comune\b"
CDI_AFTER_STAGE_PATTERN = r"\btappa\b.*"
# Parole di variante cercate solo dopo "Tappa": prima ci sono nomi di cammini ("Cammino di San Vili (Variante Bassa)")
CDI_VARIANT_WORDS_PATTERN = (
    r"\b(?:variante|alternativ\w*|deviazion\w*|estiva|invernale|opzionale|percorso (?:breve|lungo)|via diretta)\b"
)
CDI_BIKE_PATTERN = r"\b(?:mtb|bike|bici\w*|cicl(?:o|abil|ist)\w*)"
CDI_DECLARED_STAGES_PATTERN = r"^\s*(\d+)"
# Controllo di coerenza: lunghezza della tratta / distanza dichiarata fuori da questo intervallo = anomala
CDI_LENGTH_RATIO_MIN = 0.5
CDI_LENGTH_RATIO_MAX = 2.0

# --- Geometrie MiTur: lettura dei file GPS dei zip del ministero e classificazione delle tratte ---
MITUR_FONTE = "mitur"
MITUR_ID_PREFIX = "mitur:"
MITUR_GPX_EXTENSION, MITUR_KML_EXTENSION, MITUR_KMZ_EXTENSION = ".gpx", ".kml", ".kmz"
# Ordine di preferenza tra due copie della stessa tappa in formati diversi: vince il primo
MITUR_FORMAT_PREFERENCE = (MITUR_GPX_EXTENSION, MITUR_KML_EXTENSION, MITUR_KMZ_EXTENSION)
# Rumore dei zip (documenti, metadati di sistema, immagini): non e' una traccia e non va in limbo
MITUR_NOISE_EXTENSIONS = (".pdf", ".ds_store", ".qmd", ".png", ".jpg", ".jpeg", ".txt", ".docx", ".doc", ".html", ".htm")
# File non fidato: tetto sul XML letto (anche dentro i KMZ). Il prologo (decodificato come dichiara la sua codifica,
# anche UTF-16) non deve avere dichiarazioni di entita' ne' un DOCTYPE con sottoinsieme interno
MITUR_MAX_XML_BYTES = 64 * 1024**2
MITUR_XML_PROLOG_BYTES = 64 * 1024
MITUR_XML_FORBIDDEN_TOKEN = "<!entity"
MITUR_XML_DOCTYPE_SUBSET_PATTERN = r"<!doctype[^>\[]*\["
MITUR_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
# Alcuni GPX (CompeGPS) usano prefissi di namespace mai dichiarati dentro <extensions>: si riprova senza quel blocco
MITUR_EXTENSIONS_BLOCK_PATTERN = r"<extensions[^>]*/>|<extensions[^>]*>.*?</extensions>"
# Un file dichiarato UTF-8 puo' contenere byte cp1252 (un trattino lungo scritto come 0x96): ultimo tentativo
MITUR_FALLBACK_ENCODING = "cp1252"
# Estensione assente o sconosciuta: si guardano i primi byte (un KMZ e' uno zip)
MITUR_SNIFF_BYTES = 4096
MITUR_ZIP_MAGIC = b"PK"
MITUR_SNIFF_MARKERS = ((b"<gpx", MITUR_GPX_EXTENSION), (b"<kml", MITUR_KML_EXTENSION))
# Nomi degli elementi (senza namespace) letti nei file GPX e KML
GPX_LINE_TAGS, GPX_POINT_TAGS, GPX_WAYPOINT_TAG = ("trkseg", "rte"), ("trkpt", "rtept"), "wpt"
KML_PLACEMARK_TAG, KML_NAME_TAG, KML_POINT_TAG = "Placemark", "name", "Point"
KML_LINE_TAG, KML_COORDINATES_TAG, KML_TRACK_TAG, KML_TRACK_COORD_TAG = "LineString", "coordinates", "Track", "coord"
# Una tappa a piedi piu' lunga di cosi' e' un percorso intero o un asse mal nominato: diventa altro
MITUR_MAX_STAGE_LENGTH_M = 100_000
# Riquadro (lon_min, lat_min, lon_max, lat_max) che contiene l'Italia con Sicilia, Sardegna e isole minori
MITUR_ITALY_BBOX = (6.4, 35.4, 18.9, 47.2)
MOTIVO_SOLO_PUNTI = "solo punti"
MOTIVO_FILE_ILLEGGIBILE = "file illeggibile"
MOTIVO_FORMATO_NON_LETTO = "formato non letto"
MOTIVO_DUPLICATO_INTERNO = "duplicato interno"
MOTIVO_FUORI_ITALIA = "fuori Italia"
MOTIVO_SENZA_PERCORSO = "senza percorso ministeriale"
# Duplicato interno a un cammino: distanza di Hausdorff sotto la soglia e lunghezze entro la tolleranza relativa
MITUR_DUPLICATE_HAUSDORFF_M = 25
MITUR_DUPLICATE_LENGTH_TOLERANCE = 0.05
# Percorso intero consegnato accanto alle tappe: una tratta a piedi che contiene (entro la distanza) almeno
# MIN_COVERED tratte di tipo tappa, ciascuna lunga al massimo PART_RATIO volte l'intera e tutte insieme almeno
# SUM_RATIO volte l'intera, e' l'unione delle tappe. Le varianti brevi su una tappa o una sua copia non bastano.
# Si valutano solo le tratte lunghe almeno LENGTH_FACTOR volte la mediana del cammino
MITUR_FULL_ROUTE_MIN_COVERED = 3
MITUR_FULL_ROUTE_MAX_DISTANCE_M = 25
MITUR_FULL_ROUTE_LENGTH_FACTOR = 2
MITUR_FULL_ROUTE_PART_RATIO = 0.75
MITUR_FULL_ROUTE_SUM_RATIO = 0.6
MITUR_COVERAGE_SAMPLE_POINTS = 100  # vertici campionati per tratta nel controllo di copertura
# Pattern sul nome normalizzato (minuscolo, ASCII, punteggiatura a spazi); ordinali e gradi si tolgono prima
MITUR_ORDINAL_CHARS = "ºª°"
# Identificativo numerico messo davanti dal servizio di esportazione ("t231091934_sdf 01 ascoli", "1662931317490-csgt001")
MITUR_EXPORT_ID_PATTERN = r"^t?\d{6,}\s+"
# (pattern, ammette suffisso): il primo che trova un numero decide. Nomi reali: "Tappa-10-...", "1 Tappa - ...",
# "Fiore 01a tappa", "01.Molfetta", "CB-61-...", "CSN 1 - ...", "T1_Rimini", "celestino_t10", "FW01 - ..."
MITUR_STAGE_NUMBER_RULES: tuple[tuple[str, bool], ...] = (
    # "1 tappa - 3 tratto": il numero davanti a "tappa" vince su quello che segue (e' un tratto della tappa)
    (r"\b0*(\d{1,3})[a-z]?(?:\s+[a-z\d]{1,2})?\s*tappa\b", False),  # "01a tappa": la 'a' e' un ordinale
    (r"\btappa\s*0*(\d{1,3})(?!\d)", True),
    (r"\btappa\s+[a-z]{1,4}\s+0*(\d{1,3})(?!\d)", True),  # "tappa-msa-01-da-troia"
    (r"^0*(\d{1,3})(?!\d)", True),
    (r"(?<![a-z])[a-z]{0,3}t0*(\d{1,3})(?!\d)", True),
    (r"^[a-z]{1,4}\s?0*(\d{1,3})(?!\d)", True),
    (r"^[a-z]{1,4}(?:\s[a-z]{1,5}){0,2}\s0*(\d{1,3})(?!\d)", True),  # "csj-del-nord-48-da-chiaramonti"
    (r"\b[a-z]{2}0*(\d{2})(?!\d)", True),
)
# Suffisso attaccato al numero: una lettera ("05a") o bis/ter, come in cdi. La 'v' e' esclusa: nelle tracce di
# Santa Giulia chiude il nome di quasi ogni tappa ("t004v") e non indica una variante
MITUR_STAGE_SUFFIX_PATTERN = r"(?:\s*(?:bis|ter)|[a-uw-z])(?![a-z0-9])"
MITUR_VARIANT_PATTERN = (
    r"(?<![a-z])(?:variant\w*|var|alternativ\w*|deviazion\w*|estiv\w*|invernal\w*|opzion\w*|wild|bis"
    r"|percorso (?:breve|lungo)|via diretta)(?![a-z])"
)
MITUR_BIKE_PATTERN = r"(?:mtb|bike|bici|cicl(?:o|abil|ist))"
# Percorso ciclopedonale, "strada pedonale ciclabile": si cammina anche a piedi, non e' una traccia per la bici
MITUR_SHARED_PATH_PATTERN = r"ciclopedonal\w*|pedonal\w*\s+cicl\w*|cicl\w*\s+pedonal\w*"
# Percorso adatto a chi cammina e a chi pedala ("per camminanti e mtb"): si cammina, quindi non e' bici
MITUR_WALKER_PATTERN = r"(?<![a-z])(?:camminant|piedi(?![a-z])|pedon)"  # "Piediluco" non e' "a piedi"
# Percorso intero o asse che attraversa il cammino ("ASSE_CENTRALE porto-torres-cagliari"): non e' una tappa
MITUR_COMPLETE_PATTERN = (
    r"(?<![a-z])(?:complet\w*|intero|intera|integral\w*|totale|full|unica traccia|tutto il percorso|asse|axis)(?![a-z])"
)
LON_LAT_LIMITS = (180.0, 90.0)  # |lon| e |lat| massimi; coordinate oltre questi valori sono errori del file

# --- Geometrie OSM: relazioni di percorso dell'estratto Geofabrik, lette con pyosmium in tre passate ---
OSM_FONTE = "osm"
OSM_ID_PREFIX = "osm:r"  # tratta_id e cammino_id coincidono: una relazione e' un percorso intero, non una tappa
OSM_RELATION_URL = "https://www.openstreetmap.org/relation/{relation_id}"
OSM_TYPE_KEY, OSM_ROUTE_KEY = "type", "route"
OSM_MEMBER_WAY, OSM_MEMBER_RELATION = "w", "r"  # tipi di membro come li riporta pyosmium
OSM_RELATION_TYPES = ("route", "superroute")
# Si scelgono le relazioni a piedi, oppure di qualunque tipo di route purche' marcate come pellegrinaggio
OSM_WALK_ROUTES = ("hiking", "foot", "walking")
OSM_BIKE_ROUTES = ("bicycle", "mtb")  # entrano solo con pilgrimage=yes e hanno tipo bici
OSM_PILGRIMAGE_KEY, OSM_PILGRIMAGE_YES = "pilgrimage", "yes"
OSM_KEY_FILTER = (OSM_ROUTE_KEY, OSM_PILGRIMAGE_KEY)  # chiavi per il filtro di libosmium, prima del controllo dei valori
# Ruoli dei membri (way e sotto-relazioni): le varianti ufficiali fanno parte del cammino e vanno in una tratta
# a parte (tipo variante); approach ed excursion sono raccordi e escursioni, fuori dal cammino; ogni altro
# ruolo (vuoto, main, forward, backward, sconosciuto) e' il percorso principale. Il ruolo si eredita nelle sotto-relazioni
OSM_VARIANT_ROLE_PATTERN = r"^alternat"  # alternative, alternate, alternative:2, ...
OSM_EXCLUDED_ROLES = frozenset(("approach", "excursion"))
OSM_VARIANT_SUFFIX = ":variante"  # tratta_id della tratta delle varianti: osm:r<id>:variante
OSM_MAX_DEPTH = 6  # profondita' massima delle super-relazioni; oltre, i figli si ignorano
OSM_NAME_TAGS = ("name:it", "name", "ref")  # il primo presente e' il nome del cammino
OSM_TAG_COLUMNS = ("ref", "route", "network", "pilgrimage", "operator", "wikidata", "website")
OSM_STRUCTURE_COLUMNS = (
    "is_super", "n_children", "n_children_not_selected", "n_missing_ways", "n_missing_nodes",
    "n_membri_variante", "n_membri_esclusi",
)
CAMMINI_OSM_FIELDS = [*CAMMINI_FIELDS, *OSM_TAG_COLUMNS, *OSM_STRUCTURE_COLUMNS]
# Memoria: l'IdSet denso di libosmium alloca 512 KB per ogni intervallo di id toccato, quindi i nodi si
# leggono a fette di id; le coordinate si tengono come interi a 1e-7 gradi (int32) in array numpy
OSM_NODE_SLICES = 16
OSM_COORD_SCALE = 1e7
OSM_MAX_RSS_MB = 1250  # oltre questo RSS il processo si ferma da solo (il PC ha 1-3 GB liberi)
OSM_RSS_SAMPLE_S = 0.25
OSM_RSS_ABORT_EXIT_CODE = 3
OSM_CHUNK_ROWS = 1500  # tratte per blocco di scrittura del GeoPackage: le geometrie non restano tutte in memoria
# Tag distance: numero con unita' facoltativa (km se manca). Non interpretabile = non dichiarata
OSM_DISTANCE_KEY = "distance"
OSM_DISTANCE_PATTERN = r"^\s*(\d+(?:[.,]\d+)?)\s*(km|m|mi)?\s*$"
OSM_DISTANCE_DEFAULT_UNIT = "km"
OSM_DISTANCE_TO_KM = {"km": 1.0, "m": 0.001, "mi": 1.609344}
OSM_DECLARED_DECIMALS = 3

# --- Etichette dei cammini e delle tratte (tags.py) ---
TAGS_TRATTE_CSV = INTERIM_DIR / "etichette_tratte.csv"
TAGS_CAMMINI_CSV = INTERIM_DIR / "etichette_cammini.csv"
TAGS_TRATTE_FIELDS = ["tratta_id", "cammino_id", "fonte", "mezzo", "evidenza_mezzo"]
TAGS_CAMMINI_FIELDS = [
    "cammino_id", "fonte", "mezzo", "religioso", "evidenza_religioso", "tipologia", "portata", "tema",
    "stagione", "credenziale", "segnaletica", "data_catalogo",
]
# Valori nei dati intermedi: ASCII ("si", mai "sì"); i campi multivalore sono uniti da LIST_SEPARATOR e ordinati
MEZZO_PIEDI, MEZZO_BICI, MEZZO_ENTRAMBI = "piedi", "bici", "entrambi"
MEZZO_NESSUNO = ""  # cammino senza alcuna traccia: nessun dato dice come si percorre
SI, NO = "si", "no"
LIST_SEPARATOR = "|"
EVIDENZA_SILENTE = "nessuna fonte"
EVIDENZA_PIEDI = "pubblicata a piedi"
EVIDENZA_TIPO_BICI = "tipo bici"
EVIDENZA_NOME_ENTRAMBI = "nome: camminanti e bici"
EVIDENZA_PERCORSO_CONDIVISO = "pubblicata a piedi, su percorso ciclopedonale"
EVIDENZA_ROUTE = "route={}"
EVIDENZA_ROUTE_ASSENTE = "route assente"
EVIDENZA_ROUTE_ALTRA = "route={} (non e' un percorso a piedi o in bici: a piedi)"
EVIDENZA_OSM_NETWORK = "network={}"
EVIDENZA_NOME_BICI = "nome: bici"
EVIDENZA_WEBMAPP = "Webmapp: {}"
EVIDENZA_CATALOGO_RELIGIOSO = "Catalogo dei cammini religiosi del Ministero del Turismo"
EVIDENZA_TEMA_CDI = "camminiditalia.org: tema spirituale"
EVIDENZA_TEMA_LAYER = "mappa Cammini d'Italia: tema spirituale"
EVIDENZA_PILGRIMAGE = "OpenStreetMap: indicato come pellegrinaggio"
EVIDENZA_PILGRIMAGE_NOMINATO = "OpenStreetMap: indicato come pellegrinaggio ({})"
EVIDENZA_PILGRIMAGE_NEGATIVO = "OpenStreetMap: indicato come non di pellegrinaggio (pilgrimage={})"
# Parole religiose nel nome di un cammino del catalogo (cdi, mitur): una sola o piu' di una
EVIDENZA_NOME_PAROLA = "il nome contiene la parola {}"
EVIDENZA_NOME_PAROLE = "il nome contiene le parole {}"
EVIDENZA_SEPARATOR = "; "
# Webmapp: attivita' dichiarate dalla traccia (stringa JSON {"<id>": ["hiking", ...]}) e nomi della tassonomia delle
# attivita' (it/en). Un'attivita' per bici o a piedi si riconosce dal nome; la traccia a piedi con un'attivita' per
# bici e' pubblicata per entrambi (la regola piu' inclusiva)
WEBMAPP_CONFIG_FILE = WEBMAPP_RAW_DIR / "config.json"
TAGS_WEBMAPP_BIKE_PATTERN = r"bike|bici|cycling|mtb"
TAGS_WEBMAPP_WALK_PATTERN = r"walking|hiking|passeggiata|trekking"
# OSM: una relazione a piedi in una rete ciclabile (lcn, rcn, ncn, icn; network puo' avere piu' valori separati da ';')
# o con una parola di bici nel nome e' pubblicata anche per la bici. Esclusioni trovate sui nomi reali: le mura
# "ciclopiche", il toponimo "Bicione" e le "Forbici" (passo), che contengono "bici"/"ciclo" ma non c'entrano
OSM_CYCLE_NETWORKS = ("lcn", "rcn", "ncn", "icn")
OSM_VALUE_SEPARATOR = ";"
OSM_BIKE_NAME_EXCLUDE_PATTERN = r"ciclopic\w*|forbici|bicione"
# Marcatura delle icone dei servizi nella scheda MiTur: <img class="cr-ic cr-ic-service" alt="Segnaletica">
MITUR_SERVICE_CLASS = "cr-ic-service"
MITUR_SERVICE_CREDENTIAL, MITUR_SERVICE_SIGNAGE = "Credenziali", "Segnaletica"
TAGS_THEME_RELIGIOUS = "spirituale"
# pilgrimage: qualunque valore e' un si (yes o un nome come "Via di Francesco") tranne questi
OSM_PILGRIMAGE_NEGATIVE = ("no", "n")
# Il refuso della fonte cdi si normalizza prima del confronto
TAGS_PORTATA_FIXES = {"internazinale": "internazionale"}
# Webmapp riempie solo i vuoti di tipologia: "linear" e' il valore di default anche per i cammini ad anello.
# walking_network (nwn/rwn/iwn) non e' la portata di cdi (nwn e' il valore piu' comune) e non si usa
WEBMAPP_SHAPE_VALUES = {"linear": "lineare", "roundtrip": "anello"}
WEBMAPP_SEASON_VALUES = {"spring": "primavera", "summer": "estate", "autumn": "autunno", "winter": "inverno"}
# Parole che fanno sospettare un cammino religioso: intere, oppure prefissi se finiscono con "*". Aggiunte con la
# prova dei nomi reali: devozional* (cdi "Cammino Devozionale", "Sentiero devozionale"), monaster* ("Via dei
# Monasteri"), sacr* ("Sacro Monte", "Monti Sacri"). Provate e scartate: pieve (toponimi), spiritual* (nessun nome)
TAGS_RELIGIOUS_WORDS = (
    "san", "santa", "santo", "sant", "santi", "santuari*", "madonna", "mater", "francesc*", "frati", "cappuccini",
    "benedett*", "pellegrin*", "romea", "francigena", "abbazi*", "eremo", "eremi", "devozional*", "monaster*",
    "sacr*",
)

# --- Incrocio comuni: quali comuni attraversa un cammino, entro una distanza dal confine comunale ---
# Gli shapefile ISTAT non hanno il file .cpg: senza questa codifica i nomi accentati escono in mojibake
ISTAT_ENCODING = "utf-8"
ISTAT_COMUNI_SHP = ISTAT_EXTRACT_DIR / f"Com0101{ISTAT_EDITION_YEAR}" / f"Com0101{ISTAT_EDITION_YEAR}_WGS84.shp"
ISTAT_REGIONI_SHP = ISTAT_EXTRACT_DIR / f"Reg0101{ISTAT_EDITION_YEAR}" / f"Reg0101{ISTAT_EDITION_YEAR}_WGS84.shp"
ISTAT_PROVINCE_SHP = ISTAT_EXTRACT_DIR / f"ProvCM0101{ISTAT_EDITION_YEAR}" / f"ProvCM0101{ISTAT_EDITION_YEAR}_WGS84.shp"
COMUNE_ID_COL, COMUNE_NAME_COL, COMUNE_REGION_COL = "PRO_COM_T", "COMUNE", "COD_REG"
REGION_CODE_COL, REGION_NAME_COL = "COD_REG", "DEN_REG"
# Unita' territoriale sovracomunale (provincia o citta' metropolitana): codice nello shapefile dei comuni, nome e sigla in ProvCM
PROVINCE_CODE_COL, PROVINCE_NAME_COL, PROVINCE_SIGLA_COL = "COD_UTS", "DEN_UTS", "SIGLA"
ID_SEPARATOR = ":"  # separa la fonte dal resto negli identificativi ("cdi:123", "mitur:slug")
# Tipi di tratta che formano la linea di un cammino: tutti, anche le tracce per bici (decisione del committente)
CROSSING_TIPI = (TIPO_TAPPA, TIPO_VARIANTE, TIPO_ALTRO, TIPO_BICI)
# dentro = la linea tocca o entra nel comune (indipendente dall'arrotondamento di distanza_m)
CROSSING_FIELDS = ("cammino_id", COMUNE_ID_COL, "distanza_m", "dentro", "lunghezza_dentro_m")
# Raggio iniziale del ritaglio locale della linea attorno a un comune; raddoppia finche' la distanza non e' certa
CROSSING_CLIP_START_M = 1000.0

# Distanza d dal confine comunale scelta dal committente: calibrata sui dati (recall saturo, costo contenuto)
CROSSING_DISTANCE_M = 100

# --- Coppie cammino-comune (pairs.py) ---
# Contratto delle uscite: coppie_comuni.csv ha una riga per (cammino distinto, comune); cammini.csv ha una riga per
# ogni cammino distinto di dedup/cammini_unici.csv, anche senza comuni. Valori ASCII, campi multipli uniti da LIST_SEPARATOR
PAIRS_DIR = INTERIM_DIR / "comuni"
PAIRS_FILES = {"coppie": "coppie_comuni.csv", "cammini": "cammini.csv"}
PAIRS_UNICO_COL = "cammino_unico_id"
PAIRS_COPPIE_FIELDS = (
    PAIRS_UNICO_COL, "nome", COMUNE_ID_COL, "comune", "sigla", "cod_uts", "provincia", "cod_reg", "regione", "ordine",
    "mezzo", "religioso", "distanza_m", "dentro", "fonte_geometria",
)
# Etichette multivalore del cammino: unione dei valori dei membri e delle relazioni OSM abbinate. L'evidenza del carattere
# religioso e' a parte: unisce solo le evidenze delle righe che dicono si (altrimenti EVIDENZA_SILENTE)
PAIRS_MULTI_FIELDS = ("tipologia", "portata", "tema", "stagione", "credenziale", "segnaletica", "data_catalogo")
PAIRS_CAMMINI_FIELDS = (
    PAIRS_UNICO_COL, "nome", "alias", "fonti", "membri", "mezzo", "religioso", "evidenza_religioso", *PAIRS_MULTI_FIELDS, "regioni",
    "lunghezza_dichiarata_km", "lunghezza_tracciata_km", "copertura", "fonte_geometria", "nota", "n_comuni",
)
# Colonne degli shapefile ISTAT (comuni, province, regioni) -> colonne di coppie_comuni.csv
PAIRS_COMUNE_COLUMNS = {
    COMUNE_NAME_COL: "comune", PROVINCE_SIGLA_COL: "sigla", PROVINCE_CODE_COL: "cod_uts",
    PROVINCE_NAME_COL: "provincia", COMUNE_REGION_COL: "cod_reg", REGION_NAME_COL: "regione",
}
# fonte_geometria: fonti (FONTI_GEOMETRIA, dal prefisso del tratta_id) delle tracce entro d dal comune, unite da
# LIST_SEPARATOR e ordinate; nel cammino l'unione sulle sue coppie, FONTE_GEOMETRIA_NESSUNA se non ha coppie
FONTE_GEOMETRIA_NESSUNA = "nessuna"
PAIRS_COPERTURA_DECIMALS = 2
PAIRS_LUNGHEZZA_DECIMALS = 1  # lunghezza_tracciata_km
# Ordinamento naturale degli id delle tracce: i gruppi di cifre si confrontano come numeri ("tappa 2" prima di "tappa 10")
NATURAL_SORT_PATTERN = r"(\d+)"
# Ordine dei comuni lungo il percorso. Dorsale: le tracce di questi tipi del principale, nell'ordine delle tappe;
# se il principale non ne ha, quelle del membro che ne ha di piu' lunghe; se nessun membro ha tracce del primo tipo si
# passa al successivo (le tracce altro sono i percorsi interi o senza numero di tappa; poi qualunque tipo, cioe' varianti
# e bici, perche' un cammino con tracce abbia sempre una dorsale e una lunghezza); in ultimo la geometria OSM sostitutiva.
# Lo stesso tipo vale per la lunghezza tracciata: le tracce del tipo di un membro si misurano come unione, la piu' lunga per
# intero e delle altre solo le parti oltre DEDUP_NEAR_M da quanto e' gia' contato (copie e alternative non si sommano)
# Catena delle tappe: una tappa che comincia (o finisce) a meno di cosi' dalla fine della catena e' attaccata ad essa; tra
# le attaccate vince quella di ordine minore (il rumore del GPS non deve scavalcare la numerazione), se nessuna e'
# attaccata (cambio di ramo) vince la piu' vicina
PAIRS_CHAIN_TIE_M = 500
# Ancora di una sezione: la partenza di una sezione e' quella del cammino intero solo se sta a un capo della rete di tappe
# del cammino intero, cioe' a meno di cosi' da un estremo di tappa senza altri estremi di altre tappe entro
# PAIRS_CHAIN_TIE_M (un capo libero). Una sezione che parte da un incrocio a meta' rete non dice dove comincia il cammino
PAIRS_ANCHOR_MAX_M = 2000
PAIRS_TIPO_QUALUNQUE = "qualunque"
PAIRS_SPINE_TIPI = (TIPO_TAPPA, TIPO_ALTRO, PAIRS_TIPO_QUALUNQUE)
# La dorsale si campiona ogni PAIRS_ROUTE_STEP_M metri in corde (tratti tra due campioni consecutivi). Un comune che la
# dorsale attraversa sta alla prima corda che lo tocca (ingresso); uno solo vicino, alla prima corda entro
# CROSSING_DISTANCE_M; uno mai entro d, al punto piu' vicino. A pari posizione prima i comuni attraversati, poi il codice.
# Errore: la posizione vera cade tra l'inizio e la fine della corda, quindi al massimo un passo dopo quella trovata (mezzo
# passo da una parte o dall'altra per un comune mai entro d). Due comuni possono quindi scambiarsi solo se raggiunti a meno
# di 1,5 passi (15 m) l'uno dall'altro. Una corda dista al piu' mezzo passo (5 m) dalla linea reale: un ingresso piu'
# stretto di cosi' non si vede e il comune resta alla posizione del primo tratto entro d (prima, di al piu' d)
PAIRS_ROUTE_STEP_M = 10
# Note del cammino (testi del committente in italiano), unite da PAIRS_NOTA_SEPARATOR. Il limbo di dedup con geometria
# assente dice che manca il confronto; gli altri motivi di limbo riportano il motivo stesso
PAIRS_NOTA_LIMBO_GEOMETRIA = "possibile duplicato di {nome} ({unico}): manca la geometria per il confronto"
PAIRS_NOTA_LIMBO_ALTRO = "possibile duplicato di {nome} ({unico}): {motivo}"
PAIRS_NOTA_NESSUNA_GEOMETRIA = "nessuna geometria disponibile: comuni non determinabili"
PAIRS_NOTA_SEPARATOR = "; "

# --- Confronto tra cammini: nomi e sovrapposizione dei tracciati (primitive di matching.py) ---
# Passo (m) dei punti con cui si misura la quota di una linea vicina a un'altra: con 10 m su soglie di 25 m
# o piu' l'errore della quota e' sotto l'1%, e non si bufferizza mai la linea lunga (secondi per coppia)
MATCH_SAMPLE_M = 10
RATIO_DECIMALS = 4  # quote e rapporti nei CSV
# Le linee si spezzano in pezzi di al massimo tanti vertici per l'indice spaziale: la distanza di un punto da un pezzo
# corto costa poco, da una parte di decine di migliaia di vertici molto
MATCH_PIECE_VERTICES = 64
# Parole che non distinguono un cammino da un altro: tolte dal nome prima del confronto. Articoli e preposizioni
# (anche nelle forme elise, "dell'" -> "dell") e i sostantivi generici dei percorsi. Non si tolgono "alta", "anello",
# "romea" ecc. (distinguono percorsi diversi che condividono le altre parole) ne' "san", "santa" e simili: "Cammino
# di San Carlo" e "Cammino di Carlo Magno" sono cammini diversi, il santo fa parte del nome
NAME_GENERIC_WORDS = frozenset((
    "il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una",
    "di", "del", "dello", "della", "dei", "degli", "delle", "dell", "d",
    "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "dall",
    "a", "al", "allo", "alla", "ai", "agli", "alle", "all",
    "in", "nel", "nello", "nella", "nei", "negli", "nelle", "nell",
    "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle", "sull",
    "con", "per", "tra", "fra", "e", "ed", "o",
    "cammino", "cammini", "via", "vie", "sentiero", "sentieri", "percorso", "percorsi",
    "itinerario", "itinerari", "strada", "strade",
))

# --- Deduplicazione dei cammini tra le due fonti (dedup.py) ---
DEDUP_DIR = INTERIM_DIR / "dedup"
DEDUP_FONTI = (CDI_FONTE, MITUR_FONTE)
# La geometria di un cammino per la deduplica include tutte le tratte, anche bici e altro (decisione del committente)
DEDUP_TIPI = (TIPO_TAPPA, TIPO_VARIANTE, TIPO_BICI, TIPO_ALTRO)
# Regola del committente. Nomi simili (punteggio >= DEDUP_NAME_MIN_SCORE): almeno DEDUP_OVERLAP_MIN del piu'
# corto entro DEDUP_NEAR_M metri dall'altro = lo stesso cammino (duplicato); meno di DEDUP_DISTINCT_BELOW (con entrambe
# le geometrie) = cammini distinti; in mezzo = limbo "da verificare". Nome non simile: il piu' corto contenuto per almeno
# DEDUP_SECTION_OVERLAP_MIN in un altro con almeno una parola in comune nella chiave del nome = sezione dell'intero
# (si fonde); senza parole in comune la coppia resta in limbo. Con una geometria assente e nome simile: limbo.
# Un nome contenuto nell'altro ("Via Francigena" in "Magna Via Francigena nel Sud") con il tracciato contenuto per
# almeno DEDUP_SECTION_OVERLAP_MIN e' una sezione anche se il punteggio lo dice simile (cambia l'etichetta, non la fusione)
DEDUP_OVERLAP_MIN = 0.70
DEDUP_DISTINCT_BELOW = 0.50
DEDUP_SECTION_OVERLAP_MIN = 0.95
DEDUP_NEAR_M = 50
# Soglia sul punteggio dei nomi (token_set_ratio sulle chiavi, 0-100). Sui 34.453 confronti dei dati del 3 novembre
# 2026 le coppie con punteggio 100 sono 76 e nessuna sta tra 88 e 100: la piu' alta sotto e' 87,5 ("Normanna" /
# "Normanno", cammini diversi) e tra 65 e 88 ci sono nomi di cammini diversi con una parola in comune o un refuso;
# l'unica coppia di questa fascia con il tracciato contenuto e' quella delle Colline Novaresi (84,2), due cammini
# distinti. Ogni soglia tra 87,6 e 100 da' lo stesso insieme: 95 sta in mezzo con margine da entrambi i lati
DEDUP_NAME_MIN_SCORE = 95
# Griglia di sensibilita': soglia sul nome x quota minima del duplicato x quota sotto cui e' distinto x distanza.
# Le quote e la distanza sono centrate sui valori adottati (0,70 / 0,50 / 50 m); i due estremi della fascia in
# mezzo non si incrociano mai (il piu' alto dei distinti coincide col piu' basso dei duplicati)
DEDUP_SENS_NAME_SCORES = (80, 85, 90, 95)
DEDUP_SENS_OVERLAPS = (0.60, 0.65, 0.70, 0.75, 0.80)
DEDUP_SENS_DISTINCT_BELOW = (0.40, 0.50, 0.60)
DEDUP_SENS_NEAR_M = (25, 50, 100)
DEDUP_NEAR_VALUES_M = tuple(sorted({DEDUP_NEAR_M, *DEDUP_SENS_NEAR_M}))
# coppie.csv tiene i candidati: nome almeno cosi' simile (la soglia piu' bassa della griglia) oppure tracciati che si toccano
DEDUP_CANDIDATE_NAME_MIN = min(DEDUP_SENS_NAME_SCORES)
CLASSE_DUPLICATO, CLASSE_SEZIONE, CLASSE_LIMBO, CLASSE_DISTINTO = "duplicato", "sezione", "limbo", "distinto"
CLASSE_QUASI_DUPLICATO = "quasi duplicato"
MOTIVO_DUPLICATO = "nome simile, tracciato coincidente"
MOTIVO_NOME_SI_TRACCIATO_NO = "nome simile, tracciato diverso"  # classe distinto
MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO = "nome simile, sovrapposizione da verificare"  # classe limbo
MOTIVO_SEZIONE = "tracciato contenuto in un altro, parola in comune nel nome"
MOTIVO_TRACCIATO_SI_NOME_NO = "tracciato coincidente, nome diverso"  # nessuna parola in comune
MOTIVO_NOME_SI_GEOMETRIA_ASSENTE = "nome simile, geometria assente"
MOTIVO_DISTINTO = "nome e tracciato diversi"
MOTIVO_DISTINTO_GEOMETRIA_ASSENTE = "nome diverso, geometria assente"
DEDUP_PAIR_SEPARATOR = "|"  # oggetto del limbo: "cammino_a|cammino_b"
DEDUP_FILES = {
    "coppie": "coppie.csv", "limbo": "limbo_dedup.csv",
    "sensibilita": "sensibilita.csv", "quasi": "quasi_duplicati.csv", "comuni": "confronto_comuni.csv",
    "unici": "cammini_unici.csv", "membri": "membri.csv",
}
DEDUP_COPPIE_FIELDS = (
    "cammino_a", "cammino_b", "fonte_a", "fonte_b", "nome_a", "nome_b", "punteggio_nome", "punteggio_nome_sort",
    "nome_contenuto", "parola_comune", "quota_a_in_b", "quota_b_in_a", "quota_minore", "lunghezza_a_m", "lunghezza_b_m",
    "rapporto_lunghezza", "stato_a", "stato_b", "geometria_osm", "classe", "motivo",
)
# Cammini senza tracce proprie: la deduplica usa la geometria OSM abbinata (osm_match.py), se c'e'.
# La colonna geometria_osm di coppie.csv dice quale lato della coppia la usa
DEDUP_OSM_SIDE_A, DEDUP_OSM_SIDE_B, DEDUP_OSM_SIDE_BOTH, DEDUP_OSM_SIDE_NONE = "a", "b", "entrambi", ""
# Tabella dei cammini distinti (componenti connesse di duplicato e sezione) e dei loro membri
DEDUP_UNICO_ID_FORMAT = "u{number:03d}"
DEDUP_UNICI_FIELDS = ("cammino_unico_id", "nome", "fonti", "n_membri", "membri", "alias", "stato", "catena")
DEDUP_MEMBRI_FIELDS = ("cammino_unico_id", "cammino_id", "fonte", "nome", "ruolo")
RUOLO_PRINCIPALE, RUOLO_DUPLICATO, RUOLO_SEZIONE = "principale", "duplicato", "sezione"
DEDUP_LIST_SEPARATOR = "|"
# Stato del cammino unico: il migliore tra quelli dei membri
DEDUP_STATO_ORDER = (STATO_OK, STATO_PARZIALE, STATO_ASSENTE)
# n_duplicato + n_sezione + n_limbo + n_distinto = tutte le coppie confrontate
DEDUP_SENSIBILITA_FIELDS = (
    "soglia_nome", "soglia_quota", "soglia_distinto", "distanza_m", "n_duplicato", "n_sezione", "n_limbo", "n_distinto",
    "n_nome_100", "n_nome_100_tracciato_diverso", "n_nome_100_geometria_assente",
)
# Coppie quasi duplicate (solo diagnosi dei tratti lontani): nome come per i duplicati, quota minore da questo valore a DEDUP_QUASI_OVERLAP_MAX
DEDUP_QUASI_OVERLAP_MIN = 0.5
DEDUP_QUASI_OVERLAP_MAX = 0.95  # la soglia della prima prova (95%): oltre, il corto sta nell'altro per intero
DEDUP_QUASI_FIELDS = (
    "cammino_a", "cammino_b", "nome_a", "nome_b", "punteggio_nome", "quota_minore", "cammino_corto",
    "lunghezza_a_m", "lunghezza_b_m", "n_segmenti_lontani", "km_lontani", "km_segmento_max", "distanza_max_m",
    "tratta_segmento_max", "tipo_segmento_max", "km_lontani_non_tappa", "diagnosi",
)
# Tra due punti campionati consecutivi di una parte la distanza non supera il passo; un salto maggiore (con un po'
# di tolleranza numerica) è il passaggio a un'altra parte non contigua: li' un segmento lontano si interrompe
DEDUP_STEP_TOLERANCE = 1.000001
# Formati delle uscite: cifre decimali dei punteggi dei nomi nei CSV, dei numeri nel dettaglio del limbo (punteggio
# e km; le quote usano RATIO_DECIMALS), dei km
SCORE_DECIMALS = 2
DEDUP_DETAIL_DECIMALS = 1
KM_DECIMALS = 3
DEDUP_NOT_AVAILABLE = "n.d."  # numero mancante nel dettaglio del limbo
# Diagnosi in parole di un tratto che nel cammino piu' corto sta lontano dall'altro, in quest'ordine:
# 1 sfasamento: i punti lontani non superano questa distanza, il tracciato e' lo stesso spostato di lato
DEDUP_OFFSET_MAX_M = 150
# 2 variante: piu' della meta' dei km lontani sta in tratte di tipo variante, bici o altro
# 3 tappe mancanti: oltre questa distanza dall'altra fonte il tratto non ha un'alternativa vicina e manca (una tappa
# e' lunga 15-30 km); sotto, e' una versione diversa del percorso nello stesso corridoio. Sui 10 quasi duplicati
# senza varianti (dati del 3 novembre 2026) la distanza massima va da 0,7 a 3,3 km per 8 coppie e da 10,9 km in su
# per le altre 2: 5 km sta nel vuoto tra i due gruppi
DEDUP_MISSING_MIN_M = 5000
DIAGNOSI_SFASAMENTO = "sfasamento sistematico"
DIAGNOSI_VARIANTE = "variante, bici o altro tracciato presente in una sola fonte"
DIAGNOSI_MANCANTI = "tappe mancanti in una fonte"
DIAGNOSI_VERSIONE = "versione diversa del percorso su alcuni km"
DEDUP_COMUNI_FIELDS = (
    "cammino_a", "cammino_b", "nome_a", "nome_b", "classe", "quota_minore",
    "n_a", "n_b", "n_entrambi", "n_unione", "jaccard",
)

# --- Abbinamento con OSM (osm_match.py) ---
# Ogni cammino del catalogo (cdi e MiTur) si confronta con le relazioni di percorso di OSM (cammini_osm.csv,
# tratte_osm.gpkg). Nome simile come nella deduplica; per i cammini con tracce anche la geometria, per quelli
# senza tracce le regioni dichiarate e la lunghezza dichiarata
OSM_MATCH_DIR = INTERIM_DIR / "osm_match"
OSM_MATCH_FILES = {"abbinamenti": "abbinamenti.csv", "geometrie": "geometrie_osm.gpkg"}
OSM_MATCH_LAYER = "osm_cammini"
OSM_MATCH_NAME_MIN_SCORE = DEDUP_NAME_MIN_SCORE
OSM_MATCH_NEAR_M = DEDUP_NEAR_M
# Con tracce: una relazione e' candidata se il nome e' simile e almeno questa quota del catalogo sta sulla relazione o
# della relazione sul catalogo (la seconda accoglie le tappe OSM di un cammino lungo). La relazione (o l'unione di
# quelle tenute) e' abbinata se la quota vale per entrambe le direzioni, oppure per una sola e la lunghezza sta
# nell'intervallo OSM_MATCH_LENGTH_RATIO_*; altrimenti e' una parte del cammino (parziale) o lo contiene (contenitore)
OSM_MATCH_SHARE_MIN = 0.70
# Solo geometria (non e' un abbinamento): nome diverso ma entrambe le quote almeno a questo valore
OSM_MATCH_GEOMETRY_ONLY_MIN = 0.95
# Annidamento: una relazione che sta per almeno questa quota dentro un'altra piu' lunga abbinata si scarta (resta la piu' esterna)
OSM_MATCH_NESTED_MIN = 0.95
# Senza tracce: la relazione deve stare per almeno questa quota della sua lunghezza nelle regioni dichiarate
OSM_MATCH_REGION_SHARE_MIN = 0.80
# Un solo intervallo di lunghezza per i cammini con e senza tracce: lunghezza OSM / lunghezza di riferimento, che e' la
# dichiarata dal catalogo o, se manca, quella del tracciato del catalogo. Il massimo e' anche il limite oltre il quale
# una relazione e' troppo lunga per essere il cammino (un contenitore) e si scarta prima dell'annidamento. Sui dati del
# 3 novembre 2026: i 10 abbinamenti senza tracce hanno rapporto 0,53-1,34 e i 145 con tracce hanno mediana 1,00, con
# 20 fuori da 0,5-2,0 (superpercorsi di 2,4-15 volte, tratti brevi) e 26 fuori da 0,67-1,5
OSM_MATCH_LENGTH_RATIO_MIN = 0.5
OSM_MATCH_LENGTH_RATIO_MAX = 1.5
# Senza tracce, senza lunghezza dichiarata: il gruppo OSM deve essere lungo almeno cosi'. E' la distanza dichiarata piu'
# corta del catalogo (dati del 3 novembre 2026: 262 cammini su 263 la dichiarano, minimo 5 km, 1o percentile 9,6 km):
# nessun cammino del catalogo e' piu' corto, mentre i falsi positivi visti erano una ferrata di 0,3 km e un sentiero di 2,3 km
OSM_MATCH_MIN_LENGTH_M = 5000
# Senza tracce: nome simile solo se le chiavi hanno le stesse parole, o se quella contenuta nell'altra ha almeno
# tante parole. Una sola parola contenuta ("maria", "pollino") da' 100 con token_set_ratio a ogni nome che la usa
OSM_MATCH_CONTAINED_MIN_TOKENS = 2
# Senza tracce: relazioni piu' vicine di cosi' sono tappe dello stesso percorso (si uniscono), oltre sono candidati distinti
OSM_MATCH_STAGE_GAP_M = 500
# Lettura delle geometrie OSM: tratte per blocco (la geometria intera del GeoPackage non sta in memoria)
OSM_MATCH_CHUNK_ROWS = 2000
ESITO_ABBINATO, ESITO_AMBIGUO, ESITO_SOLO_GEOMETRIA, ESITO_NESSUNO = "abbinato", "ambiguo", "solo geometria", "nessuno"
# Con tracce, nome simile e quota sufficiente ma non e' il cammino: la relazione ne copre un tratto (parziale) o lo
# contiene (contenitore). Solo l'esito abbinato e' usato dagli altri passi (geometrie OSM, etichette)
ESITO_PARZIALE, ESITO_CONTENITORE = "parziale", "contenitore"
# pellegrinaggio_osm di un abbinamento: si, no (una relazione dichiara di no) o questo valore (nessuna relazione dice nulla)
OSM_MATCH_PILGRIMAGE_SILENT = "non indicato"
OSM_MATCH_FIELDS = (
    "cammino_id", "fonte", "nome", "stato_geometria", "esito", "osm_ids", "n_relazioni", "punteggio_nome",
    "quota_catalogo_in_osm", "quota_osm_in_catalogo", "lunghezza_osm_m", "lunghezza_catalogo_m",
    "lunghezza_dichiarata_km", "rapporto_dichiarata", "quota_nelle_regioni", "pellegrinaggio_osm", "nota",
)
OSM_MATCH_GEOMETRY_FIELDS = ("cammino_id", "osm_ids")

# --- Calibrazione della distanza d ---
CALIBRATION_DIR = INTERIM_DIR / "calibrazione"
CALIBRATION_DISTANCES_M = (0, 25, 50, 100, 200, 400, 800)
# Tipi di tratta della calibrazione: i numeri pubblicati (d scelta) sono stati misurati senza le tracce per bici,
# che sono entrate nel cammino solo dopo (CROSSING_TIPI). Non si allarga: riprodurrebbe numeri diversi
CALIBRATION_TIPI = (TIPO_TAPPA, TIPO_VARIANTE, TIPO_ALTRO)
CALIBRATION_PERCENTILES = (50, 90, 95, 99)
CALIBRATION_FAR_M = 400  # le coppie di riferimento oltre questa distanza vanno in un elenco da ispezionare
CALIBRATION_GRAZING_M = (50, 100)  # lunghezza dentro il comune sotto la quale la linea "sfiora" soltanto
CALIBRATION_SEED = 20261103  # semi dei disturbi casuali di B: stesso seme, stessi numeri
# Esperimento B: tracce dense (MiTur) degradate in precisione
CALIBRATION_DENSE_SPACING_M = 10
# Con meno tratte dense di cosi' in MiTur, il campione di B si completa con le tratte cdi piu' dense
CALIBRATION_DENSE_MIN_TRATTE = 50
CALIBRATION_SUBSAMPLE_SPACINGS_M = (25, 60)  # spaziatura media obiettivo di "un vertice ogni k"
CALIBRATION_SIMPLIFY_TOLERANCES_M = (10, 25)
CALIBRATION_JITTER_SIGMAS_M = (10, 25, 50)  # errore laterale: disturbo gaussiano indipendente su ogni vertice
CALIBRATION_SHIFT_OFFSETS_M = (25, 50)  # errore laterale sistematico: linea traslata di lato
SHIFT_SIDES = {"sx": 1, "dx": -1}  # segno del parametro di offset_curve (positivo = a sinistra)
# Comuni "a rischio": la traccia densa vi sta dentro per meno di cosi' (somma sulle tratte del cammino)
CALIBRATION_MARGINAL_M = 200
# Nomi dei metodi: k25, k60 (un vertice ogni k), s10, s25 (semplificazione), j10... (disturbo), o25... (traslazione)
DEGRADE_SUBSAMPLE_PREFIX, DEGRADE_SIMPLIFY_PREFIX = "k", "s"
DEGRADE_JITTER_PREFIX, DEGRADE_SHIFT_PREFIX = "j", "o"
# Esperimento C: coppie provvisorie cdi-MiTur dello stesso cammino (solo per la calibrazione, non e' la deduplica)
CALIBRATION_PAIR_NAME_MIN_SCORE = 90  # token_sort_ratio sui nomi normalizzati
CALIBRATION_PAIR_OVERLAP_MIN = 0.8  # quota del cammino piu' corto entro CALIBRATION_PAIR_NEAR_M dall'altro
CALIBRATION_PAIR_NEAR_M = 50
CALIBRATION_PAIR_SAMPLE_M = MATCH_SAMPLE_M  # passo dei punti con cui si misura la quota di linea vicina all'altra
# Si confrontano solo coppie di lunghezza simile: con tracciati di lunghezza diversa il recall misura la copertura
CALIBRATION_PAIR_LENGTH_RATIO = (0.9, 1.1)  # lunghezza cdi / lunghezza MiTur, estremi inclusi
# Esperimento A: nomi dei luoghi pubblicati confrontati con i comuni ISTAT
PLACE_SPLIT_PATTERN = r"[/(),;]|\s-\s"  # nomi doppi, parentesi, virgole e " - " dividono il testo in candidati
PLACE_STOPWORDS = frozenset((
    "a", "al", "alla", "allo", "all", "d", "da", "dal", "dalla", "dei", "del", "dell", "della", "delle", "dello",
    "di", "e", "i", "il", "in", "l", "la", "le", "lo", "nel", "nell", "nella", "su", "sul", "sull", "sulla",
))
PLACE_SAINT_TOKENS = frozenset(("s", "san", "sant", "santa", "santo"))
PLACE_SAINT_CANONICAL = "san"
ISTAT_BILINGUAL_SEPARATOR = "/"
# Esiti del confronto di un nome: omonimo = un solo pezzo con piu' comuni; due luoghi = pezzi che danno comuni diversi;
# nome_parziale = il nome e' l'inizio (parole intere) del nome di un altro comune delle stesse regioni
MATCH_OK, MATCH_HOMONYM, MATCH_TWO_PLACES = "ok", "omonimo", "due luoghi"
MATCH_PREFIX, MATCH_NOT_FOUND, MATCH_EMPTY = "nome_parziale", "non_trovato", "vuoto"
SOURCE_TAPPE, SOURCE_WEBMAPP = "tappe", "webmapp"
# Motivo di una coppia lontana, solo dove si ricava dai dati: le altre tappe del cammino sono tutte a distanza 0
# e quelle lontane sono un blocco contiguo di almeno CALIBRATION_BRANCH_MIN_STAGES tappe
MOTIVO_BRANCH = "ramo senza traccia"
CALIBRATION_BRANCH_MIN_STAGES = 2
CALIBRATION_FONTI = (CDI_FONTE, MITUR_FONTE)  # fonti dei costi D e delle coppie C; l'esperimento B sceglie la sua
CALIBRATION_RATIO_DECIMALS = RATIO_DECIMALS  # quote e recall nei CSV
CALIBRATION_FILE_SINTESI = "sintesi.csv"
CALIBRATION_FILES_A = {
    "riferimenti": "A_riferimenti.csv", "match": "A_match.csv",
    "distribuzione": "A_distribuzione.csv", "lontane": "A_oltre_400m.csv",
}
CALIBRATION_FILES_B = {"dettaglio": "B_dettaglio.csv", "totale": "B_totale.csv"}
CALIBRATION_FILES_C = {"coppie": "C_coppie.csv", "dettaglio": "C_dettaglio.csv"}
CALIBRATION_FILES_D = {"costi": "D_costi.csv", "grazing": "D_grazing.csv"}

# --- Cartella di lavoro (workbook.py) ---
# Ingressi: i due file del contratto delle coppie (PAIRS_DIR e PAIRS_FILES). Uscita: un file per giorno di elaborazione
WORKBOOK_OUTPUT_DIR = DATA_DIR / "output"
WORKBOOK_FILE_TEMPLATE = "Cammini Comuni {data}.xlsx"  # {data} = data di elaborazione in WORKBOOK_FILE_DATE_FORMAT
WORKBOOK_FILE_DATE_FORMAT = "%d.%m.%Y"
WORKBOOK_TITLE = "Cammini Comuni"
WORKBOOK_SHEET_LEGENDA, WORKBOOK_SHEET_COMUNI, WORKBOOK_SHEET_CAMMINI = "Legenda", "Comuni", "Cammini"
WORKBOOK_SHEET_TERRITORI, WORKBOOK_SHEET_ATTRIBUTI, WORKBOOK_SHEET_COPPIE = "Territori", "Cammini per attributo", "Coppie"
WORKBOOK_SHEET_PIVOT_TEMPLATE = "Territori x {nome}"
# Caratteristiche dei cammini nei fogli Territori x e Cammini per attributo, nell'ordine dei fogli:
# (nome nel foglio, chiave del foglio Cammini, piu' valori per cammino, territoriale). Territoriale: il valore viene dalle
# sole coppie del cammino dentro il territorio (mezzo e fonti dei tracciati li'), non dal cammino intero
WORKBOOK_DIMENSIONS = (
    ("Religioso", "religioso", False, False), ("Mezzo", "mezzo", False, True), ("Forma", "tipologia", False, False),
    ("Portata", "portata", False, False), ("Tema", "tema", True, False), ("Stagione", "stagione", True, False),
    ("Segnaletica", "segnaletica", False, False), ("Credenziale", "credenziale", False, False),
    ("Catalogo", "fonti", False, False), ("Fonte", "fonte_geometria", False, True),
)
WORKBOOK_PIVOT_SHEETS = tuple(WORKBOOK_SHEET_PIVOT_TEMPLATE.format(nome=name) for name, *_ in WORKBOOK_DIMENSIONS)
# Ordine dei fogli: guida, dati, tabelle per territorio (come i breakdown del committente), tabella di base in fondo
WORKBOOK_SHEET_ORDER = (
    WORKBOOK_SHEET_LEGENDA, WORKBOOK_SHEET_COMUNI, WORKBOOK_SHEET_CAMMINI, WORKBOOK_SHEET_TERRITORI,
    *WORKBOOK_PIVOT_SHEETS, WORKBOOK_SHEET_ATTRIBUTI, WORKBOOK_SHEET_COPPIE,
)
# La Legenda descrive i dieci fogli Territori x una volta sola, sotto questo nome
WORKBOOK_PIVOT_LEGEND_NAME = WORKBOOK_SHEET_PIVOT_TEMPLATE.format(
    nome="(" + ", ".join(name for name, *_ in WORKBOOK_DIMENSIONS) + ")"
)
# Titolo in cima ai fogli Territori, Territori x e Cammini per attributo: una frase che dice che cosa si conta (il nome del
# foglio resta quello di WORKBOOK_SHEET_*). Territori x: la caratteristica come si legge nella frase, per nome di WORKBOOK_DIMENSIONS
WORKBOOK_TERRITORI_TITLE = "Comuni attraversati e cammini per territorio"
WORKBOOK_ATTRIBUTI_TITLE = "Cammini in Italia e loro medie per valore di ciascuna caratteristica"
WORKBOOK_PIVOT_TITLE_TEMPLATE = "Cammini per territorio e {caratteristica}"
WORKBOOK_PIVOT_CHARACTERISTICS = {
    "Religioso": "carattere religioso", "Mezzo": "mezzo", "Forma": "forma del percorso", "Portata": "portata",
    "Tema": "tema", "Stagione": "stagione consigliata", "Segnaletica": "segnaletica", "Credenziale": "credenziale del pellegrino",
    "Catalogo": "catalogo di provenienza", "Fonte": "fonte del tracciato",
}
# Riga di separazione della Legenda prima delle colonne di ogni foglio
WORKBOOK_SHEET_DESCRIPTIONS = {
    WORKBOOK_SHEET_COMUNI: "una riga per comune attraversato da almeno un cammino",
    WORKBOOK_SHEET_CAMMINI: "una riga per cammino, con le sue caratteristiche e i comuni nell'ordine del percorso",
    WORKBOOK_SHEET_TERRITORI: "una riga per territorio (Italia, regioni, province, comuni attraversati) con comuni e cammini",
    WORKBOOK_PIVOT_LEGEND_NAME: "un foglio per caratteristica; per ogni territorio, i cammini per valore della caratteristica",
    WORKBOOK_SHEET_ATTRIBUTI: "in Italia, numero di cammini e loro medie per ciascun valore delle caratteristiche",
    WORKBOOK_SHEET_COPPIE: "una riga per ogni coppia di cammino e comune attraversato: la tabella da cui derivano gli altri fogli",
}
# Colonne attese nei due file: quelle del contratto, definite una volta sola nella sezione Coppie
WORKBOOK_COPPIE_FIELDS = PAIRS_COPPIE_FIELDS
WORKBOOK_CAMMINI_FIELDS = PAIRS_CAMMINI_FIELDS
# Lettura dei tipi. Codici ISTAT scritti come testo, con gli zeri iniziali: cifre per chiave
WORKBOOK_CODE_DIGITS = {"cod_reg": 2, "cod_uts": 3, "PRO_COM_T": 6}
WORKBOOK_INT_KEYS = ("ordine", "n_comuni")
WORKBOOK_FLOAT_KEYS = ("distanza_m", "lunghezza_dichiarata_km", "lunghezza_tracciata_km", "copertura")
WORKBOOK_BOOL_KEY = "dentro"
WORKBOOK_ENUM_KEYS = ("mezzo", "religioso")  # ammessi solo i valori di WORKBOOK_VALUE_LABELS
WORKBOOK_CAMMINI_BLANK_ENUM_KEYS = ("mezzo",)  # nei cammini il valore puo' mancare (cammino senza tracce); nelle coppie mai
WORKBOOK_TRUE_VALUES, WORKBOOK_FALSE_VALUES = ("true", "1", "si"), ("false", "0", "no")
# Colonne con piu' valori uniti da LIST_SEPARATOR nei dati intermedi: nel foglio sono separati da WORKBOOK_CELL_SEPARATOR
WORKBOOK_LIST_KEYS = ("alias", "regioni", "evidenza_religioso", *PAIRS_MULTI_FIELDS)
WORKBOOK_CELL_SEPARATOR = "; "
WORKBOOK_LINK_SEPARATOR = "\n"  # piu' schede di un catalogo: un indirizzo per riga, il collegamento sul primo
# Valori scritti come slug nei dati ("arte-e-cultura"): nel foglio si leggono come parole
WORKBOOK_WORD_KEYS = ("tema",)
WORKBOOK_SLUG_SEPARATOR = "-"
# Date ISO nei dati intermedi: nel foglio una data sola e' una data di Excel, piu' date sono testo
WORKBOOK_DATE_KEYS = ("data_catalogo",)
WORKBOOK_DATE_DISPLAY_FORMAT = "%d/%m/%Y"
# Etichette dei valori nel foglio (i dati intermedi restano ASCII); l'ordine dei dizionari e' l'ordine nei riepiloghi
WORKBOOK_SI_NO_LABELS = {SI: "sì", NO: "no"}
WORKBOOK_MEZZO_LABELS = {MEZZO_PIEDI: "a piedi", MEZZO_BICI: "in bici", MEZZO_ENTRAMBI: "a piedi e in bici"}
WORKBOOK_FORMA_LABELS = {"lineare": "lineare", "anello": "ad anello"}
WORKBOOK_SOURCE_LABELS = {CDI_FONTE: "Cammini d'Italia", MITUR_FONTE: "MiTur", OSM_FONTE: "OpenStreetMap"}
WORKBOOK_NAMES_JOIN, WORKBOOK_NAMES_LAST_JOIN = ", ", " e "  # due nomi "A e B", tre "A, B e C"
WORKBOOK_VALUE_LABELS = {
    "mezzo": WORKBOOK_MEZZO_LABELS, "religioso": WORKBOOK_SI_NO_LABELS, "tipologia": WORKBOOK_FORMA_LABELS,
    "credenziale": WORKBOOK_SI_NO_LABELS, "segnaletica": WORKBOOK_SI_NO_LABELS,
}
# Etichette vuote: la fonte non pubblica il dato o il cammino non ha tracce (tutte tranne le date, che restano celle vuote)
WORKBOOK_EMPTY_LABEL = "non indicato"
WORKBOOK_EMPTY_LABEL_KEYS = ("mezzo", "tipologia", "portata", "tema", "stagione", "credenziale", "segnaletica")
WORKBOOK_NO_TRACK_LABEL = "nessun tracciato"  # fonte_geometria = FONTE_GEOMETRIA_NESSUNA
WORKBOOK_PASSAGGIO_DENTRO = "dentro il comune"
WORKBOOK_PASSAGGIO_VICINO = f"entro {CROSSING_DISTANCE_M} m dal confine"
WORKBOOK_RAGGED_FORMAT = "{comune} ({sigla})"  # comuni del foglio Cammini: esistono comuni omonimi in province diverse
WORKBOOK_RAGGED_FORMAT_VICINO = f"{{comune}} ({{sigla}}, entro {CROSSING_DISTANCE_M} m)"
WORKBOOK_TOTAL_LABEL = "Totale"
# Livelli dei fogli per territorio (0 = Italia ... 3 = Comune): rientro dell'etichetta e livello dello schema a righe
WORKBOOK_LEVEL_LABELS = ("Italia", "Regione", "Provincia", "Comune")
# Pagine del Catalogo dei Cammini religiosi: le schede MiTur non hanno una pagina propria, si apre la pagina di elenco
MITUR_LISTING_URL = "https://www.ministeroturismo.gov.it/catalogo-dei-cammini-religiosi-italiani/"
MITUR_LISTING_PAGE_URL = MITUR_LISTING_URL + "page/{pagina}/"
# Registro delle richieste (REQUESTS_LOG): le richieste riuscite di ogni fonte danno la data di scaricamento della Legenda
WORKBOOK_LOG_OK_STATUS = "200"
WORKBOOK_DOWNLOAD_ONE, WORKBOOK_DOWNLOAD_RANGE = "{first}", "dal {first} al {last}"
WORKBOOK_DOWNLOAD_MISSING = "non registrata"

# Stile (come il Tracker Scenari Macro del committente): Calibri 11, intestazioni in grassetto centrate e a capo
WORKBOOK_FONT_NAME, WORKBOOK_FONT_SIZE = "Calibri", 11
WORKBOOK_HEADER_HEIGHT = 45
WORKBOOK_TITLE_SIZE, WORKBOOK_TITLE_COLOR = 16, "1F3864"
WORKBOOK_SECTION_SIZE = 14  # titoli dei blocchi della Legenda
WORKBOOK_SUBTITLE_COLOR = "595959"  # sottotitolo "Aggiornato: ..." e note sotto le tabelle
WORKBOOK_SUBTITLE_TEMPLATE = "Aggiornato: {data}"
WORKBOOK_LINK_COLOR = "0563C1"
WORKBOOK_MARGIN_WIDTH = 2  # colonna A vuota dei fogli Legenda e Cammini per attributo
WORKBOOK_REGION_FILL = "D9E1F2"  # righe Regione dei fogli per territorio
WORKBOOK_BAND_HEIGHT = 30  # riga delle etichette dei valori sopra le coppie di colonne
WORKBOOK_PIVOT_FROZEN_COLUMNS = 3  # Livello, Codice, Territorio
WORKBOOK_FMT_TEXT, WORKBOOK_FMT_COUNT, WORKBOOK_FMT_DECIMAL = "@", "#,##0", "0.0"
WORKBOOK_FMT_SHARE, WORKBOOK_FMT_DATE, WORKBOOK_FMT_GENERAL = "0.0%", "DD/MM/YYYY", "General"
# Colonne di identita' bloccate a sinistra (oltre alla riga di intestazione) nei fogli di dati
WORKBOOK_FROZEN_COLUMNS = {WORKBOOK_SHEET_COMUNI: 7, WORKBOOK_SHEET_CAMMINI: 2, WORKBOOK_SHEET_COPPIE: 4}

# Tipi di "Valori" della Legenda, calcolati sui dati a ogni elaborazione: numeri e date "da X a Y", categorie i valori
# presenti. Ogni altro testo e' la forma fissa dei valori (codici, nomi, testo libero)
WORKBOOK_VALUES_NUMBER, WORKBOOK_VALUES_DATE, WORKBOOK_VALUES_CATEGORY = "numero", "data", "categoria"
WORKBOOK_VALUES_RANGE = "da {low} a {high}"
WORKBOOK_VALUES_NONE = "nessun valore"
WORKBOOK_VALUES_JOIN = ", "
WORKBOOK_GROUP_K = "{k}"  # segnaposto delle colonne ripetute (Cammino k, Comune k)
WORKBOOK_GROUP_LABEL = "{first} ... {last}"  # nome di un gruppo di colonne ripetute nella Legenda
# Chiavi del registro usate anche dal codice che scrive le righe (una definizione sola)
WORKBOOK_COMUNE_CAMMINO_KEY = "cammino_{k}"  # foglio Comuni
WORKBOOK_PIVOT_N_KEY, WORKBOOK_PIVOT_SHARE_KEY = "valore_n_{k}", "valore_incidenza_{k}"  # fogli Territori x
WORKBOOK_CROSS_MODE_KEY = "x_{mode}"  # Religioso per mezzo: una colonna per mezzo
# Religioso per mezzo: i mezzi e, in fondo, i cammini senza mezzo (chiave "nessuno", mai valore dei dati)
WORKBOOK_CROSS_MODE_LABELS = {**WORKBOOK_MEZZO_LABELS, "nessuno": WORKBOOK_EMPTY_LABEL}


@dataclass(frozen=True)
class WorkbookColumn:
    """Una colonna di un foglio: unica fonte di intestazione, larghezza, formato e testi della Legenda.

    Le colonne ripetute (Cammino k, Comune k) hanno WORKBOOK_GROUP_K in key e header."""

    key: str
    header: str
    width: float
    contains: str  # Che cosa contiene
    reading: str  # Come leggerla
    values: str  # WORKBOOK_VALUES_* oppure la forma fissa dei valori
    number_format: str = WORKBOOK_FMT_GENERAL
    unit: str = ""
    multi: bool = False  # piu' valori nella cella, separati da WORKBOOK_CELL_SEPARATOR
    wrap: bool = False
    link: bool = False
    band: str = ""  # etichetta sopra la colonna (riga delle coppie dei fogli per territorio), unita alle vicine uguali


_D = CROSSING_DISTANCE_M
_C = WorkbookColumn
_NAME_ISTAT = "nome ISTAT"
_COL_COD_REG = _C("cod_reg", "Codice regione", 10, "Codice ISTAT della regione.",
                  "Testo, non numero: collega il foglio ad altri dati ISTAT.", "2 cifre con lo zero iniziale", WORKBOOK_FMT_TEXT)
_COL_REGIONE = _C("regione", "Regione", 22, "Nome della regione.", "Confini ISTAT dell'anno di elaborazione.", _NAME_ISTAT)
_COL_COD_UTS = _C("cod_uts", "Codice provincia", 10, "Codice ISTAT della provincia o città metropolitana.",
                  "Testo, non numero: collega il foglio ad altri dati ISTAT.", "3 cifre con gli zeri iniziali", WORKBOOK_FMT_TEXT)
_COL_PROVINCIA = _C("provincia", "Provincia", 24, "Nome della provincia o città metropolitana.",
                    "Confini ISTAT dell'anno di elaborazione.", _NAME_ISTAT)
_COL_SIGLA = _C("sigla", "Sigla", 7, "Sigla della provincia.", "Distingue i comuni omonimi.", "2 lettere maiuscole")
_COL_PRO_COM = _C(COMUNE_ID_COL, "Codice comune", 10, "Codice ISTAT del comune.",
                  "Testo, non numero: collega il foglio ad altri dati ISTAT.", "6 cifre con gli zeri iniziali", WORKBOOK_FMT_TEXT)
# Come si stabilisce l'ordine dei comuni: stesse regole di pairs.py (chain_anchors, chain_stages, route_positions)
_ORDINE_REGOLA = (
    "I comuni sono numerati lungo una linea principale del cammino. La partenza è il comune di partenza pubblicato dalla "
    "scheda, se è uno dei comuni del cammino; altrimenti l'inizio della sezione più lunga che sta a un capo libero del "
    "cammino; altrimenti l'inizio della tappa col numero più basso. Un cammino senza tappe numerate segue il verso "
    "memorizzato nel suo file di tracciato, quindi il verso non è garantito. Un comune che la linea principale non "
    f"attraversa e da cui non passa a meno di {_D} m (lo raggiungono solo varianti, altre versioni o tracciati in bici) è "
    "collocato nel punto della linea principale più vicino a lui."
)
_COL_COMUNE = _C("comune", "Comune", 28, "Nome del comune.", "Confini ISTAT dell'anno di elaborazione.", _NAME_ISTAT)
_COL_CODICE_CAMMINO = _C("cammino_unico_id", "Codice cammino", 10, "Identificativo del cammino in questo file.",
                         "Collega il foglio Cammini al foglio Coppie; un cammino presente in più cataloghi ha un solo codice.",
                         "u seguito da un numero (ad esempio u001)")
_COL_CAMMINO = _C("nome", "Cammino", 34, "Nome del cammino.",
                  "Per un cammino pubblicato più volte, il nome della scheda principale; gli altri sono in Altri nomi.",
                  "testo", wrap=True)
_COL_MEZZO = _C("mezzo", "Mezzo", 14, "Come sono pubblicati i tracciati del cammino.",
                "a piedi e in bici quando le fonti pubblicano tracciati per entrambi o un tracciato per entrambi. "
                f"{WORKBOOK_EMPTY_LABEL}: il cammino non ha un tracciato, quindi nessun dato dice come si percorre.",
                WORKBOOK_VALUES_CATEGORY)
_COL_RELIGIOSO = _C("religioso", "Religioso", 10, "Carattere religioso del cammino.",
                    "sì se almeno una fonte indica il cammino come religioso (Catalogo dei Cammini religiosi del Ministero "
                    "del Turismo, tema spirituale su Cammini d'Italia, pellegrinaggio in OpenStreetMap) o se il nome contiene "
                    "una parola religiosa (ad esempio San, Santa, Romea); altrimenti no.", WORKBOOK_VALUES_CATEGORY)
_COL_FONTE_TRACCIATO = _C("fonte_geometria", "Fonte del tracciato", 24,
                          "Fonti dei tracciati da cui risultano i comuni del cammino.",
                          "Cammini d'Italia e MiTur: tracce GPS dei cataloghi. OpenStreetMap: relazione di percorso OSM "
                          "abbinata al cammino, usata solo per le versioni del cammino senza tracce nei cataloghi. "
                          f"{WORKBOOK_NO_TRACK_LABEL}: il cammino non ha comuni.", WORKBOOK_VALUES_CATEGORY)
_TWO_VALUES = (
    "Se la scheda pubblica due valori, il cammino li mostra entrambi (ad esempio «{example}») e conta come quella "
    "combinazione, non sotto ciascun valore."
)
_COL_FORMA = _C("tipologia", "Forma", 12, "Forma del percorso pubblicata da Cammini d'Italia.",
                f"{WORKBOOK_EMPTY_LABEL}: Cammini d'Italia non la pubblica o il cammino è solo nel catalogo MiTur. "
                + _TWO_VALUES.format(example="ad anello; lineare"), WORKBOOK_VALUES_CATEGORY)
_COL_PORTATA = _C("portata", "Portata", 14, "Portata del cammino pubblicata da Cammini d'Italia.",
                  f"{WORKBOOK_EMPTY_LABEL}: Cammini d'Italia non la pubblica o il cammino è solo nel catalogo MiTur. "
                  + _TWO_VALUES.format(example="nazionale; regionale"), WORKBOOK_VALUES_CATEGORY)
_COL_TEMA = _C("tema", "Tema", 26, "Temi del cammino pubblicati da Cammini d'Italia (sito e mappa).",
               "Più temi separati da punto e virgola; nei fogli Territori x Tema e Cammini per attributo un cammino conta "
               "sotto ogni suo tema.",
               WORKBOOK_VALUES_CATEGORY, multi=True, wrap=True)
_COL_STAGIONE = _C("stagione", "Stagione", 24, "Stagioni consigliate da Cammini d'Italia (sito e mappa).",
                   "Più stagioni separate da punto e virgola; nei fogli Territori x Stagione e Cammini per attributo un cammino "
                   "conta sotto ogni sua stagione.",
                   WORKBOOK_VALUES_CATEGORY, multi=True, wrap=True)
_COL_CREDENZIALE = _C("credenziale", "Credenziale del pellegrino", 14,
                      "Se la scheda del cammino nel catalogo MiTur elenca il servizio «Credenziali» tra le icone dei servizi.",
                      "Dato del solo catalogo MiTur. sì: la scheda MiTur mostra l'icona; no: la scheda MiTur non la mostra; "
                      f"{WORKBOOK_EMPTY_LABEL}: il cammino non ha una scheda MiTur.", WORKBOOK_VALUES_CATEGORY)
_COL_SEGNALETICA = _C("segnaletica", "Segnaletica", 12,
                      "Se la scheda del cammino nel catalogo MiTur elenca il servizio «Segnaletica» tra le icone dei servizi.",
                      "Dato del solo catalogo MiTur. sì: la scheda MiTur mostra l'icona; no: la scheda MiTur non la mostra; "
                      f"{WORKBOOK_EMPTY_LABEL}: il cammino non ha una scheda MiTur.", WORKBOOK_VALUES_CATEGORY)
_COLS_TAGS = (_COL_FORMA, _COL_PORTATA, _COL_TEMA, _COL_STAGIONE, _COL_CREDENZIALE, _COL_SEGNALETICA)
_COL_PASSAGGIO = _C("passaggio", "Passaggio", 20, "Come il tracciato del cammino passa per il comune.",
                    f"{WORKBOOK_PASSAGGIO_DENTRO}: il tracciato tocca o entra nel comune; {WORKBOOK_PASSAGGIO_VICINO}: "
                    f"passa fuori, a non più di {_D} m dal confine.", WORKBOOK_VALUES_CATEGORY)
WORKBOOK_COMUNI_COLUMNS = (
    _COL_COD_REG, _COL_REGIONE, _COL_COD_UTS, _COL_PROVINCIA, _COL_SIGLA, _COL_PRO_COM, _COL_COMUNE,
    _C("n_cammini", "N. cammini", 10, "Cammini che attraversano il comune.",
       "Un cammino presente in più cataloghi conta una volta; il dettaglio per caratteristica è nei fogli Territori x.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "cammini"),
    _C(WORKBOOK_COMUNE_CAMMINO_KEY, "Cammino {k}", 30, "Nome del k-esimo cammino che attraversa il comune, in ordine alfabetico.",
       "Cammino k, Mezzo k, Religioso k e Passaggio k descrivono lo stesso cammino; dopo l'ultimo cammino del comune "
       "le colonne restano vuote.", "testo", wrap=True),
    _C("mezzo_{k}", "Mezzo {k}", 14, "Come sono pubblicati i tracciati del cammino k vicini al comune.",
       f"Contano i soli tracciati entro {_D} m dal comune: un cammino a piedi e in bici può passare per il comune solo a piedi.",
       WORKBOOK_VALUES_CATEGORY),
    _C("religioso_{k}", "Religioso {k}", 10, "Carattere religioso del cammino k.", "Vedi Religioso nel foglio Cammini.",
       WORKBOOK_VALUES_CATEGORY),
    _C("passaggio_{k}", "Passaggio {k}", 20, "Come il tracciato del cammino k passa per il comune.", _COL_PASSAGGIO.reading,
       WORKBOOK_VALUES_CATEGORY),
)
WORKBOOK_CAMMINI_COLUMNS = (
    _COL_CODICE_CAMMINO, _COL_CAMMINO,
    _C("alias", "Altri nomi", 30, "Altri nomi con cui lo stesso cammino è pubblicato (in un altro catalogo o in un'altra scheda).",
       "Più nomi separati da punto e virgola.", "testo", multi=True, wrap=True),
    _C("fonti", "Catalogo di provenienza", 22, "Cataloghi che pubblicano il cammino.",
       "Cammini d'Italia: camminiditalia.org; MiTur: Catalogo dei Cammini religiosi del Ministero del Turismo. "
       "Un cammino presente in entrambi è una riga sola.", WORKBOOK_VALUES_CATEGORY),
    _C("scheda_cdi", "Scheda Cammini d'Italia", 40, "Pagina del cammino su camminiditalia.org.",
       "Più schede dello stesso catalogo: un indirizzo per riga, il collegamento è sul primo.",
       "indirizzo web", wrap=True, link=True),
    _C("scheda_mitur", "Scheda MiTur", 40,
       "Pagina del Catalogo dei Cammini religiosi del Ministero del Turismo che contiene la scheda del cammino.",
       "Le schede MiTur non hanno una pagina propria: si apre la pagina dell'elenco in cui compare la scheda. "
       "Più schede: un indirizzo per riga, il collegamento è sul primo.", "indirizzo web", wrap=True, link=True),
    _COL_MEZZO, _COL_RELIGIOSO,
    _C("evidenza_religioso", "Perché religioso", 40, "Da che cosa risulta il carattere religioso.",
       "Per i cammini non religiosi vale nessuna fonte.", "testi separati da punto e virgola", multi=True, wrap=True),
    *_COLS_TAGS,
    _C("data_catalogo", "Data della scheda", 12, "Data da cui il cammino è nel catalogo MiTur, come scritta nella scheda.",
       "Vuota per i cammini senza scheda MiTur e per le schede MiTur che non riportano la data.", WORKBOOK_VALUES_DATE,
       WORKBOOK_FMT_DATE),
    _C("regioni", "Regioni", 28, "Regioni indicate dai cataloghi.", "Più regioni separate da punto e virgola.",
       "nomi ISTAT separati da punto e virgola", multi=True, wrap=True),
    _C("lunghezza_dichiarata_km", "Lunghezza dichiarata (km)", 12,
       "Lunghezza pubblicata dal catalogo da cui viene il tracciato misurato.",
       "Vuota quando nessun catalogo la pubblica.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "km"),
    _C("lunghezza_tracciata_km", "Lunghezza del tracciato (km)", 12,
       "Lunghezza del tracciato del cammino, contando una volta sola le parti ripetute.",
       "Vuota quando il cammino non ha tracciato.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "km"),
    _C("copertura", "Copertura del tracciato", 12, "Quota della lunghezza dichiarata coperta dal tracciato, al massimo 100%.",
       "Vuota quando manca una delle due lunghezze; sotto il 100% il tracciato pubblicato è incompleto.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_SHARE),
    _COL_FONTE_TRACCIATO,
    _C("n_comuni", "N. comuni", 10, "Comuni attraversati dal cammino.",
       f"È la somma di di cui dentro e di cui solo entro {_D} m.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni"),
    _C("n_dentro", "di cui dentro", 10, "Comuni in cui il tracciato tocca o entra.", "Compresi in N. comuni.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni"),
    _C("n_vicino", f"di cui solo entro {_D} m", 11,
       f"Comuni in cui il tracciato non entra ma passa a non più di {_D} m dal confine.", "Compresi in N. comuni.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni"),
    _C("nota", "Nota", 50, "Osservazioni sul cammino: possibili duplicati da verificare, tracciato mancante.",
       "Vuota quando non c'è nulla da segnalare.", "testo", wrap=True),
    _C("comune_{k}", "Comune {k}", 26, "k-esimo comune nell'ordine del percorso, con la sigla della provincia.",
       f"Dalla partenza all'arrivo; per i cammini con più rami un ramo dopo l'altro. La dicitura entro {_D} m indica un "
       f"comune in cui il tracciato non entra. {_ORDINE_REGOLA}", f"Nome (SIGLA) oppure Nome (SIGLA, entro {_D} m)"),
)
WORKBOOK_COPPIE_COLUMNS = (
    _COL_CODICE_CAMMINO, _COL_CAMMINO, _COL_PRO_COM, _COL_COMUNE, _COL_SIGLA, _COL_COD_UTS, _COL_PROVINCIA,
    _COL_COD_REG, _COL_REGIONE,
    _C("ordine", "Ordine lungo il cammino", 10, "Posizione del comune lungo il percorso del cammino, a partire da 1.",
       _ORDINE_REGOLA, WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT),
    _COL_PASSAGGIO,
    _C("distanza_m", "Distanza dal confine (m)", 11, "Distanza minima del tracciato dal confine del comune.",
       "0 quando il tracciato tocca o entra nel comune.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "m"),
    replace(
        _COL_MEZZO, contains="Come sono pubblicati i tracciati del cammino vicini al comune.",
        reading=f"Contano i soli tracciati entro {_D} m dal comune.",
    ),
    replace(
        _COL_FONTE_TRACCIATO, contains=f"Fonti dei tracciati del cammino che passano entro {_D} m dal comune.",
        reading="Vedi Fonte del tracciato nel foglio Cammini.",
    ),
    _COL_RELIGIOSO, *_COLS_TAGS,
)
# Cammini per attributo: una tabella per caratteristica di WORKBOOK_DIMENSIONS (valori del cammino intero)
WORKBOOK_SUMMARY_MULTI_NOTE = "Un cammino con più valori conta sotto ciascuno: le quote non sommano al 100%."
WORKBOOK_SUMMARY_CROSS_TITLE = "Religioso per mezzo (numero di cammini)"
_AVG = "Media sui cammini con il valore della riga"
WORKBOOK_SUMMARY_COLUMNS = (
    _C("valore", "Valore", 34, "Valore della caratteristica indicata nel titolo della tabella.",
       f"Le etichette sono quelle del foglio Cammini; la riga {WORKBOOK_TOTAL_LABEL} conta l'insieme dei cammini.",
       "valori del foglio Cammini", wrap=True),
    _C("n_cammini", "N. cammini", 12, "Cammini con il valore della riga.", "Ogni cammino conta una volta per tabella, "
       "salvo Tema e Stagione.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "cammini"),
    _C("quota", "Incidenza sul totale", 12, "N. cammini diviso per il numero totale dei cammini.",
       "In Tema e Stagione le quote non sommano al 100%.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_SHARE),
    _C("media_dichiarata", "Lunghezza media dichiarata (km)", 14, "Media della Lunghezza dichiarata (km).",
       f"{_AVG} e una lunghezza dichiarata.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "km"),
    _C("media_tracciata", "Lunghezza media del tracciato (km)", 14, "Media della Lunghezza del tracciato (km).",
       f"{_AVG} e un tracciato.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "km"),
    _C("media_comuni", "N. medio di comuni", 12, "Media di N. comuni.", f"{_AVG}, compresi quelli senza comuni.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "comuni"),
    _C("comuni_distinti", "Comuni distinti attraversati", 14, "Comuni attraversati da almeno uno dei cammini della riga.",
       "Un comune attraversato da più cammini della riga conta una volta.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni"),
)
_CROSS = "Cammini con il carattere religioso della riga e il mezzo della colonna."
WORKBOOK_SUMMARY_CROSS_COLUMNS = (
    _C("x_religioso", "Religioso", 34, "Valore di Religioso (righe della tabella Religioso per mezzo).",
       f"La riga {WORKBOOK_TOTAL_LABEL} somma le colonne.", WORKBOOK_VALUES_CATEGORY),
    *(_C(WORKBOOK_CROSS_MODE_KEY.format(mode=mode), label, 12, _CROSS, "Vedi Mezzo nel foglio Cammini.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "cammini")
      for mode, label in WORKBOOK_CROSS_MODE_LABELS.items()),
    _C("x_totale", WORKBOOK_TOTAL_LABEL, 12, "Cammini con il carattere religioso della riga.", "Somma delle colonne del mezzo.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "cammini"),
)
# Fogli per territorio: Italia, poi ogni regione seguita dalle sue province, ogni provincia dai suoi comuni attraversati
WORKBOOK_TERRITORI_NOTES = (
    "Una riga per territorio: Italia, regioni, province e comuni attraversati (con + e - a sinistra si aprono e chiudono "
    "province e comuni).",
    "Un cammino che tocca più territori conta in ciascuno: le righe non sommano alla riga del territorio superiore.",
)
WORKBOOK_PIVOT_NOTES = (
    "N. cammini: cammini che toccano il territorio e hanno il valore scritto sopra la coppia di colonne; "
    "Incidenza: N. cammini diviso per i Cammini della riga.",
    "Un cammino che tocca più territori conta in ciascuno: le righe non sommano alla riga del territorio superiore.",
)
WORKBOOK_PIVOT_NOTE_TERRITORIAL = "Il valore è quello dei soli tracciati del cammino che passano per il territorio."
WORKBOOK_PIVOT_NOTE_MULTI = "Un cammino con più valori conta sotto ciascuno: le Incidenze possono sommare oltre il 100%."
_COL_LIVELLO = _C("livello", "Livello", 11, "Livello del territorio della riga.",
                  "Si filtra per vedere un solo livello; le righe dei livelli inferiori sono raggruppate sotto il superiore.",
                  WORKBOOK_VALUES_CATEGORY)
_COL_CODICE = _C("codice", "Codice", 9, "Codice ISTAT del territorio.", "Testo, non numero; vuoto per l'Italia.",
                 "2 cifre per la regione, 3 per la provincia, 6 per il comune", WORKBOOK_FMT_TEXT)
_COL_TERRITORIO = _C("territorio", "Territorio", 34, "Nome ISTAT del territorio, rientrato secondo il livello.",
                     "Le province seguono la loro regione, i comuni la loro provincia.", _NAME_ISTAT)
_COL_TERRITORY_CAMMINI = _C("cammini", "Cammini", 11, "Cammini che toccano il territorio.",
                            f"Un cammino tocca il territorio se attraversa almeno uno dei suoi comuni (entro {_D} m); "
                            "conta una volta anche se ne attraversa molti.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "cammini")
_COL_TERRITORY_CROSSED = _C("comuni_attraversati", "Comuni attraversati", 12,
                            "Comuni del territorio attraversati da almeno un cammino.",
                            "Sono le righe del foglio Comuni di quel territorio.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni")
WORKBOOK_TERRITORI_COLUMNS = (
    _COL_LIVELLO, _COL_CODICE, _COL_TERRITORIO,
    _C("comuni_territorio", "Comuni del territorio", 12, "Comuni ISTAT del territorio.",
       "Confini ISTAT dell'anno di elaborazione; 1 nelle righe dei comuni.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT, "comuni"),
    _COL_TERRITORY_CROSSED,
    _C("quota_attraversati", "% comuni attraversati", 12, "Comuni attraversati diviso per i Comuni del territorio.",
       "100% nelle righe dei comuni.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_SHARE),
    _COL_TERRITORY_CAMMINI,
    _C("media_cammini", "Media cammini per comune attraversato", 14,
       "Media di N. cammini del foglio Comuni sui comuni attraversati del territorio.",
       "I comuni non attraversati non entrano nella media; nelle righe dei comuni è il numero dei suoi cammini.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_DECIMAL, "cammini"),
)
WORKBOOK_PIVOT_COLUMNS = (
    _COL_LIVELLO, _COL_CODICE, _COL_TERRITORIO,
    _C(WORKBOOK_PIVOT_N_KEY, "N. cammini", 10, "Cammini che toccano il territorio e hanno il valore scritto sopra la coppia.",
       "Mezzo e Fonte: valore dei soli tracciati del cammino nel territorio; Tema e Stagione: un cammino conta sotto ogni "
       "suo valore. Negli altri fogli i valori di una riga sommano ai Cammini.", WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_COUNT,
       "cammini"),
    _C(WORKBOOK_PIVOT_SHARE_KEY, "Incidenza", 10, "N. cammini diviso per i Cammini della riga.",
       "Vuota quando il territorio non ha cammini; oltre il 100% in somma solo per Tema e Stagione.",
       WORKBOOK_VALUES_NUMBER, WORKBOOK_FMT_SHARE),
    replace(_COL_TERRITORY_CAMMINI, band=WORKBOOK_TOTAL_LABEL),
    replace(_COL_TERRITORY_CROSSED, band=WORKBOOK_TOTAL_LABEL),
)

# Legenda: scopo, fonti, dizionario delle colonne
WORKBOOK_LEGENDA_SCOPE_TITLE = "Scopo"
WORKBOOK_LEGENDA_SCOPE = (
    "Il file risponde a due domande: quali cammini attraversano ogni comune italiano e quali comuni attraversa ogni cammino, "
    "nell'ordine del percorso. "
    f"Un cammino attraversa un comune quando il suo tracciato entra nel comune o passa a non più di {_D} m dal confine "
    f"(confini ISTAT al 1 gennaio {ISTAT_EDITION_YEAR}); un cammino pubblicato da più cataloghi conta una volta. "
    "Il metodo è descritto nella nota metodologica del repository, report/Cammini Comuni - Nota Metodologica.docx."
)
WORKBOOK_LEGENDA_SOURCES_TITLE = "Fonti"
WORKBOOK_LEGENDA_SOURCES_HEADERS = ("Fonte", "Che cosa fornisce", "Data di scaricamento", "Licenza e condizioni")
# (fonte, che cosa fornisce, host del registro delle richieste, licenza)
WORKBOOK_LEGENDA_SOURCES = (
    ("Cammini d'Italia", "Elenco dei cammini con scheda tecnica e tappe (camminiditalia.org) e tracce GPS delle tappe "
     "(app Cammini d'Italia, Webmapp).", (HOST_CDI, HOST_MAPHUB, HOST_S3),
     "Dati di Cammini d'Italia S.R.L., usati in base al contratto «Commercial Data License Agreement and API access term» "
     "stipulato con Nomisma: Nomisma può condividerli con i propri partner, che non possono ridistribuirli."),
    ("MiTur (Catalogo dei Cammini religiosi)", "Schede dei cammini religiosi del Ministero del Turismo e tracce GPS dei file "
     "scaricabili.", (HOST_MITUR,), "Licenza CC BY 3.0 salvo diversa indicazione (Note legali del portale: "
     "https://www.ministeroturismo.gov.it/note-legali/); fonte: Ministero del Turismo."),
    ("OpenStreetMap", "Relazioni di percorso dell'estratto Italia di Geofabrik, usate per abbinare i cammini e come tracciato "
     "dei cammini senza tracce.", (HOST_GEOFABRIK,),
     "© OpenStreetMap contributors, ODbL (https://www.openstreetmap.org/copyright)."),
    ("ISTAT confini comunali", f"Confini non generalizzati di comuni, province e regioni al 1 gennaio {ISTAT_EDITION_YEAR}.",
     (HOST_ISTAT,), "Licenza CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), fonte: ISTAT."),
)
WORKBOOK_LEGENDA_COLUMNS_TITLE = "Colonne"
WORKBOOK_LEGENDA_HEADERS = ("Colonna", "Che cosa contiene", "Valori", "Come leggerla")
WORKBOOK_LEGENDA_WIDTHS = (34, 60, 45, 70)  # colonne B..E
WORKBOOK_LEGENDA_SEPARATOR = "{sheet}: {description}"
