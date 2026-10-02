"""Inspecter une base SQLite sans jamais la modifier, même par effet de bord.
Mesuré dans cette session : `sqlite3.connect('absent.db')` sur un chemin inexistant crée un fichier
de 0 octet, et ouvrir une base en mode WAL avec `?mode=ro` (sans immutable) a créé `-shm` et `-wal`
à côté d'elle, restés après fermeture ; seule l'URI `mode=ro&immutable=1` n'a rien créé.

QUESTION
    Que contient cette base SQLite (tables, colonnes, index, clés étrangères, lignes), et est-elle
    saine ?
MESURE
    En-tête lu en octets (signature, taille de page, mode de journal 1=rollback/2=WAL, encodage,
    nombre de pages déclaré, version de SQLite qui l'a écrite) ; présence et taille des fichiers
    annexes -wal, -shm, -journal (simple stat, jamais ouverts) ; puis ouverture en lecture
    stricte par l'URI file:...?mode=ro&immutable=1 : PRAGMA table_list, table_xinfo, index_list,
    index_info, foreign_key_list, comptage des lignes, PRAGMA quick_check (integrity_check avec
    --integrite-complete), foreign_key_check table par table (orphelins et clés mal définies),
    page_size, page_count, freelist_count, journal_mode, encoding, auto_vacuum, user_version.
    Une clé étrangère est « indexée » si le plan (explain query plan) de la recherche côté enfant,
    sous la collation de la clé parente, commence par SEARCH ; sinon repli structurel (colonnes
    en tête d'un index). Statut : defauts (intégrité, orphelins, clé mal définie, délai),
    incomplet (-wal non vide ou -journal présent, non vus), vide (0 octet), pas_sqlite,
    illisible, sinon sain. Si sqlite-utils est importable, il relit la même connexion (tables,
    comptes, colonnes, clés étrangères) et les écarts sont rapportés.
HYPOTHÈSES
    Personne n'écrit dans la base pendant l'inspection (immutable=1 désactive tout verrou) ; les
    fichiers donnés ou trouvés dans un dossier (.db, .sqlite, .sqlite3, .db3) sont ceux à juger.
LIMITES
    immutable=1 ignore le contenu d'un -wal non reporté et ne rejoue pas un -journal chaud :
    l'outil le signale (statut incomplet) mais ne le voit pas. SQLite ne porte aucune somme de
    contrôle des valeurs : une altération qui garde la structure intacte passe. Les tables
    virtuelles dont le module manque ne sont pas comptées. Le comptage parcourt chaque table
    (--sans-comptes l'évite ; --delai le borne).
CONTRE-EXEMPLES
    Constaté : dans altere.db (copie d'une base saine), 4 octets d'un titre de livre remplacés
    par quatre « X » — integrity_check rend ok et l'outil conclut « sain », alors que la ligne
    1235 a changé.
    Constaté aussi : stale.db, base WAL dont l'écrivain est mort sans fermer (-wal de 12392
    octets) — l'outil rend « incomplet » mais décrit zéro table, car la table t créée après le
    passage en WAL n'existe que dans le -wal (« no such table: t » en immutable).
INVOCATION
    {outil} {dossier}/base.db --json
DOMAINE
    Fichiers SQLite 3 de production (applications, caches, bases embarquées) inspectés avant
    sauvegarde, migration ou livraison, sans droit d'écriture et sans arrêter l'application.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import sqlite_utils
except ImportError:
    sqlite_utils = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
URI_LECTURE = "?mode=ro&immutable=1"
SIGNATURE = b"SQLite format 3\x00"
EXTENSIONS = (".db", ".sqlite", ".sqlite3", ".db3")
ANNEXES = ("-wal", "-shm", "-journal")
PRAGMA = "PRAGMA"
PLAN_RECHERCHE = "SEARCH"
PRAGMAS_SIMPLES = ("page_size", "page_count", "freelist_count", "journal_mode", "encoding",
                   "auto_vacuum", "user_version", "application_id", "schema_version")
ENCODAGES_ENTETE = MappingProxyType({1: "UTF-8", 2: "UTF-16le", 3: "UTF-16be"})
MAX_FICHIERS = 1000
MAX_EXAMINES = 200
MAX_TABLES = 500
MAX_EXEMPLES = 50
DELAI_PAR_BASE = 60.0
MESSAGE_SQLITE_UTILS_ABSENT = ("sqlite-utils absent : inspection par sqlite3 (stdlib) seule, "
                               "sans contrôle croisé.")


class ErreurEntree(Exception):
    """Entrée invalide : chemin introuvable (code 2)."""


# --------------------------------------------------------------------------- fichiers


def lister_bases(chemins: list[Path], recursif: bool) -> list[Path]:
    """Fichiers donnés tels quels ; dossiers développés en fichiers .db/.sqlite/.sqlite3/.db3."""
    bases: list[Path] = []
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            motif = chemin.rglob("*") if recursif else chemin.iterdir()
            bases += sorted(p for p in motif if p.is_file() and p.suffix.lower() in EXTENSIONS)
        else:
            bases.append(chemin)
    return bases[:MAX_FICHIERS]


def lire_entete(chemin: Path) -> dict[str, Any]:
    """Décode les 100 octets d'en-tête ; « valide » est faux si la signature manque."""
    with chemin.open("rb") as flux:
        octets = flux.read(100)
    if len(octets) < 100 or not octets.startswith(SIGNATURE):
        return {"valide": False, "debut_hex": octets[:16].hex()}

    def entier(debut: int, taille: int) -> int:
        return int.from_bytes(octets[debut:debut + taille], "big")

    taille_page = entier(16, 2)
    return {
        "valide": True,
        "taille_page": 65536 if taille_page == 1 else taille_page,
        "mode_journal": {1: "rollback", 2: "wal"}.get(octets[18], f"inconnu({octets[18]})"),
        "compteur_modifications": entier(24, 4),
        "pages_declarees": entier(28, 4),
        "pages_declarees_fiables": entier(92, 4) == entier(24, 4),
        "pages_libres_declarees": entier(36, 4),
        "encodage": ENCODAGES_ENTETE.get(entier(56, 4), "inconnu"),
        "user_version": entier(60, 4),
        "version_sqlite_ecrivain": entier(96, 4),
    }


