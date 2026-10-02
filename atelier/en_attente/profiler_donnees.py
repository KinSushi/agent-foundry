"""Profiler un jeu de données tabulaire colonne par colonne, en flux, avant d'en faire un modèle.

Lire un CSV « pour voir » trompe : mesuré dans cette session, pandas 3.0.6 (read_csv par
défaut) lit la colonne code_postal d'un CSV de 44 lignes en int64 et 18 valeurs y perdent leur
zéro initial (01000 devient 1000) ; l'interpréteur de référence n'a d'ailleurs ni pandas ni polars
(ModuleNotFoundError). Ce profileur stdlib lève l'alerte zeros_initiaux sur ce fichier, et a
profilé 300 000 lignes × 5 colonnes en 4,0 s avec 49 Mo de mémoire résidente.

QUESTION
    Que contient ce jeu de données colonne par colonne : types, manquants, cardinalité,
    valeurs aberrantes ?
MESURE
    Lecture en flux d'un csv/tsv (séparateur deviné parmi , ; tabulation |), d'un jsonl, d'un
    JSON (tableau d'objets) ou d'un texte JSON en ligne ; .gz accepté. Par colonne : type inféré
    (booleen, entier, decimal, date, date-heure, objet, tableau, texte, ou mixte) avec sa
    conformité et des exemples non conformes ; manquants (cellule vide, blanche, ou jeton de la
    liste par défaut de pandas : NA, null, NaN...) ; valeurs distinctes ; cinq valeurs les plus
    fréquentes ; min, max, moyenne, écart-type (Welford, exacts) ; quantiles 1, 5, 25, 50, 75, 95,
    99 % (interpolation linéaire, type 7) ; valeurs aberrantes hors des bornes de Tukey
    (Q1 - 1,5 IQR, Q3 + 1,5 IQR) ; longueurs ; dates extrêmes ; zéros initiaux ; espaces
    superflus. Tant qu'une colonne a au plus --max-distincts valeurs distinctes (défaut 50 000),
    tout est exact (compteur complet). Au-delà, la colonne bascule : distinctes estimées par
    HyperLogLog (2^14 registres, blake2b 64 bits, erreur type 1,04/128 = 0,81 %, Flajolet et al.
    2007), fréquences par Misra-Gries (1000 compteurs, bornes publiées), quantiles et aberrantes
    sur un réservoir uniforme (algorithme R, --echantillon valeurs, --graine). Au niveau du
    fichier : lignes, lignes dupliquées (empreintes, 5 millions de lignes au plus), lignes mal
    formées. Alertes (code 1) : colonne vide, constante, type mixte, valeurs non conformes au
    type, zéros initiaux dans un entier, doublons, lignes mal formées. Colonnes quasi
    identifiantes (texte ou entier, au moins 95 % de valeurs distinctes sur au moins 10 valeurs)
    signalées comme observation. Si polars ou pandas est installé, il relit le fichier et les
    manquants, distinctes, min, max et moyennes lui sont comparés.
HYPOTHÈSES
    La première ligne d'un CSV est l'en-tête ; le fichier est en utf-8 (ou --encodage) ; une
    valeur décimale s'écrit avec un point (ou --virgule-decimale) ; le réservoir est un tirage
    uniforme, donc ses quantiles approchent ceux de la colonne entière.
LIMITES
    Pas de détection d'un CSV sans en-tête (la première ligne devient l'en-tête) ; séparateurs de
    milliers (1 234 ou 1,234) lus comme texte ; dates reconnues : ISO 8601 et jj/mm/aaaa ou
    mm/jj/aaaa (sens publié), les autres sont du texte ; dates extrêmes comparées comme du texte,
    fuseau horaire ignoré ; un objet ou tableau JSON imbriqué compte comme une valeur, sans
    aplatissement ; un .json est chargé entier (200 Mio au plus, le jsonl est lu en flux) ;
    doublons comptés par empreinte sur 5 millions de lignes au plus ; après bascule, quantiles et
    aberrantes sont estimés sur le réservoir, et Misra-Gries ne publie aucune valeur moins
    fréquente que son incertitude (n/1001 environ) ; la contre-vérification relit le fichier par
    polars ou pandas pour un CSV, mais reçoit nos enregistrements pour un JSON (lecture non
    indépendante) ; elle est omise au-delà de --max-octets-comparaison. Un champ CSV de plus de
    131 072 caractères est refusé (limite par défaut du module csv).
CONTRE-EXEMPLES
    Constaté : une colonne montant_centimes de 1000 montants entiers tirés entre 100 et 999 999
    (1000 valeurs distinctes) est signalée quasi_identifiant, « identifiant probable », alors que
    c'est une mesure continue écrite en entiers ; la règle ne regarde que la part de valeurs
    distinctes, pas le sens de la colonne.
INVOCATION
    {outil} '[{"id": 1, "age": 34, "ville": "Lyon"}, {"id": 2, "age": 41, "ville": "Lyon"}, {"id": 3, "age": null, "ville": "Paris"}]' --json
DOMAINE
    Exports tabulaires destinés à l'apprentissage automatique ou à l'analyse (CSV de quelques
    lignes à plusieurs dizaines de millions, jsonl de journaux), à profiler avant tout choix de
    variables, de nettoyage ou de modèle.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
             INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE)

CODE_OK = 0
CODE_ALERTE = 1
CODE_USAGE = 2
CODE_RIEN = 3

NOM_OUTIL = "profiler_donnees"
VALEURS_MANQUANTES = frozenset({
    "", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN",
    "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"})
SEPARATEURS_CANDIDATS = ",;\t|"
OCTETS_SONDE = 65536
SUFFIXES_CSV = MappingProxyType({".csv": "", ".tsv": "\t", ".tab": "\t", ".txt": "", ".dat": "", ".psv": "|"})
SUFFIXES_JSONL = frozenset({".jsonl", ".ndjson", ".jsonlines"})
SUFFIXES_JSON = frozenset({".json"})
QUANTILES = (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
PRECISION_HLL = 14
ERREUR_TYPE_HLL = 1.04 / math.sqrt(1 << PRECISION_HLL)
MASQUE_64 = (1 << 64) - 1
CAPACITE_MISRA_GRIES = 1000
MAX_LIGNES_DOUBLONS = 5_000_000
MIN_VALEURS_IDENTIFIANT = 10
SEUIL_IDENTIFIANT = 0.95
FACTEUR_TUKEY = 1.5
EXEMPLES_MAX = 5
MAX_EXAMINES = 200
OCTETS_MAX_JSON = 200 * 1024 * 1024
OCTETS_MAX_COMPARAISON = 256 * 1024 * 1024
TOLERANCE_RELATIVE = 1e-9
TYPES_PAR_SPECIFICITE = ("booleen", "entier", "decimal", "date", "date-heure", "objet", "tableau")
CONFORMES = MappingProxyType({
    "booleen": frozenset({"booleen"}), "entier": frozenset({"entier"}),
    "decimal": frozenset({"entier", "decimal"}), "date": frozenset({"date"}),
    "date-heure": frozenset({"date", "date-heure"}), "objet": frozenset({"objet"}),
    "tableau": frozenset({"tableau"})})
TYPES_IDENTIFIANTS = frozenset({"entier", "texte", "mixte"})
BOOLEENS = frozenset({"true", "false", "vrai", "faux", "oui", "non", "yes", "no"})
MOTIF_ENTIER = re.compile(r"[+-]?\d+")
MOTIF_ZERO_INITIAL = re.compile(r"[+-]?0\d+")
MOTIF_DECIMAL = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
MOTIF_DECIMAL_VIRGULE = re.compile(r"[+-]?\d+,\d+")
MOTIF_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MOTIF_DATE_HEURE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
MOTIF_DATE_BARRES = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Entree:
    """Une source tabulaire : fichier CSV/JSONL/JSON ou texte JSON en ligne."""

    nom: str
    genre: str
    chemin: Path | None = None
    texte: str | None = None
    separateur: str = ","
    encodage: str = "utf-8-sig"
    max_octets_json: int = OCTETS_MAX_JSON
    colonnes: list[str] = field(default_factory=list)
    mal_formees: int = 0
    exemples_mal_formees: list[int] = field(default_factory=list)
    non_objets: int = 0


@dataclass
class Contexte:
    """Paramètres de l'analyse, passés partout au lieu d'un état global."""

    max_distincts: int
    taille_reservoir: int
    virgule: bool
    seuil_type: float
    top: int
    alea: random.Random


@dataclass
class Resume:
    """Agrégats d'une colonne, mis à jour par valeur pondérée (exacts quel que soit le mode)."""

    classes: Counter = field(default_factory=Counter)
    exemples: dict[str, list[str]] = field(default_factory=dict)
    n_num: int = 0
    moyenne: float = 0.0
    m2: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf
    zeros: int = 0
    negatifs: int = 0
    zeros_initiaux: int = 0
    espaces_superflus: int = 0
    longueur_min: int | None = None
    longueur_max: int = 0
    longueur_somme: int = 0
    date_min: str | None = None
    date_max: str | None = None
    barres_jour_premier: int = 0
    barres_mois_premier: int = 0


