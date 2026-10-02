"""Détecter, colonne par colonne, si les données de production ont dérivé de la référence d'apprentissage.

Un test seul crie à la dérive dès que les données sont nombreuses : mesuré dans cette session
sur deux tirages normaux de 200 000 valeurs (écart-type 1, moyennes 0 et 0,03), le test de
Kolmogorov-Smirnov rend p = 4,2e-17 alors que le PSI vaut 0,001 ; l'outil exige les deux et
déclare la colonne stable. Ses p-valeurs KS exactes égalent celles de scipy 1.18.1 à 1,4e-16 près (5000 contre 3176 valeurs).

QUESTION
    La distribution de ces données a-t-elle dérivé entre la référence et la production ?
MESURE
    Deux fichiers (csv, tsv, jsonl, json, ou textes JSON en ligne) lus en flux ; colonnes
    communes comparées une à une, colonnes disparues ou nouvelles rapportées. Nature d'une
    colonne : numérique si au moins 95 % des valeurs non manquantes sont des nombres des deux
    côtés et plus de 20 valeurs distinctes ; discrète (nombres peu nombreux) ou catégorielle
    sinon ; un passage de numérique à non numérique est un changement de type. Numérique :
    test de Kolmogorov-Smirnov à deux échantillons, statistique D exacte ; p-valeur exacte par
    comptage des chemins du treillis (entiers exacts, Hodges 1958) quand la bande à parcourir
    compte au plus --ks-exact-max cases, sinon asymptotique par la loi de Kolmogorov
    Q(racine(nm/(n+m)) D) ; distance de Wasserstein ; PSI
    sur les déciles de la référence. Discrète ou catégorielle : khi-deux d'homogénéité 2 × K
    (catégories d'effectif attendu < 5 regroupées, règle de Cochran 1954) avec le V de Cramér,
    PSI sur les mêmes catégories, catégories nouvelles et disparues avec leur part. Pour toutes :
    taux de manquants comparé (khi-deux 2 × 2). Les p-valeurs de toutes les colonnes sont
    corrigées (Holm, par défaut). Une colonne dérive si son test est significatif après
    correction ET son PSI >= 0,1, ou si son taux de manquants change significativement d'au
    moins 5 points, ou si son type change ; dérive forte si PSI >= 0,25. Seuils PSI 0,1 et 0,25 :
    usage du crédit, publiés par Siddiqi (2006, Credit Risk Scorecards, Wiley) ; alpha 0,05 :
    convention. Colonnes classées par gravité puis PSI décroissant. Au-delà de --echantillon
    valeurs par côté, tests et PSI portent sur un réservoir uniforme (algorithme R, --graine).
    Si scipy est installé, ks_2samp, chi2_contingency et wasserstein_distance recalculent les
    mêmes quantités et l'écart est publié. Code 1 si une colonne dérive ou disparaît.
HYPOTHÈSES
    Les lignes de chaque fichier sont des tirages indépendants de leur population ; la
    référence représente les données d'apprentissage ; une même colonne a le même sens des
    deux côtés ; pour le test KS, la loi est continue (sans ex aequo).
LIMITES
    Univarié : une dérive de la loi jointe (corrélations) à marges inchangées échappe, de même
    qu'une dérive du lien entre variables et cible (dérive de concept). Le test KS suppose une
    loi continue : avec des ex aequo il devient conservateur. En asymptotique (bande trop
    large), la p-valeur suit la loi limite de Kolmogorov et non la loi exacte de scipy : écart
    mesuré jusqu'à 0,0038 sur 5000 contre 4000 valeurs. Le PSI dépend du découpage (déciles de
    la référence) et du plancher --epsilon ; une catégorie rare est fondue dans (autres) pour
    le khi-deux et le PSI, mais reste listée parmi les nouvelles. Une colonne de plus de
    --max-categories valeurs distinctes non numérique n'est pas testée. La correction de Holm
    réduit la puissance quand les colonnes sont nombreuses. Au-delà de --echantillon valeurs
    par côté, tests et PSI portent sur un réservoir, pas sur toutes les lignes.
CONTRE-EXEMPLES
    Constaté : x et y corrélés à 0,958 dans la référence et indépendants (corrélation 0,001)
    dans la production, marges identiques par construction (y permuté), 3000 lignes de chaque
    côté : les deux colonnes sont déclarées stables (PSI 0,006 et 0,002), code 0. La dérive
    est réelle, mais jointe.
INVOCATION
    {outil} '[{"x": 1}, {"x": 2}, {"x": 3}, {"x": 4}, {"x": 5}, {"x": 6}]' '[{"x": 11}, {"x": 12}, {"x": 13}, {"x": 14}, {"x": 15}, {"x": 16}]' --json
DOMAINE
    Surveillance de modèles en production : lot d'apprentissage contre lot récent d'inférence,
    d'un même schéma tabulaire, de quelques dizaines à quelques millions de lignes par côté.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import math
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
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
CODE_DERIVE = 1
CODE_USAGE = 2
CODE_RIEN = 3

NOM_OUTIL = "detecter_derive_donnees"
VALEURS_MANQUANTES = frozenset({
    "", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN",
    "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"})
SEPARATEURS_CANDIDATS = ",;\t|"
OCTETS_SONDE = 65536
SUFFIXES_CSV = MappingProxyType({".csv": "", ".tsv": "\t", ".tab": "\t", ".txt": "", ".dat": "", ".psv": "|"})
SUFFIXES_JSONL = frozenset({".jsonl", ".ndjson", ".jsonlines"})
SUFFIXES_JSON = frozenset({".json"})
OCTETS_MAX_JSON = 200 * 1024 * 1024
MAX_EXAMINES = 200
MOTIF_ENTIER = re.compile(r"[+-]?\d+")
MOTIF_DECIMAL = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
MOTIF_DECIMAL_VIRGULE = re.compile(r"[+-]?\d+,\d+")
SEUIL_NUMERIQUE = 0.95
DISTINCTES_MAX_DISCRETE = 20
EFFECTIF_MIN = 5
ATTENDU_MIN_COCHRAN = 5.0
AUTRES = "(autres)"
EPSILON_GAMMA = 1e-15
MINUSCULE = 1e-300
ITERATIONS_MAX = 100_000
SOURCES_SEUILS = MappingProxyType({
    "psi": "0,1 (dérive modérée) et 0,25 (dérive forte) : Siddiqi, Credit Risk Scorecards, Wiley, 2006",
    "alpha": "0,05 : convention ; correction de Holm (1979) sur toutes les colonnes",
    "cochran": "effectif attendu >= 5 par case : Cochran, Biometrics, 1954 ; les catégories plus rares sont regroupées",
    "manquants": "écart de 5 points de taux de manquants : choix de l'outil, sans source, réglable",
    "discrete": "20 valeurs distinctes au plus : choix de l'outil, la colonne est alors traitée en catégories",
})
RANG_STATUT = MappingProxyType({"dérive forte": 0, "dérive": 1, "stable": 2, "non testée": 3})


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
class Cote:
    """Ce qu'on retient d'une colonne d'un côté (référence ou production)."""

    non_manquants: int = 0
    numeriques: int = 0
    reservoir: list[float] = field(default_factory=list)
    vus: int = 0
    categories: Counter | None = field(default_factory=Counter)


