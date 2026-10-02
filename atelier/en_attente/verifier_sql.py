"""Vérifie des requêtes SQL : syntaxe (moteur SQLite réel), tables lues et écrites, instructions dangereuses.

Un agent qui « relit » du SQL laisse passer ce qu'un vrai analyseur refuse, et le seul test
de la stdlib est trompeur : mesuré dans cette session, `sqlite3.complete_statement("selec *
from;")` rend True (il ne juge que la fin de l'instruction). Cet outil fait compiler chaque
instruction par SQLite (explain sur une base :memory:, sans jamais l'exécuter) ; mesuré sur
un corpus de 25 instructions : sqlglot 30.21.0, en dialecte sqlite, accepte 3 instructions
que SQLite 3.53.1 refuse (extract, grant, truncate) ; sur les 22 que sqlglot analyse, ses
tables coïncident avec celles de l'analyseur lexical 21 fois (l'écart : un déclencheur).

QUESTION
    Cette requête SQL est-elle valide, que touche-t-elle, et est-elle dangereuse ?
MESURE
    Analyseur lexical (chaînes, identifiants entre guillemets, commentaires, chaînes
    dollar) qui découpe les instructions aux points-virgules de premier niveau
    (corps de déclencheurs begin ... end compris) ; tables lues (from, join,
    references, using) et écrites (insert, update, delete, merge, create, alter,
    drop, truncate, grant ou revoke ... on), hors noms de CTE ; risques : drop,
    truncate, alter, delete ou update sans where de premier niveau ou avec un where
    toujours vrai, select *, grant, revoke. Syntaxe : sqlite3.complete_statement
    puis explain dans une base :memory: (attach et detach interdits par un
    autorisateur), après création des tables de --schema ; les create analysés sont
    rejoués dans cette base pour les instructions suivantes. Avec sqlglot, chaque
    instruction est aussi analysée dans le dialecte choisi et les tables trouvées
    par l'arbre syntaxique sont confrontées à celles de l'analyseur lexical ; avec
    sqlparse, le découpage en instructions est confronté.
HYPOTHÈSES
    Le dialecte par défaut est SQLite ; une instruction dont la seule erreur est
    « no such table » ou « no such column » est syntaxiquement valide (schéma
    inconnu) ; un where de premier niveau limite réellement les lignes touchées.
LIMITES
    Sans sqlglot, un autre dialecte n'est vérifié que lexicalement (le verdict
    SQLite est alors indicatif) ; l'analyse des tables est lexicale : SQL dynamique,
    procédures stockées, synonymes et vues ne sont pas résolus ; un where qui
    filtre peu (where id > 0) n'est pas jugé dangereux ; delete ou update dans le
    corps d'un déclencheur ne sont pas jugés.
CONTRE-EXEMPLES
    Constatés dans cette session : « delete from commandes where id > 0 » sort en
    code 0, sans risque, alors qu'elle efface toutes les lignes d'identifiant
    positif, c'est-à-dire en pratique toute la table ; « select a::int from t »
    (PostgreSQL) est déclarée invalide (unrecognized token ":") si l'on oublie
    --dialecte postgres : juste pour SQLite, faux pour la base visée.
INVOCATION
    {outil} --sql "select id, nom from clients where id = 1" --schema-sql "create table clients(id integer primary key, nom text)" --json
DOMAINE
    Scripts SQL de migration, requêtes d'application et extraits de revue de code,
    en SQLite surtout, et dans les dialectes de sqlglot quand il est installé.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import sqlglot
    from sqlglot import exp as sqlglot_exp
except ImportError:
    sqlglot = None
    sqlglot_exp = None

try:
    import sqlparse
except ImportError:
    sqlparse = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)
MOT_EXPLAIN = "EXPLAIN"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

OCTETS_MAX_DEFAUT = 20 * 1024 * 1024
FICHIERS_MAX = 5000
EXAMINES_MAX = 50
PAS_PROGRES = 1000
BUDGET_PROGRES = 2000
NIVEAUX = ("faible", "eleve", "critique")

MOTIF_JETON = re.compile(r"""
 (?P<espace>\s+)
