"""Répondre à une requête SQL sur des fichiers .csv, .tsv, .json ou .jsonl sans monter de base.
Un CSV lu tel quel ne contient que du texte, et le SQL compare alors des chaînes : mesuré dans cette
session, sqlite3 sur les deux valeurs texte '9' et '10' rend `select max(x)` = ('9',) — d'où
l'inférence de types faite ici avant toute requête.

QUESTION
    Que répond cette requête SQL (lecture seule) sur ces fichiers de données, sans base de données ?
MESURE
    Chaque fichier (.csv, .tsv, .tab, .json, .jsonl, .ndjson ; un dossier vaut ses fichiers de ces
    extensions, sans récursion) devient une table nommée d'après le fichier. Moteur stdlib : sqlite3
    en mémoire ; colonnes CSV typées par inférence sur toutes les valeurs chargées (BOOLEAN pour
    true/false, INTEGER sans zéro de tête, REAL, sinon TEXT ; cellule vide = valeur nulle) ; au
    plus --plafond-chargement lignes par table (défaut 1000000), la table est alors marquée
    tronquée. Seules les requêtes qui commencent par SELECT ou WITH sont acceptées ; sqlite3 tourne
    ensuite sous un autorisateur qui refuse toute action autre que lecture, et sous un délai
    (--delai). Sortie : colonnes, lignes (plafonnées par --max-lignes), nombre total de lignes.
    Si duckdb est importable, il lit lui-même les fichiers, répond, et la réponse stdlib lui est
    comparée (multiensemble de lignes normalisées) ; tout désaccord est rapporté (code 1).
HYPOTHÈSES
    Les fichiers sont en utf-8 (ou dans l'encodage passé par --encodage) ; la première ligne d'un
    CSV est l'en-tête (sauf --sans-entete) et son séparateur le plus fréquent parmi , ; tabulation
    | est celui du fichier ; un .json est une liste d'objets, une liste de valeurs, ou un objet
    dont une seule clé porte une telle liste ; un .jsonl porte un objet par ligne.
LIMITES
    Aucune écriture, aucune jointure avec une vraie base. Pas de dates typées en stdlib : elles
    restent du texte et se comparent comme du texte. Virgule décimale (3,14) lue comme texte. Un
    .json est chargé entier en mémoire (au plus 100 Mo). Une table au-delà de 2000 colonnes est
    refusée par sqlite3. Sans clause « order by », l'ordre des lignes n'est pas garanti et deux
    moteurs peuvent différer sur une requête avec « limit ». Dialectes : mesuré, `select 1/2` rend
    0 en sqlite3 et 0.5 en duckdb 1.5.6 — le contrôle croisé le signale, il ne le corrige pas.
CONTRE-EXEMPLES
    Constaté : une colonne de codes produit valant 1E5 et 2E3 est inférée REAL ; `select code from
    codes` rend 100000.0 et 2000.0 au lieu des codes, et duckdb 1.5.6 rend la même chose, donc le
    contrôle croisé dit « concordant ». Seul un code non numérique dans la colonne (ou un zéro de
    tête) la garde en texte.
INVOCATION
    {outil} {dossier}/donnees.csv --requete "select count(*) as n from donnees" --json
DOMAINE
    Exports tabulaires de production (journaux, extractions, inventaires) de taille compatible
    avec la mémoire vive, interrogés en lecture pour vérifier un chiffre avant de le publier.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import duckdb
except ImportError:
    duckdb = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
MOT_SELECT = "SELECT"
MOT_WITH = "WITH"
MOTS_LECTURE = (MOT_SELECT, MOT_WITH)
TYPE_BOOLEEN = "BOOLEAN"
TYPE_ENTIER = "INTEGER"
TYPE_REEL = "REAL"
TYPE_TEXTE = "TEXT"
TYPE_MIXTE = "MIXTE"
MESSAGE_DUCKDB_ABSENT = ("duckdb absent : moteur sqlite3 en mémoire (types inférés par l'outil, "
                         "plafond de lignes par table), sans contrôle croisé.")

TABLE_BRUTE = "_chargement_brut_"
SEPARATEURS_CANDIDATS = (",", ";", "\t", "|")
EXTENSIONS = MappingProxyType({".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".json": "json",
                               ".jsonl": "jsonl", ".ndjson": "jsonl"})
PLAFOND_CHARGEMENT = 1_000_000
MAX_LIGNES_RESULTAT = 200
MAX_LIGNES_COMPAREES = 10_000
MAX_OCTETS_JSON = 100 * 1024 * 1024
MAX_EXAMINES = 200
MAX_ANOMALIES = 20
DELAI_REQUETE = 30.0
LONGUEUR_MAX_SQLITE = 100_000_000
MOTIF_ENTIER = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
MOTIF_REEL = re.compile(
    r"^[+-]?(?:(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
EXPRESSION_PAR_TYPE = MappingProxyType({
    TYPE_ENTIER: "CAST(NULLIF({c}, '') AS INTEGER)",
    TYPE_REEL: "CAST(NULLIF({c}, '') AS REAL)",
    TYPE_BOOLEEN: "CASE lower({c}) WHEN 'true' THEN 1 WHEN 'false' THEN 0 END",
    TYPE_TEXTE: "NULLIF({c}, '')",
})
ACTIONS_LECTURE = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                             getattr(sqlite3, "SQLITE_RECURSIVE", 33)})


class ErreurEntree(Exception):
    """Entrée invalide : fichier, format, requête refusée ou fautive (code 2)."""


class RequeteRefusee(ErreurEntree):
    """Requête qui tente autre chose qu'une lecture, ou qui dépasse le délai."""