@dataclass
class Jeu:
    """Un fichier lu : lignes, colonnes, et côté par colonne."""

    entree: Entree
    lignes: int = 0
    colonnes: dict[str, Cote] = field(default_factory=dict)


@dataclass
class Reglages:
    """Paramètres de lecture et d'échantillonnage passés partout."""

    taille_reservoir: int
    max_categories: int
    virgule: bool
    alea: random.Random
    choisies: frozenset[str] | None
    ignorees: frozenset[str]


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


# --------------------------------------------------------------------------- collecte

def categorie_et_nombre(valeur: Any, virgule: bool) -> tuple[str, float | None]:
    """Forme canonique d'une valeur (catégorie) et sa valeur numérique éventuelle."""
    if isinstance(valeur, bool):
        return ("true" if valeur else "false"), None
    if isinstance(valeur, (int, float)):
        return canonique_nombre(valeur, str(valeur))
    if isinstance(valeur, (dict, list)):
        return json.dumps(valeur, ensure_ascii=False, sort_keys=True), None
    texte = str(valeur).strip()
    if MOTIF_ENTIER.fullmatch(texte) or MOTIF_DECIMAL.fullmatch(texte):
        return canonique_nombre(texte, texte)
    if virgule and MOTIF_DECIMAL_VIRGULE.fullmatch(texte):
        return canonique_nombre(texte.replace(",", "."), texte)
    return texte, None


def canonique_nombre(valeur: Any, repli: str) -> tuple[str, float | None]:
    """Nombre fini et sa forme canonique (1, 1.0 et 1e0 se confondent) ; sinon texte."""
    try:
        nombre = float(valeur)
    except OverflowError:
        return repli, None
    if not math.isfinite(nombre):
        return repli, None
    return format(nombre, ".15g"), nombre


def ajouter_reservoir(cote: Cote, nombre: float, reglages: Reglages) -> None:
    """Algorithme R (Vitter 1985) : réservoir uniforme des valeurs numériques."""
    cote.vus += 1
    if len(cote.reservoir) < reglages.taille_reservoir:
        cote.reservoir.append(nombre)
        return
    j = reglages.alea.randrange(cote.vus)
    if j < reglages.taille_reservoir:
        cote.reservoir[j] = nombre


def observer(cote: Cote, valeur: Any, reglages: Reglages) -> None:
    """Intègre une cellule non manquante."""
    cote.non_manquants += 1
    categorie, nombre = categorie_et_nombre(valeur, reglages.virgule)
    if nombre is not None:
        cote.numeriques += 1
        ajouter_reservoir(cote, nombre, reglages)
    if cote.categories is not None:
        cote.categories[categorie] += 1
        if len(cote.categories) > reglages.max_categories:
            cote.categories = None


def lire_jeu(entree: Entree, reglages: Reglages) -> Jeu:
    """Une passe sur un fichier : par colonne, effectifs, réservoir numérique, catégories."""
    jeu = Jeu(entree)
    for _numero, enregistrement in iterer(entree):
        jeu.lignes += 1
        for nom, valeur in enregistrement.items():
            if nom in reglages.ignorees or (reglages.choisies is not None and nom not in reglages.choisies):
                continue
            cote = jeu.colonnes.get(nom)
            if cote is None:
                cote = jeu.colonnes[nom] = Cote()
            if valeur is not None:
                observer(cote, valeur, reglages)
    return jeu