@dataclass
class Colonne:
    """État d'une colonne : compteur exact, puis résumé en flux au-delà du plafond."""

    nom: str
    non_manquants: int = 0
    manquants: int = 0
    compteur: Counter | None = field(default_factory=Counter)
    resume: Resume | None = None
    registres: bytearray | None = None
    frequents: dict[Any, int] = field(default_factory=dict)
    erreur_frequences: int = 0
    reservoir: list[float] = field(default_factory=list)
    numeriques_vus: int = 0


@dataclass
class Bilan:
    """Compteurs du fichier entier."""

    lignes: int = 0
    doublons: int = 0
    empreintes: set[int] = field(default_factory=set)
    doublons_tronques: bool = False
    tronque: bool = False


# --------------------------------------------------------------------------- lecture

def resoudre(chemin: str, racine: Path | None) -> Path:
    """Résout un chemin relatif contre --racine, ou le répertoire courant."""
    brut = Path(chemin)
    return brut if brut.is_absolute() or racine is None else racine / brut


def est_texte_json(spec: str) -> bool:
    """Un argument qui commence par [ ou { est un texte JSON en ligne, pas un chemin."""
    return spec.lstrip()[:1] in ("[", "{")


def normaliser_encodage(encodage: str) -> str:
    """utf-8 devient utf-8-sig (BOM retiré) ; refuse un nom d'encodage inconnu."""
    try:
        "".encode(encodage)
    except LookupError as exc:
        raise ErreurEntree(f"encodage inconnu : {encodage}") from exc
    return "utf-8-sig" if encodage.lower().replace("_", "-") in ("utf-8", "utf8") else encodage


def ouvrir_texte(chemin: Path, encodage: str) -> Any:
    """Ouvre un fichier texte, décompressé à la volée s'il finit par .gz."""
    if chemin.suffix.lower() == ".gz":
        return gzip.open(chemin, "rt", encoding=encodage, newline="")
    return chemin.open("r", encoding=encodage, newline="")


def lire_sonde(chemin: Path) -> bytes:
    """Premiers octets (décompressés) du fichier, pour le refus des binaires et le séparateur."""
    try:
        if chemin.suffix.lower() == ".gz":
            with gzip.open(chemin, "rb") as flux:
                return flux.read(OCTETS_SONDE)
        with chemin.open("rb") as flux:
            return flux.read(OCTETS_SONDE)
    except (OSError, EOFError) as exc:
        raise ErreurEntree(f"lecture impossible de {chemin} : {exc}") from exc


def verifier_fichier(chemin: Path, encodage: str) -> bytes:
    """Refuse absent, dossier, non ordinaire, binaire ; rend la sonde."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} est un dossier : un fichier CSV, TSV, JSONL ou JSON est attendu")
    if not chemin.is_file():
        raise ErreurEntree(f"{chemin} n'est pas un fichier ordinaire")
    sonde = lire_sonde(chemin)
    if b"\x00" in sonde and not encodage.lower().startswith(("utf-16", "utf-32")):
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas un CSV/JSON "
                           f"(s'il est en UTF-16, passer --encodage utf-16)")
    return sonde


def deviner_genre(chemin: Path, impose: str) -> str:
    """Format d'après --format ou l'extension (.gz ignoré)."""
    if impose != "auto":
        return "csv" if impose == "tsv" else impose
    suffixes = [s.lower() for s in chemin.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    suffixe = suffixes[-1] if suffixes else ""
    if suffixe in SUFFIXES_CSV:
        return "csv"
    if suffixe in SUFFIXES_JSONL:
        return "jsonl"
    if suffixe in SUFFIXES_JSON:
        return "json"
    raise ErreurEntree(f"extension « {suffixe or '(aucune)'} » non reconnue pour {chemin.name} : attendu "
                       f".csv .tsv .txt .jsonl .ndjson .json (éventuellement .gz), ou --format pour forcer")


def deviner_separateur(chemin: Path, sonde: bytes, encodage: str, impose: str | None, format_impose: str) -> str:
    """Séparateur : imposé, déduit de l'extension, ou deviné par csv.Sniffer sur la sonde."""
    if impose:
        return "\t" if impose in ("\\t", "tab") else impose
    if format_impose == "tsv":
        return "\t"
    suffixes = [s.lower() for s in chemin.suffixes if s.lower() != ".gz"]
    connu = SUFFIXES_CSV.get(suffixes[-1] if suffixes else "", "")
    if connu:
        return connu
    texte = sonde.decode(encodage, errors="replace")
    texte = texte[: texte.rfind("\n") + 1] or texte
    try:
        return csv.Sniffer().sniff(texte, delimiters=SEPARATEURS_CANDIDATS).delimiter
    except csv.Error:
        premiere = texte.splitlines()[0] if texte.splitlines() else ""
        return max(SEPARATEURS_CANDIDATS, key=premiere.count) if premiere else ","


def preparer_entree(spec: str, args: argparse.Namespace) -> Entree:
    """Construit la description de la source après les contrôles d'entrée."""
    encodage = normaliser_encodage(args.encodage)
    if est_texte_json(spec):
        return Entree(nom="(texte JSON en ligne)", genre="json", texte=spec, encodage=encodage)
    chemin = resoudre(spec, args.racine)
    sonde = verifier_fichier(chemin, encodage)
    genre = deviner_genre(chemin, args.format)
    entree = Entree(nom=str(chemin), genre=genre, chemin=chemin, encodage=encodage,
                    max_octets_json=args.max_octets_json)
    if genre == "csv":
        entree.separateur = deviner_separateur(chemin, sonde, encodage, args.separateur, args.format)
    return entree


def nommer_colonnes(entete: list[str]) -> list[str]:
    """Noms de colonnes uniques : vide -> colonne_N, doublon -> nom.1, nom.2 (comme pandas)."""
    noms: list[str] = []
    vus: set[str] = set()
    for rang, brut in enumerate(entete, 1):
        base = brut if brut.strip() else f"colonne_{rang}"
        nom, suite = base, 0
        while nom in vus:
            suite += 1
            nom = f"{base}.{suite}"
        vus.add(nom)
        noms.append(nom)
    return noms


def cellule_csv(valeur: str) -> str | None:
    """Une cellule vide, blanche ou égale à un jeton manquant vaut None."""
    return None if valeur in VALEURS_MANQUANTES or not valeur.strip() else valeur


def valeur_json(valeur: Any) -> Any:
    """Une valeur JSON nulle, chaîne blanche ou flottant non fini vaut None."""
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        return None
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return None
    return valeur


def noter_mal_formee(entree: Entree, numero: int) -> None:
    """Compte une ligne mal formée et garde les premiers numéros."""
    entree.mal_formees += 1
    if len(entree.exemples_mal_formees) < 10:
        entree.exemples_mal_formees.append(numero)