@dataclass
class Options:
    """Réglages de lecture et d'exécution."""

    separateur: str | None
    sans_entete: bool
    encodage: str
    plafond: int
    max_lignes: int
    delai: float


@dataclass
class Table:
    """Une table chargée à partir d'un fichier."""

    nom: str
    fichier: Path
    format: str
    colonnes: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    lignes: int = 0
    tronquee: bool = False
    irregulieres: int = 0
    anomalies: list[str] = field(default_factory=list)


@dataclass
class Resultat:
    """Résultat d'une requête : colonnes, lignes gardées, nombre total."""

    colonnes: list[str]
    lignes: list[tuple[Any, ...]]
    total: int


# --------------------------------------------------------------------------- fichiers


def lister_fichiers(chemins: list[Path]) -> list[Path]:
    """Développe les dossiers (sans récursion) et vérifie les fichiers donnés."""
    fichiers: list[Path] = []
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            fichiers += sorted(p for p in chemin.iterdir()
                               if p.is_file() and p.suffix.lower() in EXTENSIONS)
        elif chemin.suffix.lower() not in EXTENSIONS:
            raise ErreurEntree(f"extension non prise en charge : {chemin.name} "
                               f"(attendu : {', '.join(sorted(EXTENSIONS))})")
        else:
            fichiers.append(chemin)
    return fichiers


def nom_de_table(chemin: Path, pris: set[str]) -> str:
    """Nom SQL tiré du nom de fichier (lettres, chiffres, _), unique sans tenir compte de la casse."""
    nom = re.sub(r"\W", "_", chemin.stem) or "table"
    if nom[0].isdigit() or nom.lower().startswith("sqlite_"):
        nom = "t_" + nom
    candidat, rang = nom, 2
    while candidat.lower() in pris:
        candidat, rang = f"{nom}_{rang}", rang + 1
    pris.add(candidat.lower())
    return candidat


def ident(nom: str) -> str:
    """Identifiant SQL entre guillemets doubles."""
    return '"' + nom.replace('"', '""') + '"'


def noms_colonnes(entete: list[str]) -> list[str]:
    """En-tête nettoyé : vide → colonne_N, doublons suffixés."""
    noms: list[str] = []
    vus: set[str] = set()
    for rang, brut in enumerate(entete, start=1):
        nom = brut.strip() or f"colonne_{rang}"
        candidat, suite = nom, 2
        while candidat.lower() in vus:
            candidat, suite = f"{nom}_{suite}", suite + 1
        vus.add(candidat.lower())
        noms.append(candidat)
    return noms


# --------------------------------------------------------------------------- types


def genre_valeur(valeur: str) -> str:
    """Type le plus étroit d'une cellule CSV non vide."""
    if valeur.lower() in ("true", "false"):
        return TYPE_BOOLEEN
    if MOTIF_ENTIER.match(valeur) and len(valeur) <= 20 and abs(int(valeur)) < 2 ** 63:
        return TYPE_ENTIER
    if MOTIF_REEL.match(valeur) and math.isfinite(float(valeur)):
        return TYPE_REEL
    return TYPE_TEXTE


def affiner_type(etat: str | None, valeur: str) -> str | None:
    """Fusionne le type courant d'une colonne avec une nouvelle cellule."""
    if valeur == "":
        return etat
    genre = genre_valeur(valeur)
    if etat is None or etat == genre:
        return genre
    if {etat, genre} == {TYPE_ENTIER, TYPE_REEL}:
        return TYPE_REEL
    return TYPE_TEXTE


def type_json(valeurs_types: set[str]) -> str:
    """Type déclaré d'une colonne JSON d'après les types Python rencontrés."""
    if not valeurs_types:
        return TYPE_TEXTE
    if valeurs_types == {"bool"}:
        return TYPE_BOOLEEN
    if valeurs_types <= {"int"}:
        return TYPE_ENTIER
    if valeurs_types <= {"int", "float"}:
        return TYPE_REEL
    if valeurs_types == {"str"}:
        return TYPE_TEXTE
    return TYPE_MIXTE