# --------------------------------------------------------------------------- lois

def survie_kolmogorov(lam: float) -> float:
    """P(K > lam) pour la loi de Kolmogorov (deux développements selon lam)."""
    if lam <= 0:
        return 1.0
    if lam < 1.18:
        y = math.exp(-math.pi * math.pi / (8 * lam * lam))
        somme, k = 0.0, 1
        while True:
            terme = y ** ((2 * k - 1) ** 2)
            somme += terme
            if terme < 1e-17 * somme or k > 100:
                break
            k += 1
        return min(1.0, max(0.0, 1.0 - math.sqrt(2 * math.pi) / lam * somme))
    somme = 0.0
    for k in range(1, 101):
        terme = math.exp(-2.0 * k * k * lam * lam)
        somme += terme if k % 2 else -terme
        if terme < 1e-17:
            break
    return min(1.0, max(0.0, 2.0 * somme))


def p_ks_exacte(n: int, m: int, d_num: int) -> float:
    """P(D >= d) sous H0, par comptage des chemins du treillis restant dans |i·m - j·n| < d (Hodges 1958).

    Calcul en entiers exacts ; d_num = D·n·m est entier."""
    if d_num <= 0:
        return 1.0
    precedent = [0] * (m + 1)
    for i in range(n + 1):
        courant = [0] * (m + 1)
        j_min = max(0, (i * m - d_num) // n + 1)
        j_max = min(m, -((-(i * m + d_num)) // n) - 1)
        for j in range(j_min, j_max + 1):
            if i == 0 and j == 0:
                courant[0] = 1
                continue
            courant[j] = precedent[j] + (courant[j - 1] if j > 0 else 0)
        precedent = courant
    total = math.comb(n + m, n)
    return (total - precedent[m]) / total


def statistique_ks(a: Sequence[float], b: Sequence[float]) -> int:
    """Numérateur entier de D = max |F_a - F_b| ; D = d_num / (n·m). Ex aequo traités par paliers."""
    n, m = len(a), len(b)
    i = j = d_num = 0
    while i < n and j < m:
        x = a[i] if a[i] <= b[j] else b[j]
        while i < n and a[i] == x:
            i += 1
        while j < m and b[j] == x:
            j += 1
        d_num = max(d_num, abs(i * m - j * n))
    return d_num


def wasserstein(a: Sequence[float], b: Sequence[float]) -> float:
    """Distance de Wasserstein d'ordre 1 entre lois empiriques : intégrale de |F_a - F_b|."""
    n, m = len(a), len(b)
    i = j = 0
    aire, precedent = 0.0, None
    while i < n or j < m:
        x = min(a[i] if i < n else math.inf, b[j] if j < m else math.inf)
        if precedent is not None:
            aire += abs(i / n - j / m) * (x - precedent)
        while i < n and a[i] == x:
            i += 1
        while j < m and b[j] == x:
            j += 1
        precedent = x
    return aire


def log_gamma_inf(a: float, x: float) -> float:
    """log(x^a e^-x / Gamma(a)), facteur commun des gammas incomplètes."""
    return a * math.log(x) - x - math.lgamma(a)


def gamma_inf_serie(a: float, x: float) -> float:
    """P(a, x) par la série (x < a + 1)."""
    terme = somme = 1.0 / a
    ap = a
    for _ in range(ITERATIONS_MAX):
        ap += 1.0
        terme *= x / ap
        somme += terme
        if abs(terme) < abs(somme) * EPSILON_GAMMA:
            break
    return somme * math.exp(log_gamma_inf(a, x))


def gamma_sup_fraction(a: float, x: float) -> float:
    """Q(a, x) par fraction continue de Lentz (x >= a + 1)."""
    b = x + 1.0 - a
    c = 1.0 / MINUSCULE
    d = 1.0 / b
    h = d
    for i in range(1, ITERATIONS_MAX):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = MINUSCULE if abs(d) < MINUSCULE else d
        c = b + an / c
        c = MINUSCULE if abs(c) < MINUSCULE else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPSILON_GAMMA:
            break
    return math.exp(log_gamma_inf(a, x)) * h


def survie_khi_deux(statistique: float, ddl: int) -> float:
    """P(khi-deux(ddl) > statistique) = Q(ddl/2, statistique/2)."""
    if statistique <= 0:
        return 1.0
    a, x = ddl / 2.0, statistique / 2.0
    if x < a + 1.0:
        return max(0.0, 1.0 - gamma_inf_serie(a, x))
    return gamma_sup_fraction(a, x)


def khi_deux(table: Sequence[Sequence[int]]) -> tuple[float, int, float]:
    """Khi-deux d'homogénéité sans correction de continuité : (statistique, ddl, p)."""
    lignes = [sum(rangee) for rangee in table]
    colonnes = [sum(c) for c in zip(*table)]
    total = sum(lignes)
    statistique = 0.0
    for r, rangee in enumerate(table):
        for c, observe in enumerate(rangee):
            attendu = lignes[r] * colonnes[c] / total
            statistique += (observe - attendu) ** 2 / attendu
    ddl = (len(lignes) - 1) * (len(colonnes) - 1)
    return statistique, ddl, survie_khi_deux(statistique, ddl)


def psi(parts_ref: Sequence[float], parts_prod: Sequence[float], epsilon: float) -> float:
    """Population Stability Index : somme de (p - q) ln(p / q), parts nulles remplacées par epsilon."""
    total = 0.0
    for q, p in zip(parts_ref, parts_prod):
        q, p = max(q, epsilon), max(p, epsilon)
        total += (p - q) * math.log(p / q)
    return total


def quantile_type7(tries: Sequence[float], q: float) -> float:
    """Quantile par interpolation linéaire (type 7)."""
    position = (len(tries) - 1) * q
    bas = math.floor(position)
    haut = min(bas + 1, len(tries) - 1)
    return tries[bas] + (position - bas) * (tries[haut] - tries[bas])


def holm(p_valeurs: Sequence[float]) -> list[float]:
    """p-valeurs ajustées de Holm (1979), dans l'ordre d'entrée."""
    ordre = sorted(range(len(p_valeurs)), key=lambda k: p_valeurs[k])
    ajustees = [1.0] * len(p_valeurs)
    courant = 0.0
    for rang, k in enumerate(ordre):
        courant = max(courant, min(1.0, (len(p_valeurs) - rang) * p_valeurs[k]))
        ajustees[k] = courant
    return ajustees


def ajuster(p_valeurs: Sequence[float], methode: str) -> list[float]:
    """Correction pour comparaisons multiples : holm, bonferroni ou aucune."""
    if methode == "holm":
        return holm(p_valeurs)
    if methode == "bonferroni":
        return [min(1.0, p * len(p_valeurs)) for p in p_valeurs]
    return list(p_valeurs)


# --------------------------------------------------------------------------- tests par colonne

def test_numerique(ref: Cote, prod: Cote, args: argparse.Namespace) -> dict[str, Any]:
    """KS, Wasserstein et PSI sur déciles de la référence."""
    a, b = sorted(ref.reservoir), sorted(prod.reservoir)
    n, m = len(a), len(b)
    d_num = statistique_ks(a, b)
    exacte = (n + 1) * min(m + 1, 2 * d_num // n + 1) <= args.ks_exact_max
    p = p_ks_exacte(n, m, d_num) if exacte else survie_kolmogorov(math.sqrt(n * m / (n + m)) * d_num / (n * m))
    bornes = sorted({quantile_type7(a, k / args.classes) for k in range(1, args.classes)})
    effectifs_ref, effectifs_prod = [0] * (len(bornes) + 1), [0] * (len(bornes) + 1)
    for valeurs, effectifs in ((a, effectifs_ref), (b, effectifs_prod)):
        for x in valeurs:
            effectifs[bisect.bisect_left(bornes, x)] += 1
    valeur_psi = psi([e / n for e in effectifs_ref], [e / m for e in effectifs_prod], args.epsilon)
    moyenne_ref = math.fsum(a) / n
    ecart_type = math.sqrt(math.fsum((x - moyenne_ref) ** 2 for x in a) / (n - 1)) if n > 1 else 0.0
    distance = wasserstein(a, b)
    return {
        "test": "Kolmogorov-Smirnov deux échantillons", "statistique": d_num / (n * m), "p": p,
        "methode_p": "exacte (chemins du treillis)" if exacte else "asymptotique (loi de Kolmogorov)",
        "n_reference": n, "n_production": m, "echantillonne": ref.vus > n or prod.vus > m,
        "psi": valeur_psi, "classes_psi": len(bornes) + 1,
        "wasserstein": distance, "wasserstein_en_ecarts_types": distance / ecart_type if ecart_type else None,
        "moyennes": [moyenne_ref, math.fsum(b) / m], "medianes": [quantile_type7(a, 0.5), quantile_type7(b, 0.5)],
        "_echantillons": (a, b),
    }


def regrouper_rares(ref: Counter, prod: Counter) -> tuple[list[str], list[int], list[int]]:
    """Catégories triées par effectif, les rares (attendu < 5 d'un côté) fondues dans (autres)."""
    total_ref, total_prod = sum(ref.values()), sum(prod.values())
    total = total_ref + total_prod
    noms, eff_ref, eff_prod = [], [], []
    autres_ref = autres_prod = 0
    for categorie in sorted(set(ref) | set(prod), key=lambda c: (-(ref[c] + prod[c]), c)):
        colonne = ref[categorie] + prod[categorie]
        if min(total_ref, total_prod) * colonne / total < ATTENDU_MIN_COCHRAN:
            autres_ref += ref[categorie]
            autres_prod += prod[categorie]
            continue
        noms.append(categorie)
        eff_ref.append(ref[categorie])
        eff_prod.append(prod[categorie])
    if autres_ref + autres_prod:
        noms.append(AUTRES)
        eff_ref.append(autres_ref)
        eff_prod.append(autres_prod)
    return noms, eff_ref, eff_prod


def test_categoriel(ref: Cote, prod: Cote, args: argparse.Namespace) -> dict[str, Any]:
    """Khi-deux d'homogénéité, V de Cramér, PSI, catégories nouvelles et disparues."""
    assert ref.categories is not None and prod.categories is not None
    noms, eff_ref, eff_prod = regrouper_rares(ref.categories, prod.categories)
    n, m = sum(eff_ref), sum(eff_prod)
    if len(noms) < 2:
        statistique, ddl, p = 0.0, 0, 1.0
    else:
        statistique, ddl, p = khi_deux([eff_ref, eff_prod])
    nouvelles = sorted((c for c in prod.categories if c not in ref.categories), key=lambda c: -prod.categories[c])
    disparues = sorted((c for c in ref.categories if c not in prod.categories), key=lambda c: -ref.categories[c])
    return {
        "test": "khi-deux d'homogénéité", "statistique": statistique, "ddl": ddl, "p": p,
        "methode_p": "loi du khi-deux (gamma incomplète)" if ddl else "sans objet (une seule catégorie)",
        "v_cramer": math.sqrt(statistique / (n + m)) if n + m else 0.0,
        "n_reference": n, "n_production": m, "categories_testees": len(noms),
        "psi": psi([e / n for e in eff_ref], [e / m for e in eff_prod], args.epsilon), "classes_psi": len(noms),
        "nouvelles_categories": [{"valeur": c[:80], "part_production": prod.categories[c] / m} for c in nouvelles[:10]],
        "nouvelles_categories_total": len(nouvelles),
        "part_production_nouvelles": sum(prod.categories[c] for c in nouvelles) / m if m else 0.0,
        "categories_disparues": [{"valeur": c[:80], "part_reference": ref.categories[c] / n} for c in disparues[:10]],
        "categories_disparues_total": len(disparues),
        "_table": [eff_ref, eff_prod],
    }


def test_manquants(ref: Cote, prod: Cote, lignes_ref: int, lignes_prod: int) -> dict[str, Any]:
    """Comparaison des taux de manquants (khi-deux 2 × 2 sans correction)."""
    manq_ref, manq_prod = lignes_ref - ref.non_manquants, lignes_prod - prod.non_manquants
    table = [[manq_ref, ref.non_manquants], [manq_prod, prod.non_manquants]]
    taux_ref, taux_prod = manq_ref / lignes_ref, manq_prod / lignes_prod
    if not (manq_ref + manq_prod) or not (ref.non_manquants + prod.non_manquants):
        return {"taux_reference": taux_ref, "taux_production": taux_prod, "ecart": taux_prod - taux_ref,
                "p": 1.0, "methode_p": "sans objet (aucun manquant ou aucune valeur)", "_table": None}
    statistique, _ddl, p = khi_deux(table)
    return {"taux_reference": taux_ref, "taux_production": taux_prod, "ecart": taux_prod - taux_ref,
            "statistique": statistique, "p": p, "methode_p": "khi-deux 2 × 2", "_table": table}


def nature_colonne(ref: Cote, prod: Cote, nom: str, args: argparse.Namespace) -> tuple[str, bool]:
    """(numerique | discrete | categorielle, changement de type)."""
    part_ref = ref.numeriques / ref.non_manquants if ref.non_manquants else 0.0
    part_prod = prod.numeriques / prod.non_manquants if prod.non_manquants else 0.0
    numerique_ref, numerique_prod = part_ref >= SEUIL_NUMERIQUE, part_prod >= SEUIL_NUMERIQUE
    changement = numerique_ref != numerique_prod and ref.non_manquants > 0 and prod.non_manquants > 0
    if nom in args.categorielles_forcees or not (numerique_ref and numerique_prod):
        return "categorielle", changement
    if ref.categories is not None and prod.categories is not None \
            and len(set(ref.categories) | set(prod.categories)) <= DISTINCTES_MAX_DISCRETE:
        return "discrete", changement
    return "numerique", changement


def tester_colonne(nom: str, ref: Cote, prod: Cote, jeux: tuple[Jeu, Jeu], args: argparse.Namespace) -> dict[str, Any]:
    """Tous les tests d'une colonne commune (décision après correction globale)."""
    nature, changement = nature_colonne(ref, prod, nom, args)
    resultat: dict[str, Any] = {"nom": nom, "nature": nature, "changement_type": changement,
                                "part_numerique": [ref.numeriques / ref.non_manquants if ref.non_manquants else None,
                                                   prod.numeriques / prod.non_manquants if prod.non_manquants else None],
                                "manquants": test_manquants(ref, prod, jeux[0].lignes, jeux[1].lignes)}
    if toutes_distinctes(ref) and toutes_distinctes(prod):
        resultat["avertissement"] = ("valeurs toutes distinctes des deux côtés : identifiant ou horodatage probable, "
                                     "dont la « dérive » est attendue ; --ignorer pour l'exclure")
    if min(ref.non_manquants, prod.non_manquants) < EFFECTIF_MIN:
        resultat["raison_non_testee"] = f"moins de {EFFECTIF_MIN} valeurs renseignées d'un côté"
    elif nature == "numerique":
        resultat["distribution"] = test_numerique(ref, prod, args)
    elif ref.categories is None or prod.categories is None:
        resultat["raison_non_testee"] = f"plus de {args.max_categories} catégories distinctes : identifiant probable"
    else:
        resultat["distribution"] = test_categoriel(ref, prod, args)
    return resultat


def toutes_distinctes(cote: Cote) -> bool:
    """Vrai si chaque valeur renseignée est unique (sur au moins 20 valeurs)."""
    return cote.categories is not None and cote.non_manquants >= 20 and len(cote.categories) == cote.non_manquants


def decider(resultats: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Correction des p-valeurs sur l'ensemble des tests, puis statut et gravité de chaque colonne."""
    familles = [(r, "distribution") for r in resultats if "distribution" in r] + \
               [(r, "manquants") for r in resultats if r["manquants"]["_table"] is not None]
    ajustees = ajuster([r[cle]["p"] for r, cle in familles], args.correction)
    for (resultat, cle), p_ajustee in zip(familles, ajustees):
        resultat[cle]["p_ajustee"] = p_ajustee
    for resultat in resultats:
        resultat["statut"], resultat["motifs"] = statut_colonne(resultat, args)
        resultat["niveau_psi"] = niveau_psi(resultat.get("distribution", {}).get("psi"), args)


def statut_colonne(resultat: dict[str, Any], args: argparse.Namespace) -> tuple[str, list[str]]:
    """Statut (dérive forte, dérive, stable, non testée) et ses motifs."""
    motifs = []
    distribution, manquants = resultat.get("distribution"), resultat["manquants"]
    if resultat["changement_type"]:
        motifs.append("changement de type numérique / non numérique")
    if distribution and distribution.get("p_ajustee", 1.0) < args.alpha and distribution["psi"] >= args.seuil_psi:
        motifs.append(f"distribution : p ajustée {distribution['p_ajustee']:.3g} < {args.alpha} et PSI "
                      f"{distribution['psi']:.3f} >= {args.seuil_psi}")
    if manquants.get("p_ajustee", 1.0) < args.alpha and abs(manquants["ecart"]) >= args.seuil_manquants:
        motifs.append(f"manquants : {manquants['taux_reference']:.1%} -> {manquants['taux_production']:.1%}")
    if not motifs:
        return ("non testée" if "raison_non_testee" in resultat else "stable"), motifs
    fort = (distribution is not None and distribution["psi"] >= args.seuil_psi_fort) or resultat["changement_type"] \
        or abs(manquants["ecart"]) >= args.seuil_psi_fort
    return ("dérive forte" if fort else "dérive"), motifs


def niveau_psi(valeur: float | None, args: argparse.Namespace) -> str | None:
    """Lecture du PSI selon les seuils usuels."""
    if valeur is None:
        return None
    if valeur >= args.seuil_psi_fort:
        return "forte"
    return "modérée" if valeur >= args.seuil_psi else "faible"


# --------------------------------------------------------------------------- scipy

def charger_scipy() -> Any:
    """scipy.stats s'il est installé, sinon None."""
    try:
        import scipy.stats as stats
    except ImportError:
        return None
    return stats


def version_scipy() -> str | None:
    """Version de scipy installée."""
    try:
        import scipy
    except ImportError:
        return None
    return scipy.__version__


def comparer_scipy(stats: Any, resultats: list[dict[str, Any]]) -> dict[str, Any]:
    """Recalcule p-valeurs KS, khi-deux et Wasserstein par scipy ; publie les écarts."""
    lignes: list[dict[str, Any]] = []
    for resultat in resultats:
        distribution = resultat.get("distribution")
        if distribution and "_echantillons" in distribution:
            a, b = distribution["_echantillons"]
            ks = stats.ks_2samp(a, b)
            lignes.append({"colonne": resultat["nom"], "mesure": "p KS", "stdlib": distribution["p"],
                           "scipy": float(ks.pvalue), "methode_stdlib": distribution["methode_p"]})
            lignes.append({"colonne": resultat["nom"], "mesure": "D KS", "stdlib": distribution["statistique"],
                           "scipy": float(ks.statistic)})
            lignes.append({"colonne": resultat["nom"], "mesure": "Wasserstein", "stdlib": distribution["wasserstein"],
                           "scipy": float(stats.wasserstein_distance(a, b))})
        elif distribution and distribution["ddl"]:
            khi = stats.chi2_contingency(distribution["_table"], correction=False)
            lignes.append({"colonne": resultat["nom"], "mesure": "p khi-deux", "stdlib": distribution["p"],
                           "scipy": float(khi.pvalue)})
        table = resultat["manquants"]["_table"]
        if table is not None:
            khi = stats.chi2_contingency(table, correction=False)
            lignes.append({"colonne": resultat["nom"], "mesure": "p manquants", "stdlib": resultat["manquants"]["p"],
                           "scipy": float(khi.pvalue)})
    for ligne in lignes:
        ligne["ecart_absolu"] = abs(ligne["stdlib"] - ligne["scipy"])
    return {"version": version_scipy(), "mesures": lignes,
            "ecart_absolu_max": max((ligne["ecart_absolu"] for ligne in lignes), default=0.0)}


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
        description="Compare la distribution de chaque colonne entre un fichier de référence et un fichier de "
                    "production : Kolmogorov-Smirnov, khi-deux, PSI, manquants, catégories nouvelles ; "
                    "classe les colonnes par dérive. Code 1 si une colonne dérive.",
        epilog="exemple : detecter_derive_donnees.py apprentissage.csv production.jsonl --ignorer id --json   |   "
               "detecter_derive_donnees.py ref.csv prod.csv --alpha 0.01 --seuil-psi 0.2",
    )
    parseur.add_argument("reference", metavar="FICHIER_REFERENCE",
                         help="données de référence (.csv .tsv .jsonl .json, .gz possible) ou texte JSON en ligne")
    parseur.add_argument("production", metavar="FICHIER_PRODUCTION", help="données de production, même format possible")
    parseur.add_argument("--format", choices=("auto", "csv", "tsv", "jsonl", "json"), default="auto",
                         help="format imposé aux deux fichiers (défaut : d'après l'extension)")
    parseur.add_argument("--separateur", default=None, help="séparateur CSV imposé (défaut : deviné ; tab pour tabulation)")
    parseur.add_argument("--encodage", default="utf-8", help="encodage des fichiers (défaut utf-8, BOM toléré)")
    parseur.add_argument("--virgule-decimale", action="store_true", help="3,14 est un nombre décimal")
    parseur.add_argument("--colonnes", default=None, help="ne comparer que ces colonnes (séparées par des virgules)")
    parseur.add_argument("--ignorer", default="", help="colonnes à ne pas comparer (identifiants, horodatages)")
    parseur.add_argument("--categorielles", default="", help="colonnes numériques à traiter en catégories")
    parseur.add_argument("--alpha", type=float, default=0.05, help="seuil de signification après correction (défaut 0.05)")
    parseur.add_argument("--correction", choices=("holm", "bonferroni", "aucune"), default="holm",
                         help="correction pour comparaisons multiples (défaut holm)")
    parseur.add_argument("--seuil-psi", type=float, default=0.1, help="PSI de dérive (défaut 0.1, Siddiqi 2006)")
    parseur.add_argument("--seuil-psi-fort", type=float, default=0.25, help="PSI de dérive forte (défaut 0.25)")
    parseur.add_argument("--seuil-manquants", type=float, default=0.05,
                         help="écart absolu de taux de manquants qui compte (défaut 0.05)")
    parseur.add_argument("--classes", type=int, default=10, help="classes du PSI numérique, quantiles de la référence (défaut 10)")
    parseur.add_argument("--epsilon", type=float, default=1e-4, help="part plancher d'une classe vide dans le PSI (défaut 1e-4)")
    parseur.add_argument("--echantillon", type=int, default=100_000, help="valeurs numériques gardées par colonne et par côté (défaut 100000)")
    parseur.add_argument("--graine", type=int, default=0, help="graine du réservoir (défaut 0)")
    parseur.add_argument("--ks-exact-max", type=int, default=2_000_000,
                         help="p-valeur KS exacte si le treillis à parcourir (n × largeur de bande) compte au plus "
                              "ce nombre de cases, asymptotique sinon (défaut 2000000)")
    parseur.add_argument("--max-categories", type=int, default=10_000,
                         help="au-delà, la colonne n'est pas testée en catégories (défaut 10000)")
    parseur.add_argument("--max-octets-json", type=int, default=OCTETS_MAX_JSON, help="taille maximale d'un .json chargé entier")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : recalcule les p-valeurs avec scipy s'il est installé")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse les paramètres hors domaine et prépare les listes de colonnes."""
    if not 0.0 < args.alpha < 1.0 or args.seuil_psi < 0 or args.seuil_psi_fort < args.seuil_psi:
        raise ErreurEntree("--alpha dans ]0;1[, et 0 <= --seuil-psi <= --seuil-psi-fort")
    if args.classes < 2 or args.echantillon < 10 or not 0 < args.epsilon < 0.1 or args.max_categories < 2:
        raise ErreurEntree("--classes >= 2, --echantillon >= 10, 0 < --epsilon < 0.1, --max-categories >= 2")
    if args.separateur is not None and args.separateur not in ("\\t", "tab") and len(args.separateur) != 1:
        raise ErreurEntree("--separateur doit être un seul caractère (ou tab)")
    args.categorielles_forcees = liste_noms(args.categorielles)


def liste_noms(texte: str | None) -> frozenset[str]:
    """Noms de colonnes séparés par des virgules."""
    return frozenset(nom.strip() for nom in (texte or "").split(",") if nom.strip())


def nettoyer(objet: Any) -> Any:
    """Retire les champs internes (_...) et remplace NaN et infinis par None."""
    if isinstance(objet, float) and not math.isfinite(objet):
        return None
    if isinstance(objet, dict):
        return {cle: nettoyer(valeur) for cle, valeur in objet.items() if not str(cle).startswith("_")}
    if isinstance(objet, (list, tuple)):
        return [nettoyer(valeur) for valeur in objet]
    return objet


def presenter_json(rapport: dict[str, Any]) -> None:
    """Écrit l'objet JSON unique sur stdout."""
    print(json.dumps(nettoyer(rapport), ensure_ascii=False, indent=2, allow_nan=False))


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Tableau classé par gravité, puis schéma et contre-vérification."""
    print(f"référence : {rapport['reference']['nom']} ({rapport['reference']['lignes']} lignes) ; "
          f"production : {rapport['production']['nom']} ({rapport['production']['lignes']} lignes)")
    print(f"{'rang':>4} {'colonne':<24} {'nature':<12} {'statut':<13} {'PSI':>7} {'test':>10} {'p ajustée':>10} {'Δ manq.':>9}")
    for rang, colonne in enumerate(rapport["colonnes"], 1):
        d = colonne.get("distribution") or {}
        stat = "-" if not d else f"{'D' if 'ddl' not in d else 'χ²'}={d['statistique']:.3g}"
        psi_texte = "-" if not d else f"{d['psi']:.3f}"
        p_texte = "-" if not d else f"{d.get('p_ajustee', d['p']):.3g}"
        print(f"{rang:>4} {colonne['nom'][:24]:<24} {colonne['nature']:<12} {colonne['statut']:<13} {psi_texte:>7} "
              f"{stat:>10} {p_texte:>10} {colonne['manquants']['ecart'] * 100:>+6.1f} pt")
        for motif in colonne["motifs"]:
            print(f"       {motif}")
        if colonne.get("avertissement"):
            print(f"       attention : {colonne['avertissement']}")
        if d.get("nouvelles_categories_total"):
            print(f"       {d['nouvelles_categories_total']} catégorie(s) nouvelle(s), "
                  f"{d['part_production_nouvelles']:.1%} de la production, ex. {[c['valeur'] for c in d['nouvelles_categories'][:3]]}")
    schema = rapport["schema"]
    if schema["colonnes_disparues"] or schema["colonnes_nouvelles"]:
        print(f"schéma : disparues {schema['colonnes_disparues']} ; nouvelles {schema['colonnes_nouvelles']}")
    print(f"bilan : {rapport['resume']}")
    if rapport.get("comparaison_scipy"):
        print(f"contre-vérification scipy {rapport['comparaison_scipy']['version']} : écart absolu max des mesures "
              f"{rapport['comparaison_scipy']['ecart_absolu_max']:.3g}")


def base_rapport(examines: list[str], moteur: str) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    return {"outil": NOM_OUTIL, "moteur": moteur, "version_moteur": version_scipy() if moteur == "scipy" else None,
            "denominateur": len(examines), "unite_denominateur": "colonnes testées",
            "examines": examines[:MAX_EXAMINES], "examines_tronques": len(examines) > MAX_EXAMINES,
            "contrat": extraire_contrat(__doc__ or "")}


def decrire_jeu(jeu: Jeu) -> dict[str, Any]:
    """Description d'un fichier lu."""
    entree = jeu.entree
    return {"nom": entree.nom, "format": entree.genre, "lignes": jeu.lignes, "colonnes": len(jeu.colonnes),
            "lignes_mal_formees": entree.mal_formees, "elements_non_objets": entree.non_objets}


def analyser(args: argparse.Namespace, stats: Any) -> dict[str, Any]:
    """Lit les deux fichiers, teste chaque colonne commune, décide, classe."""
    reglages = Reglages(args.echantillon, args.max_categories, args.virgule_decimale, random.Random(args.graine),
                        liste_noms(args.colonnes) or None, liste_noms(args.ignorer))
    jeux = (lire_jeu(preparer_entree(args.reference, args), reglages),
            lire_jeu(preparer_entree(args.production, args), reglages))
    for jeu in jeux:
        if not jeu.lignes:
            raise ErreurEntree(f"dénominateur nul : {jeu.entree.nom} ne contient aucune ligne, rien à examiner", CODE_RIEN)
    if reglages.choisies and (inconnues := sorted(reglages.choisies - set(jeux[0].colonnes) - set(jeux[1].colonnes))):
        raise ErreurEntree(f"colonne(s) inconnue(s) dans --colonnes : {inconnues}")
    communes = [nom for nom in jeux[0].colonnes if nom in jeux[1].colonnes]
    resultats = [tester_colonne(nom, jeux[0].colonnes[nom], jeux[1].colonnes[nom], jeux, args) for nom in communes]
    decider(resultats, args)
    resultats.sort(key=lambda r: (RANG_STATUT[r["statut"]], -(r.get("distribution") or {}).get("psi", -1.0)))
    testees = [r["nom"] for r in resultats if "distribution" in r]
    if not testees:
        raise ErreurEntree(f"dénominateur nul : aucune colonne commune testable sur {len(communes)} commune(s) "
                           f"(effectifs < {EFFECTIF_MIN} ou cardinalité trop forte), rien à examiner", CODE_RIEN)
    disparues = [nom for nom in jeux[0].colonnes if nom not in jeux[1].colonnes]
    rapport = base_rapport(testees, "scipy" if stats else "stdlib") | {
        "reference": decrire_jeu(jeux[0]), "production": decrire_jeu(jeux[1]),
        "parametres": {cle: getattr(args, cle) for cle in ("alpha", "correction", "seuil_psi", "seuil_psi_fort",
                                                            "seuil_manquants", "classes", "epsilon", "echantillon",
                                                            "graine", "ks_exact_max")},
        "sources_des_seuils": dict(SOURCES_SEUILS),
        "schema": {"colonnes_disparues": disparues,
                   "colonnes_nouvelles": [nom for nom in jeux[1].colonnes if nom not in jeux[0].colonnes]},
        "colonnes": resultats,
        "resume": dict(Counter(r["statut"] for r in resultats)) | {"colonnes_disparues": len(disparues)},
    }
    rapport["comparaison_scipy"] = comparer_scipy(stats, resultats) if stats else None
    return rapport


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, refuse ou compare, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    stats = charger_scipy() if args.moteur == "auto" else None
    if args.moteur == "auto" and stats is None:
        print(f"{NOM_OUTIL} : scipy absent — moteur stdlib seul, p-valeurs non contre-vérifiées", file=sys.stderr)
    moteur = "scipy" if stats else "stdlib"
    try:
        verifier_parametres(args)
        rapport = analyser(args, stats)
    except ErreurEntree as exc:
        print(f"{NOM_OUTIL} : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport([], moteur) | {"refus": str(exc)})
        return exc.code
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    derive = any(r["statut"] in ("dérive", "dérive forte") for r in rapport["colonnes"])
    return CODE_DERIVE if derive or rapport["schema"]["colonnes_disparues"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