def fichiers_annexes(chemin: Path) -> dict[str, int]:
    """Taille des fichiers -wal, -shm, -journal présents (stat seulement)."""
    annexes = {}
    for suffixe in ANNEXES:
        voisin = chemin.with_name(chemin.name + suffixe)
        if voisin.is_file():
            annexes[suffixe] = voisin.stat().st_size
    return annexes


def avertissements_annexes(entete: dict[str, Any], annexes: dict[str, int],
                           taille: int) -> list[str]:
    """Ce que l'ouverture immutable ne verra pas, et les incohérences de taille."""
    avis = []
    if annexes.get("-wal"):
        avis.append(f"-wal de {annexes['-wal']} octets : son contenu non reporté est IGNORÉ "
                    "(immutable=1) ; schéma et comptes peuvent être périmés")
    if "-journal" in annexes:
        avis.append("-journal présent : transaction peut-être interrompue, non rejouée en lecture "
                    "immutable ; un défaut d'intégrité peut en découler")
    if entete["valide"] and entete["pages_declarees_fiables"]:
        attendu = entete["pages_declarees"] * entete["taille_page"]
        if attendu != taille:
            avis.append(f"taille du fichier {taille} octets ≠ pages déclarées × taille de page "
                        f"({attendu})")
    return avis


# --------------------------------------------------------------------------- connexion


def ouvrir_lecture_seule(chemin: Path) -> sqlite3.Connection:
    """Connexion par URI mode=ro&immutable=1 : aucun fichier créé ni modifié."""
    conn = sqlite3.connect(chemin.resolve().as_uri() + URI_LECTURE, uri=True)
    conn.execute(f"{PRAGMA} query_only = ON")
    return conn