|(?P<commentaire>--[^\n]*|/\*.*?\*/|\#[^\n]*)
|(?P<chaine>[eEnNxXbB]?'(?:[^']|'')*')
|(?P<dollar>\$(?P<etiquette>[A-Za-z_]\w*)?\$.*?\$(?P=etiquette)?\$)
|(?P<ident_cite>"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]\n]*\])
|(?P<nombre>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
|(?P<parametre>\?\d*|[:@][A-Za-z_]\w*|\$\d+)
|(?P<mot>[^\W\d]\w*)
|(?P<operateur>\|\||::|<=|>=|<>|!=|==|<<|>>|->>|->|[-+*/%<>=~!&|^])
|(?P<ponct>[(),;.])
""", re.VERBOSE | re.DOTALL)

FIN_DE_LISTE = frozenset({
    "WHERE", "GROUP", "ORDER", "LIMIT", "OFFSET", "HAVING", "UNION", "INTERSECT", "EXCEPT", "WINDOW",
    "ON", "USING", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "NATURAL", "OUTER", "SET",
    "VALUES", "RETURNING", "SELECT", "FROM", "INTO", "AS", "FOR", "LATERAL", "ONLY", "WITH", "DEFAULT",
    "DO", "WHEN", "THEN", "ELSE", "END", "BEGIN", "IF", "NOT", "EXISTS", "TABLE", "AND", "OR", "OF",
})
MODIFICATEURS = frozenset({"TEMP", "TEMPORARY", "UNIQUE", "VIRTUAL", "OR", "REPLACE", "IF", "NOT", "EXISTS",
                           "ONLY", "LATERAL", "GLOBAL", "LOCAL", "UNLOGGED", "MATERIALIZED", "RECURSIVE"})
OBJETS_TABLES = frozenset({"TABLE", "VIEW"})
ERREURS_SYNTAXE = ("syntax error", "incomplete input", "unrecognized token", "near \"")
ERREURS_SCHEMA = ("no such table", "no such column", "no such index", "no such view", "no such trigger",
                  "already exists", "no such function", "no such module", "no such collation")
GRAVITE = MappingProxyType({niveau: rang for rang, niveau in enumerate(NIVEAUX)})


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Jeton:
    """Un jeton lexical : nature, texte, ligne, position dans la source."""

    nature: str
    texte: str
    ligne: int
    debut: int
    fin: int

    @property
    def haut(self) -> str:
        return self.texte.upper() if self.nature == "mot" else ""


@dataclass
class Instruction:
    """Une instruction SQL découpée, avec son origine et ses jetons significatifs."""

    source: str
    ligne: int
    texte: str
    jetons: list[Jeton]
    erreurs_lexicales: list[str] = field(default_factory=list)


def decouper_jetons(sql: str) -> tuple[list[Jeton], list[tuple[int, int, str]]]:
    """Analyse lexicale ; un élément non terminé devient une erreur (ligne, position, message)."""
    jetons, erreurs, position, ligne = [], [], 0, 1
    while position < len(sql):
        if sql.startswith("/*", position) and sql.find("*/", position + 2) < 0:
            erreurs.append((ligne, position, f"ligne {ligne} : commentaire /* non terminé"))
            break
        trouve = MOTIF_JETON.match(sql, position)
        if trouve is None:
            erreurs.append((ligne, position, f"ligne {ligne} : {nature_inachevee(sql[position:position + 2])} non terminé(e)"
                            if sql[position] in "'\"`[$" else f"ligne {ligne} : caractère inattendu « {sql[position]} »"))
            if sql[position] in "'\"`[":
                break
            position += 1
            continue
        nature = trouve.lastgroup if trouve.lastgroup != "etiquette" else "dollar"
        if nature not in ("espace", "commentaire"):
            jetons.append(Jeton(nature, trouve.group(0), ligne, trouve.start(), trouve.end()))
        ligne += trouve.group(0).count("\n")
        position = trouve.end()
    return jetons, erreurs


def nature_inachevee(debut: str) -> str:
    """Nom de l'élément lexical resté ouvert."""
    return {"'": "chaîne", '"': "identifiant entre guillemets", "`": "identifiant entre accents graves",
            "[": "identifiant entre crochets"}.get(debut[:1], "chaîne dollar")


def est_debut_declencheur(jetons: Sequence[Jeton]) -> bool:
    """Vrai si l'instruction commence par CREATE [TEMP] TRIGGER."""
    mots = [j.haut for j in jetons[:4]]
    return bool(mots) and mots[0] == "CREATE" and "TRIGGER" in mots[1:4]


def decouper_instructions(sql: str, source: str) -> list[Instruction]:
    """Découpe aux « ; » de premier niveau, sans couper un corps BEGIN ... END de déclencheur."""
    jetons, erreurs = decouper_jetons(sql)
    instructions, courant, profondeur_bloc, cas = [], [], 0, 0
    for jeton in jetons:
        if jeton.texte == ";" and profondeur_bloc == 0:
            ajouter_instruction(instructions, courant, sql, source)
            courant = []
            continue
        courant.append(jeton)
        if est_debut_declencheur(courant):
            if jeton.haut == "CASE":
                cas += 1
            elif jeton.haut == "BEGIN":
                profondeur_bloc += 1
            elif jeton.haut == "END":
                if cas:
                    cas -= 1
                else:
                    profondeur_bloc = max(profondeur_bloc - 1, 0)
    ajouter_instruction(instructions, courant, sql, source)
    if erreurs:
        messages = [message for _, _, message in erreurs]
        if courant:
            instructions[-1].erreurs_lexicales.extend(messages)
        else:
            ligne, debut, _ = erreurs[0]
            instructions.append(Instruction(source, ligne, sql[debut:debut + 200], [], messages))
    return instructions


def ajouter_instruction(instructions: list[Instruction], jetons: list[Jeton], sql: str, source: str) -> None:
    """Ajoute une instruction non vide, avec son texte source exact."""
    if jetons:
        instructions.append(Instruction(source, jetons[0].ligne, sql[jetons[0].debut:jetons[-1].fin], list(jetons)))


def profondeurs(jetons: Sequence[Jeton]) -> list[int]:
    """Profondeur de parenthèses de chaque jeton."""
    resultat, profondeur = [], 0
    for jeton in jetons:
        if jeton.texte == ")":
            profondeur -= 1
        resultat.append(profondeur)
        if jeton.texte == "(":
            profondeur += 1
    return resultat


def sauter_parentheses(jetons: Sequence[Jeton], i: int) -> int:
    """Indice qui suit la parenthèse fermante associée à jetons[i] == '('."""
    profondeur = 0
    for k in range(i, len(jetons)):
        profondeur += {"(": 1, ")": -1}.get(jetons[k].texte, 0)
        if profondeur == 0:
            return k + 1
    return len(jetons)


def passer_ctes(jetons: Sequence[Jeton], i: int, ctes: set[str]) -> int:
    """À partir d'un WITH : note les noms de CTE et rend l'indice qui suit leur liste."""
    i += 1 + (i + 1 < len(jetons) and jetons[i + 1].haut == "RECURSIVE")
    while i < len(jetons):
        ctes.add(nom_simple(jetons[i]).lower())
        i += 1
        if i < len(jetons) and jetons[i].texte == "(":
            i = sauter_parentheses(jetons, i)
        while i < len(jetons) and jetons[i].texte != "(":
            i += 1
        i = sauter_parentheses(jetons, i)
        if i < len(jetons) and jetons[i].texte == ",":
            i += 1
            continue
        return i
    return i


def verbe_et_cte(jetons: Sequence[Jeton]) -> tuple[str, set[str], int]:
    """Verbe principal (après WITH, EXPLAIN, parenthèses), noms de toutes les CTE, indice du verbe."""
    i, ctes = 0, set()
    while i < len(jetons) and (jetons[i].texte == "(" or jetons[i].haut in (MOT_EXPLAIN, "QUERY", "PLAN")):
        i += 1
    if i < len(jetons) and jetons[i].haut == "WITH":
        i = passer_ctes(jetons, i, ctes)
    for k, jeton in enumerate(jetons):
        if jeton.haut == "WITH" and k + 1 < len(jetons) and jetons[k + 1].nature in ("mot", "ident_cite"):
            passer_ctes(jetons, k, ctes)
    verbe = jetons[i].haut if i < len(jetons) else ""
    return verbe, ctes, i


def nom_simple(jeton: Jeton) -> str:
    """Identifiant sans guillemets, crochets ni accents graves."""
    texte = jeton.texte
    if jeton.nature == "ident_cite":
        return texte[1:-1].replace('""', '"').replace("``", "`")
    return texte


def lire_reference(jetons: Sequence[Jeton], i: int) -> tuple[str | None, int]:
    """Lit un nom qualifié a.b.c à partir de i ; None si ce n'est pas un nom de table."""
    while i < len(jetons) and jetons[i].haut in MODIFICATEURS:
        i += 1
    if i >= len(jetons) or jetons[i].nature not in ("mot", "ident_cite") or jetons[i].haut in FIN_DE_LISTE:
        return None, i
    morceaux = [nom_simple(jetons[i])]
    i += 1
    while i + 1 < len(jetons) and jetons[i].texte == "." and jetons[i + 1].nature in ("mot", "ident_cite"):
        morceaux.append(nom_simple(jetons[i + 1]))
        i += 2
    return ".".join(morceaux), i


def sauter_alias(jetons: Sequence[Jeton], i: int) -> int:
    """Passe un alias éventuel (AS x, ou x nu qui n'est pas un mot réservé)."""
    if i < len(jetons) and jetons[i].haut == "AS":
        i += 1
    if i < len(jetons) and jetons[i].nature in ("mot", "ident_cite") and jetons[i].haut not in FIN_DE_LISTE:
        i += 1
    if i < len(jetons) and jetons[i].texte == "(":
        i = sauter_parentheses(jetons, i)
    return i


def lire_liste_lue(jetons: Sequence[Jeton], i: int, lues: list[str]) -> None:
    """Après FROM ou JOIN : références lues, séparées par des virgules au même niveau."""
    while i < len(jetons):
        if jetons[i].texte == "(":
            i = sauter_alias(jetons, sauter_parentheses(jetons, i))
        else:
            nom, suivant = lire_reference(jetons, i)
            if nom is None:
                return
            if suivant < len(jetons) and jetons[suivant].texte == "(":
                suivant = sauter_parentheses(jetons, suivant)
            else:
                lues.append(nom)
            i = sauter_alias(jetons, suivant)
        if i < len(jetons) and jetons[i].texte == ",":
            i += 1
            continue
        return


def precedent_mot(jetons: Sequence[Jeton], i: int, ignorer: frozenset[str] = frozenset()) -> str:
    """Mot significatif qui précède jetons[i], en sautant les modificateurs donnés."""
    k = i - 1
    while k >= 0 and jetons[k].haut in ignorer:
        k -= 1
    return jetons[k].haut if k >= 0 else ""


def cible_ecrite(jetons: Sequence[Jeton], i: int, ecrites: list[str]) -> None:
    """Après TABLE/VIEW, INTO, UPDATE, TRUNCATE, ON : table(s) écrite(s), liste à virgules comprise."""
    while True:
        nom, i = lire_reference(jetons, i)
        if nom is None:
            return
        ecrites.append(nom)
        if i < len(jetons) and jetons[i].texte == ",":
            i += 1
            continue
        return


def from_de_table(jetons: Sequence[Jeton], i: int, niveaux: Sequence[int]) -> bool:
    """FROM de requête (et non EXTRACT(x FROM y) ni IS DISTINCT FROM) : un SELECT ou DELETE le précède au même niveau."""
    if i >= 2 and jetons[i - 1].haut == "DISTINCT" and jetons[i - 2].haut in ("IS", "NOT"):
        return False
    for k in range(i - 1, -1, -1):
        if niveaux[k] < niveaux[i]:
            return False
        if niveaux[k] == niveaux[i] and jetons[k].haut in ("SELECT", "DELETE"):
            return True
    return False


def tables_lexicales(jetons: Sequence[Jeton], ctes: set[str]) -> tuple[list[str], list[str]]:
    """Tables lues et écrites selon les mots-clés qui les introduisent."""
    lues: list[str] = []
    ecrites: list[str] = []
    niveaux = profondeurs(jetons)
    for i, jeton in enumerate(jetons):
        mot, avant = jeton.haut, precedent_mot(jetons, i, MODIFICATEURS)
        if (mot == "FROM" and avant == "DELETE") or (mot == "DELETE" and i + 1 < len(jetons)
                                                     and jetons[i + 1].nature in ("mot", "ident_cite")
                                                     and jetons[i + 1].haut not in ("FROM", "OR")):
            cible_ecrite(jetons, i + 1, ecrites)
        elif mot == "FROM" and from_de_table(jetons, i, niveaux):
            lire_liste_lue(jetons, i + 1, lues)
        elif mot == "REFERENCES":
            lues.extend(nom for nom in (lire_reference(jetons, i + 1)[0],) if nom)
        elif mot == "JOIN" or (mot == "USING" and i + 1 < len(jetons) and jetons[i + 1].texte != "("):
            lire_liste_lue(jetons, i + 1, lues)
        elif mot == "INTO" or (mot == "UPDATE" and avant not in ("DO", "FOR", "ON", "BEFORE", "AFTER", "OF")):
            cible_ecrite(jetons, i + 1, ecrites)
        elif mot in OBJETS_TABLES and avant in ("CREATE", "DROP", "ALTER", "TRUNCATE"):
            cible_ecrite(jetons, i + 1, ecrites)
        elif mot == "TRUNCATE" and i + 1 < len(jetons) and jetons[i + 1].haut != "TABLE":
            cible_ecrite(jetons, i + 1, ecrites)
        elif mot == "ON" and (on_de_schema(jetons, i) or premier_mot(jetons) in ("GRANT", "REVOKE")):
            cible_ecrite(jetons, i + 1, ecrites)
    return filtrer_cte(lues, ctes), filtrer_cte(ecrites, ctes)


def premier_mot(jetons: Sequence[Jeton]) -> str:
    """Premier mot de l'instruction."""
    return jetons[0].haut if jetons else ""


def on_de_schema(jetons: Sequence[Jeton], i: int) -> bool:
    """Vrai si ce ON désigne la table d'un CREATE INDEX ou d'un CREATE TRIGGER (et non une jointure)."""
    return premier_mot(jetons) == "CREATE" and any(j.haut in ("INDEX", "TRIGGER") for j in jetons[1:i]) \
        and not any(j.haut in ("SELECT", "JOIN", "BEGIN", "ON") for j in jetons[:i])


def filtrer_cte(noms: Sequence[str], ctes: set[str]) -> list[str]:
    """Dédoublonne (sans casse) et retire les noms de CTE."""
    vus: dict[str, str] = {}
    for nom in noms:
        if nom.lower() not in ctes:
            vus.setdefault(nom.lower(), nom)
    return list(vus.values())


def clause_where(jetons: Sequence[Jeton], debut: int) -> list[Jeton] | None:
    """Jetons du WHERE de premier niveau après le verbe ; None s'il n'y en a pas."""
    niveaux = profondeurs(jetons)
    for i in range(debut, len(jetons)):
        if jetons[i].haut == "WHERE" and niveaux[i] == niveaux[debut]:
            fin = next((k for k in range(i + 1, len(jetons)) if niveaux[k] == niveaux[debut]
                        and jetons[k].haut in ("RETURNING", "ORDER", "LIMIT")), len(jetons))
            return list(jetons[i + 1:fin])
    return None


def where_toujours_vrai(condition: Sequence[Jeton]) -> bool:
    """WHERE 1, WHERE TRUE, WHERE 1=1, WHERE 'a'='a', ou « OR 1=1 » au premier niveau."""
    textes = [j.texte.upper() for j in condition]
    if textes in (["1"], ["TRUE"]):
        return True
    for k in range(len(condition) - 2):
        a, op, b = condition[k], condition[k + 1], condition[k + 2]
        litteraux = a.nature in ("nombre", "chaine") and b.nature == a.nature and a.texte == b.texte
        if litteraux and op.texte in ("=", "==") and (k == 0 or textes[k - 1] == "OR"):
            return True
    return False


def selects_etoile(jetons: Sequence[Jeton]) -> int:
    """Nombre de SELECT * (ou t.*) dans une liste de sélection, hors EXISTS (SELECT * ...)."""
    niveaux, compte = profondeurs(jetons), 0
    for i, jeton in enumerate(jetons):
        if jeton.texte != "*" or i == 0:
            continue
        avant = jetons[i - 1]
        en_tete = avant.haut in ("SELECT", "DISTINCT", "ALL") or avant.texte in (",", ".")
        k = i - 1
        while k >= 0 and not (jetons[k].haut == "SELECT" and niveaux[k] == niveaux[i]):
            k -= 1
        dans_exists = k >= 2 and jetons[k - 1].texte == "(" and jetons[k - 2].haut == "EXISTS"
        if en_tete and k >= 0 and not dans_exists:
            compte += 1
    return compte


def risques_de(jetons: Sequence[Jeton], verbe: str, indice_verbe: int) -> list[dict[str, str]]:
    """Liste des risques d'une instruction, avec leur niveau."""
    risques = []
    def signaler(niveau: str, code: str, message: str) -> None:
        risques.append({"niveau": niveau, "code": code, "message": message})
    mots = {j.haut for j in jetons}
    if verbe == "DROP":
        signaler("critique", "DROP", "suppression définitive d'un objet de schéma")
    if verbe == "TRUNCATE":
        signaler("critique", "TRUNCATE", "vidage complet de table, sans WHERE possible")
    if verbe == "ALTER":
        destructif = "DROP" in mots
        signaler("critique" if destructif else "eleve", "ALTER",
                 "ALTER ... DROP : suppression de colonne ou de contrainte" if destructif else "modification de schéma")
    if verbe in ("DELETE", "UPDATE"):
        condition = clause_where(jetons, indice_verbe)
        if condition is None:
            signaler("critique", f"{verbe}_SANS_WHERE", f"{verbe} sans WHERE : toutes les lignes sont touchées")
        elif where_toujours_vrai(condition):
            signaler("critique", f"{verbe}_WHERE_VRAI", f"{verbe} avec un WHERE toujours vrai")
    if verbe in ("GRANT", "REVOKE"):
        signaler("eleve", verbe, "modification de privilèges")
    if verbe == "ATTACH" or (verbe == "VACUUM" and "INTO" in mots):
        signaler("eleve", verbe, "accès à un fichier de base hors de la connexion courante")
    etoiles = selects_etoile(jetons)
    if etoiles:
        signaler("faible", "SELECT_ETOILE", f"SELECT * ({etoiles}) : colonnes implicites, fragile aux évolutions du schéma")
    return risques


def ouvrir_base_isolee() -> sqlite3.Connection:
    """Base :memory: dont l'autorisateur interdit ATTACH/DETACH (aucun fichier ne peut être ouvert)."""
    connexion = sqlite3.connect(":memory:")
    def autoriser(action: int, *_: Any) -> int:
        return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK
    connexion.set_authorizer(autoriser)
    return connexion


def borner(connexion: sqlite3.Connection) -> None:
    """Pose un budget neuf d'instructions de la machine virtuelle SQLite pour le prochain appel."""
    compteur = [0]
    def progres() -> int:
        compteur[0] += 1
        return 1 if compteur[0] > BUDGET_PROGRES else 0
    connexion.set_progress_handler(progres, PAS_PROGRES)


def rejouable(instruction: Instruction) -> bool:
    """CREATE TABLE/VIEW/INDEX/TRIGGER sans AS SELECT : sans effet hors de la base :memory:."""
    jetons = instruction.jetons
    if premier_mot(jetons) != "CREATE":
        return False
    objets = {j.haut for j in jetons[1:5]}
    if not objets & {"TABLE", "VIEW", "INDEX", "TRIGGER"}:
        return False
    niveaux = profondeurs(jetons)
    return not ("TABLE" in objets and any(j.haut == "AS" and n == 0 for j, n in zip(jetons, niveaux)))


def verifier_sqlite(connexion: sqlite3.Connection, instruction: Instruction) -> dict[str, str]:
    """Compile l'instruction avec EXPLAIN (jamais exécutée) ; rejoue les CREATE sûrs."""
    if instruction.erreurs_lexicales:
        return {"statut": "invalide", "detail": "; ".join(instruction.erreurs_lexicales), "moteur": "lexical"}
    if not sqlite3.complete_statement(instruction.texte + ";"):
        return {"statut": "invalide", "detail": "instruction incomplète (sqlite3.complete_statement)", "moteur": "sqlite"}
    prefixe = "" if premier_mot(instruction.jetons) == MOT_EXPLAIN else MOT_EXPLAIN + " "
    try:
        borner(connexion)
        connexion.execute(prefixe + instruction.texte)
    except sqlite3.Error as exc:
        message = str(exc)
        if any(marque in message for marque in ERREURS_SCHEMA):
            return {"statut": "schema_inconnu", "detail": message, "moteur": "sqlite EXPLAIN"}
        statut = "invalide" if any(marque in message for marque in ERREURS_SYNTAXE) else "non_verifie"
        return {"statut": statut, "detail": message, "moteur": "sqlite EXPLAIN"}
    if rejouable(instruction):
        try:
            borner(connexion)
            connexion.execute(instruction.texte)
        except sqlite3.Error as exc:
            return {"statut": "valide", "detail": f"compilée ; non rejouée : {exc}", "moteur": "sqlite EXPLAIN"}
    return {"statut": "valide", "detail": "compilée par SQLite", "moteur": "sqlite EXPLAIN"}


def charger_schema(connexion: sqlite3.Connection, sql: str) -> list[str]:
    """Crée dans la base :memory: les objets des CREATE du schéma ; ignore le reste."""
    notes = []
    for instruction in decouper_instructions(sql, "schéma"):
        if not rejouable(instruction):
            notes.append(f"schéma ligne {instruction.ligne} : ignorée (seuls les CREATE sont rejoués)")
            continue
        try:
            borner(connexion)
            connexion.execute(instruction.texte)
        except sqlite3.Error as exc:
            notes.append(f"schéma ligne {instruction.ligne} : {exc}")
    return notes


def tables_sqlglot(arbre: Any) -> tuple[list[str], list[str]]:
    """Tables de l'arbre sqlglot : écrites si hors requête de lecture, lues sinon ; CTE exclues."""
    lecture = (sqlglot_exp.From, sqlglot_exp.Join, sqlglot_exp.Subquery, sqlglot_exp.Select,
               sqlglot_exp.SetOperation, sqlglot_exp.Reference, sqlglot_exp.CTE)
    ctes = {cte.alias.lower() for cte in arbre.find_all(sqlglot_exp.CTE)}
    lues, ecrites = [], []
    for table in arbre.find_all(sqlglot_exp.Table):
        nom = ".".join(p for p in (table.catalog, table.db, table.name) if p)
        if not nom or table.name.lower() in ctes:
            continue
        (lues if table.find_ancestor(*lecture) else ecrites).append(nom)
    return filtrer_cte(lues, set()), filtrer_cte(ecrites, set())


def analyser_sqlglot(instruction: Instruction, dialecte: str) -> dict[str, Any]:
    """Analyse sqlglot dans le dialecte : verdict syntaxique et tables de l'arbre."""
    try:
        arbre = sqlglot.parse_one(instruction.texte, read=dialecte)
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        return {"statut": "invalide", "detail": str(exc).splitlines()[0][:300]}
    if arbre is None or isinstance(arbre, sqlglot_exp.Command):
        return {"statut": "non_verifie", "detail": "instruction non analysée par sqlglot (repli « Command »)"}
    lues, ecrites = tables_sqlglot(arbre)
    return {"statut": "valide", "tables_lues": lues, "tables_ecrites": ecrites}


def ecarts_tables(lexical: dict[str, list[str]], arbre: dict[str, Any]) -> list[str]:
    """Différences entre les tables de l'analyseur lexical et celles de sqlglot."""
    ecarts = []
    for cle in ("tables_lues", "tables_ecrites"):
        a = {n.lower() for n in lexical[cle]}
        b = {n.lower() for n in arbre.get(cle, [])}
        if a != b:
            ecarts.append(f"{cle} : lexical {sorted(a)} / sqlglot {sorted(b)}")
    return ecarts


def analyser_instruction(numero: int, instruction: Instruction, connexion: sqlite3.Connection,
                         args: argparse.Namespace, avec_sqlglot: bool) -> dict[str, Any]:
    """Verbe, tables, risques et syntaxe d'une instruction."""
    verbe, ctes, indice = verbe_et_cte(instruction.jetons)
    lues, ecrites = tables_lexicales(instruction.jetons, ctes)
    resultat: dict[str, Any] = {
        "numero": numero, "source": instruction.source, "ligne": instruction.ligne,
        "texte": " ".join(instruction.texte.split())[:200], "verbe": verbe or "?",
        "tables_lues": lues, "tables_ecrites": ecrites, "ctes": sorted(ctes),
        "risques": risques_de(instruction.jetons, verbe, indice),
        "syntaxe": verifier_sqlite(connexion, instruction),
    }
    if args.dialecte != "sqlite" and resultat["syntaxe"]["moteur"] != "lexical":
        resultat["syntaxe"]["indicatif"] = True
    if avec_sqlglot and not instruction.erreurs_lexicales:
        arbre = analyser_sqlglot(instruction, args.dialecte)
        arbre["ecarts_tables"] = ecarts_tables(resultat, arbre) if arbre["statut"] == "valide" else []
        resultat["sqlglot"] = arbre
    resultat["valide"] = verdict_final(resultat, args.dialecte)
    return resultat


def verdict_final(resultat: dict[str, Any], dialecte: str) -> bool | None:
    """Valide ? SQLite fait foi pour le dialecte sqlite, sqlglot pour les autres ; None si non vérifiable."""
    syntaxe = resultat["syntaxe"]
    if syntaxe["moteur"] == "lexical" or syntaxe["detail"].startswith("instruction incomplète"):
        return False
    if dialecte == "sqlite":
        return {"valide": True, "schema_inconnu": True, "invalide": False}.get(syntaxe["statut"])
    arbre = resultat.get("sqlglot")
    if arbre is None or arbre["statut"] == "non_verifie":
        return None
    return arbre["statut"] == "valide"


def lire_sql(chemin: Path, octets_max: int) -> str:
    """Lit un fichier SQL UTF-8 borné ; refuse binaire et encodage invalide."""
    taille = chemin.stat().st_size
    if taille > octets_max:
        raise ErreurEntree(f"{chemin} pèse {taille} octets, au-delà de --max-octets {octets_max}")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas du SQL")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en UTF-8 (octet {exc.start}) : {exc.reason}") from exc


def sources_sql(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    """(nom, texte) de chaque source : --sql, fichier, ou .sql d'un dossier (récursif, borné)."""
    if args.sql is not None:
        yield "--sql", args.sql
    if args.cible is None:
        return
    chemin = Path(args.cible) if Path(args.cible).is_absolute() or args.racine is None else args.racine / args.cible
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    fichiers = [chemin] if chemin.is_file() else sorted(p for p in chemin.rglob("*.sql") if p.is_file())
    if len(fichiers) > FICHIERS_MAX:
        raise ErreurEntree(f"{len(fichiers)} fichiers .sql sous {chemin}, au-delà de {FICHIERS_MAX}")
    for fichier in fichiers:
        yield str(fichier), lire_sql(fichier, args.max_octets)


def texte_schema(args: argparse.Namespace) -> str | None:
    """Texte du schéma : --schema (fichier) puis --schema-sql (en ligne)."""
    morceaux = []
    if args.schema:
        chemin = Path(args.schema) if Path(args.schema).is_absolute() or args.racine is None else args.racine / args.schema
        if not chemin.is_file():
            raise ErreurEntree(f"--schema : fichier introuvable ou dossier : {chemin}")
        morceaux.append(lire_sql(chemin, args.max_octets))
    if args.schema_sql:
        morceaux.append(args.schema_sql)
    return ";\n".join(morceaux) if morceaux else None


def controle_sqlparse(textes: Sequence[str], nb_instructions: int) -> dict[str, Any]:
    """Compare le nombre d'instructions trouvé par sqlparse au découpage lexical."""
    compte = sum(1 for texte in textes for morceau in sqlparse.split(texte) if morceau.strip().strip(";").strip())
    return {"instructions_sqlparse": compte, "concordant": compte == nb_instructions, "version": sqlparse.__version__}


def resumer(instructions: Sequence[dict[str, Any]], seuil: str) -> dict[str, Any]:
    """Synthèse : tables, risques par niveau, invalides, défaut au sens du seuil."""
    risques = [r for i in instructions for r in i["risques"]]
    retenus = [r for r in risques if GRAVITE[r["niveau"]] >= GRAVITE[seuil]]
    invalides = [i["numero"] for i in instructions if i["valide"] is False]
    return {
        "tables_lues": filtrer_cte([t for i in instructions for t in i["tables_lues"]], set()),
        "tables_ecrites": filtrer_cte([t for i in instructions for t in i["tables_ecrites"]], set()),
        "risques_par_niveau": {n: sum(1 for r in risques if r["niveau"] == n) for n in NIVEAUX},
        "instructions_invalides": invalides,
        "non_verifiees": [i["numero"] for i in instructions if i["valide"] is None],
        "defaut": bool(retenus or invalides), "seuil": seuil,
    }


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait du docstring les sections du contrat de mesure."""
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
        description="Vérifie du SQL : syntaxe compilée par SQLite (EXPLAIN, sans exécution), tables lues et "
                    "écrites, instructions dangereuses. Code 1 si une instruction est invalide ou risquée.",
        epilog='exemple : verifier_sql.py migrations/ --schema schema.sql --json   '
               '| verifier_sql.py --sql "DELETE FROM commandes" --seuil critique',
    )
    parseur.add_argument("cible", nargs="?", metavar="CIBLE", help="fichier .sql, ou dossier (ses .sql, récursivement)")
    parseur.add_argument("--sql", metavar="TEXTE", help="SQL en ligne (une ou plusieurs instructions)")
    parseur.add_argument("--schema", metavar="FICHIER", help="fichier de CREATE à jouer d'abord dans la base :memory:")
    parseur.add_argument("--schema-sql", metavar="TEXTE", help="CREATE en ligne à jouer d'abord")
    parseur.add_argument("--dialecte", default="sqlite",
                         help="dialecte (sqlite par défaut ; postgres, mysql, tsql... vérifiés avec sqlglot seulement)")
    parseur.add_argument("--seuil", choices=NIVEAUX, default="faible",
                         help="niveau de risque minimal qui rend le code 1 (défaut faible : tout risque)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale par fichier")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : sqlglot (arbre syntaxique, dialectes) et sqlparse (découpage) s'ils sont installés")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def moteurs_actifs(moteur: str) -> list[str]:
    """Bibliothèques utilisées ; une ligne sur stderr pour celles qui manquent."""
    if moteur == "stdlib":
        return []
    presentes = [("sqlglot", sqlglot), ("sqlparse", sqlparse)]
    manquants = [nom for nom, module in presentes if module is None]
    if manquants:
        print(f"verifier_sql : {' et '.join(manquants)} absent(s) — repli stdlib (analyseur lexical + "
              f"sqlite3.complete_statement + EXPLAIN :memory:)", file=sys.stderr)
    if sqlglot is not None:
        logging.getLogger("sqlglot").setLevel(logging.ERROR)
    return [nom for nom, module in presentes if module is not None]


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Une ligne par instruction, puis la synthèse."""
    for i in rapport["instructions"]:
        etat = {True: "valide", False: "INVALIDE", None: "non vérifiée"}[i["valide"]]
        if i["syntaxe"]["statut"] == "schema_inconnu":
            etat = "syntaxe valide, schéma inconnu"
        detail = i["syntaxe"]["detail"][:120]
        if i["syntaxe"].get("indicatif") and "sqlglot" in i:
            arbre = i["sqlglot"]
            detail = (f"{rapport['dialecte']} selon sqlglot : {arbre['statut']}"
                      f"{' — ' + arbre['detail'][:80] if arbre.get('detail') else ''} ; SQLite, indicatif : {detail}")
        elif i["syntaxe"].get("indicatif"):
            detail = f"{rapport['dialecte']} non vérifiable sans sqlglot ; SQLite, indicatif : {detail}"
        print(f"[{i['numero']}] {i['source']}:{i['ligne']} {i['verbe']} — {etat} ({detail})")
        if i["tables_lues"] or i["tables_ecrites"]:
            print(f"     lues : {', '.join(i['tables_lues']) or '—'} ; écrites : {', '.join(i['tables_ecrites']) or '—'}")
        for r in i["risques"]:
            print(f"     RISQUE {r['niveau']} {r['code']} : {r['message']}")
    resume = rapport["resume"]
    print(f"{rapport['denominateur']} instruction(s) ; invalides : {len(resume['instructions_invalides'])} ; "
          f"risques : {resume['risques_par_niveau']} ; tables écrites : {', '.join(resume['tables_ecrites']) or '—'}")


def verifier_dialecte(dialecte: str) -> None:
    """Refuse un dialecte que sqlglot ne connaît pas."""
    try:
        sqlglot.Dialect.get_or_raise(dialecte)
    except ValueError as exc:
        raise ErreurEntree(f"--dialecte : {exc}") from exc


def evaluer(args: argparse.Namespace, actifs: Sequence[str]) -> dict[str, Any]:
    """Découpe, prépare la base isolée, analyse chaque instruction, résume."""
    sources = list(sources_sql(args))
    instructions = [ins for nom, texte in sources for ins in decouper_instructions(texte, nom)]
    connexion = ouvrir_base_isolee()
    try:
        schema = texte_schema(args)
        notes = charger_schema(connexion, schema) if schema else []
        analyses = [analyser_instruction(n, ins, connexion, args, "sqlglot" in actifs)
                    for n, ins in enumerate(instructions, start=1)]
    finally:
        connexion.close()
    rapport = {
        "outil": "verifier_sql", "moteur": "+".join(actifs) or "stdlib", "dialecte": args.dialecte,
        "version_sqlite": sqlite3.sqlite_version, "version_sqlglot": getattr(sqlglot, "__version__", None),
        "denominateur": len(analyses), "unite_denominateur": "instructions",
        "examines": [f"{a['source']}:{a['ligne']} {a['texte'][:60]}" for a in analyses[:EXAMINES_MAX]],
        "examines_tronques": len(analyses) > EXAMINES_MAX, "sources": [nom for nom, _ in sources],
        "notes_schema": notes, "instructions": analyses, "resume": resumer(analyses, args.seuil),
    }
    if "sqlparse" in actifs:
        rapport["controle_sqlparse"] = controle_sqlparse([texte for _, texte in sources], len(analyses))
    return rapport | {"contrat": extraire_contrat(__doc__ or "")}


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : vérifie, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    if args.cible is None and args.sql is None:
        print("verifier_sql : donnez un fichier .sql, un dossier ou --sql", file=sys.stderr)
        return CODE_USAGE
    actifs = moteurs_actifs(args.moteur)
    try:
        if args.max_octets < 1:
            raise ErreurEntree("--max-octets doit être positif")
        if "sqlglot" in actifs:
            verifier_dialecte(args.dialecte)
        rapport = evaluer(args, actifs)
    except (ErreurEntree, OSError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"verifier_sql : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "verifier_sql", "moteur": "+".join(actifs) or "stdlib",
                              "denominateur": 0, "examines": [], "refus": str(exc)},
                             ensure_ascii=False))
        return code
    if rapport["denominateur"] == 0:
        print("verifier_sql : dénominateur nul : aucune instruction SQL trouvée, rien à examiner", file=sys.stderr)
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    elif rapport["denominateur"]:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        return CODE_RIEN
    return CODE_DEFAUT if rapport["resume"]["defaut"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