def iterer_csv(entree: Entree) -> Iterator[tuple[int, dict[str, Any]]]:
    """Lignes d'un CSV en flux : (numéro de ligne physique, {colonne: valeur ou None})."""
    assert entree.chemin is not None
    numero = 0
    try:
        with ouvrir_texte(entree.chemin, entree.encodage) as flux:
            lecteur = csv.reader(flux, delimiter=entree.separateur)
            entete = next(lecteur, None)
            if entete is None:
                return
            entree.colonnes = nommer_colonnes(entete)
            largeur = len(entree.colonnes)
            for ligne in lecteur:
                numero = lecteur.line_num
                if not ligne or (len(ligne) == 1 and not ligne[0].strip() and largeur > 1):
                    continue
                if len(ligne) != largeur:
                    noter_mal_formee(entree, numero)
                yield numero, {nom: cellule_csv(v) for nom, v in zip(entree.colonnes, ligne)}
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{entree.nom} : décodage {entree.encodage} impossible vers la ligne {numero + 1} "
                           f"({exc.reason}) ; essayer --encodage latin-1 ou cp1252") from exc
    except csv.Error as exc:
        raise ErreurEntree(f"{entree.nom} : CSV illisible vers la ligne {numero + 1} : {exc}") from exc
    except (OSError, EOFError) as exc:
        raise ErreurEntree(f"{entree.nom} : lecture interrompue après la ligne {numero} : {exc}") from exc


def iterer_jsonl(entree: Entree) -> Iterator[tuple[int, dict[str, Any]]]:
    """Objets d'un JSONL en flux ; une ligne invalide est comptée mal formée, pas fatale."""
    assert entree.chemin is not None
    numero = 0
    try:
        with ouvrir_texte(entree.chemin, entree.encodage) as flux:
            for numero, ligne in enumerate(flux, 1):
                if not ligne.strip():
                    continue
                try:
                    objet = json.loads(ligne)
                except json.JSONDecodeError:
                    noter_mal_formee(entree, numero)
                    continue
                if not isinstance(objet, dict):
                    entree.non_objets += 1
                    continue
                yield numero, {cle: valeur_json(v) for cle, v in objet.items()}
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{entree.nom} : décodage {entree.encodage} impossible vers la ligne {numero + 1} "
                           f"({exc.reason}) ; essayer --encodage latin-1") from exc
    except (OSError, EOFError) as exc:
        raise ErreurEntree(f"{entree.nom} : lecture interrompue vers la ligne {numero + 1} : {exc}") from exc


def charger_json(entree: Entree) -> Any:
    """Document JSON entier (fichier borné en taille, ou texte en ligne) ; repli JSONL."""
    texte = entree.texte if entree.texte is not None else lire_json_borne(entree)
    try:
        return json.loads(texte)
    except json.JSONDecodeError as exc:
        erreur = exc
    lignes = [ligne for ligne in texte.splitlines() if ligne.strip()]
    if len(lignes) > 1:
        try:
            return [json.loads(ligne) for ligne in lignes]
        except json.JSONDecodeError:
            pass
    raise ErreurEntree(f"{entree.nom} : JSON invalide (ligne {erreur.lineno}, colonne {erreur.colno}) : {erreur.msg}")


def lire_json_borne(entree: Entree) -> str:
    """Texte d'un fichier .json, refusé au-delà de --max-octets-json."""
    assert entree.chemin is not None
    try:
        with ouvrir_texte(entree.chemin, entree.encodage) as flux:
            texte = flux.read(entree.max_octets_json + 1)
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{entree.nom} : décodage {entree.encodage} impossible ({exc.reason})") from exc
    except (OSError, EOFError) as exc:
        raise ErreurEntree(f"{entree.nom} : lecture impossible : {exc}") from exc
    if len(texte) > entree.max_octets_json:
        raise ErreurEntree(f"{entree.nom} dépasse --max-octets-json ({entree.max_octets_json}) : "
                           f"le convertir en JSONL pour une lecture en flux")
    return texte


def iterer_json(entree: Entree) -> Iterator[tuple[int, dict[str, Any]]]:
    """Objets d'un document JSON : un objet, ou un tableau d'objets (rang 1, 2...)."""
    document = charger_json(entree)
    elements = document if isinstance(document, list) else [document]
    for rang, objet in enumerate(elements, 1):
        if not isinstance(objet, dict):
            entree.non_objets += 1
            continue
        yield rang, {cle: valeur_json(v) for cle, v in objet.items()}


def iterer(entree: Entree) -> Iterator[tuple[int, dict[str, Any]]]:
    """Enregistrements de la source, quel que soit son format ; ré-appelable."""
    if entree.genre == "csv":
        return iterer_csv(entree)
    if entree.genre == "jsonl":
        return iterer_jsonl(entree)
    return iterer_json(entree)


# --------------------------------------------------------------------------- classement

def cle_de(valeur: Any) -> Any:
    """Clé de comptage : la chaîne elle-même, ou un couple (étiquette, valeur) pour le JSON typé."""
    if isinstance(valeur, str):
        return valeur
    if isinstance(valeur, bool):
        return ("b", valeur)
    if isinstance(valeur, int):
        return ("i", valeur)
    if isinstance(valeur, float):
        return ("f", valeur)
    etiquette = "o" if isinstance(valeur, dict) else "t"
    return (etiquette, json.dumps(valeur, ensure_ascii=False, sort_keys=True, default=str))


def texte_de(cle: Any) -> str:
    """Forme affichable d'une clé de comptage."""
    if isinstance(cle, str):
        return cle
    etiquette, valeur = cle
    if etiquette in ("o", "t"):
        return valeur
    return json.dumps(valeur)


def classer_date_barres(correspondance: re.Match[str]) -> tuple[bool, bool]:
    """(lisible jour/mois/année, lisible mois/jour/année) pour une date à barres obliques."""
    a, b, annee = (int(x) for x in correspondance.groups())
    return date_valide(annee, b, a), date_valide(annee, a, b)


def date_valide(annee: int, mois: int, jour: int) -> bool:
    """Vrai si la date existe au calendrier."""
    try:
        date(annee, mois, jour)
    except ValueError:
        return False
    return True


def classer_chaine(brut: str, virgule: bool) -> tuple[str, float | None]:
    """Classe d'une chaîne (espaces de bord ignorés) et sa valeur numérique éventuelle."""
    texte = brut.strip()
    if MOTIF_ENTIER.fullmatch(texte) or MOTIF_DECIMAL.fullmatch(texte):
        nombre = float(texte)
        if math.isfinite(nombre):
            return ("entier" if MOTIF_ENTIER.fullmatch(texte) else "decimal"), nombre
        return "texte", None
    if virgule and MOTIF_DECIMAL_VIRGULE.fullmatch(texte):
        return "decimal", float(texte.replace(",", "."))
    if texte.lower() in BOOLEENS:
        return "booleen", None
    return classer_temporel(texte), None


def classer_temporel(texte: str) -> str:
    """date, date-heure ou texte (dates ISO 8601 vérifiées au calendrier, ou jj/mm/aaaa)."""
    if MOTIF_DATE.fullmatch(texte):
        return "date" if _iso_valide(date.fromisoformat, texte) else "texte"
    if MOTIF_DATE_HEURE.fullmatch(texte):
        return "date-heure" if _iso_valide(datetime.fromisoformat, texte) else "texte"
    barres = MOTIF_DATE_BARRES.fullmatch(texte)
    if barres and any(classer_date_barres(barres)):
        return "date"
    return "texte"


def _iso_valide(analyseur: Any, texte: str) -> bool:
    """Vrai si l'analyseur ISO de la stdlib accepte le texte."""
    try:
        analyseur(texte)
    except ValueError:
        return False
    return True


