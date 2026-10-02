"""Découper un jeu de données en apprentissage, validation et test sans fuite, ou auditer un découpage existant.

FAIT_MESURE_A_REMPLIR

QUESTION
    Comment découper ce jeu en apprentissage/validation/test sans fuite ni déséquilibre ?
MESURE
    Lecture d'un fichier csv, tsv, jsonl, json (ou texte JSON en ligne). Chaque ligne reçoit
    deux empreintes sur ses colonnes (moins --ignorer) : exacte (valeurs telles que lues, jetons
    manquants confondus) et normalisée (Unicode NFKC, casse repliée, espaces réduits, nombres
    canoniques : 1, 1.0 et 01 se confondent). Les lignes d'un même groupe (--grouper) et les
    lignes de même empreinte normalisée sont réunies en unités (union-find) : une unité ne
    franchit jamais une frontière de partition. Les unités sont mélangées par strate
    (--stratifier : strate majoritaire de l'unité) avec random.Random(--graine), puis versées
    une à une dans la partition la plus en retard sur sa cible (proportion demandée × lignes de
    la strate) : même graine, même découpage. Publié : lignes, unités et proportions obtenues
    contre demandées, répartition de chaque strate, strates absentes d'une partition, puis
    contrôle de fuite rejoué sur le résultat (empreintes exactes et normalisées présentes dans
    deux partitions, groupes partagés). Fichiers écrits seulement avec --sortie DOSSIER, une
    seconde passe recopiant chaque ligne d'origine dans sa partition. Avec plusieurs fichiers,
    pas de découpage : audit des fuites entre ces partitions existantes. Si scikit-learn est
    installé et --stratifier donné, train_test_split stratifié est rejoué et l'écart maximal
    des parts de strates des deux méthodes est publié. Code 1 si fuite, partition vide ou
    proportion hors --tolerance.
HYPOTHÈSES
    Deux lignes de même empreinte normalisée décrivent le même exemple ; la colonne de groupe
    identifie l'entité (patient, client, session) dont les lignes sont corrélées ; les lignes
    sont échangeables à l'intérieur d'une strate (pas d'ordre temporel à respecter).
LIMITES
    LIMITES_A_REMPLIR
CONTRE-EXEMPLES
    CONTRE_EXEMPLE_A_REMPLIR
INVOCATION
    {outil} '[{"id": 1, "y": "a"}, {"id": 2, "y": "a"}, {"id": 3, "y": "a"}, {"id": 4, "y": "a"}, {"id": 5, "y": "a"}, {"id": 6, "y": "b"}, {"id": 7, "y": "b"}, {"id": 8, "y": "b"}, {"id": 9, "y": "b"}, {"id": 10, "y": "b"}]' --proportions 0.6,0.2,0.2 --stratifier y --json
DOMAINE
    Préparation de jeux tabulaires pour l'apprentissage supervisé (classification, régression)
    où les exemples sont indépendants à l'intérieur des groupes déclarés ; pas pour les séries
    temporelles, qui exigent un découpage chronologique.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
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
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

NOM_OUTIL = "diviser_jeu_donnees"
VALEURS_MANQUANTES = frozenset({
    "", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN",
    "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"})
SEPARATEURS_CANDIDATS = ",;\t|"
OCTETS_SONDE = 65536
SUFFIXES_CSV = MappingProxyType({".csv": "", ".tsv": "\t", ".tab": "\t", ".txt": "", ".dat": "", ".psv": "|"})
SUFFIXES_JSONL = frozenset({".jsonl", ".ndjson", ".jsonlines"})
SUFFIXES_JSON = frozenset({".json"})
OCTETS_MAX_JSON = 200 * 1024 * 1024
MAX_EXAMINES = 50
MAX_EXEMPLES = 10
NOMS_PARTITIONS = MappingProxyType({2: ("apprentissage", "test"), 3: ("apprentissage", "validation", "test")})
STRATE_MANQUANTE = "(manquante)"
MOTIF_ENTIER = re.compile(r"[+-]?\d+")
MOTIF_DECIMAL = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")


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
class Lignes:
    """Ce que la première passe retient de chaque ligne (une entrée par ligne, même rang)."""

    sources: list[int] = field(default_factory=list)
    numeros: list[int] = field(default_factory=list)
    strates: list[str] = field(default_factory=list)
    groupes: list[str | None] = field(default_factory=list)
    exactes: list[int] = field(default_factory=list)
    normalisees: list[int] = field(default_factory=list)


@dataclass
class Options:
    """Colonnes utilisées par la première passe."""

    stratifier: str | None
    grouper: str | None
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


# --------------------------------------------------------------------------- empreintes

def nombre_canonique(texte: str) -> str | None:
    """Forme canonique d'un nombre écrit en texte, ou None si ce n'en est pas un."""
    if not (MOTIF_ENTIER.fullmatch(texte) or MOTIF_DECIMAL.fullmatch(texte)):
        return None
    try:
        nombre = float(texte)
    except OverflowError:
        return None
    return format(nombre, ".15g") if math.isfinite(nombre) else None


def normaliser_valeur(valeur: Any) -> str:
    """Valeur normalisée : NFKC, casse repliée, espaces réduits, nombre canonique."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, (int, float)):
        try:
            nombre = float(valeur)
        except OverflowError:
            return str(valeur)
        return format(nombre, ".15g") if math.isfinite(nombre) else str(valeur)
    if isinstance(valeur, (dict, list)):
        return json.dumps(valeur, ensure_ascii=False, sort_keys=True)
    texte = " ".join(unicodedata.normalize("NFKC", valeur).casefold().split())
    return nombre_canonique(texte) or texte