def pragma(conn: sqlite3.Connection, nom: str, argument: str | None = None) -> list[tuple[Any, ...]]:
    """Exécute un PRAGMA (argument entre guillemets doubles si c'est un nom)."""
    suite = f"({ident(argument)})" if argument is not None else ""
    return conn.execute(f"{PRAGMA} {nom}{suite}").fetchall()


def ident(nom: str) -> str:
    """Identifiant SQL entre guillemets doubles."""
    return '"' + nom.replace('"', '""') + '"'


def pragmas_simples(conn: sqlite3.Connection) -> dict[str, Any]:
    """Valeurs scalaires des PRAGMA de configuration."""
    valeurs = {}
    for nom in PRAGMAS_SIMPLES:
        try:
            lignes = pragma(conn, nom)
            valeurs[nom] = lignes[0][0] if lignes else None
        except sqlite3.Error as exc:
            valeurs[nom] = f"erreur : {exc}"
    return valeurs


# --------------------------------------------------------------------------- schéma


def liste_tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tables du schéma principal (PRAGMA table_list, repli sur sqlite_schema)."""
    try:
        lignes = pragma(conn, "table_list")
        return [{"nom": l[1], "type": l[2], "sans_rowid": bool(l[4]), "strict": bool(l[5])}
                for l in lignes if l[0] == "main" and l[2] in ("table", "virtual", "shadow")
                and l[1] != "sqlite_schema"]
    except sqlite3.Error:
        lignes = conn.execute("SELECT name, sql FROM sqlite_schema WHERE type='table'").fetchall()
        return [{"nom": n, "type": "virtual" if (s or "").upper().startswith("CREATE VIRTUAL")
                 else "table", "sans_rowid": "WITHOUT ROWID" in (s or "").upper(), "strict": False}
                for n, s in lignes]


def colonnes_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Colonnes (PRAGMA table_xinfo)."""
    return [{"nom": l[1], "type": l[2], "non_nul": bool(l[3]), "defaut": l[4], "cle_primaire": l[5],
             "cachee": l[6]} for l in pragma(conn, "table_xinfo", table)]