def classer(cle: Any, virgule: bool) -> tuple[str, float | None]:
    """Classe et valeur numérique d'une clé de comptage."""
    if isinstance(cle, str):
        return classer_chaine(cle, virgule)
    etiquette, valeur = cle
    if etiquette == "b":
        return "booleen", None
    if etiquette == "i":
        nombre = float(valeur) if abs(valeur) < 1e308 else math.inf
        return ("entier", nombre) if math.isfinite(nombre) else ("texte", None)
    if etiquette == "f":
        return "decimal", valeur
    if etiquette == "o":
        return "objet", None
    return "tableau", None


# --------------------------------------------------------------------------- agrégats

def cumuler_moments(resume: Resume, x: float, poids: int) -> None:
    """Moyenne et somme des carrés des écarts, pondérées (Welford, West 1979)."""
    resume.n_num += poids
    ecart = x - resume.moyenne
    resume.moyenne += ecart * poids / resume.n_num
    resume.m2 += poids * ecart * (x - resume.moyenne)
    resume.minimum = min(resume.minimum, x)
    resume.maximum = max(resume.maximum, x)
    if x == 0:
        resume.zeros += poids
    elif x < 0:
        resume.negatifs += poids


def cumuler_texte(resume: Resume, cle: Any, texte: str, poids: int) -> None:
    """Longueurs et espaces superflus."""
    longueur = len(texte)
    resume.longueur_min = longueur if resume.longueur_min is None else min(resume.longueur_min, longueur)
    resume.longueur_max = max(resume.longueur_max, longueur)
    resume.longueur_somme += longueur * poids
    if isinstance(cle, str) and cle != cle.strip():
        resume.espaces_superflus += poids


def cumuler_date(resume: Resume, texte: str, poids: int) -> None:
    """Dates extrêmes (forme ISO, comparaison lexicale) et sens des dates à barres."""
    propre = texte.strip()
    if len(propre) > 10 and propre[10] == " ":
        propre = propre[:10] + "T" + propre[11:]
    barres = MOTIF_DATE_BARRES.fullmatch(propre)
    if barres:
        jour_premier, mois_premier = classer_date_barres(barres)
        resume.barres_jour_premier += poids if jour_premier and not mois_premier else 0
        resume.barres_mois_premier += poids if mois_premier and not jour_premier else 0
        return
    resume.date_min = propre if resume.date_min is None else min(resume.date_min, propre)
    resume.date_max = propre if resume.date_max is None else max(resume.date_max, propre)


def absorber(resume: Resume, cle: Any, poids: int, virgule: bool) -> float | None:
    """Intègre une valeur (pondérée) au résumé ; rend sa valeur numérique éventuelle."""
    classe, nombre = classer(cle, virgule)
    texte = texte_de(cle)
    resume.classes[classe] += poids
    exemples = resume.exemples.setdefault(classe, [])
    if len(exemples) < EXEMPLES_MAX:
        exemples.append(texte[:80])
    cumuler_texte(resume, cle, texte, poids)
    if nombre is not None:
        cumuler_moments(resume, nombre, poids)
        if classe == "entier" and isinstance(cle, str) and MOTIF_ZERO_INITIAL.fullmatch(cle.strip()):
            resume.zeros_initiaux += poids
    elif classe in ("date", "date-heure"):
        cumuler_date(resume, texte, poids)
    return nombre


def hll_ajouter(registres: bytearray, texte: str) -> None:
    """Ajoute une valeur au croquis HyperLogLog (hachage blake2b sur 64 bits)."""
    x = int.from_bytes(hashlib.blake2b(texte.encode("utf-8", "surrogatepass"), digest_size=8).digest(), "big")
    indice = x >> (64 - PRECISION_HLL)
    reste = (x << PRECISION_HLL) & MASQUE_64
    rang = 64 - reste.bit_length() + 1 if reste else 64 - PRECISION_HLL + 1
    if rang > registres[indice]:
        registres[indice] = rang


def hll_estimer(registres: bytearray) -> float:
    """Estimateur HyperLogLog avec correction de petite portée (comptage linéaire)."""
    m = len(registres)
    alpha = 0.7213 / (1 + 1.079 / m)
    estimation = alpha * m * m / math.fsum(2.0 ** -r for r in registres)
    vides = registres.count(0)
    if estimation <= 2.5 * m and vides:
        return m * math.log(m / vides)
    return estimation


def elaguer_frequences(compteur: Counter, capacite: int) -> tuple[dict[Any, int], int]:
    """Réduit un compteur exact à un résumé de Misra-Gries de `capacite` compteurs."""
    tries = compteur.most_common(capacite + 1)
    seuil = tries[capacite][1] if len(tries) > capacite else 0
    garde = {cle: effectif - seuil for cle, effectif in tries[:capacite] if effectif > seuil}
    return garde, seuil


def mg_ajouter(colonne: Colonne, cle: Any) -> None:
    """Misra-Gries : incrémente, insère, ou décrémente tous les compteurs."""
    frequents = colonne.frequents
    if cle in frequents:
        frequents[cle] += 1
    elif len(frequents) < CAPACITE_MISRA_GRIES:
        frequents[cle] = 1
    else:
        colonne.erreur_frequences += 1
        for autre in list(frequents):
            if frequents[autre] == 1:
                del frequents[autre]
            else:
                frequents[autre] -= 1


def reservoir_ajouter(colonne: Colonne, nombre: float, contexte: Contexte) -> None:
    """Algorithme R (Vitter 1985) : réservoir uniforme des valeurs numériques."""
    colonne.numeriques_vus += 1
    if len(colonne.reservoir) < contexte.taille_reservoir:
        colonne.reservoir.append(nombre)
        return
    j = contexte.alea.randrange(colonne.numeriques_vus)
    if j < contexte.taille_reservoir:
        colonne.reservoir[j] = nombre


def basculer_en_flux(colonne: Colonne, contexte: Contexte) -> None:
    """Passe du compteur exact aux croquis : HLL, Misra-Gries, réservoir amorcé sans biais."""
    assert colonne.compteur is not None
    resume = Resume()
    registres = bytearray(1 << PRECISION_HLL)
    valeurs: list[float] = []
    poids: list[int] = []
    for cle, effectif in colonne.compteur.items():
        nombre = absorber(resume, cle, effectif, contexte.virgule)
        hll_ajouter(registres, texte_de(cle))
        if nombre is not None:
            valeurs.append(nombre)
            poids.append(effectif)
    total = sum(poids)
    if total <= contexte.taille_reservoir:
        colonne.reservoir = [v for v, p in zip(valeurs, poids) for _ in range(p)]
    else:
        colonne.reservoir = contexte.alea.sample(valeurs, contexte.taille_reservoir, counts=poids)
    colonne.numeriques_vus = total
    colonne.frequents, colonne.erreur_frequences = elaguer_frequences(colonne.compteur, CAPACITE_MISRA_GRIES)
    colonne.resume, colonne.registres, colonne.compteur = resume, registres, None


def observer(colonne: Colonne, valeur: Any, contexte: Contexte) -> None:
    """Intègre une cellule à sa colonne."""
    if valeur is None:
        colonne.manquants += 1
        return
    colonne.non_manquants += 1
    cle = cle_de(valeur)
    if colonne.compteur is not None:
        colonne.compteur[cle] += 1
        if len(colonne.compteur) > contexte.max_distincts:
            basculer_en_flux(colonne, contexte)
        return
    assert colonne.resume is not None and colonne.registres is not None
    nombre = absorber(colonne.resume, cle, 1, contexte.virgule)
    hll_ajouter(colonne.registres, texte_de(cle))
    mg_ajouter(colonne, cle)
    if nombre is not None:
        reservoir_ajouter(colonne, nombre, contexte)


def empreinte_ligne(enregistrement: dict[str, Any], genre: str) -> int:
    """Empreinte d'une ligne pour compter les doublons exacts."""
    if genre == "csv":
        return hash(tuple(enregistrement.values()))
    return hash(json.dumps(enregistrement, sort_keys=True, ensure_ascii=False, default=str))