def empreintes(enregistrement: dict[str, Any], options: Options) -> tuple[int, int]:
    """(empreinte exacte, empreinte normalisée) sur les colonnes non ignorées."""
    utiles = sorted((nom, valeur) for nom, valeur in enregistrement.items()
                    if nom not in options.ignorees and valeur is not None)
    exacte = json.dumps(utiles, ensure_ascii=False, default=str)
    normalisee = "\x1f".join(f"{nom}\x1e{normaliser_valeur(valeur)}" for nom, valeur in utiles)
    return hash(exacte), hash(normalisee)


def etiquette(valeur: Any) -> str | None:
    """Valeur de strate ou de groupe sous forme de texte (None si manquante)."""
    if valeur is None:
        return None
    if isinstance(valeur, (dict, list)):
        return json.dumps(valeur, ensure_ascii=False, sort_keys=True)
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    return str(valeur).strip()


def premiere_passe(entrees: Sequence[Entree], options: Options) -> Lignes:
    """Lit chaque source une fois et retient, par ligne, strate, groupe et empreintes."""
    lignes = Lignes()
    for rang, entree in enumerate(entrees):
        for numero, enregistrement in iterer(entree):
            exacte, normalisee = empreintes(enregistrement, options)
            lignes.sources.append(rang)
            lignes.numeros.append(numero)
            strate = etiquette(enregistrement.get(options.stratifier)) if options.stratifier else ""
            lignes.strates.append(STRATE_MANQUANTE if strate is None else strate)
            lignes.groupes.append(etiquette(enregistrement.get(options.grouper)) if options.grouper else None)
            lignes.exactes.append(exacte)
            lignes.normalisees.append(normalisee)
    return lignes


def verifier_colonnes(entrees: Sequence[Entree], options: Options, lignes: Lignes) -> None:
    """Refuse une colonne de strate ou de groupe absente (en-tête CSV) ou jamais renseignée."""
    if lignes.numeros and options.stratifier and all(s == STRATE_MANQUANTE for s in lignes.strates):
        raise ErreurEntree(f"--stratifier {options.stratifier} : colonne absente ou jamais renseignée")
    if lignes.numeros and options.grouper and all(g is None for g in lignes.groupes):
        raise ErreurEntree(f"--grouper {options.grouper} : colonne absente ou jamais renseignée")
    for entree in entrees:
        if entree.genre != "csv" or not entree.colonnes:
            continue
        for nom in (options.stratifier, options.grouper):
            if nom and nom not in entree.colonnes:
                raise ErreurEntree(f"colonne « {nom} » absente de {entree.nom} ; colonnes : {entree.colonnes}")


# --------------------------------------------------------------------------- unités

