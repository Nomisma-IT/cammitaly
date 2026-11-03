"""Unica sede di tutte le costanti: percorsi, URL, ritmo delle richieste, tetti."""
from dataclasses import dataclass
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
    HOST_S3: HostPolicy(1.0, 1400),  # hosting statico S3, 1 richiesta/s e' molto cortese
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
MITUR_SITE_LABEL = "Visita sito web"
MITUR_DOWNLOAD_LABEL = "Scarica percorso"
SLUG_STRIP_PATTERN = r"[^a-z0-9]+"
# Il numero di pagina si legge dal link canonical, non dal nome del file salvato a mano
MITUR_CANONICAL_PATTERN = r'<link\s+rel="canonical"\s+href="([^"]+)"'
MITUR_PAGE_NUMBER_PATTERN = r"/page/(\d+)/?$"
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
ISTAT_EDITION_YEAR = "2026"
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
OSM_RELATION_TYPES = ("route", "superroute")
# Si scelgono le relazioni a piedi, oppure di qualunque tipo di route purche' marcate come pellegrinaggio
OSM_WALK_ROUTES = ("hiking", "foot", "walking")
OSM_BIKE_ROUTES = ("bicycle", "mtb")  # entrano solo con pilgrimage=yes e hanno tipo bici
OSM_PILGRIMAGE_KEY, OSM_PILGRIMAGE_YES = "pilgrimage", "yes"
OSM_KEY_FILTER = ("route", OSM_PILGRIMAGE_KEY)  # chiavi per il filtro di libosmium, prima del controllo dei valori
# Ruoli dei way che non fanno parte del tracciato principale
OSM_SKIPPED_ROLES = frozenset(("alternative", "excursion", "approach"))
OSM_MAX_DEPTH = 6  # profondita' massima delle super-relazioni; oltre, i figli si ignorano
OSM_NAME_TAGS = ("name:it", "name", "ref")  # il primo presente e' il nome del cammino
OSM_TAG_COLUMNS = ("ref", "route", "network", "pilgrimage", "operator", "wikidata", "website")
OSM_STRUCTURE_COLUMNS = ("is_super", "n_children", "n_children_not_selected", "n_missing_ways")
CAMMINI_OSM_FIELDS = [*CAMMINI_FIELDS, *OSM_TAG_COLUMNS, *OSM_STRUCTURE_COLUMNS]
# Memoria: l'IdSet denso di libosmium alloca 512 KB per ogni intervallo di id toccato, quindi i nodi si
# leggono a fette di id; le coordinate si tengono come interi a 1e-7 gradi (int32) in array numpy
OSM_NODE_SLICES = 16
OSM_COORD_SCALE = 1e7
OSM_MAX_RSS_MB = 1250  # oltre questo RSS il processo si ferma da solo (il PC ha 1-3 GB liberi)
OSM_RSS_SAMPLE_S = 0.25
OSM_RSS_ABORT_EXIT_CODE = 3
OSM_CHUNK_ROWS = 1500  # tratte per blocco di scrittura del GeoPackage: le geometrie non restano tutte in memoria

# --- Incrocio comuni: quali comuni attraversa un cammino, entro una distanza dal confine comunale ---
# Gli shapefile ISTAT non hanno il file .cpg: senza questa codifica i nomi accentati escono in mojibake
ISTAT_ENCODING = "utf-8"
ISTAT_COMUNI_SHP = ISTAT_EXTRACT_DIR / f"Com0101{ISTAT_EDITION_YEAR}" / f"Com0101{ISTAT_EDITION_YEAR}_WGS84.shp"
ISTAT_REGIONI_SHP = ISTAT_EXTRACT_DIR / f"Reg0101{ISTAT_EDITION_YEAR}" / f"Reg0101{ISTAT_EDITION_YEAR}_WGS84.shp"
COMUNE_ID_COL, COMUNE_NAME_COL, COMUNE_REGION_COL = "PRO_COM_T", "COMUNE", "COD_REG"
REGION_CODE_COL, REGION_NAME_COL = "COD_REG", "DEN_REG"
# Tipi di tratta che formano la linea di un cammino; le tracce per bici non sono cammino a piedi
CROSSING_TIPI = (TIPO_TAPPA, TIPO_VARIANTE, TIPO_ALTRO)
CROSSING_FIELDS = ("cammino_id", COMUNE_ID_COL, "distanza_m", "lunghezza_dentro_m")
# Raggio iniziale del ritaglio locale della linea attorno a un comune; raddoppia finche' la distanza non e' certa
CROSSING_CLIP_START_M = 1000.0