def parcourir(entree: Entree, contexte: Contexte, choisies: set[str] | None,
              max_lignes: int) -> tuple[dict[str, Colonne], Bilan]:
    """Une seule passe sur la source : colonnes, doublons, lignes."""
    colonnes: dict[str, Colonne] = {}
    bilan = Bilan()
    for _numero, enregistrement in iterer(entree):
        if max_lignes and bilan.lignes >= max_lignes:
            bilan.tronque = True
            break
        bilan.lignes += 1
        noter_doublon(bilan, enregistrement, entree.genre)
        for nom, valeur in enregistrement.items():
            if choisies is not None and nom not in choisies:
                continue
            colonne = colonnes.get(nom)
            if colonne is None:
                colonne = colonnes[nom] = Colonne(nom)
            observer(colonne, valeur, contexte)
    return colonnes, bilan


def noter_doublon(bilan: Bilan, enregistrement: dict[str, Any], genre: str) -> None:
    """Compte la ligne comme doublon si son empreinte a déjà été vue (plafond de mémoire)."""
    if len(bilan.empreintes) >= MAX_LIGNES_DOUBLONS:
        bilan.doublons_tronques = True
        return
    empreinte = empreinte_ligne(enregistrement, genre)
    if empreinte in bilan.empreintes:
        bilan.doublons += 1
    else:
        bilan.empreintes.add(empreinte)


# --------------------------------------------------------------------------- profils

def quantile_pondere(paires: Sequence[tuple[float, int]], total: int, q: float) -> float:
    """Quantile de type 7 (interpolation linéaire) sur des valeurs triées et pondérées."""
    position = (total - 1) * q
    bas = math.floor(position)
    fraction = position - bas
    v_bas = valeur_au_rang(paires, bas)
    if fraction == 0:
        return v_bas
    return v_bas + fraction * (valeur_au_rang(paires, bas + 1) - v_bas)


def valeur_au_rang(paires: Sequence[tuple[float, int]], rang: int) -> float:
    """Valeur de rang `rang` (base 0) dans une suite triée pondérée."""
    cumul = 0
    for valeur, poids in paires:
        cumul += poids
        if rang < cumul:
            return valeur
    return paires[-1][0]


def decrire_numerique(resume: Resume, paires: list[tuple[float, int]], exacts: bool,
                      n_colonne: int) -> dict[str, Any]:
    """Statistiques numériques : moments exacts, quantiles et aberrantes (exacts ou sur réservoir)."""
    paires.sort()
    total = sum(p for _, p in paires)
    quantiles = {f"p{round(q * 100):02d}": quantile_pondere(paires, total, q) for q in QUANTILES}
    q1, q3 = quantiles["p25"], quantiles["p75"]
    ecart = q3 - q1
    basse, haute = q1 - FACTEUR_TUKEY * ecart, q3 + FACTEUR_TUKEY * ecart
    hors = [(v, p) for v, p in paires if v < basse or v > haute]
    nombre_hors = sum(p for _, p in hors)
    taux = nombre_hors / total if total else 0.0
    return {
        "n": resume.n_num, "min": resume.minimum, "max": resume.maximum, "moyenne": resume.moyenne,
        "ecart_type": math.sqrt(resume.m2 / (resume.n_num - 1)) if resume.n_num > 1 else None,
        "zeros": resume.zeros, "negatifs": resume.negatifs, "quantiles": quantiles,
        "quantiles_exacts": exacts,
        "aberrantes": {
            "methode": "bornes de Tukey : Q1 - 1,5 IQR et Q3 + 1,5 IQR", "borne_basse": basse,
            "borne_haute": haute, "nombre": nombre_hors if exacts else round(taux * resume.n_num),
            "taux": taux, "exact": exacts,
            "exemples": sorted({v for v, _ in hors[:3] + hors[-3:]}),
        },
        "valeurs_numeriques_sur_colonne": resume.n_num / n_colonne if n_colonne else 0.0,
    }


def choisir_type(resume: Resume, seuil: float) -> tuple[str, float, str]:
    """(type, conformité, type de référence) : le plus conforme, le plus spécifique à égalité ;
    sous le seuil, mixte si une majorité est typée (référence = ce type), sinon texte."""
    total = sum(resume.classes.values())
    if not total:
        return "vide", 1.0, "vide"
    meilleur, conformite = "texte", 0.0
    for candidat in TYPES_PAR_SPECIFICITE:
        part = sum(resume.classes[c] for c in CONFORMES[candidat]) / total
        if part > conformite:
            meilleur, conformite = candidat, part
    if conformite >= seuil:
        return meilleur, conformite, meilleur
    if conformite >= 0.5:
        return "mixte", conformite, meilleur
    return "texte", 1.0, "texte"


def finaliser_exact(colonne: Colonne, contexte: Contexte) -> tuple[Resume, list[tuple[float, int]], int, list[dict[str, Any]]]:
    """Résumé, paires numériques, distinctes et fréquentes d'une colonne restée exacte."""
    assert colonne.compteur is not None
    resume = Resume()
    paires: list[tuple[float, int]] = []
    for cle, effectif in colonne.compteur.items():
        nombre = absorber(resume, cle, effectif, contexte.virgule)
        if nombre is not None:
            paires.append((nombre, effectif))
    frequentes = [{"valeur": texte_de(cle)[:120], "effectif": effectif,
                   "part": effectif / colonne.non_manquants}
                  for cle, effectif in colonne.compteur.most_common(contexte.top)]
    return resume, paires, len(colonne.compteur), frequentes


def finaliser_flux(colonne: Colonne, contexte: Contexte) -> tuple[Resume, list[tuple[float, int]], int, list[dict[str, Any]]]:
    """Résumé, réservoir, estimation HLL et fréquences Misra-Gries d'une colonne basculée.

    Seules sont publiées les valeurs dont l'effectif garanti dépasse l'incertitude : les autres
    compteurs de Misra-Gries ne distinguent pas une valeur fréquente du bruit."""
    assert colonne.resume is not None and colonne.registres is not None
    paires = [(v, 1) for v in colonne.reservoir]
    erreur = colonne.erreur_frequences
    tries = sorted(colonne.frequents.items(), key=lambda item: -item[1])[: contexte.top]
    frequentes = [{"valeur": texte_de(cle)[:120], "effectif_min": effectif, "effectif_max": effectif + erreur,
                   "part_min": effectif / colonne.non_manquants} for cle, effectif in tries if effectif > erreur]
    distinctes = min(round(hll_estimer(colonne.registres)), colonne.non_manquants)
    return colonne.resume, paires, distinctes, frequentes


def profiler_colonne(colonne: Colonne, lignes: int, contexte: Contexte) -> dict[str, Any]:
    """Profil complet d'une colonne."""
    exacte = colonne.compteur is not None
    resume, paires, distinctes, frequentes = (finaliser_exact if exacte else finaliser_flux)(colonne, contexte)
    manquants = lignes - colonne.non_manquants
    type_infere, conformite, reference = choisir_type(resume, contexte.seuil_type)
    profil: dict[str, Any] = {
        "nom": colonne.nom, "type": type_infere, "conformite": conformite,
        "repartition_classes": dict(resume.classes.most_common()),
        "non_manquants": colonne.non_manquants, "manquants": manquants,
        "taux_manquants": manquants / lignes if lignes else 0.0,
        "distinctes": distinctes,
        "distinctes_methode": "exacte" if exacte else "HyperLogLog p=14 (erreur type 0,81 %)",
        "ratio_distinctes": distinctes / colonne.non_manquants if colonne.non_manquants else 0.0,
        "plus_frequentes": frequentes,
        "frequences_methode": "exacte" if exacte else (
            f"Misra-Gries {CAPACITE_MISRA_GRIES} compteurs : une valeur plus fréquente que "
            f"{colonne.erreur_frequences} occurrences y figure ; en deçà, rien n'est publié"),
        "type_majoritaire": reference,
        "non_conformes": non_conformes(resume, reference),
        "texte": decrire_texte(resume, colonne.non_manquants),
    }
    if paires and type_infere in ("entier", "decimal", "mixte"):
        exacts = exacte or len(paires) >= resume.n_num
        profil["numerique"] = decrire_numerique(resume, paires, exacts, colonne.non_manquants)
    if type_infere in ("date", "date-heure"):
        profil["dates"] = decrire_dates(resume)
    profil["constante"] = distinctes == 1
    profil["quasi_identifiant"] = est_quasi_identifiant(profil)
    return profil