def trouver(parent: list[int], x: int) -> int:
    """Racine d'un élément (union-find, compression par division de chemin)."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def unir(parent: list[int], a: int, b: int) -> None:
    """Réunit deux ensembles ; la plus petite racine représente l'union."""
    ra, rb = trouver(parent, a), trouver(parent, b)
    if ra != rb:
        parent[max(ra, rb)] = min(ra, rb)


def former_unites(lignes: Lignes, regrouper_doublons: bool) -> list[list[int]]:
    """Unités indivisibles : même groupe, ou (par défaut) même empreinte normalisée."""
    parent = list(range(len(lignes.numeros)))
    premiers: dict[Any, int] = {}
    for k, groupe in enumerate(lignes.groupes):
        cles = ([("g", groupe)] if groupe is not None else []) + \
               ([("n", lignes.normalisees[k])] if regrouper_doublons else [])
        for cle in cles:
            if cle in premiers:
                unir(parent, premiers[cle], k)
            else:
                premiers[cle] = k
    membres: dict[int, list[int]] = defaultdict(list)
    for k in range(len(parent)):
        membres[trouver(parent, k)].append(k)
    return [membres[racine] for racine in sorted(membres)]


def strate_unite(unite: list[int], strates: list[str]) -> tuple[str, bool]:
    """Strate majoritaire d'une unité (la plus petite à égalité) et mélange éventuel."""
    compte = Counter(strates[k] for k in unite)
    meilleure = min(compte, key=lambda s: (-compte[s], s))
    return meilleure, len(compte) > 1


def repartir(unites: list[list[int]], lignes: Lignes, proportions: Sequence[float],
             graine: int) -> tuple[list[int], int]:
    """Affecte chaque unité à une partition : mélange par strate puis remplissage du plus grand retard."""
    alea = random.Random(graine)
    par_strate: dict[str, list[list[int]]] = defaultdict(list)
    melangees = 0
    for unite in unites:
        strate, melange = strate_unite(unite, lignes.strates)
        par_strate[strate].append(unite)
        melangees += melange
    affectation = [0] * len(lignes.numeros)
    for strate in sorted(par_strate):
        groupe = par_strate[strate]
        alea.shuffle(groupe)
        total = sum(len(u) for u in groupe)
        cibles = [p * total for p in proportions]
        courant = [0] * len(proportions)
        for unite in groupe:
            choix = max(range(len(proportions)), key=lambda p: (cibles[p] - courant[p], -p))
            courant[choix] += len(unite)
            for k in unite:
                affectation[k] = choix
    return affectation, melangees


# --------------------------------------------------------------------------- contrôles

def chercher_fuites(cles: Sequence[Any], partitions: Sequence[int], lignes: Lignes,
                    noms: Sequence[str], entrees: Sequence[Entree]) -> dict[str, Any]:
    """Clés présentes dans plusieurs partitions : nombre, lignes touchées, exemples."""
    vues: dict[Any, set[int]] = defaultdict(set)
    for cle, partition in zip(cles, partitions):
        if cle is not None:
            vues[cle].add(partition)
    fuyantes = {cle for cle, ensemble in vues.items() if len(ensemble) > 1}
    exemples: dict[Any, list[str]] = defaultdict(list)
    touchees = 0
    for k, (cle, partition) in enumerate(zip(cles, partitions)):
        if cle in fuyantes:
            touchees += 1
            if len(exemples) < MAX_EXEMPLES or cle in exemples:
                exemples[cle].append(f"{noms[partition]} <- {entrees[lignes.sources[k]].nom}:{lignes.numeros[k]}")
    return {"cles_partagees": len(fuyantes), "lignes_touchees": touchees,
            "exemples": [lieux[:4] for lieux in list(exemples.values())[:MAX_EXEMPLES]]}