def cellule_json(valeur: Any) -> Any:
    """Valeur JSON → valeur sqlite3 (objets et listes en texte JSON)."""
    if isinstance(valeur, bool):
        return int(valeur)
    if isinstance(valeur, int) and abs(valeur) >= 2 ** 63:
        return str(valeur)
    if isinstance(valeur, (dict, list)):
        return json.dumps(valeur, ensure_ascii=False)
    return valeur


# --------------------------------------------------------------------------- lecture CSV


def detecter_separateur(echantillon: str, table: Table, options: Options) -> str:
    """Séparateur imposé, sinon tabulation pour .tsv, sinon le plus fréquent de , ; tab | dans la
    première ligne hors guillemets (une virgule décimale des données ne la trompe pas)."""
    if options.separateur:
        return options.separateur
    if table.format == "tsv":
        return "\t"
    entete = re.sub(r'"[^"]*"', "", echantillon.partition("\n")[0])
    comptes = {c: entete.count(c) for c in SEPARATEURS_CANDIDATS}
    meilleur = max(comptes, key=lambda c: comptes[c])
    return meilleur if comptes[meilleur] else ","


def lignes_csv(table: Table, options: Options) -> Iterator[list[str]]:
    """Enregistrements CSV en flux (erreurs de décodage et de format en ErreurEntree)."""
    try:
        with table.fichier.open("r", encoding=options.encodage, newline="") as flux:
            echantillon = flux.read(65536)
            if "\x00" in echantillon:
                raise ErreurEntree(f"{table.fichier.name} : octets NUL, fichier binaire refusé")
            separateur = detecter_separateur(echantillon, table, options)
            flux.seek(0)
            yield from csv.reader(flux, delimiter=separateur)
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{table.fichier.name} : non décodable en {options.encodage} "
                           f"(essayez --encodage) : {exc.reason}") from exc
    except csv.Error as exc:
        raise ErreurEntree(f"{table.fichier.name} : CSV illisible : {exc}") from exc
    except OSError as exc:
        raise ErreurEntree(f"{table.fichier.name} : lecture impossible : {exc}") from exc


def ajuster_ligne(ligne: list[str], largeur: int, numero: int, table: Table) -> list[str]:
    """Complète ou tronque une ligne irrégulière et la consigne."""
    if len(ligne) != largeur and len(table.anomalies) < MAX_ANOMALIES:
        table.anomalies.append(f"enregistrement {numero} : {len(ligne)} champ(s) au lieu de "
                               f"{largeur}")
    return (ligne + [""] * largeur)[:largeur]


def lignes_a_charger(lignes: Iterator[list[str]], table: Table, options: Options,
                     etats: list[str | None], premier_numero: int) -> Iterator[list[str]]:
    """Lignes ajustées à la largeur de l'en-tête (lignes vides sautées) ; affine les types ;
    s'arrête au plafond."""
    largeur = len(table.colonnes)
    for numero, ligne in enumerate(lignes, start=premier_numero):
        if not ligne:
            continue
        if table.lignes >= options.plafond:
            table.tronquee = True
            return
        table.irregulieres += len(ligne) != largeur
        cellules = ajuster_ligne(ligne, largeur, numero, table)
        for i, cellule in enumerate(cellules):
            etats[i] = affiner_type(etats[i], cellule)
        table.lignes += 1
        yield cellules


def charger_csv(conn: sqlite3.Connection, table: Table, options: Options) -> None:
    """Charge un CSV dans une table brute, infère les types, puis crée la table typée."""
    flux = lignes_csv(table, options)
    premiere = next((ligne for ligne in flux if ligne), None)
    if premiere is None:
        raise ErreurEntree(f"{table.fichier.name} : fichier vide")
    en_attente = [premiere] if options.sans_entete else []
    table.colonnes = ([f"c{i}" for i in range(1, len(premiere) + 1)] if options.sans_entete
                      else noms_colonnes(premiere))
    largeur = len(table.colonnes)
    etats: list[str | None] = [None] * largeur
    brut = [f"c{i}" for i in range(largeur)]
    creer_table(conn, TABLE_BRUTE, brut, [""] * largeur)
    conn.executemany(f"INSERT INTO {TABLE_BRUTE} VALUES ({', '.join('?' * largeur)})",
                     lignes_a_charger(itertools.chain(en_attente, flux), table, options, etats,
                                      1 if options.sans_entete else 2))
    if table.irregulieres:
        table.anomalies.insert(0, f"{table.irregulieres} ligne(s) irrégulière(s), complétées de "
                                  "NULL ou tronquées à la largeur de l'en-tête")
    table.types = [etat or TYPE_TEXTE for etat in etats]
    typer_table(conn, table, brut)


def typer_table(conn: sqlite3.Connection, table: Table, brut: list[str]) -> None:
    """Crée la table typée à partir de la table brute, puis supprime celle-ci."""
    creer_table(conn, table.nom, table.colonnes, table.types)
    expressions = [EXPRESSION_PAR_TYPE[t].format(c=ident(b)) for t, b in zip(table.types, brut)]
    conn.execute(f"INSERT INTO {ident(table.nom)} SELECT {', '.join(expressions)} FROM {TABLE_BRUTE}")
    conn.execute(f"DROP TABLE {TABLE_BRUTE}")