def non_conformes(resume: Resume, type_infere: str) -> dict[str, Any]:
    """Valeurs qui ne relèvent pas du type retenu (nombre et exemples)."""
    if type_infere not in CONFORMES:
        return {"nombre": 0, "exemples": []}
    autres = [c for c in resume.classes if c not in CONFORMES[type_infere] and resume.classes[c]]
    exemples = [e for c in autres for e in resume.exemples.get(c, [])][:EXEMPLES_MAX]
    return {"nombre": sum(resume.classes[c] for c in autres), "exemples": exemples}


def decrire_texte(resume: Resume, n: int) -> dict[str, Any]:
    """Longueurs des valeurs (forme texte) et espaces superflus."""
    return {"longueur_min": resume.longueur_min, "longueur_max": resume.longueur_max,
            "longueur_moyenne": resume.longueur_somme / n if n else None,
            "espaces_superflus": resume.espaces_superflus, "zeros_initiaux": resume.zeros_initiaux}


def decrire_dates(resume: Resume) -> dict[str, Any]:
    """Dates extrêmes ISO et sens des dates à barres obliques."""
    if resume.barres_jour_premier and resume.barres_mois_premier:
        sens = "incohérent (jj/mm et mm/jj mêlés)"
    elif resume.barres_jour_premier:
        sens = "jj/mm/aaaa"
    elif resume.barres_mois_premier:
        sens = "mm/jj/aaaa"
    else:
        sens = "ambigu ou absent"
    return {"min_iso": resume.date_min, "max_iso": resume.date_max, "dates_a_barres": sens}


def est_quasi_identifiant(profil: dict[str, Any]) -> bool:
    """Texte ou entier, presque toutes distinctes, sur assez de valeurs pour en juger."""
    return (profil["type"] in TYPES_IDENTIFIANTS and profil["non_manquants"] >= MIN_VALEURS_IDENTIFIANT
            and profil["ratio_distinctes"] >= SEUIL_IDENTIFIANT)


def alertes_colonne(profil: dict[str, Any]) -> list[dict[str, Any]]:
    """Défauts d'une colonne qui justifient le code 1."""
    nom, alertes = profil["nom"], []
    if profil["type"] == "vide":
        alertes.append({"colonne": nom, "alerte": "colonne_vide", "detail": "aucune valeur renseignée"})
    elif profil["constante"]:
        valeur = profil["plus_frequentes"][0]["valeur"] if profil["plus_frequentes"] else ""
        alertes.append({"colonne": nom, "alerte": "colonne_constante", "detail": f"une seule valeur : {valeur!r}"})
    if profil["type"] == "mixte":
        alertes.append({"colonne": nom, "alerte": "type_mixte", "detail": profil["repartition_classes"]})
    elif profil["non_conformes"]["nombre"]:
        alertes.append({"colonne": nom, "alerte": "valeurs_non_conformes",
                        "detail": f"{profil['non_conformes']['nombre']} valeur(s) hors du type {profil['type']}, "
                                  f"ex. {profil['non_conformes']['exemples']}"})
    if profil["type"] == "entier" and profil["texte"]["zeros_initiaux"]:
        alertes.append({"colonne": nom, "alerte": "zeros_initiaux",
                        "detail": f"{profil['texte']['zeros_initiaux']} valeur(s) comme 007 : un code, pas un "
                                  f"nombre ; une lecture numérique perd le zéro"})
    return alertes


def observations_colonne(profil: dict[str, Any]) -> list[dict[str, Any]]:
    """Faits utiles qui ne sont pas des défauts (code inchangé)."""
    nom, notes = profil["nom"], []
    if profil["quasi_identifiant"]:
        notes.append({"colonne": nom, "observation": "quasi_identifiant",
                      "detail": f"{profil['ratio_distinctes']:.1%} de valeurs distinctes : identifiant probable, "
                                f"à exclure des variables d'un modèle"})
    aberrantes = profil.get("numerique", {}).get("aberrantes")
    if aberrantes and aberrantes["nombre"]:
        notes.append({"colonne": nom, "observation": "valeurs_aberrantes",
                      "detail": f"{aberrantes['nombre']} hors de [{aberrantes['borne_basse']:.6g} ; "
                                f"{aberrantes['borne_haute']:.6g}]" + ("" if aberrantes["exact"] else " (estimé)")})
    if profil["texte"]["espaces_superflus"]:
        notes.append({"colonne": nom, "observation": "espaces_superflus",
                      "detail": f"{profil['texte']['espaces_superflus']} valeur(s) avec espaces de bord"})
    return notes


def alertes_fichier(entree: Entree, bilan: Bilan) -> list[dict[str, Any]]:
    """Défauts au niveau du fichier."""
    alertes = []
    if bilan.doublons:
        alertes.append({"colonne": None, "alerte": "lignes_dupliquees",
                        "detail": f"{bilan.doublons} ligne(s) identique(s) à une ligne précédente"})
    if entree.mal_formees:
        alertes.append({"colonne": None, "alerte": "lignes_mal_formees",
                        "detail": f"{entree.mal_formees} ligne(s) au nombre de champs ou au JSON invalide, "
                                  f"ex. lignes {entree.exemples_mal_formees}"})
    if entree.non_objets:
        alertes.append({"colonne": None, "alerte": "elements_non_objets",
                        "detail": f"{entree.non_objets} élément(s) JSON qui ne sont pas des objets, ignorés"})
    return alertes


# --------------------------------------------------------------------------- comparaison

def charger_bibliotheque(nom: str) -> Any:
    """Importe pandas ou polars s'ils sont installés, sinon None."""
    try:
        if nom == "polars":
            import polars as module
        else:
            import pandas as module
    except ImportError:
        return None
    return module


def choisir_moteur(demande: str) -> tuple[str, Any]:
    """Moteur de contre-vérification : polars, puis pandas, sinon stdlib (une ligne sur stderr)."""
    if demande == "stdlib":
        return "stdlib", None
    for nom in (("polars", "pandas") if demande == "auto" else (demande,)):
        module = charger_bibliotheque(nom)
        if module is not None:
            return nom, module
    manquants = "ni polars ni pandas n'est installé" if demande == "auto" else f"{demande} n'est pas installé"
    print(f"{NOM_OUTIL} : {manquants} — moteur stdlib seul, profil non contre-vérifié", file=sys.stderr)
    return "stdlib", None


def enregistrements_textes(entree: Entree, colonnes: list[str], max_lignes: int) -> list[dict[str, str | None]]:
    """Enregistrements JSON mis en texte (même forme que le profil) pour la contre-vérification."""
    lignes = []
    for _numero, enregistrement in iterer(entree):
        if max_lignes and len(lignes) >= max_lignes:
            break
        lignes.append({nom: None if enregistrement.get(nom) is None else texte_de(cle_de(enregistrement[nom]))
                       for nom in colonnes})
    return lignes


def table_pandas(module: Any, entree: Entree, colonnes: list[str], max_lignes: int) -> Any:
    """DataFrame pandas tout en texte, lu par pandas lui-même pour un CSV."""
    if entree.genre == "csv" and entree.chemin is not None:
        return module.read_csv(entree.chemin, sep=entree.separateur, dtype=str, keep_default_na=False,
                               na_filter=False, encoding=entree.encodage, on_bad_lines="skip",
                               nrows=max_lignes or None, skip_blank_lines=True)
    return module.DataFrame.from_records(enregistrements_textes(entree, colonnes, max_lignes), columns=colonnes)