def controler_fuites(lignes: Lignes, partitions: Sequence[int], noms: Sequence[str],
                     entrees: Sequence[Entree], avec_groupes: bool) -> dict[str, Any]:
    """Fuites exactes, normalisées et par groupe entre partitions ; doublons internes."""
    fuites = {"exacte": chercher_fuites(lignes.exactes, partitions, lignes, noms, entrees),
              "normalisee": chercher_fuites(lignes.normalisees, partitions, lignes, noms, entrees)}
    if avec_groupes:
        fuites["groupes"] = chercher_fuites(lignes.groupes, partitions, lignes, noms, entrees)
    internes = Counter()
    vus: set[tuple[int, int]] = set()
    for cle, partition in zip(lignes.normalisees, partitions):
        if (cle, partition) in vus:
            internes[noms[partition]] += 1
        vus.add((cle, partition))
    fuites["doublons_internes_normalises"] = dict(internes)
    fuites["fuite"] = any(f["cles_partagees"] for nom, f in fuites.items() if isinstance(f, dict) and "cles_partagees" in f)
    return fuites


def bilan_partitions(lignes: Lignes, partitions: Sequence[int], noms: Sequence[str],
                     proportions: Sequence[float] | None, unites: list[list[int]] | None) -> list[dict[str, Any]]:
    """Lignes, unités et proportion obtenue de chaque partition, contre la demande."""
    total = len(partitions)
    effectifs = Counter(partitions)
    nb_unites = Counter(partitions[u[0]] for u in unites) if unites else Counter()
    bilan = []
    for p, nom in enumerate(noms):
        ligne: dict[str, Any] = {"partition": nom, "lignes": effectifs[p], "proportion_obtenue": effectifs[p] / total}
        if proportions is not None:
            ligne["proportion_demandee"] = proportions[p]
            ligne["ecart"] = effectifs[p] / total - proportions[p]
        if unites:
            ligne["unites"] = nb_unites[p]
        bilan.append(ligne)
    return bilan


def bilan_strates(lignes: Lignes, partitions: Sequence[int], noms: Sequence[str]) -> dict[str, Any]:
    """Part de chaque strate dans chaque partition, comparée à sa part globale."""
    globales = Counter(lignes.strates)
    total = len(partitions)
    par_partition = [Counter() for _ in noms]
    for strate, partition in zip(lignes.strates, partitions):
        par_partition[partition][strate] += 1
    tailles = [sum(c.values()) for c in par_partition]
    strates, ecart_max, absentes = [], 0.0, []
    for strate in sorted(globales, key=lambda s: -globales[s]):
        parts = {noms[p]: (par_partition[p][strate] / tailles[p] if tailles[p] else None) for p in range(len(noms))}
        for p, part in enumerate(parts.values()):
            if part is not None:
                ecart_max = max(ecart_max, abs(part - globales[strate] / total))
            if not par_partition[p][strate] and globales[strate] >= len(noms):
                absentes.append({"strate": strate, "partition": noms[p], "lignes_de_la_strate": globales[strate]})
        strates.append({"strate": strate, "lignes": globales[strate], "part_globale": globales[strate] / total,
                        "parts_par_partition": parts})
    return {"strates": strates[:100], "nombre_strates": len(globales), "ecart_max_part_strate": ecart_max,
            "strates_absentes": absentes[:50]}


# --------------------------------------------------------------------------- écriture

def iterer_brut(entree: Entree) -> Iterator[Any]:
    """Seconde passe : chaque enregistrement sous sa forme d'origine, dans l'ordre de la première."""
    if entree.genre == "csv":
        yield from iterer_csv_brut(entree)
    elif entree.genre == "jsonl":
        yield from iterer_jsonl_brut(entree)
    else:
        document = charger_json(entree)
        for element in document if isinstance(document, list) else [document]:
            if isinstance(element, dict):
                yield json.dumps(element, ensure_ascii=False)


def iterer_csv_brut(entree: Entree) -> Iterator[list[str]]:
    """En-tête d'origine puis lignes brutes, mêmes lignes ignorées qu'à la première passe."""
    assert entree.chemin is not None
    with ouvrir_texte(entree.chemin, entree.encodage) as flux:
        lecteur = csv.reader(flux, delimiter=entree.separateur)
        entete = next(lecteur, None)
        if entete is None:
            return
        yield entete
        for ligne in lecteur:
            if not ligne or (len(ligne) == 1 and not ligne[0].strip() and len(entete) > 1):
                continue
            yield ligne