def creer_table(conn: sqlite3.Connection, nom: str, colonnes: list[str], types: list[str]) -> None:
    """CREATE TABLE ; le type MIXTE n'a pas d'affinité (valeurs gardées telles quelles)."""
    definitions = ", ".join(f"{ident(c)} {'' if t == TYPE_MIXTE else t}".strip()
                            for c, t in zip(colonnes, types))
    try:
        conn.execute(f"CREATE TABLE {ident(nom)} ({definitions})")
    except sqlite3.Error as exc:
        raise ErreurEntree(f"table {nom} impossible à créer : {exc}") from exc


# --------------------------------------------------------------------------- lecture JSON


def enregistrements_json(table: Table) -> list[Any]:
    """Liste d'enregistrements d'un .json (liste, ou objet à une seule liste)."""
    if table.fichier.stat().st_size > MAX_OCTETS_JSON:
        raise ErreurEntree(f"{table.fichier.name} : plus de {MAX_OCTETS_JSON} octets (.json "
                           "chargé entier) ; convertissez-le en .jsonl")
    try:
        donnees = json.loads(table.fichier.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ErreurEntree(f"{table.fichier.name} : JSON invalide : {exc}") from exc
    if isinstance(donnees, dict):
        listes = [k for k, v in donnees.items() if isinstance(v, list)]
        if len(listes) != 1:
            raise ErreurEntree(f"{table.fichier.name} : objet JSON sans liste unique "
                               "d'enregistrements")
        table.anomalies.append(f"enregistrements lus sous la clé « {listes[0]} »")
        donnees = donnees[listes[0]]
    if not isinstance(donnees, list):
        raise ErreurEntree(f"{table.fichier.name} : ni liste ni objet JSON")
    return donnees


def enregistrements_jsonl(table: Table, options: Options) -> Iterator[Any]:
    """Enregistrements d'un .jsonl en flux ; lignes invalides consignées et sautées."""
    try:
        with table.fichier.open("r", encoding=options.encodage) as flux:
            for numero, ligne in enumerate(flux, start=1):
                if not ligne.strip():
                    continue
                try:
                    objet = json.loads(ligne)
                except (ValueError, RecursionError) as exc:
                    if len(table.anomalies) < MAX_ANOMALIES:
                        table.anomalies.append(f"ligne {numero} : JSON invalide ({exc})"[:200])
                    continue
                yield objet
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{table.fichier.name} : non décodable en {options.encodage}") from exc


def en_dictionnaire(enregistrement: Any) -> dict[str, Any]:
    """Un objet reste tel quel ; une valeur isolée devient {"valeur": ...}."""
    return enregistrement if isinstance(enregistrement, dict) else {"valeur": enregistrement}


def schema_json(source: Callable[[], Iterator[Any]], plafond: int) -> tuple[list[str], list[str]]:
    """Premier passage : clés brutes (ordre d'apparition) et types déclarés."""
    types: dict[str, set[str]] = {}
    for rang, brut in enumerate(source()):
        if rang >= plafond:
            break
        for cle, valeur in en_dictionnaire(brut).items():
            vus = types.setdefault(str(cle), set())
            if valeur is not None:
                vus.add(type(valeur).__name__ if not isinstance(valeur, (dict, list)) else "str")
    return list(types), [type_json(v) for v in types.values()]


def charger_json(conn: sqlite3.Connection, table: Table, options: Options) -> None:
    """Charge un .json ou un .jsonl (deux passages : schéma, puis insertion)."""
    if table.format == "json":
        donnees = enregistrements_json(table)
        source: Callable[[], Iterator[Any]] = lambda: iter(donnees)
    else:
        source = lambda: enregistrements_jsonl(Table(table.nom, table.fichier, "jsonl"), options)
    cles, table.types = schema_json(source, options.plafond)
    if not cles:
        raise ErreurEntree(f"{table.fichier.name} : aucun enregistrement")
    table.colonnes = noms_colonnes(cles)
    creer_table(conn, table.nom, table.colonnes, table.types)
    insertion = f"INSERT INTO {ident(table.nom)} VALUES ({', '.join('?' * len(cles))})"
    flux = enregistrements_jsonl(table, options) if table.format == "jsonl" else source()
    conn.executemany(insertion, lignes_json(flux, cles, table, options.plafond))


def lignes_json(flux: Iterator[Any], cles: list[str], table: Table, plafond: int
                ) -> Iterator[tuple[Any, ...]]:
    """Tuples à insérer, dans l'ordre des colonnes ; marque la troncature."""
    for brut in flux:
        if table.lignes >= plafond:
            table.tronquee = True
            return
        objet = {str(k): v for k, v in en_dictionnaire(brut).items()}
        table.lignes += 1
        yield tuple(cellule_json(objet.get(cle)) for cle in cles)


# --------------------------------------------------------------------------- moteur sqlite3


def charger_tables(fichiers: list[Path], options: Options) -> tuple[sqlite3.Connection, list[Table]]:
    """Crée la base en mémoire et y charge chaque fichier."""
    conn = sqlite3.connect(":memory:")
    if hasattr(conn, "setlimit"):
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, LONGUEUR_MAX_SQLITE)
    pris: set[str] = {TABLE_BRUTE.lower()}
    tables = []
    for fichier in fichiers:
        table = Table(nom_de_table(fichier, pris), fichier, EXTENSIONS[fichier.suffix.lower()])
        if table.format in ("csv", "tsv"):
            charger_csv(conn, table, options)
        else:
            charger_json(conn, table, options)
        tables.append(table)
    conn.commit()
    return conn, tables


