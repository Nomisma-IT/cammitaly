"""Unica sede di tutte le costanti: percorsi, URL, ritmo delle richieste, tetti."""
import os
from dataclasses import dataclass
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
FONTE_GEOMETRIA_CATALOGO, FONTE_GEOMETRIA_OSM, FONTE_GEOMETRIA_NESSUNA = "catalogo", "osm", "nessuna"
# Tracce di catalogo di un membro insieme al sostituto OSM di un altro membro dello stesso cammino distinto
FONTE_GEOMETRIA_CATALOGO_OSM = "catalogo e osm"
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
# Ingressi: i due file del contratto P5 (PAIRS_DIR e PAIRS_FILES). Uscita: un file per giorno di elaborazione
WORKBOOK_OUTPUT_DIR = DATA_DIR / "output"
WORKBOOK_FILE_TEMPLATE = "cammini_comuni_{data}.xlsx"  # {data} = data di elaborazione, AAAA-MM-GG
WORKBOOK_SHEET_COMUNI, WORKBOOK_SHEET_CAMMINI = "Comuni", "Cammini"
WORKBOOK_SHEET_COPPIE, WORKBOOK_SHEET_NOTE = "Coppie", "Note"
# Colonne attese nei due file: quelle del contratto, definite una volta sola nella sezione Coppie
WORKBOOK_COPPIE_FIELDS = PAIRS_COPPIE_FIELDS
WORKBOOK_TAG_FIELDS = PAIRS_MULTI_FIELDS
WORKBOOK_CAMMINI_FIELDS = PAIRS_CAMMINI_FIELDS
# Colonne di ogni foglio (chiave -> intestazione in WORKBOOK_HEADERS)
WORKBOOK_COMUNI_KEYS = (
    "cod_reg", "regione", "cod_uts", "provincia", "sigla", "PRO_COM_T", "comune",
    "mezzo", "religioso", "n_cammini", "cammini",
)
WORKBOOK_COPPIE_KEYS = (*WORKBOOK_COPPIE_FIELDS, *WORKBOOK_TAG_FIELDS)
WORKBOOK_HEADERS = {
    "cod_reg": "Codice regione", "regione": "Regione", "cod_uts": "Codice provincia", "provincia": "Provincia",
    "sigla": "Sigla", "PRO_COM_T": "Codice comune", "comune": "Comune", "mezzo": "Mezzo",
    "religioso": "Religioso", "n_cammini": "Numero cammini", "cammini": "Cammini",
    "cammino_unico_id": "Codice cammino", "nome": "Cammino", "alias": "Altri nomi", "fonti": "Fonti",
    "membri": "Codici nelle fonti", "evidenza_religioso": "Indicazione del carattere religioso",
    "tipologia": "Tipologia", "portata": "Portata", "tema": "Tema", "stagione": "Stagione",
    "credenziale": "Credenziale", "segnaletica": "Segnaletica", "data_catalogo": "Data nel catalogo",
    "regioni": "Regioni", "lunghezza_dichiarata_km": "Lunghezza dichiarata (km)",
    "lunghezza_tracciata_km": "Lunghezza del tracciato (km)", "copertura": "Copertura del tracciato",
    "fonte_geometria": "Fonte del tracciato", "nota": "Nota", "n_comuni": "Numero comuni",
    "ordine": "Ordine lungo il cammino", "distanza_m": "Distanza dal comune (m)", "dentro": "Dentro il comune",
}
WORKBOOK_RAGGED_HEADER = "Comune {numero}"  # colonne dei comuni del foglio Cammini, nell'ordine del percorso
WORKBOOK_RAGGED_FORMAT = "{comune} ({sigla})"  # esistono comuni omonimi in province diverse
# Codici ISTAT scritti come testo, con gli zeri iniziali: cifre per chiave
WORKBOOK_CODE_DIGITS = {"cod_reg": 2, "cod_uts": 3, "PRO_COM_T": 6}
WORKBOOK_INT_KEYS = ("ordine", "n_comuni")
WORKBOOK_FLOAT_KEYS = ("distanza_m", "lunghezza_dichiarata_km", "lunghezza_tracciata_km", "copertura")
WORKBOOK_BOOL_KEY = "dentro"
WORKBOOK_ENUM_KEYS = ("mezzo", "religioso")  # ammessi solo i valori di WORKBOOK_VALUE_LABELS
WORKBOOK_TRUE_VALUES, WORKBOOK_FALSE_VALUES = ("true", "1", "si"), ("false", "0", "no")
# Colonne con piu' valori uniti da LIST_SEPARATOR nei dati intermedi: nel foglio sono separati da WORKBOOK_CELL_SEPARATOR
WORKBOOK_LIST_KEYS = (
    "alias", "fonti", "membri", "regioni", "fonte_geometria", "evidenza_religioso", *WORKBOOK_TAG_FIELDS,
)
WORKBOOK_CELL_SEPARATOR = "; "
# Valori scritti come slug nei dati ("arte-e-cultura"): nel foglio si leggono come parole
WORKBOOK_WORD_KEYS = ("tema",)
# Date ISO nei dati intermedi: nel foglio una data sola e' una data di Excel con questo formato, piu' date sono testo
WORKBOOK_DATE_KEYS = ("data_catalogo",)
WORKBOOK_DATE_NUMBER_FORMAT = "DD/MM/YYYY"
WORKBOOK_SLUG_SEPARATOR = "-"
# Etichette di visualizzazione; l'ordine dei dizionari e' l'ordine delle righe di dettaglio del foglio Comuni
WORKBOOK_MEZZO_LABELS = {MEZZO_PIEDI: "A piedi", MEZZO_BICI: "In bici", MEZZO_ENTRAMBI: "A piedi e in bici"}
WORKBOOK_SI_NO_LABELS = {SI: "Sì", NO: "No"}
WORKBOOK_ALL_LABEL = "Tutti"  # Mezzo e Religioso della riga di totale di un comune
WORKBOOK_VALUE_LABELS = {
    "mezzo": WORKBOOK_MEZZO_LABELS,
    "religioso": WORKBOOK_SI_NO_LABELS,
    "fonti": {
        CDI_FONTE: "camminiditalia.org", MITUR_FONTE: "Catalogo dei cammini religiosi del Ministero del Turismo",
        OSM_FONTE: "OpenStreetMap",
    },
    "fonte_geometria": {
        FONTE_GEOMETRIA_CATALOGO: "Catalogo", FONTE_GEOMETRIA_OSM: "OpenStreetMap",
        FONTE_GEOMETRIA_CATALOGO_OSM: "Catalogo e OpenStreetMap", FONTE_GEOMETRIA_NESSUNA: "Nessuna",
    },
    "credenziale": WORKBOOK_SI_NO_LABELS,
    "segnaletica": WORKBOOK_SI_NO_LABELS,
}
# Registro delle richieste (REQUESTS_LOG): le richieste riuscite di ogni fonte danno la data di scarico del foglio Note.
# Chiave = segnaposto nelle righe di WORKBOOK_NOTE_SECTIONS, valore = host del registro (HOST_*) di quella fonte
WORKBOOK_SOURCE_HOSTS = {
    "data_cdi": (HOST_CDI, HOST_MAPHUB, HOST_S3), "data_mitur": (HOST_MITUR,),
    "data_osm": (HOST_GEOFABRIK,), "data_istat": (HOST_ISTAT,),
}
WORKBOOK_LOG_OK_STATUS = "200"
WORKBOOK_DOWNLOAD_ONE, WORKBOOK_DOWNLOAD_RANGE = "il {first}", "dal {first} al {last}"
WORKBOOK_DOWNLOAD_MISSING = "in data non registrata"
# Righe di dettaglio di un comune sotto il totale: riassunte con lo schema a righe di Excel, chiuse all'apertura
WORKBOOK_OUTLINE_LEVEL = 1
WORKBOOK_FREEZE_CELL = "A2"  # sotto la riga di intestazione
WORKBOOK_WIDTH_MIN, WORKBOOK_WIDTH_MAX, WORKBOOK_WIDTH_PADDING = 8, 60, 2
WORKBOOK_NOTE_WIDTH = 120
WORKBOOK_DATE_DISPLAY_FORMAT = "%d/%m/%Y"
# Foglio Note: (titolo della sezione, righe). I segnaposto {distanza_m}, {anno_istat}, {data} si riempiono dai parametri;
# {data_cdi}, {data_mitur}, {data_osm}, {data_istat} dal registro delle richieste (WORKBOOK_SOURCE_HOSTS)
WORKBOOK_NOTE_SECTIONS = (
    ("Contenuto della cartella di lavoro", (
        "Comuni: per ogni comune una riga di totale, in grassetto, con il numero e i nomi dei cammini che lo attraversano; "
        "sotto, le righe di dettaglio per mezzo e carattere religioso, raggruppate e chiuse (si aprono con il segno + a sinistra).",
        "Cammini: una riga per cammino, con le sue caratteristiche e i comuni attraversati nell'ordine del percorso.",
        "Coppie: una riga per ogni coppia cammino e comune, con i dati dell'incrocio e le caratteristiche del cammino.",
    )),
    ("Significato delle colonne", (
        "Codice regione, Codice provincia, Codice comune: codici ISTAT, scritti come testo con gli zeri iniziali (2, 3 e 6 cifre).",
        "Sigla: sigla della provincia.",
        "Mezzo: come è pubblicato il cammino. A piedi o In bici; A piedi e in bici quando le fonti lo pubblicano in entrambi i modi. "
        "Nel foglio Comuni e nel foglio Coppie si riferisce ai tracciati del cammino che passano vicino a quel comune. "
        "Nella riga di totale di un comune vale Tutti.",
        "Religioso: Sì se almeno una fonte indica il cammino come religioso (Catalogo dei cammini religiosi del Ministero del Turismo, "
        "tema spirituale su camminiditalia.org o sulla mappa Cammini d'Italia, pellegrinaggio in OpenStreetMap) o se il nome del "
        "cammino contiene una parola religiosa (ad esempio San, Santa, Romea), altrimenti No. Nella riga di totale di un comune vale Tutti.",
        "Numero cammini: cammini diversi che attraversano il comune. Il totale conta ogni cammino una volta; le righe di dettaglio sommano al totale.",
        "Cammini: nomi dei cammini in ordine alfabetico, separati da punto e virgola.",
        "Codice cammino: identificativo del cammino in questa cartella di lavoro. Cammino: nome del cammino. "
        "Altri nomi: nomi con cui lo stesso cammino compare nelle altre fonti.",
        "Fonti: fonti in cui il cammino compare; un cammino presente in più fonti è contato una sola volta. "
        "Codici nelle fonti: identificativi del cammino presso le singole fonti.",
        "Indicazione del carattere religioso: da che cosa risulta la scelta tra Sì e No.",
        "Tipologia, Portata, Tema, Stagione, Credenziale, Segnaletica, Data nel catalogo: informazioni pubblicate dalle fonti; "
        "se le fonti non concordano compaiono tutti i valori.",
        "Regioni: regioni indicate dalle fonti.",
        "Lunghezza dichiarata (km): lunghezza pubblicata dalla fonte da cui viene il tracciato misurato. Lunghezza del tracciato (km): "
        "lunghezza di quel tracciato, contando una volta sola le parti ripetute. "
        "Copertura del tracciato: quota della lunghezza dichiarata coperta dal tracciato (al massimo 1), quando entrambe sono note.",
        "Fonte del tracciato: Catalogo, OpenStreetMap, Catalogo e OpenStreetMap (quando il cammino ha tracciati del catalogo e, "
        "per un'altra sua versione, di OpenStreetMap) o Nessuna quando del cammino non c'è un tracciato. Nota: osservazioni sul cammino.",
        "Numero comuni: comuni attraversati dal cammino. Comune 1, Comune 2, ...: i comuni nell'ordine del percorso, con la sigla della provincia.",
        "Ordine lungo il cammino: posizione del comune sul percorso, a partire da 1. I comuni seguono il percorso dalla partenza "
        "all'arrivo; per i cammini con più rami seguono un ramo dopo l'altro. Per i tracciati presi da OpenStreetMap senza una "
        "partenza pubblicata il verso non è garantito.",
        "Distanza dal comune (m): distanza minima del tracciato dal confine del comune. "
        "Dentro il comune: Sì se il tracciato tocca o entra nel comune, No se passa fuori ma a distanza non superiore a quella indicata sotto.",
    )),
    ("Parametri", (
        "Distanza: un cammino è associato a un comune se il suo tracciato dista dal confine comunale al massimo {distanza_m} m.",
        "Confini comunali: ISTAT, edizione al 1 gennaio {anno_istat}.",
        "Data di elaborazione: {data}.",
    )),
    ("Fonti", (
        "camminiditalia.org: elenco e tracciati dei cammini del portale Cammini d'Italia. Scaricato {data_cdi}. "
        "Dati di Cammini d'Italia S.R.L., usati in base al contratto «Commercial Data License Agreement and API access term» stipulato con Nomisma: Nomisma può condividerli con i propri partner, che non possono ridistribuirli.",
        "Catalogo dei cammini religiosi del Ministero del Turismo: elenco e tracciati dei cammini religiosi. Scaricato {data_mitur}.",
        "OpenStreetMap, estratto di Geofabrik: relazioni di percorso. Scaricato {data_osm}. © OpenStreetMap contributors, ODbL "
        "(https://www.openstreetmap.org/copyright).",
        "ISTAT, confini delle unità amministrative a fini statistici. Scaricato {data_istat}. "
        "Licenza CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), fonte: ISTAT.",
    )),
)