def iterer_jsonl_brut(entree: Entree) -> Iterator[str]:
    """Lignes JSONL d'origine (objets valides seulement), sans fin de ligne."""
    assert entree.chemin is not None
    with ouvrir_texte(entree.chemin, entree.encodage) as flux:
        for ligne in flux:
            if not ligne.strip():
                continue
            try:
                objet = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if isinstance(objet, dict):
                yield ligne.rstrip("\r\n")


def chemins_sortie(dossier: Path, noms: Sequence[str], entree: Entree, ecraser: bool) -> list[Path]:
    """Fichiers de sortie (même format que la source, json devient jsonl) ; refuse d'écraser."""
    if dossier.exists() and not dossier.is_dir():
        raise ErreurEntree(f"--sortie {dossier} existe et n'est pas un dossier")
    extension = ".jsonl" if entree.genre != "csv" else (".tsv" if entree.separateur == "\t" else ".csv")
    chemins = [dossier / f"{nom}{extension}" for nom in noms]
    existants = [str(c) for c in chemins if c.exists()]
    if existants and not ecraser:
        raise ErreurEntree(f"fichier(s) déjà présent(s) : {existants} ; --ecraser pour les remplacer")
    return chemins


def ecrire_partitions(entree: Entree, partitions: Sequence[int], chemins: Sequence[Path]) -> list[int]:
    """Seconde passe : recopie chaque ligne dans le fichier de sa partition (écriture puis renommage)."""
    chemins[0].parent.mkdir(parents=True, exist_ok=True)
    provisoires = [c.with_name(f".{c.name}.partiel") for c in chemins]
    flux = [p.open("w", encoding="utf-8", newline="") for p in provisoires]
    comptes = [0] * len(chemins)
    try:
        recopier(entree, partitions, flux, comptes)
    except (ErreurEntree, OSError):
        for f, provisoire in zip(flux, provisoires):
            f.close()
            provisoire.unlink(missing_ok=True)
        raise
    for f in flux:
        f.close()
    if sum(comptes) != len(partitions):
        for provisoire in provisoires:
            provisoire.unlink(missing_ok=True)
        raise ErreurEntree(f"{entree.nom} a changé entre les deux passes ({sum(comptes)} lignes contre {len(partitions)})")
    for provisoire, chemin in zip(provisoires, chemins):
        os.replace(provisoire, chemin)
    return comptes


def recopier(entree: Entree, partitions: Sequence[int], flux: Sequence[Any], comptes: list[int]) -> None:
    """Recopie les enregistrements d'origine dans les flux de leurs partitions (en-tête CSV en tête)."""
    source = iterer_brut(entree)
    ecrivains = None
    if entree.genre == "csv":
        ecrivains = [csv.writer(f, delimiter=entree.separateur, lineterminator="\n") for f in flux]
        entete = next(source, None)
        for ecrivain in ecrivains:
            ecrivain.writerow(entete or [])
    for k, element in enumerate(source):
        if k >= len(partitions):
            raise ErreurEntree(f"{entree.nom} a changé entre les deux passes (plus de lignes qu'à la première)")
        p = partitions[k]
        if ecrivains is not None:
            ecrivains[p].writerow(element)
        else:
            flux[p].write(element + "\n")
        comptes[p] += 1


# --------------------------------------------------------------------------- scikit-learn

def charger_sklearn() -> Any:
    """train_test_split de scikit-learn s'il est installé, sinon None."""
    try:
        from sklearn.model_selection import train_test_split
    except ImportError:
        return None
    return train_test_split


def version_sklearn() -> str | None:
    """Version de scikit-learn installée."""
    try:
        import sklearn
    except ImportError:
        return None
    return sklearn.__version__