def premier_mot(requete: str) -> str:
    """Premier mot-clé de la requête, commentaires -- et /* */ sautés."""
    texte = requete
    while True:
        texte = texte.lstrip()
        if texte.startswith("--"):
            texte = texte.partition("\n")[2]
        elif texte.startswith("/*"):
            texte = texte.partition("*/")[2]
        else:
            break
    m = re.match(r"[A-Za-z]+", texte)
    return m.group(0).upper() if m else ""


def verifier_lecture_seule(requete: str) -> None:
    """Refuse toute requête qui ne commence pas par SELECT ou WITH."""
    mot = premier_mot(requete)
    if mot not in MOTS_LECTURE:
        raise RequeteRefusee(f"requête refusée : seules les lectures ({' / '.join(MOTS_LECTURE)}) "
                           f"sont acceptées, reçu « {mot or requete.strip()[:20]} »")


def autoriser_lecture(action: int, *_: Any) -> int:
    """Autorisateur sqlite3 : lecture permise, tout le reste refusé."""
    return sqlite3.SQLITE_OK if action in ACTIONS_LECTURE else sqlite3.SQLITE_DENY


def executer_sqlite(conn: sqlite3.Connection, requete: str, options: Options) -> Resultat:
    """Exécute la requête sous autorisateur et délai ; compte toutes les lignes."""
    limite = time.monotonic() + options.delai
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(autoriser_lecture)
    conn.set_progress_handler(lambda: int(time.monotonic() > limite), 10_000)
    try:
        curseur = conn.execute(requete)
        colonnes = [d[0] for d in curseur.description or []]
        return consommer(colonnes, iter(curseur), options.max_lignes)
    except sqlite3.Error as exc:
        if time.monotonic() > limite:
            raise RequeteRefusee(f"requête interrompue après {options.delai} s") from exc
        if "not authorized" in str(exc):
            raise RequeteRefusee(f"requête refusée : action autre que lecture ({exc})") from exc
        raise ErreurEntree(f"requête rejetée par sqlite3 : {exc}") from exc


def consommer(colonnes: list[str], lignes: Iterator[tuple[Any, ...]], max_lignes: int) -> Resultat:
    """Garde les premières lignes (affichage et comparaison) et compte le reste."""
    garde = max(max_lignes, MAX_LIGNES_COMPAREES)
    gardees: list[tuple[Any, ...]] = []
    total = 0
    for ligne in lignes:
        if total < garde:
            gardees.append(tuple(ligne))
        total += 1
    return Resultat(colonnes, gardees, total)


# --------------------------------------------------------------------------- moteur duckdb


def litteral(texte: str) -> str:
    """Chaîne SQL entre apostrophes."""
    return "'" + texte.replace("'", "''") + "'"


def source_duckdb(table: Table, options: Options) -> str:
    """Fonction de lecture duckdb du fichier de la table."""
    chemin = litteral(str(table.fichier))
    if table.format == "json":
        return f"read_json_auto({chemin})"
    if table.format == "jsonl":
        return f"read_json({chemin}, format='newline_delimited', ignore_errors=true)"
    reglages = [f"header={'false' if options.sans_entete else 'true'}", "null_padding=true"]
    if options.separateur or table.format == "tsv":
        reglages.append(f"delim={litteral(options.separateur or chr(9))}")
    if options.encodage.lower() not in ("utf-8", "utf-8-sig", "utf8"):
        reglages.append(f"encoding={litteral(options.encodage)}")
    return f"read_csv({chemin}, {', '.join(reglages)})"


def charger_duckdb(tables: list[Table], options: Options) -> tuple[Any, dict[str, str]]:
    """duckdb lit les fichiers, matérialise les tables, puis coupe tout accès fichier."""
    assert duckdb is not None
    conn = duckdb.connect(":memory:", config={"autoinstall_known_extensions": False,
                                              "temp_directory": ""})
    echecs: dict[str, str] = {}
    for table in tables:
        try:
            conn.execute(f"CREATE TABLE {ident(table.nom)} AS SELECT * FROM "
                         f"{source_duckdb(table, options)}")
        except duckdb.Error as exc:
            echecs[table.nom] = str(exc).splitlines()[0]
    conn.execute("SET enable_external_access = false")
    conn.execute("SET lock_configuration = true")
    return conn, echecs