# --- Calibrazione della distanza d ---
CALIBRATION_DIR = INTERIM_DIR / "calibrazione"
CALIBRATION_DISTANCES_M = (0, 25, 50, 100, 200, 400, 800)
CALIBRATION_PERCENTILES = (50, 90, 95, 99)
CALIBRATION_FAR_M = 400  # le coppie di riferimento oltre questa distanza vanno in un elenco da ispezionare
CALIBRATION_GRAZING_M = (50, 100)  # lunghezza dentro il comune sotto la quale la linea "sfiora" soltanto
# Esperimento B: tracce dense (MiTur) degradate alla precisione di Webmapp
CALIBRATION_DENSE_SPACING_M = 10
# Con meno tratte dense di cosi' in MiTur, il campione di B si completa con le tratte cdi piu' dense
CALIBRATION_DENSE_MIN_TRATTE = 50
CALIBRATION_SUBSAMPLE_SPACINGS_M = (25, 60)  # spaziatura media obiettivo di "un vertice ogni k"
CALIBRATION_SIMPLIFY_TOLERANCES_M = (10, 25)
DEGRADE_SUBSAMPLE_PREFIX, DEGRADE_SIMPLIFY_PREFIX = "k", "s"  # nomi dei metodi: k25, k60, s10, s25
# Esperimento C: coppie provvisorie cdi-MiTur dello stesso cammino (solo per la calibrazione, non e' la deduplica)
CALIBRATION_PAIR_NAME_MIN_SCORE = 90  # token_sort_ratio sui nomi normalizzati
CALIBRATION_PAIR_OVERLAP_MIN = 0.8  # quota del cammino piu' corto entro CALIBRATION_PAIR_NEAR_M dall'altro
CALIBRATION_PAIR_NEAR_M = 50
CALIBRATION_PAIR_SAMPLE_M = 10  # passo dei punti con cui si misura la quota di linea vicina all'altra
# Esperimento A: nomi dei luoghi pubblicati confrontati con i comuni ISTAT
PLACE_SPLIT_PATTERN = r"[/(),;]|\s-\s"  # nomi doppi, parentesi, virgole e " - " dividono il testo in candidati
PLACE_STOPWORDS = frozenset((
    "a", "al", "alla", "allo", "all", "d", "da", "dal", "dalla", "dei", "del", "dell", "della", "delle", "dello",
    "di", "e", "i", "il", "in", "l", "la", "le", "lo", "nel", "nell", "nella", "su", "sul", "sull", "sulla",
))
PLACE_SAINT_TOKENS = frozenset(("s", "san", "sant", "santa", "santo"))
PLACE_SAINT_CANONICAL = "san"
ISTAT_BILINGUAL_SEPARATOR = "/"
MATCH_OK, MATCH_HOMONYM, MATCH_NOT_FOUND, MATCH_EMPTY = "ok", "omonimo", "non_trovato", "vuoto"
SOURCE_TAPPE, SOURCE_WEBMAPP = "tappe", "webmapp"
CALIBRATION_FONTI = (CDI_FONTE, MITUR_FONTE)  # fonti dei costi D e delle coppie C; l'esperimento B sceglie la sua
CALIBRATION_RATIO_DECIMALS = 4  # quote e recall nei CSV
CALIBRATION_FILE_SINTESI = "sintesi.csv"
CALIBRATION_FILES_A = {
    "riferimenti": "A_riferimenti.csv", "match": "A_match.csv",
    "distribuzione": "A_distribuzione.csv", "lontane": "A_oltre_400m.csv",
}
CALIBRATION_FILES_B = {"dettaglio": "B_dettaglio.csv", "totale": "B_totale.csv"}
CALIBRATION_FILES_C = {"coppie": "C_coppie.csv", "dettaglio": "C_dettaglio.csv"}
CALIBRATION_FILES_D = {"costi": "D_costi.csv", "grazing": "D_grazing.csv"}