def comparer_sklearn(decouper: Any, lignes: Lignes, proportions: Sequence[float], graine: int,
                     noms: Sequence[str]) -> dict[str, Any]:
    """Rejoue un découpage stratifié par train_test_split et compare les parts de strates."""
    indices = list(range(len(lignes.strates)))
    try:
        premier, reste = decouper(indices, train_size=proportions[0], stratify=lignes.strates, random_state=graine)
        morceaux = [premier]
        if len(proportions) == 3:
            part = proportions[1] / (proportions[1] + proportions[2])
            milieu, dernier = decouper(reste, train_size=part, stratify=[lignes.strates[k] for k in reste],
                                       random_state=graine)
            morceaux += [milieu, dernier]
        else:
            morceaux.append(reste)
    except ValueError as exc:
        return {"statut": "impossible", "raison": str(exc), "version": version_sklearn()}
    partitions = [0] * len(indices)
    for p, morceau in enumerate(morceaux):
        for k in morceau:
            partitions[k] = p
    strates = bilan_strates(lignes, partitions, noms)
    return {"statut": "faite", "version": version_sklearn(),
            "proportions_obtenues": [len(m) / len(indices) for m in morceaux],
            "ecart_max_part_strate": strates["ecart_max_part_strate"],
            "strates_absentes": strates["strates_absentes"]}


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
        description="Découpe un jeu de données en apprentissage/validation/test, déterministe (graine), stratifié, "
                    "groupé, doublons regroupés, avec contrôle de fuite ; avec plusieurs fichiers, audite un "
                    "découpage existant. Code 1 si fuite, partition vide ou proportion hors tolérance.",
        epilog="exemple : diviser_jeu_donnees.py patients.csv --proportions 0.7,0.15,0.15 --stratifier diagnostic "
               "--grouper patient_id --ignorer id --sortie decoupe --json   |   diviser_jeu_donnees.py "
               "train.csv test.csv --grouper patient_id --ignorer id",
    )
    parseur.add_argument("sources", nargs="+", metavar="FICHIER",
                         help="un fichier à découper (.csv .tsv .jsonl .json, .gz possible, ou texte JSON en ligne) ; "
                              "plusieurs fichiers : audit des fuites entre eux")
    parseur.add_argument("--proportions", default="0.7,0.15,0.15",
                         help="deux ou trois parts : apprentissage,[validation,]test (défaut 0.7,0.15,0.15)")
    parseur.add_argument("--graine", type=int, default=0, help="graine du mélange (défaut 0)")
    parseur.add_argument("--stratifier", default=None, help="colonne dont la répartition est conservée dans chaque partition")
    parseur.add_argument("--grouper", default=None, help="colonne de groupe : un groupe entier va dans une seule partition")
    parseur.add_argument("--ignorer", default="", help="colonnes exclues des empreintes de doublons (identifiants, dates d'insertion)")
    parseur.add_argument("--sans-regroupement-doublons", action="store_true",
                         help="ne pas réunir les doublons normalisés (la fuite est alors seulement mesurée)")
    parseur.add_argument("--tolerance", type=float, default=0.05,
                         help="écart absolu toléré entre proportion obtenue et demandée (défaut 0.05)")
    parseur.add_argument("--sortie", type=Path, default=None, metavar="DOSSIER",
                         help="dossier où écrire les partitions ; sans lui, rien n'est écrit")
    parseur.add_argument("--ecraser", action="store_true", help="remplacer des fichiers de partition existants")
    parseur.add_argument("--format", choices=("auto", "csv", "tsv", "jsonl", "json"), default="auto",
                         help="format imposé (défaut : d'après l'extension)")
    parseur.add_argument("--separateur", default=None, help="séparateur CSV imposé (défaut : deviné ; tab pour tabulation)")
    parseur.add_argument("--encodage", default="utf-8", help="encodage des fichiers (défaut utf-8, BOM toléré)")
    parseur.add_argument("--max-octets-json", type=int, default=OCTETS_MAX_JSON, help="taille maximale d'un .json chargé entier")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : compare la stratification à scikit-learn s'il est installé")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def lire_proportions(texte: str) -> list[float]:
    """Deux ou trois parts strictement positives de somme 1 (ou 100)."""
    try:
        parts = [float(x) for x in texte.replace(";", ",").split(",") if x.strip()]
    except ValueError as exc:
        raise ErreurEntree(f"--proportions illisible : {texte!r}") from exc
    total = sum(parts)
    if abs(total - 100) < 1e-6:
        parts, total = [p / 100 for p in parts], 1.0
    if len(parts) not in NOMS_PARTITIONS or any(not math.isfinite(p) or p <= 0 for p in parts) or abs(total - 1) > 1e-6:
        raise ErreurEntree(f"--proportions : deux ou trois parts > 0 de somme 1 (ou 100) attendues, reçu {texte!r}")
    return parts


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse les combinaisons incohérentes."""
    if len(args.sources) > 1 and args.sortie is not None:
        raise ErreurEntree("--sortie ne s'emploie qu'avec un seul fichier à découper (plusieurs fichiers = audit)")
    if not 0 <= args.tolerance <= 1:
        raise ErreurEntree("--tolerance doit être entre 0 et 1")
    if args.separateur is not None and args.separateur not in ("\\t", "tab") and len(args.separateur) != 1:
        raise ErreurEntree("--separateur doit être un seul caractère (ou tab)")


def presenter_json(rapport: dict[str, Any]) -> None:
    """Écrit l'objet JSON unique sur stdout."""
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Bilan lisible : partitions, strates, fuites, fichiers."""
    print(f"{rapport['mode']} : {rapport['denominateur']} lignes, {rapport.get('unites', '-')} unités "
          f"(graine {rapport.get('graine', '-')})")
    for ligne in rapport["partitions"]:
        demande = f" (demandé {ligne['proportion_demandee']:.1%})" if "proportion_demandee" in ligne else ""
        print(f"  {ligne['partition']:<14} {ligne['lignes']:>9} lignes  {ligne['proportion_obtenue']:6.1%}{demande}")
    strates = rapport.get("strates")
    if strates and strates["nombre_strates"] > 1:
        print(f"strates : {strates['nombre_strates']}, écart maximal de part {strates['ecart_max_part_strate']:.2%}")
        for absente in strates["strates_absentes"][:5]:
            print(f"  strate {absente['strate']!r} absente de {absente['partition']}")
    fuites = rapport["fuites"]
    for genre in ("exacte", "normalisee", "groupes"):
        if genre in fuites:
            f = fuites[genre]
            print(f"fuite {genre} : {f['cles_partagees']} clé(s) partagée(s), {f['lignes_touchees']} ligne(s)"
                  + (f", ex. {f['exemples'][0]}" if f["exemples"] else ""))
    for defaut in rapport["defauts"]:
        print(f"DÉFAUT : {defaut}")
    for chemin, compte in rapport.get("fichiers_ecrits", {}).items():
        print(f"écrit : {chemin} ({compte} lignes)")
    if rapport.get("comparaison_sklearn"):
        c = rapport["comparaison_sklearn"]
        print(f"scikit-learn {c.get('version')} : {c['statut']}"
              + (f", écart maximal de part de strate {c['ecart_max_part_strate']:.2%}" if c["statut"] == "faite" else f" ({c['raison']})"))


def base_rapport(lignes: Lignes | None, entrees: Sequence[Entree], moteur: str) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    n = len(lignes.numeros) if lignes else 0
    examines = [f"{entrees[lignes.sources[k]].nom}:{lignes.numeros[k]}" for k in range(min(n, MAX_EXAMINES))] if lignes else []
    return {"outil": NOM_OUTIL, "moteur": moteur, "version_moteur": version_sklearn() if moteur == "scikit-learn" else None,
            "denominateur": n, "unite_denominateur": "lignes", "examines": examines, "examines_tronques": n > MAX_EXAMINES,
            "contrat": extraire_contrat(__doc__ or "")}


def defauts(rapport: dict[str, Any], tolerance: float) -> list[str]:
    """Défauts qui justifient le code 1."""
    trouves = []
    for genre in ("exacte", "normalisee", "groupes"):
        f = rapport["fuites"].get(genre)
        if f and f["cles_partagees"]:
            trouves.append(f"fuite {genre} : {f['lignes_touchees']} ligne(s) partagent une clé entre partitions")
    for ligne in rapport["partitions"]:
        if not ligne["lignes"]:
            trouves.append(f"partition {ligne['partition']} vide")
        elif abs(ligne.get("ecart", 0.0)) > tolerance:
            trouves.append(f"partition {ligne['partition']} : {ligne['proportion_obtenue']:.1%} contre "
                           f"{ligne['proportion_demandee']:.1%} demandés (tolérance {tolerance:.0%})")
    return trouves


def decouper(args: argparse.Namespace, entree: Entree, options: Options, decoupeur: Any) -> dict[str, Any]:
    """Mode découpage : unités, répartition, contrôles, écriture éventuelle."""
    proportions = lire_proportions(args.proportions)
    noms = NOMS_PARTITIONS[len(proportions)]
    lignes = premiere_passe([entree], options)
    verifier_colonnes([entree], options, lignes)
    if not lignes.numeros:
        raise ErreurEntree(f"dénominateur nul : {entree.nom} ne contient aucune ligne, rien à examiner", CODE_RIEN)
    chemins = None if args.sortie is None else \
        chemins_sortie(resoudre(str(args.sortie), args.racine), noms, entree, args.ecraser)
    unites = former_unites(lignes, not args.sans_regroupement_doublons)
    partitions, melangees = repartir(unites, lignes, proportions, args.graine)
    rapport = base_rapport(lignes, [entree], "scikit-learn" if decoupeur else "stdlib") | {
        "mode": "découpage", "source": entree.nom, "graine": args.graine, "proportions_demandees": proportions,
        "unites": len(unites), "unites_de_plusieurs_lignes": sum(len(u) > 1 for u in unites),
        "unites_a_strates_melangees": melangees,
        "lignes_sans_groupe": sum(g is None for g in lignes.groupes) if options.grouper else None,
        "lignes_mal_formees": entree.mal_formees,
        "partitions": bilan_partitions(lignes, partitions, noms, proportions, unites),
        "strates": bilan_strates(lignes, partitions, noms) if options.stratifier else None,
        "fuites": controler_fuites(lignes, partitions, noms, [entree], options.grouper is not None),
    }
    rapport["defauts"] = defauts(rapport, args.tolerance)
    if decoupeur:
        rapport["comparaison_sklearn"] = comparer_sklearn(decoupeur, lignes, proportions, args.graine, noms)
    if chemins is not None:
        comptes = ecrire_partitions(entree, partitions, chemins)
        rapport["fichiers_ecrits"] = {str(c): n for c, n in zip(chemins, comptes)}
    return rapport


def auditer(args: argparse.Namespace, entrees: list[Entree], options: Options) -> dict[str, Any]:
    """Mode audit : fuites et équilibre entre des partitions déjà faites (un fichier par partition)."""
    lignes = premiere_passe(entrees, options)
    verifier_colonnes(entrees, options, lignes)
    if not lignes.numeros:
        raise ErreurEntree("dénominateur nul : aucun des fichiers ne contient de ligne, rien à examiner", CODE_RIEN)
    noms = [e.nom for e in entrees]
    partitions = lignes.sources
    rapport = base_rapport(lignes, entrees, "stdlib") | {
        "mode": "audit", "sources": noms,
        "partitions": bilan_partitions(lignes, partitions, noms, None, None),
        "strates": bilan_strates(lignes, partitions, noms) if options.stratifier else None,
        "fuites": controler_fuites(lignes, partitions, noms, entrees, options.grouper is not None),
    }
    rapport["defauts"] = defauts(rapport, args.tolerance)
    return rapport


def executer(args: argparse.Namespace) -> dict[str, Any]:
    """Prépare les entrées et lance le découpage ou l'audit."""
    verifier_parametres(args)
    options = Options(args.stratifier, args.grouper,
                      frozenset(n.strip() for n in args.ignorer.split(",") if n.strip()))
    entrees = [preparer_entree(source, args) for source in args.sources]
    if len(entrees) > 1:
        return auditer(args, entrees, options)
    decoupeur = None
    if args.moteur == "auto" and args.stratifier:
        decoupeur = charger_sklearn()
        if decoupeur is None:
            print(f"{NOM_OUTIL} : scikit-learn absent — stratification stdlib seule, non comparée", file=sys.stderr)
    return decouper(args, entrees[0], options, decoupeur)


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : découpe ou audite, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    try:
        rapport = executer(args)
    except ErreurEntree as exc:
        print(f"{NOM_OUTIL} : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport(None, [], "stdlib") | {"refus": str(exc)})
        return exc.code
    except OSError as exc:
        print(f"{NOM_OUTIL} : erreur d'entrée-sortie : {exc}", file=sys.stderr)
        return CODE_USAGE
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_DEFAUT if rapport["defauts"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