def executer_duckdb(conn: Any, requete: str, options: Options) -> Resultat:
    """Exécute une seule instruction SELECT dans duckdb, sous délai."""
    assert duckdb is not None
    instructions = conn.extract_statements(requete)
    if len(instructions) != 1 or instructions[0].type != duckdb.StatementType.SELECT:
        raise ErreurEntree("requête refusée par duckdb : une seule instruction de lecture permise")
    minuterie = threading.Timer(options.delai, conn.interrupt)
    minuterie.start()
    try:
        curseur = conn.execute(requete)
        colonnes = [d[0] for d in curseur.description or []]
        lots = iter(lambda: curseur.fetchmany(1000), [])
        return consommer(colonnes, (ligne for lot in lots for ligne in lot), options.max_lignes)
    except duckdb.Error as exc:
        raise ErreurEntree(f"requête rejetée par duckdb : {str(exc).splitlines()[0]}") from exc
    finally:
        minuterie.cancel()


# --------------------------------------------------------------------------- comparaison


def normaliser_cellule(valeur: Any) -> Any:
    """Forme comparable entre moteurs (nombres en flottant à 12 chiffres, reste en texte)."""
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        return float(valeur)
    if isinstance(valeur, (int, float)) or type(valeur).__name__ == "Decimal":
        nombre = float(valeur)
        return float(f"{nombre:.12g}") if math.isfinite(nombre) else str(nombre)
    if isinstance(valeur, (bytes, bytearray, memoryview)):
        return bytes(valeur).hex()
    return str(valeur)


def multiensemble(resultat: Resultat) -> list[tuple[Any, ...]]:
    """Lignes normalisées triées (l'ordre n'entre pas dans la comparaison)."""
    return sorted((tuple(normaliser_cellule(v) for v in ligne) for ligne in resultat.lignes),
                  key=repr)


def comparer(reference: Resultat, autre: Resultat, tables: list[Table]) -> dict[str, Any]:
    """Compare deux résultats : nombre de lignes, largeur, contenu (si comparable)."""
    if any(t.tronquee for t in tables):
        return {"statut": "non comparable", "detail": "table tronquée par le plafond stdlib"}
    if reference.total != autre.total or len(reference.colonnes) != len(autre.colonnes):
        return {"statut": "divergent", "detail": f"duckdb : {reference.total} ligne(s) × "
                f"{len(reference.colonnes)} colonne(s) ; stdlib : {autre.total} × "
                f"{len(autre.colonnes)}"}
    if reference.total > MAX_LIGNES_COMPAREES:
        return {"statut": "non comparable", "detail": f"plus de {MAX_LIGNES_COMPAREES} lignes : "
                "seuls les nombres de lignes ont été comparés (égaux)"}
    a, b = multiensemble(reference), multiensemble(autre)
    if a == b:
        return {"statut": "concordant", "detail": f"{reference.total} ligne(s) identiques"}
    premiere = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
    return {"statut": "divergent", "detail": "contenus différents (après tri)",
            "exemple": {"duckdb": list(a[premiere]), "stdlib": list(b[premiere])}}


# --------------------------------------------------------------------------- orchestration


def valeur_json(valeur: Any) -> Any:
    """Cellule sérialisable en JSON strict."""
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return str(valeur)
    if isinstance(valeur, (bytes, bytearray, memoryview)):
        return "x'" + bytes(valeur).hex()[:200] + "'"
    if valeur is None or isinstance(valeur, (bool, int, float, str)):
        return valeur
    return str(valeur)


def decrire_table(table: Table, types_duckdb: dict[str, list[str]]) -> dict[str, Any]:
    """Description JSON d'une table chargée."""
    description = {"nom": table.nom, "fichier": table.fichier.name, "format": table.format,
                   "lignes_chargees": table.lignes, "tronquee": table.tronquee,
                   "colonnes": [{"nom": c, "type": t} for c, t in zip(table.colonnes, table.types)],
                   "anomalies": table.anomalies}
    if table.nom in types_duckdb:
        description["types_duckdb"] = types_duckdb[table.nom]
    return description


def types_des_tables_duckdb(conn: Any, tables: list[Table], echecs: dict[str, str]
                            ) -> dict[str, list[str]]:
    """Types inférés par duckdb pour chaque table qu'il a lue."""
    return {t.nom: [f"{r[0]}:{r[1]}" for r in conn.execute(f"DESCRIBE {ident(t.nom)}").fetchall()]
            for t in tables if t.nom not in echecs}