def index_table(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Index (PRAGMA index_list + index_info)."""
    resultat = []
    for l in pragma(conn, "index_list", table):
        colonnes = [c[2] for c in pragma(conn, "index_info", l[1])]
        resultat.append({"nom": l[1], "unique": bool(l[2]), "origine": l[3],
                         "partiel": bool(l[4]), "colonnes": colonnes})
    return resultat


def cles_etrangeres(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Clés étrangères regroupées par identifiant (colonnes composées)."""
    groupes: dict[int, dict[str, Any]] = {}
    for l in pragma(conn, "foreign_key_list", table):
        groupe = groupes.setdefault(l[0], {"table_parente": l[2], "colonnes": [],
                                           "colonnes_parentes": [], "on_delete": l[6]})
        groupe["colonnes"].append(l[3])
        groupe["colonnes_parentes"].append(l[4])
    return list(groupes.values())


def alias_rowid(table: dict[str, Any]) -> str | None:
    """Colonne INTEGER PRIMARY KEY d'une table rowid (alias du rowid), sinon None."""
    cles = [c for c in table["colonnes"] if c["cle_primaire"]]
    if table["sans_rowid"] or len(cles) != 1 or (cles[0]["type"] or "").upper() != "INTEGER":
        return None
    return cles[0]["nom"]


def cle_couverte(table: dict[str, Any], colonnes: list[str]) -> bool:
    """Repli structurel : un index (ou l'alias du rowid) a ces colonnes en tête."""
    cibles = {c.lower() for c in colonnes}
    alias = alias_rowid(table)
    if alias is not None and cibles == {alias.lower()}:
        return True
    return any({c.lower() for c in idx["colonnes"][:len(cibles)] if c} == cibles
               for idx in table["index"])


def colonnes_parentes(conn: sqlite3.Connection, cle: dict[str, Any]) -> list[str]:
    """Colonnes de la clé parente ; référence implicite = clé primaire de la parente."""
    if all(c is not None for c in cle["colonnes_parentes"]):
        return cle["colonnes_parentes"]
    primaires = sorted((l[5], l[1]) for l in pragma(conn, "table_xinfo", cle["table_parente"]) if l[5])
    return [nom for _, nom in primaires] or ["rowid"]


def collations_parentes(conn: sqlite3.Connection, parente: str, colonnes: list[str]) -> list[str]:
    """Collation de chaque colonne parente, lue dans l'index unique qui porte la clé (BINARY sinon)."""
    voulues = {c.lower() for c in colonnes}
    for idx in pragma(conn, "index_list", parente):
        cles = [(l[2], l[4]) for l in pragma(conn, "index_xinfo", idx[1]) if l[5] and l[2]]
        if idx[2] and {n.lower() for n, _ in cles} == voulues:
            par_nom = {n.lower(): coll for n, coll in cles}
            return [par_nom[c.lower()] for c in colonnes]
    return ["BINARY"] * len(colonnes)


def plan_cherche_par_index(conn: sqlite3.Connection, table: str, colonnes: list[str],
                           collations: list[str]) -> bool:
    """EXPLAIN QUERY PLAN de la recherche que fait SQLite côté enfant : SEARCH = index utilisable."""
    conditions = " AND ".join(f"{ident(c)} = ? COLLATE {ident(k)}" for c, k in zip(colonnes, collations))
    plan = conn.execute(f"EXPLAIN QUERY PLAN SELECT 1 FROM {ident(table)} WHERE {conditions}",
                        [None] * len(colonnes)).fetchall()
    return any(str(ligne[-1]).startswith(PLAN_RECHERCHE) for ligne in plan)


def juger_couverture(conn: sqlite3.Connection, table: dict[str, Any], cle: dict[str, Any]) -> None:
    """Pose cle["indexee"] par le plan de requête (collation parente), repli structurel."""
    try:
        parentes = colonnes_parentes(conn, cle)
        collations = collations_parentes(conn, cle["table_parente"], parentes)
        if len(collations) != len(cle["colonnes"]):
            collations = ["BINARY"] * len(cle["colonnes"])
        cle["indexee"] = plan_cherche_par_index(conn, table["nom"], cle["colonnes"], collations)
        cle["methode"] = "plan de requête"
    except sqlite3.Error as exc:
        cle["indexee"] = cle_couverte(table, cle["colonnes"])
        cle["methode"] = f"structure (plan indisponible : {exc})"


def compter_lignes(conn: sqlite3.Connection, table: str) -> tuple[int | None, str | None]:
    """count(*) d'une table, ou (None, raison)."""
    try:
        return conn.execute(f"SELECT count(*) FROM {ident(table)}").fetchone()[0], None
    except sqlite3.Error as exc:
        return None, str(exc)


def decrire_table(conn: sqlite3.Connection, info: dict[str, Any], compter: bool
                  ) -> dict[str, Any]:
    """Colonnes, index, clés étrangères (couvertes ou non) et nombre de lignes d'une table."""
    table = dict(info)
    try:
        table["colonnes"] = colonnes_table(conn, info["nom"])
        table["index"] = index_table(conn, info["nom"])
        table["cles_etrangeres"] = cles_etrangeres(conn, info["nom"])
    except sqlite3.Error as exc:
        table.update(colonnes=[], index=[], cles_etrangeres=[], erreur=str(exc))
    for cle in table["cles_etrangeres"]:
        juger_couverture(conn, table, cle)
    table["lignes"], raison = compter_lignes(conn, info["nom"]) if compter else (None, None)
    if raison:
        table["erreur_comptage"] = raison
    return table


# --------------------------------------------------------------------------- santé


def controle_integrite(conn: sqlite3.Connection, complet: bool) -> dict[str, Any]:
    """PRAGMA quick_check ou integrity_check (au plus 50 messages)."""
    nom = "integrity_check" if complet else "quick_check"
    try:
        messages = [l[0] for l in conn.execute(f"{PRAGMA} {nom}({MAX_EXEMPLES})").fetchall()]
    except sqlite3.Error as exc:
        return {"controle": nom, "ok": False, "messages": [f"erreur : {exc}"]}
    return {"controle": nom, "ok": messages == ["ok"], "messages": messages}


def violations_cles(conn: sqlite3.Connection, tables: list[dict[str, Any]]) -> dict[str, Any]:
    """PRAGMA foreign_key_check table par table : orphelins, et clés mal définies (mismatch)."""
    nombre, exemples, erreurs = 0, [], []
    for table in (t for t in tables if t.get("cles_etrangeres")):
        try:
            lignes = pragma(conn, "foreign_key_check", table["nom"])
        except sqlite3.Error as exc:
            erreurs.append(f"{table['nom']} : {exc}")
            continue
        nombre += len(lignes)
        exemples += [{"table": l[0], "rowid": l[1], "parente": l[2]} for l in lignes]
    return {"nombre": nombre, "exemples": exemples[:MAX_EXEMPLES], "cles_mal_definies": erreurs}


def cles_sans_index(tables: list[dict[str, Any]]) -> list[str]:
    """Clés étrangères dont les colonnes enfants ne sont couvertes par aucun index."""
    return [f"{t['nom']}({', '.join(c['colonnes'])}) → {c['table_parente']}"
            for t in tables for c in t.get("cles_etrangeres", []) if not c["indexee"]]


# --------------------------------------------------------------------------- sqlite-utils


def controle_sqlite_utils(conn: sqlite3.Connection, tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Relit tables, comptes, colonnes et clés étrangères avec sqlite-utils, et compare."""
    assert sqlite_utils is not None
    db = sqlite_utils.Database(conn, recursive_triggers=False, execute_plugins=False)
    ecarts = []
    noms_su = set(db.table_names())
    for table in (t for t in tables if t["type"] == "table"):
        if table["nom"] not in noms_su:
            ecarts.append({"table": table["nom"], "champ": "presence"})
            continue
        vue = db[table["nom"]]
        autres = {
            "colonnes": [c.name for c in vue.columns],
            "lignes": vue.count if table["lignes"] is not None else None,
            "cles_etrangeres": sorted((fk.column, fk.other_table) for fk in vue.foreign_keys),
        }
        miennes = {
            "colonnes": [c["nom"] for c in table["colonnes"] if not c["cachee"]],
            "lignes": table["lignes"],
            "cles_etrangeres": sorted((c, k["table_parente"]) for k in table["cles_etrangeres"]
                                      for c in k["colonnes"]),
        }
        ecarts += [{"table": table["nom"], "champ": champ, "stdlib": miennes[champ],
                    "sqlite_utils": autres[champ]} for champ in autres if autres[champ] != miennes[champ]]
    return {"effectue": True, "bibliotheque": version_bibliotheque(), "ecarts": ecarts}


def version_bibliotheque() -> str:
    """Nom et version de sqlite-utils."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        return f"sqlite-utils {version('sqlite-utils')}"
    except (ImportError, PackageNotFoundError):
        return "sqlite-utils"


# --------------------------------------------------------------------------- inspection


def inspecter_contenu(conn: sqlite3.Connection, options: argparse.Namespace) -> dict[str, Any]:
    """Schéma, intégrité, clés étrangères, PRAGMA d'une base ouverte."""
    limite = time.monotonic() + options.delai
    conn.set_progress_handler(lambda: int(time.monotonic() > limite), 10_000)
    objets = conn.execute("SELECT type, name, tbl_name FROM sqlite_schema "
                          "WHERE type IN ('view', 'trigger')").fetchall()
    infos = sorted(liste_tables(conn), key=lambda t: (t["type"] != "table", t["nom"].lower()))
    tables = [decrire_table(conn, info, not options.sans_comptes) for info in infos[:options.max_tables]]
    resultat = {
        "pragmas": pragmas_simples(conn),
        "integrite": controle_integrite(conn, options.integrite_complete),
        "cles_etrangeres_violees": violations_cles(conn, tables),
        "tables": tables, "tables_omises": max(0, len(infos) - options.max_tables),
        "vues": [n for t, n, _ in objets if t == "view"],
        "declencheurs": [f"{n} ({tb})" for t, n, tb in objets if t == "trigger"],
        "cles_etrangeres_sans_index": cles_sans_index(tables),
    }
    if time.monotonic() > limite:
        resultat["delai_depasse"] = True
    return resultat


def statut_base(fiche: dict[str, Any]) -> str:
    """defauts (intégrité, clés étrangères, délai), incomplet (-wal ou -journal non vus), ou sain."""
    cles = fiche["cles_etrangeres_violees"]
    if (not fiche["integrite"]["ok"] or cles["nombre"] or cles["cles_mal_definies"]
            or fiche.get("delai_depasse")):
        return "defauts"
    annexes = fiche["fichiers_annexes"]
    return "incomplet" if annexes.get("-wal") or "-journal" in annexes else "sain"


def examiner_fichier(chemin: Path) -> dict[str, Any]:
    """Taille, en-tête et fichiers annexes ; statut posé si ce n'est pas une base lisible."""
    fiche: dict[str, Any] = {"fichier": chemin.name, "chemin": str(chemin)}
    try:
        fiche["taille_octets"] = chemin.stat().st_size
        fiche["entete"] = lire_entete(chemin)
    except OSError as exc:
        return {**fiche, "statut": "illisible", "erreur": str(exc)}
    if fiche["taille_octets"] == 0:
        return {**fiche, "statut": "vide",
                "erreur": "0 octet : SQLite l'ouvrirait comme une base vide (souvent créée par "
                          "erreur par un connect() sur un mauvais chemin)"}
    if not fiche["entete"]["valide"]:
        return {**fiche, "statut": "pas_sqlite",
                "erreur": "signature « SQLite format 3 » absente des 16 premiers octets"}
    fiche["fichiers_annexes"] = fichiers_annexes(chemin)
    fiche["avertissements"] = avertissements_annexes(fiche["entete"], fiche["fichiers_annexes"],
                                                     fiche["taille_octets"])
    return fiche


def inspecter_base(chemin: Path, options: argparse.Namespace, utiliser_lib: bool) -> dict[str, Any]:
    """Fiche complète d'un fichier : en-tête, annexes, puis contenu si c'est bien du SQLite."""
    fiche = examiner_fichier(chemin)
    if "statut" in fiche:
        return fiche
    try:
        conn = ouvrir_lecture_seule(chemin)
    except sqlite3.Error as exc:
        return {**fiche, "statut": "illisible", "erreur": str(exc)}
    try:
        fiche.update(inspecter_contenu(conn, options))
        fiche["statut"] = statut_base(fiche)
        if utiliser_lib:
            fiche["controle_croise"] = controle_sqlite_utils(conn, fiche["tables"])
    except sqlite3.Error as exc:
        fiche.update(statut="illisible", erreur=str(exc))
    finally:
        conn.close()
    return fiche


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
        description="Décrit une base SQLite (tables, colonnes, index, clés étrangères, lignes) et "
                    "juge sa santé, en lecture strictement seule (mode=ro&immutable=1).",
        epilog=f"Exemple : python {RACINE.name}/inspecter_sqlite.py app.db --json")
    p.add_argument("chemins", nargs="+", help="fichiers SQLite, ou dossiers (.db .sqlite .sqlite3 .db3)")
    p.add_argument("--recursif", action="store_true", help="parcourir les sous-dossiers")
    p.add_argument("--integrite-complete", action="store_true",
                   help="PRAGMA integrity_check au lieu de quick_check (plus lent)")
    p.add_argument("--sans-comptes", action="store_true", help="ne pas compter les lignes")
    p.add_argument("--max-tables", type=entier_positif, default=MAX_TABLES,
                   help=f"tables décrites au plus par base (défaut {MAX_TABLES})")
    p.add_argument("--delai", type=float, default=DELAI_PAR_BASE,
                   help=f"délai maximal par base en secondes (défaut {DELAI_PAR_BASE:g})")
    p.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                   help="auto : contrôle croisé par sqlite-utils s'il est importable")
    p.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    p.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return p


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Sortie lisible."""
    for base in sortie["bases"]:
        print(f"{base['fichier']} : {base['statut'].upper()} ({base.get('taille_octets', '?')} octets)")
        if "erreur" in base:
            print(f"  {base['erreur']}")
        for avis in base.get("avertissements", []):
            print(f"  ! {avis}")
        if "integrite" not in base:
            continue
        p = base["pragmas"]
        print(f"  pages {p['page_count']} × {p['page_size']} o, libres {p['freelist_count']}, "
              f"journal (en-tête) {base['entete']['mode_journal']}, encodage {p['encoding']}")
        print(f"  {base['integrite']['controle']} : {'ok' if base['integrite']['ok'] else base['integrite']['messages'][:3]}"
              f" ; clés étrangères violées : {base['cles_etrangeres_violees']['nombre']}")
        for erreur in base["cles_etrangeres_violees"]["cles_mal_definies"]:
            print(f"  ! clé étrangère mal définie : {erreur}")
        for t in base["tables"]:
            print(f"  - {t['nom']} [{t['type']}] {t['lignes'] if t['lignes'] is not None else '?'} ligne(s), "
                  f"{len(t['colonnes'])} colonne(s), {len(t['index'])} index")
        if base["tables_omises"]:
            print(f"  ({base['tables_omises']} table(s) omise(s) : --max-tables ; leurs clés "
                  "étrangères ne sont pas vérifiées)")
        for cle in base["cles_etrangeres_sans_index"]:
            print(f"  ! clé étrangère sans index : {cle}")
        croise = base.get("controle_croise")
        if croise:
            print(f"  contrôle croisé ({croise['bibliotheque']}) : {len(croise['ecarts'])} écart(s)")


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
            print(json.dumps(sortie, ensure_ascii=False, indent=2, default=str))
        else:
            afficher_humain(sortie)
        sys.stdout.flush()
    except BrokenPipeError:
        neutraliser_sortie()


def assembler_sortie(bases: list[Path], fiches: list[dict[str, Any]], utiliser_lib: bool
                     ) -> dict[str, Any]:
    """Objet de sortie : dénominateur en tête, fiches, bases non saines, contrat."""
    examines = [str(b) for b in bases]
    return {"denominateur": len(bases), "examines": examines[:MAX_EXAMINES],
            "examines_tronques": len(examines) > MAX_EXAMINES,
            "moteur": "sqlite-utils" if utiliser_lib else "stdlib",
            "ouverture": f"file:<chemin>{URI_LECTURE}", "bases": fiches,
            "malsaines": [f["fichier"] for f in fiches if f["statut"] != "sain"],
            "contrat": extraire_contrat(__doc__ or "")}


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = Path(args.racine) if args.racine else None
    chemins = [base / c if base is not None and not Path(c).is_absolute() else Path(c)
               for c in args.chemins]
    try:
        bases = lister_bases(chemins, args.recursif)
    except ErreurEntree as exc:
        return afficher_refus(f"entrée invalide : {exc}", 2, args.json)
    if not bases:
        return afficher_refus("dénominateur nul : rien à examiner (aucun fichier .db, .sqlite, "
                              ".sqlite3 ou .db3).", 3, args.json)
    utiliser_lib = sqlite_utils is not None and args.moteur == "auto"
    if sqlite_utils is None and args.moteur == "auto":
        print(MESSAGE_SQLITE_UTILS_ABSENT, file=sys.stderr)
    sortie = assembler_sortie(bases, [inspecter_base(c, args, utiliser_lib) for c in bases],
                              utiliser_lib)
    ecarts = sum(len(f.get("controle_croise", {}).get("ecarts", [])) for f in sortie["bases"])
    code = 1 if sortie["malsaines"] or ecarts else 0
    if code:
        print(f"défaut : {len(sortie['malsaines'])} base(s) non saine(s), {ecarts} écart(s) "
              "sqlite-utils.", file=sys.stderr)
    afficher_resultat(sortie, args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