# --- Numeri del rapporto (report_numbers.py) ---
# Ogni numero citato dal rapporto metodologico e' una riga di REPORT_NUMBERS_FILE, calcolata dai dati gia' prodotti dalla
# catena (mai rilanciando un passo). Valori ASCII negli id; descrizioni in italiano; quote e recall sempre in percentuale
REPORT_NUMBERS_FILE = WORKBOOK_OUTPUT_DIR / "numeri_rapporto.csv"
REPORT_NUMBERS_FIELDS = ("id", "sezione", "descrizione", "valore", "unita", "fonte")
REPORT_SEZIONI = (
    "fonti", "tracciati", "calibrazione", "deduplicazione", "osm", "etichette", "comuni", "ordine", "cartella",
)
REPORT_ID_PATTERN = r"[a-z][a-z0-9_]*"
REPORT_EMPTY_SLUG = "vuoto"  # valore mancante usato come parte di un id
REPORT_PERCENT_DECIMALS = 2
REPORT_VALUE_DECIMALS = 4  # numeri non percentuali (m, km, punteggi)
REPORT_BYTES_PER_MB = 1024**2
REPORT_SOURCE_NOTE_SEPARATOR = "; "  # tra il file e il filtro nella colonna fonte
REPORT_DATA_PREFIX = "data/"  # i percorsi nella colonna fonte partono da qui
# Percentili di una distribuzione: nome nell'id -> quantile
REPORT_QUANTILES = {"mediana": 0.5, "p90": 0.9}
# Fonti: registro delle richieste e scheda MiTur
REPORT_LOG_DATE_CHARS = 10  # AAAA-MM-GG, i primi caratteri di utc_iso
REPORT_MITUR_FORMAT_SEPARATOR = ":"  # "gpx:33|kml:2" in mitur_cammini.csv: formato e numero di file
REPORT_CDI_TIPOLOGIE = ("lineare", "anello")
# Calibrazione: colonne di sintesi.csv riportate per ogni d (colonna, parte dell'id, descrizione con {d}, unita', fattore).
# Le quote (fattore 100) escono in percentuale
REPORT_CALIBRATION_COLUMNS = (
    ("A_recall_cammino", "a_recall_cammino", "Calibrazione A: estremi di tappa pubblicati ritrovati entro {d} m dalla linea del cammino", "%", 100),
    ("A_recall_tratta", "a_recall_tratta", "Calibrazione A: estremi di tappa pubblicati ritrovati entro {d} m dalla linea della tratta", "%", 100),
    ("C_recall", "c_recall", "Calibrazione C: comuni dell'altra fonte ritrovati a d = {d} m (coppie cdi-MiTur con lunghezze simili)", "%", 100),
    ("C_extra_tot", "c_extra", "Calibrazione C: comuni in piu' rispetto all'altra fonte a d = {d} m (coppie con lunghezze simili)", "comuni", 1),
    ("C_tutte_recall", "c_tutte_recall", "Calibrazione C: comuni dell'altra fonte ritrovati a d = {d} m (tutte le coppie cdi-MiTur)", "%", 100),
    ("C_tutte_extra_tot", "c_tutte_extra", "Calibrazione C: comuni in piu' rispetto all'altra fonte a d = {d} m (tutte le coppie cdi-MiTur)", "comuni", 1),
    ("D_coppie_totali", "d_coppie", "Coppie cammino-comune dei cammini del catalogo con tracce a d = {d} m", "coppie", 1),
    ("D_coppie_cdi", "d_coppie_cdi", "Coppie cammino-comune dei cammini cdi a d = {d} m", "coppie", 1),
    ("D_coppie_mitur", "d_coppie_mitur", "Coppie cammino-comune dei cammini MiTur a d = {d} m", "coppie", 1),
    ("D_comuni_per_cammino_cdi", "d_comuni_per_cammino_cdi", "Comuni per cammino cdi (media) a d = {d} m", "comuni", 1),
    ("D_comuni_per_cammino_mitur", "d_comuni_per_cammino_mitur", "Comuni per cammino MiTur (media) a d = {d} m", "comuni", 1),
    ("D_aumento_vs_0", "d_aumento", "Coppie cammino-comune in piu' a d = {d} m rispetto a d = 0", "coppie", 1),
    ("D_aumento_vs_0_pct", "d_aumento_pct", "Coppie cammino-comune in piu' a d = {d} m rispetto a d = 0, in percentuale", "%", 1),
)
# Esperimento B: colonne per metodo di degradazione (sintesi.csv: B_<metodo>_<misura>). Il caso peggiore su tutti i
# metodi esce per ogni d; il dettaglio per metodo solo a d scelta
REPORT_CALIBRATION_B_MEASURES = (
    ("recall", "recall", "min", "Recall sui comuni di riferimento", "%", 100),
    ("recall_marginale", "recall_marginale", "min", "Recall sui comuni a rischio (traccia dentro per meno di 200 m)", "%", 100),
    ("extra_tot", "extra", "max", "Comuni in piu' rispetto al riferimento", "comuni", 1),
)
REPORT_CALIBRATION_B_METHOD_TEXT = {
    DEGRADE_SUBSAMPLE_PREFIX: "un vertice ogni {n} m",
    DEGRADE_SIMPLIFY_PREFIX: "semplificazione con tolleranza {n} m",
    DEGRADE_JITTER_PREFIX: "errore laterale casuale con sigma {n} m",
    DEGRADE_SHIFT_PREFIX: "traslazione laterale di {n} m (lato peggiore)",
}
# Deduplicazione: id breve della diagnosi dei quasi duplicati
REPORT_DEDUP_DIAGNOSI_IDS = {
    DIAGNOSI_SFASAMENTO: "sfasamento", DIAGNOSI_VARIANTE: "variante", DIAGNOSI_MANCANTI: "mancanti", DIAGNOSI_VERSIONE: "versione",
}
REPORT_DEDUP_CLASSES = (CLASSE_DUPLICATO, CLASSE_SEZIONE, CLASSE_LIMBO, CLASSE_DISTINTO)
# Abbinamento con OSM: esiti in ordine di lettura
REPORT_OSM_ESITI = (
    ESITO_ABBINATO, ESITO_AMBIGUO, ESITO_SOLO_GEOMETRIA, ESITO_NESSUNO, ESITO_PARZIALE, ESITO_CONTENITORE,
)
REPORT_OSM_PILGRIMAGE_VALUES = ("si", "no", OSM_MATCH_PILGRIMAGE_SILENT)
REPORT_OSM_TAG_YES = "yes"  # pilgrimage=yes in cammini_osm.csv; no = valori negativi di tags.py
# Etichette: da che cosa risulta il carattere religioso (id, descrizione); la classificazione e' in report_numbers.py
REPORT_EVIDENCE_KINDS = (
    ("catalogo", "nel Catalogo dei cammini religiosi del Ministero del Turismo"),
    ("tema_cdi", "con tema spirituale su camminiditalia.org"),
    ("tema_mappa", "con tema spirituale sulla mappa Cammini d'Italia"),
    ("pellegrinaggio_osm", "indicati come pellegrinaggio in OpenStreetMap"),
    ("nome", "con una parola religiosa nel nome"),
)
REPORT_TAG_MULTI_FIELDS = ("tipologia", "portata")  # campi multivalore elencati per valore
REPORT_TAG_FLAG_FIELDS = ("credenziale", "segnaletica")  # campi si/no: cammini con almeno un si
# Comuni
REPORT_TOP_COMUNI = 10
REPORT_COPERTURA_BANDS = (0.5, 0.8)  # sotto la prima, tra le due, dalla seconda in su; vuota a parte
REPORT_NOTA_KINDS = (("possibile_duplicato", "possibile duplicato"), ("nessuna_geometria", "nessuna geometria"))
# Ordine: salto tra comuni consecutivi (distanza tra i centroidi) oltre cui il passaggio a un altro ramo e' sospetto
REPORT_JUMP_THRESHOLDS_KM = (30, 60, 100)
REPORT_ENDPOINT_TIPOLOGIA = "lineare"
# Cartella di lavoro
REPORT_WORKBOOK_GLOB = WORKBOOK_FILE_TEMPLATE.format(data="*")
REPORT_WORKBOOK_DATE_PATTERN = r"(\d{4}-\d{2}-\d{2})\.xlsx$"
REPORT_WORKBOOK_DATA_SHEETS = (WORKBOOK_SHEET_COMUNI, WORKBOOK_SHEET_CAMMINI, WORKBOOK_SHEET_COPPIE)  # una riga di intestazione
# Calibrazione: colonne di sintesi.csv che non dipendono da d (colonna, id, descrizione, unita', fattore)
REPORT_CALIBRATION_FIXED = (
    ("A_n_coppie_cammino", "calib_a_coppie_cammino", "Calibrazione A: coppie (cammino, luogo) misurate sulla linea del cammino", "coppie", 1),
    ("A_n_coppie_tratta", "calib_a_coppie_tratta", "Calibrazione A: coppie (tratta, luogo) misurate sulla linea della tratta", "coppie", 1),
    ("A_n_senza_linea_coppie", "calib_a_senza_linea_coppie", "Calibrazione A: coppie escluse perche' il cammino non ha una linea", "coppie", 1),
    ("A_n_senza_linea_cammini", "calib_a_senza_linea_cammini", "Calibrazione A: cammini senza una linea", "cammini", 1),
    ("B_n_tratte", "calib_b_tratte", "Calibrazione B: tratte a tappa dense usate come riferimento", "tratte", 1),
    ("B_n_cammini", "calib_b_cammini", "Calibrazione B: cammini con tracce dense", "cammini", 1),
    ("B_n_riferimento", "calib_b_riferimento", "Calibrazione B: coppie (cammino, comune) di riferimento, dove la traccia densa entra nel comune", "coppie", 1),
    ("B_n_marginali", "calib_b_marginali", "Calibrazione B: coppie a rischio, dove la traccia densa sta dentro il comune per meno di 200 m", "coppie", 1),
    ("C_n_gruppi", "calib_c_gruppi_lunghezza_simile", "Calibrazione C: coppie cdi-MiTur dello stesso cammino con lunghezze simili", "coppie", 1),
    ("C_densa_n_gruppi", "calib_c_gruppi_densi", "Calibrazione C: coppie cdi-MiTur con lunghezze simili e traccia MiTur densa", "coppie", 1),
    ("C_tutte_n_gruppi", "calib_c_gruppi_tutti", "Calibrazione C: coppie cdi-MiTur dello stesso cammino, di qualunque lunghezza", "coppie", 1),
    ("D_n_cammini", "calib_d_cammini", "Calibrazione D: cammini del catalogo con tracce misurati", "cammini", 1),
)
# Calibrazione A, distribuzione della distanza dei luoghi pubblicati dalla linea (A_distribuzione.csv, una riga per ambito)
REPORT_CALIBRATION_DISTRIBUTION = (
    ("n_misurate", "n_misurate", "coppie (luogo, {ambito}) misurate", "coppie", 1),
    ("quota_a_0", "quota_a_0", "quota di luoghi pubblicati a distanza 0 dalla linea del {ambito}", "%", 100),
    ("p50", "p50_m", "distanza mediana dei luoghi pubblicati dalla linea del {ambito}", "m", 1),
    ("p90", "p90_m", "90o percentile della distanza dei luoghi pubblicati dalla linea del {ambito}", "m", 1),
    ("p95", "p95_m", "95o percentile della distanza dei luoghi pubblicati dalla linea del {ambito}", "m", 1),
    ("p99", "p99_m", "99o percentile della distanza dei luoghi pubblicati dalla linea del {ambito}", "m", 1),
    ("max", "max_m", "distanza massima di un luogo pubblicato dalla linea del {ambito}", "m", 1),
)
# Calibrazione A, esiti del confronto dei nomi dei luoghi con i comuni ISTAT (riga origine "tutte", ruolo "tutti")
REPORT_CALIBRATION_MATCH_ROW = ("tutte", "tutti")
REPORT_CALIBRATION_MATCH_COLUMNS = ("ok", "omonimo", "due luoghi", "nome_parziale", "non_trovato", "vuoto", "n_nomi")
# Lunghezza del tracciato su lunghezza dichiarata (cammini.csv, prima del tetto a 1 della copertura): soglie contate
REPORT_LENGTH_RATIO_THRESHOLDS = (1.0, 1.1, 1.5)
REPORT_OSM_NETWORKS = ("lwn", "rwn", "nwn", "iwn")  # reti di percorsi a piedi di OSM: locale, regionale, nazionale, internazionale