def interroger(fichiers: list[Path], requete: str | None, options: Options, moteur: str
               ) -> dict[str, Any]:
    """Charge les fichiers, exécute la requête sur le(s) moteur(s), compare."""
    conn, tables = charger_tables(fichiers, options)
    utiliser_duckdb = duckdb is not None and moteur in ("auto", "duckdb")
    conn_d, echecs = charger_duckdb(tables, options) if utiliser_duckdb else (None, {})
    types_d = types_des_tables_duckdb(conn_d, tables, echecs) if conn_d is not None else {}
    rapport: dict[str, Any] = {"moteur": "duckdb" if conn_d is not None else "stdlib",
                               "tables": [decrire_table(t, types_d) for t in tables],
                               "echecs_duckdb": echecs, "resultat": None,
                               "controle_croise": {"effectue": False}}
    if requete is None:
        return rapport
    verifier_lecture_seule(requete)
    principal, nom_moteur, rapport["controle_croise"] = executer_moteurs(
        conn, (conn_d, echecs), requete, options, tables)
    rapport["moteur"] = nom_moteur
    rapport["resultat"] = {
        "moteur": nom_moteur, "colonnes": principal.colonnes, "nombre_total": principal.total,
        "lignes": [[valeur_json(v) for v in ligne] for ligne in principal.lignes[:options.max_lignes]],
        "lignes_omises": max(0, principal.total - options.max_lignes),
    }
    return rapport


def executer_moteurs(conn: sqlite3.Connection, duck: tuple[Any, dict[str, str]], requete: str,
                     options: Options, tables: list[Table]) -> tuple[Resultat, str, dict[str, Any]]:
    """sqlite3 puis duckdb ; rend (résultat principal, moteur, contrôle croisé)."""
    conn_d, echecs = duck
    erreur_stdlib = ""
    try:
        stdlib: Resultat | None = executer_sqlite(conn, requete, options)
    except RequeteRefusee:
        raise
    except ErreurEntree as exc:
        if conn_d is None or echecs:
            raise
        stdlib, erreur_stdlib = None, str(exc)
    if conn_d is None or echecs:
        detail = {"statut": "non comparable", "detail": "duckdb n'a pas pu lire : "
                  + ", ".join(echecs)} if echecs else {}
        assert stdlib is not None
        return stdlib, "stdlib", {"effectue": False, **detail}
    try:
        resultat_d = executer_duckdb(conn_d, requete, options)
    except ErreurEntree as exc:
        if stdlib is None:
            raise ErreurEntree(f"{erreur_stdlib} ; {exc}") from exc
        return stdlib, "stdlib", {"effectue": True, "statut": "divergent", "detail": str(exc)}
    if stdlib is None:
        return resultat_d, "duckdb", {"effectue": True, "statut": "divergent",
                                      "detail": f"stdlib : {erreur_stdlib}"}
    return resultat_d, "duckdb", {"effectue": True, **comparer(resultat_d, stdlib, tables)}


def code_de_sortie(rapport: dict[str, Any]) -> int:
    """1 si anomalie de données, troncature ou désaccord entre moteurs ; sinon 0."""
    tables = rapport["tables"]
    if any(t["anomalies"] or t["tronquee"] for t in tables):
        return 1
    return 1 if rapport["controle_croise"].get("statut") == "divergent" else 0


# --------------------------------------------------------------------------- interface


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans le docstring."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            contrat[courant] = ""
        elif courant is not None:
            contrat[courant] = (contrat[courant] + " " + ligne.strip()).strip()
    return contrat


def separateur_argument(texte: str) -> str:
    """Convertit --separateur (\\t ou tab acceptés) ; un seul caractère."""
    valeur = {"\\t": "\t", "tab": "\t", "tabulation": "\t"}.get(texte.lower(), texte)
    if len(valeur) != 1:
        raise argparse.ArgumentTypeError("un seul caractère attendu (ou \\t)")
    return valeur


def entier_positif(texte: str) -> int:
    """Entier strictement positif pour argparse."""
    try:
        valeur = int(texte)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"entier attendu : {texte}") from exc
    if valeur <= 0:
        raise argparse.ArgumentTypeError(f"entier > 0 attendu : {texte}")
    return valeur


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    p = argparse.ArgumentParser(
        description="Exécute une requête SQL en lecture seule sur des fichiers CSV/TSV/JSON/JSON "
                    "Lines, chacun devenant une table, sans base de données.",
        epilog=f"Exemple : python {RACINE.name}/interroger_donnees_sql.py ventes.csv clients.jsonl "
               "--requete \"SELECT region, SUM(montant) FROM ventes GROUP BY region\" --json")
    p.add_argument("fichiers", nargs="+", help="fichiers .csv .tsv .tab .json .jsonl .ndjson ou "
                                              "dossiers qui en contiennent")
    p.add_argument("--requete", help="requête SELECT ou WITH (sans elle : schéma des tables)")
    p.add_argument("--max-lignes", type=entier_positif, default=MAX_LIGNES_RESULTAT,
                   help=f"lignes du résultat affichées (défaut {MAX_LIGNES_RESULTAT})")
    p.add_argument("--plafond-chargement", type=entier_positif, default=PLAFOND_CHARGEMENT,
                   help=f"lignes chargées au plus par table en stdlib (défaut {PLAFOND_CHARGEMENT})")
    p.add_argument("--separateur", type=separateur_argument, help="séparateur CSV imposé")
    p.add_argument("--sans-entete", action="store_true", help="la première ligne CSV est une donnée")
    p.add_argument("--encodage", default="utf-8-sig", help="encodage des CSV et JSON Lines")
    p.add_argument("--delai", type=float, default=DELAI_REQUETE, help="délai maximal de la requête (s)")
    p.add_argument("--moteur", choices=("auto", "stdlib", "duckdb"), default="auto",
                   help="auto : duckdb s'il est importable, contrôlé par sqlite3")
    p.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    p.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return p