def mesures_pandas(module: Any, table: Any, nom: str, numerique: bool, virgule: bool) -> dict[str, Any]:
    """Manquants, distinctes et statistiques numériques d'une colonne, calculés par pandas."""
    serie = table[nom]
    manque = serie.isna() | serie.isin(list(VALEURS_MANQUANTES)) | (serie.astype(str).str.strip() == "")
    presentes = serie[~manque]
    mesures: dict[str, Any] = {"lignes": len(serie), "manquants": int(manque.sum()),
                               "distinctes": int(presentes.nunique())}
    if numerique:
        textes = presentes.astype(str).str.strip()
        nombres = module.to_numeric(textes.str.replace(",", ".") if virgule else textes, errors="coerce").dropna()
        if len(nombres):
            mesures |= {"min": float(nombres.min()), "max": float(nombres.max()), "moyenne": float(nombres.mean())}
    return mesures


def table_polars(module: Any, entree: Entree, colonnes: list[str], max_lignes: int) -> Any:
    """DataFrame polars tout en texte, lu par polars lui-même pour un CSV non compressé en utf-8."""
    if entree.genre == "csv" and entree.chemin is not None and entree.chemin.suffix.lower() != ".gz" \
            and entree.encodage == "utf-8-sig":
        return module.read_csv(entree.chemin, separator=entree.separateur, infer_schema=False,
                               n_rows=max_lignes or None, truncate_ragged_lines=True)
    lignes = enregistrements_textes(entree, colonnes, max_lignes)
    return module.DataFrame(lignes, schema={nom: module.String for nom in colonnes})


def mesures_polars(module: Any, table: Any, nom: str, numerique: bool, virgule: bool) -> dict[str, Any]:
    """Manquants, distinctes et statistiques numériques d'une colonne, calculés par polars."""
    serie = table.get_column(nom)
    manque = serie.is_null() | serie.is_in(list(VALEURS_MANQUANTES)) | (serie.str.strip_chars() == "")
    presentes = serie.filter(~manque.fill_null(True))
    mesures: dict[str, Any] = {"lignes": table.height, "manquants": int(manque.fill_null(True).sum()),
                               "distinctes": presentes.n_unique()}
    if numerique:
        textes = presentes.str.strip_chars()
        if virgule:
            textes = textes.str.replace(",", ".", literal=True)
        nombres = textes.cast(module.Float64, strict=False).drop_nulls()
        if nombres.len():
            mesures |= {"min": float(nombres.min()), "max": float(nombres.max()), "moyenne": float(nombres.mean())}
    return mesures


def comparer_mesure(nom: str, cle: str, notre: Any, leur: Any) -> dict[str, Any] | None:
    """Écart entre notre mesure et celle de la bibliothèque, ou None si concordant."""
    if notre is None or leur is None:
        return None
    if isinstance(notre, float) or isinstance(leur, float):
        if abs(notre - leur) <= TOLERANCE_RELATIVE * max(abs(notre), abs(leur), 1e-300):
            return None
    elif notre == leur:
        return None
    return {"colonne": nom, "mesure": cle, "stdlib": notre, "bibliotheque": leur}


def confronter_colonne(profil: dict[str, Any], leurs: dict[str, Any], lignes: int,
                       ecarts: list[dict[str, Any]], estimations: list[dict[str, Any]]) -> int:
    """Confronte nos mesures d'une colonne aux leurs ; les estimations HLL vont à part."""
    nos = nos_mesures(profil, lignes)
    for cle, leur in leurs.items():
        if cle == "distinctes" and profil["distinctes_methode"] != "exacte":
            erreur = abs(nos[cle] - leur) / leur if leur else 0.0
            estimations.append({"colonne": profil["nom"], "estimation_hll": nos[cle], "exact_bibliotheque": leur,
                                "erreur_relative": erreur})
            if erreur > 3 * ERREUR_TYPE_HLL:
                ecarts.append({"colonne": profil["nom"], "mesure": cle, "stdlib": nos[cle], "bibliotheque": leur})
            continue
        ecart = comparer_mesure(profil["nom"], cle, nos.get(cle), leur)
        if ecart:
            ecarts.append(ecart)
    return len(leurs)


def comparer(moteur: str, module: Any, entree: Entree, profils: list[dict[str, Any]], lignes: int,
             args: argparse.Namespace) -> dict[str, Any]:
    """Contre-vérification par pandas ou polars : lignes, manquants, distinctes, min, max, moyenne."""
    if entree.chemin is not None and entree.chemin.stat().st_size > args.max_octets_comparaison:
        return {"statut": "non faite", "raison": f"fichier au-delà de --max-octets-comparaison ({args.max_octets_comparaison})"}
    noms = [p["nom"] for p in profils]
    lire, mesurer = (table_polars, mesures_polars) if moteur == "polars" else (table_pandas, mesures_pandas)
    ecarts: list[dict[str, Any]] = []
    estimations: list[dict[str, Any]] = []
    comparees = 0
    try:
        table = lire(module, entree, noms, args.max_lignes)
        for profil in profils:
            if profil["nom"] not in table.columns:
                ecarts.append({"colonne": profil["nom"], "mesure": "presence", "stdlib": True, "bibliotheque": False})
                continue
            leurs = mesurer(module, table, profil["nom"], "numerique" in profil, args.virgule_decimale)
            comparees += confronter_colonne(profil, leurs, lignes, ecarts, estimations)
    except erreurs_bibliotheque(module) as exc:
        return {"statut": "échec", "raison": f"{type(exc).__name__} : {exc}"}
    return {"statut": "faite", "mesures_comparees": comparees, "ecarts": ecarts,
            "estimations_hll": estimations, "concordance": not ecarts}


def erreurs_bibliotheque(module: Any) -> tuple[type[BaseException], ...]:
    """Exceptions attendues d'une lecture par la bibliothèque."""
    propres = []
    for espace in (getattr(module, "exceptions", None), getattr(module, "errors", None)):
        for nom in ("PolarsError", "ParserError", "EmptyDataError"):
            classe = getattr(espace, nom, None)
            if isinstance(classe, type) and issubclass(classe, BaseException):
                propres.append(classe)
    return (ValueError, TypeError, KeyError, OSError, MemoryError, *propres)


def nos_mesures(profil: dict[str, Any], lignes: int) -> dict[str, Any]:
    """Nos valeurs pour les mesures contre-vérifiées."""
    mesures: dict[str, Any] = {"lignes": lignes, "manquants": profil["manquants"], "distinctes": profil["distinctes"]}
    numerique = profil.get("numerique")
    if numerique:
        mesures |= {"min": numerique["min"], "max": numerique["max"], "moyenne": numerique["moyenne"]}
    return mesures


# --------------------------------------------------------------------------- rapport