def resoudre(chemins: list[str], racine: str | None) -> list[Path]:
    """Chemins relatifs résolus depuis --racine si elle est donnée."""
    base = Path(racine) if racine else None
    return [base / c if base is not None and not Path(c).is_absolute() else Path(c) for c in chemins]


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Sortie lisible."""
    for t in rapport["tables"]:
        colonnes = ", ".join(f"{c['nom']} {c['type']}" for c in t["colonnes"][:12])
        print(f"table {t['nom']} ({t['fichier']}) : {t['lignes_chargees']} ligne(s)"
              f"{' TRONQUÉE' if t['tronquee'] else ''} — {colonnes}")
        for anomalie in t["anomalies"][:5]:
            print(f"  ! {anomalie}")
    resultat = rapport["resultat"]
    if resultat is None:
        return
    print(" | ".join(resultat["colonnes"]))
    for ligne in resultat["lignes"]:
        print(" | ".join("NULL" if v is None else str(v)[:40] for v in ligne))
    print(f"({resultat['nombre_total']} ligne(s), moteur {resultat['moteur']}"
          f"{', ' + str(resultat['lignes_omises']) + ' omise(s)' if resultat['lignes_omises'] else ''})")
    croise = rapport["controle_croise"]
    if croise.get("statut"):
        print(f"contrôle croisé : {croise['statut']} — {croise.get('detail', '')}")


def neutraliser_sortie() -> None:
    """Lecteur parti (tube fermé) : stdout est redirigé vers le néant, sans trace d'erreur."""
    nul = os.open(os.devnull, os.O_WRONLY)
    os.dup2(nul, sys.stdout.fileno())


def afficher_refus(message: str, code: int, en_json: bool) -> int:
    """Message sur stderr, objet JSON minimal (dénominateur 0) si --json ; rend le code."""
    print(message, file=sys.stderr)
    if en_json:
        print(json.dumps({"denominateur": 0, "examines": [], "erreur": message}, ensure_ascii=False))
    return code


def afficher_resultat(sortie: dict[str, Any], en_json: bool) -> None:
    """Écrit le rapport (JSON ou lisible) ; un tube fermé par le lecteur n'est pas une erreur."""
    try:
        if en_json:
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        else:
            afficher_humain(sortie)
        sys.stdout.flush()
    except BrokenPipeError:
        neutraliser_sortie()


def assembler_sortie(fichiers: list[Path], requete: str | None, rapport: dict[str, Any]
                     ) -> dict[str, Any]:
    """Objet de sortie : dénominateur en tête, puis le rapport et le contrat."""
    examines = [f.name for f in fichiers]
    return {"denominateur": len(fichiers), "examines": examines[:MAX_EXAMINES],
            "examines_tronques": len(examines) > MAX_EXAMINES, "requete": requete, **rapport,
            "contrat": extraire_contrat(__doc__ or "")}


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    options = Options(args.separateur, args.sans_entete, args.encodage, args.plafond_chargement,
                      args.max_lignes, args.delai)
    if args.moteur == "duckdb" and duckdb is None:
        return afficher_refus("duckdb absent : --moteur duckdb impossible (installez duckdb ou "
                              "prenez --moteur stdlib).", 2, args.json)
    try:
        fichiers = lister_fichiers(resoudre(args.fichiers, args.racine))
        if not fichiers:
            return afficher_refus("dénominateur nul : rien à examiner (aucun fichier .csv, .tsv, "
                                  ".json ou .jsonl).", 3, args.json)
        if duckdb is None and args.moteur == "auto":
            print(MESSAGE_DUCKDB_ABSENT, file=sys.stderr)
        rapport = interroger(fichiers, args.requete, options, args.moteur)
    except ErreurEntree as exc:
        return afficher_refus(f"entrée invalide : {exc}", 2, args.json)
    code = code_de_sortie(rapport)
    if code == 1:
        print("défaut signalé : anomalie de données, table tronquée ou désaccord entre moteurs.",
              file=sys.stderr)
    afficher_resultat(assembler_sortie(fichiers, args.requete, rapport), args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