def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans le docstring."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def construire_parseur() -> argparse.ArgumentParser:
    """Déclare l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Profile un jeu de données (CSV, TSV, JSONL, JSON ou texte JSON en ligne) colonne par "
                    "colonne : type inféré, manquants, distinctes, fréquences, quantiles, aberrantes (IQR), "
                    "colonnes constantes et quasi identifiantes. Code 1 si une alerte est levée.",
        epilog="exemple : profiler_donnees.py ventes.csv --json   |   profiler_donnees.py journal.jsonl.gz "
               "--colonnes statut,duree --echantillon 50000 --graine 7",
    )
    parseur.add_argument("source", metavar="FICHIER",
                         help="fichier .csv .tsv .txt .jsonl .ndjson .json (éventuellement .gz), ou texte JSON "
                              "en ligne commençant par [ ou {")
    parseur.add_argument("--format", choices=("auto", "csv", "tsv", "jsonl", "json"), default="auto",
                         help="format imposé (défaut : d'après l'extension)")
    parseur.add_argument("--separateur", default=None, help="séparateur CSV imposé (défaut : deviné ; tab pour tabulation)")
    parseur.add_argument("--encodage", default="utf-8", help="encodage du fichier (défaut utf-8, BOM toléré)")
    parseur.add_argument("--colonnes", default=None, help="ne profiler que ces colonnes (noms séparés par des virgules)")
    parseur.add_argument("--max-lignes", type=int, default=0, help="arrêter après N lignes (défaut 0 : tout lire)")
    parseur.add_argument("--max-distincts", type=int, default=50_000,
                         help="valeurs distinctes suivies exactement par colonne avant bascule en croquis (défaut 50000)")
    parseur.add_argument("--echantillon", type=int, default=100_000,
                         help="taille du réservoir de valeurs numériques après bascule (défaut 100000)")
    parseur.add_argument("--graine", type=int, default=0, help="graine du réservoir (défaut 0)")
    parseur.add_argument("--seuil-type", type=float, default=0.95,
                         help="part minimale de valeurs conformes pour retenir un type (défaut 0.95)")
    parseur.add_argument("--top", type=int, default=5, help="valeurs les plus fréquentes publiées (défaut 5)")
    parseur.add_argument("--virgule-decimale", action="store_true", help="3,14 est un nombre décimal")
    parseur.add_argument("--max-octets-json", type=int, default=OCTETS_MAX_JSON,
                         help="taille maximale d'un .json chargé entier (défaut 200 Mio)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "polars", "pandas"), default="auto",
                         help="auto : contre-vérifie avec polars ou pandas s'ils sont installés")
    parseur.add_argument("--max-octets-comparaison", type=int, default=OCTETS_MAX_COMPARAISON,
                         help="pas de contre-vérification au-delà de cette taille de fichier (défaut 256 Mio)")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse les paramètres hors domaine."""
    if args.max_distincts < 1 or args.echantillon < 10 or args.top < 0 or args.max_lignes < 0:
        raise ErreurEntree("--max-distincts >= 1, --echantillon >= 10, --top >= 0, --max-lignes >= 0")
    if not 0.5 <= args.seuil_type <= 1.0:
        raise ErreurEntree("--seuil-type doit être entre 0.5 et 1")
    if args.separateur is not None and args.separateur not in ("\\t", "tab") and len(args.separateur) != 1:
        raise ErreurEntree("--separateur doit être un seul caractère (ou tab)")


def nettoyer_non_finis(objet: Any) -> Any:
    """Remplace récursivement NaN et infinis par None : le JSON strict les interdit."""
    if isinstance(objet, float) and not math.isfinite(objet):
        return None
    if isinstance(objet, dict):
        return {cle: nettoyer_non_finis(valeur) for cle, valeur in objet.items()}
    if isinstance(objet, list):
        return [nettoyer_non_finis(valeur) for valeur in objet]
    return objet


def presenter_json(rapport: dict[str, Any]) -> None:
    """Écrit l'objet JSON unique sur stdout."""
    print(json.dumps(nettoyer_non_finis(rapport), ensure_ascii=False, indent=2, allow_nan=False))


def format_court(valeur: Any) -> str:
    """Nombre court pour le tableau humain."""
    if valeur is None:
        return "-"
    if isinstance(valeur, float):
        return f"{valeur:.4g}"
    return str(valeur)


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Tableau lisible : une ligne par colonne, puis alertes et observations."""
    source = rapport["source"]
    print(f"{source['nom']} : {rapport['lignes']} lignes, {rapport['denominateur']} colonnes "
          f"({source['format']}" + (f", séparateur {source['separateur']!r}" if source["format"] == "csv" else "") + ")")
    print(f"{'colonne':<24} {'type':<11} {'manq.':>7} {'distinctes':>10} {'min':>10} {'médiane':>10} {'max':>10} {'aberr.':>7}")
    for profil in rapport["colonnes"]:
        num = profil.get("numerique") or {}
        aberr = (num.get("aberrantes") or {}).get("nombre")
        distinctes = f"{'~' if profil['distinctes_methode'] != 'exacte' else ''}{profil['distinctes']}"
        print(f"{profil['nom'][:24]:<24} {profil['type']:<11} {profil['taux_manquants']:>7.1%} {distinctes:>10} "
              f"{format_court(num.get('min')):>10} {format_court((num.get('quantiles') or {}).get('p50')):>10} "
              f"{format_court(num.get('max')):>10} {format_court(aberr):>7}")
    for alerte in rapport["alertes"]:
        print(f"ALERTE {alerte['alerte']} [{alerte['colonne'] or 'fichier'}] : {alerte['detail']}")
    for note in rapport["observations"]:
        print(f"note {note['observation']} [{note['colonne']}] : {note['detail']}")
    comparaison = rapport.get("comparaison")
    if comparaison:
        print(f"contre-vérification {rapport['moteur']} : {comparaison['statut']}"
              + (f", {len(comparaison['ecarts'])} écart(s) sur {comparaison['mesures_comparees']} mesures"
                 if comparaison["statut"] == "faite" else f" ({comparaison.get('raison')})"))


def base_rapport(examines: list[str], moteur: str, module: Any) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    return {"outil": NOM_OUTIL, "moteur": moteur, "version_moteur": getattr(module, "__version__", None),
            "denominateur": len(examines), "unite_denominateur": "colonnes",
            "examines": examines[:MAX_EXAMINES], "examines_tronques": len(examines) > MAX_EXAMINES,
            "contrat": extraire_contrat(__doc__ or "")}


def analyser(args: argparse.Namespace, moteur: str, module: Any) -> dict[str, Any]:
    """Lit, profile, compare ; rend le rapport complet."""
    entree = preparer_entree(args.source, args)
    contexte = Contexte(args.max_distincts, args.echantillon, args.virgule_decimale, args.seuil_type,
                        args.top, random.Random(args.graine))
    choisies = {c.strip() for c in args.colonnes.split(",") if c.strip()} if args.colonnes else None
    colonnes, bilan = parcourir(entree, contexte, choisies, args.max_lignes)
    if choisies is not None and (inconnues := sorted(choisies - set(colonnes))):
        raise ErreurEntree(f"colonne(s) inconnue(s) : {inconnues} ; disponibles : {entree.colonnes or sorted(colonnes)}")
    if not bilan.lignes or not colonnes:
        raise ErreurEntree(f"dénominateur nul : {entree.nom} ne contient aucune ligne de données, rien à examiner",
                           CODE_RIEN)
    profils = [profiler_colonne(c, bilan.lignes, contexte) for c in colonnes.values()]
    alertes = alertes_fichier(entree, bilan) + [a for p in profils for a in alertes_colonne(p)]
    rapport = base_rapport([p["nom"] for p in profils], moteur, module) | {
        "source": {"nom": entree.nom, "format": entree.genre, "separateur": entree.separateur if entree.genre == "csv" else None,
                   "encodage": entree.encodage, "lignes_mal_formees": entree.mal_formees,
                   "elements_non_objets": entree.non_objets, "lecture_tronquee": bilan.tronque},
        "lignes": bilan.lignes, "lignes_dupliquees": bilan.doublons,
        "doublons_comptes_sur": min(bilan.lignes, MAX_LIGNES_DOUBLONS),
        "echantillonnage": {"max_distincts_exacts": args.max_distincts, "taille_reservoir": args.echantillon,
                            "graine": args.graine,
                            "colonnes_basculees": [p["nom"] for p in profils if p["distinctes_methode"] != "exacte"]},
        "colonnes": profils, "alertes": alertes,
        "observations": [n for p in profils for n in observations_colonne(p)],
    }
    rapport["comparaison"] = comparer(moteur, module, entree, profils, bilan.lignes, args) if module else None
    return rapport


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, refuse ou profile, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    moteur, module = choisir_moteur(args.moteur)
    try:
        verifier_parametres(args)
        rapport = analyser(args, moteur, module)
    except ErreurEntree as exc:
        print(f"{NOM_OUTIL} : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport([], moteur, module) | {"refus": str(exc)})
        return exc.code
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_ALERTE if rapport["alertes"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
