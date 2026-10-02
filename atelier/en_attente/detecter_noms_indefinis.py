"""Un LLM invente des noms (json.parse, une variable jamais définie) et laisse
des imports morts ; l'interpréteur ne le dit qu'à l'exécution de la ligne.
Mesuré dans cette session :
sur outils/ de ce dépôt (210 fichiers écrits par des agents), l'outil relève
http.client.urlparse (contrat_mcp.py:64, AttributeError à l'appel) et trois
ast.Str retirés en 3.14, qu'aucun des 149 constats de pyflakes 4.0.1 ne contient ;
sur la stdlib 3.14.7 (1057 fichiers), 1943 constats communs, 88 propres à
l'outil, 56 propres à pyflakes.

QUESTION
    Ce code utilise-t-il des noms qui n'existent pas, ou importe-t-il pour rien ?
MESURE
    Sans exécuter le code analysé : ast pour l'ordre des liaisons et des
    lectures, symtable (CPython) pour classer chaque nom par portée (locale,
    libre, globale), builtins de l'interpréteur pour le reste. Catégories :
    nom_indefini (lecture d'un nom lié nulle part, ni builtin ; aussi dans
    __all__ et dans les annotations, chaînes comprises), nom_peut_etre_indefini
    (même cas en présence d'un import *), import_inutilise, variable_inutilisee
    (affectation simple, with … as, except … as, jamais lue dans la fonction ni
    par une fonction imbriquée), redefinition_inutilisee (import, def ou class
    remplacé avant usage, hors branches exclusives et @overload), import_etoile,
    usage_avant_affectation (lecture d'une locale avant sa première liaison,
    hors boucle : UnboundLocalError), attribut_inexistant (module de la stdlib
    importé puis lu par un attribut ou un from-import qu'il n'a pas, vérifié en
    important ce module de la stdlib, jamais le code analysé ; hors blocs gardés
    par try/except AttributeError|ImportError, hasattr ou test de plateforme),
    attribut_absent_plateforme (même cas pour os, sys, ctypes, signal… : peut
    exister sur une autre plateforme).
    Si pyflakes est installé, ses messages des mêmes catégories sont recoupés
    (communs, seulement outil, seulement pyflakes).
HYPOTHÈSES
    Le source est du Python que la grammaire de l'interpréteur courant accepte ;
    les builtins et la stdlib de référence sont ceux de cet interpréteur.
LIMITES
    Les noms créés dynamiquement (globals()[…], setattr, exec, __getattr__ de
    module) sont déclarés indéfinis ; l'usage avant affectation suit l'ordre du
    texte, pas le flot (une liaison dans une branche précédente compte) ; les
    variables de déballage de tuple et de boucle for ne sont jamais déclarées
    inutilisées, ni celles qui commencent par « _ » ; les attributs ne sont
    vérifiés que sur les modules de la stdlib et leurs classes, pas sur les
    objets ni sur les bibliothèques tierces ; un import inutilisé d'un
    __init__.py peut être une réexportation voulue. Plus strict que pyflakes :
    un import de repli (try/except ImportError) jamais lu est signalé même si
    une affectation le remplace ; un sous-module importé pour son effet aussi.
CONTRE-EXEMPLES
    `import multiprocessing.connection` (stdlib, concurrent/futures/process.py:54)
    est déclaré import_inutilise alors qu'il charge un sous-module lu ensuite
    sous le nom mp.connection ; io._WindowsConsoleIO (_pyrepl/windows_console.py)
    est déclaré attribut_inexistant sous Linux alors qu'il existe sous Windows.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Sources Python 3 (fichiers ou arbres de .py) ; revue de code généré,
    CI ; noms et imports statiques d'un module, attributs de la stdlib.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import importlib
import importlib.util
import io
import json
import os
import symtable
import sys
import tokenize
import types
import warnings
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    from pyflakes import checker as pyflakes_checker  # optionnel : recoupement
except ImportError:
    pyflakes_checker = None

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")

CATEGORIES = ("nom_indefini", "nom_peut_etre_indefini", "import_inutilise", "variable_inutilisee",
              "redefinition_inutilisee", "import_etoile", "usage_avant_affectation",
              "attribut_inexistant", "attribut_absent_plateforme")
CORRESPONDANCE_PYFLAKES = (
    ("UndefinedName", "nom_indefini"), ("UndefinedExport", "nom_indefini"),
    ("ImportStarUsage", "nom_peut_etre_indefini"), ("UnusedImport", "import_inutilise"),
    ("UnusedVariable", "variable_inutilisee"), ("RedefinedWhileUnused", "redefinition_inutilisee"),
    ("ImportStarUsed", "import_etoile"), ("UndefinedLocal", "usage_avant_affectation"),
)
NOMS_MAGIQUES = frozenset({
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__path__", "__annotations__", "__cached__", "__dict__",
    "__debug__", "WindowsError", "__annotate__",
})
NOMS_CLASSE = frozenset({"__qualname__", "__module__", "__classdictcell__", "__firstlineno__",
                         "__static_attributes__"})
MODULES_NON_IMPORTES = frozenset({
    "antigravity", "this", "idlelib", "turtledemo", "turtle", "tkinter", "readline",
    "rlcompleter", "__hello__", "__phello__", "test", "pydoc_data", "ensurepip",
})
EXCEPTIONS_NOM = frozenset({"NameError", "UnboundLocalError"})
EXCEPTIONS_ATTRIBUT = frozenset({"AttributeError", "ImportError", "ModuleNotFoundError", "Exception",
                                 "BaseException"})
MODULES_PLATEFORME = frozenset({
    "os", "sys", "ctypes", "_ctypes", "select", "signal", "socket", "errno", "time", "mmap",
    "subprocess", "codecs", "ssl", "_ssl", "msvcrt", "winreg", "winsound", "posix", "nt",
    "resource", "fcntl", "termios", "multiprocessing", "_multiprocessing", "asyncio", "shutil",
    "stat", "locale", "curses", "pty", "tty", "grp", "pwd", "_winapi", "_overlapped", "_socket",
    "_posixsubprocess", "faulthandler", "platform", "selectors", "_thread", "threading",
})
ATTRIBUTS_DYNAMIQUES = frozenset({
    "sys.ps1", "sys.ps2", "sys.last_exc", "sys.last_type", "sys.last_value",
    "sys.last_traceback", "sys.tracebacklimit",
})
LIAISONS_VARIABLES = frozenset({"assign", "with", "except"})
LIAISONS_REDEFINISSABLES = frozenset({"import", "def", "class"})
TAILLE_MAX_DEFAUT = 5_000_000
MAX_FICHIERS_DEFAUT = 20_000
MAX_EXAMINES = 200
MAX_ECARTS = 50
DOSSIERS_IGNORES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".tox", ".nox", ".venv", "venv",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


class EntreeInvalide(Exception):
    """Entrée refusée : chemin absent, fichier illisible ou non analysable."""


@dataclass
class Liaison:
    """Une liaison d'un nom dans une portée."""

    nom: str
    genre: str
    position: tuple[int, int]
    chemin: tuple[tuple[int, str], ...]
    boucles: tuple[int, ...]
    detail: str = ""
    objet: str = ""
    module_racine: str = ""
    reexport: bool = False
    surcharge: bool = False
    utilisee: bool = False


@dataclass
class Lecture:
    """Une lecture directe d'un nom dans sa propre portée."""

    nom: str
    position: tuple[int, int]
    boucles: tuple[int, ...]


@dataclass
class Portee:
    """Une portée lexicale : module, classe, fonction ou paramètres de type."""

    genre: str
    table: symtable.SymbolTable | None
    liaisons: dict[str, list[Liaison]] = field(default_factory=dict)
    lectures: list[Lecture] = field(default_factory=list)
    lus: set[str] = field(default_factory=set)
    declares: set[str] = field(default_factory=set)
    annotes: set[str] = field(default_factory=set)
    comprehensions: list[set[str]] = field(default_factory=list)
    utilise_locals: bool = False
    noms_types: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Constat:
    """Un défaut trouvé."""

    categorie: str
    nom: str
    ligne: int
    message: str


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #

def lister_python(dossier: Path, maximum: int) -> list[Path]:
    """Liste les .py d'un dossier, récursivement, sans suivre les liens."""
    trouves: list[Path] = []
    for base, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            if nom.endswith(".py"):
                trouves.append(Path(base) / nom)
                if len(trouves) >= maximum:
                    print(f"avertissement : arrêt à {maximum} fichiers (--max-fichiers)", file=sys.stderr)
                    return trouves
    return trouves


def collecter_cibles(cibles: list[Path], maximum: int) -> tuple[list[Path], set[Path]]:
    """Fichiers à examiner et ensemble de ceux donnés explicitement."""
    fichiers: list[Path] = []
    explicites: set[Path] = set()
    for cible in cibles:
        if cible.is_dir():
            fichiers.extend(lister_python(cible, maximum - len(fichiers)))
        elif cible.is_file():
            fichiers.append(cible)
            explicites.add(cible)
        elif cible.exists():
            raise EntreeInvalide(f"ni fichier ni dossier : {cible}")
        else:
            raise EntreeInvalide(f"chemin introuvable : {cible}")
    return fichiers, explicites


def lire_source(chemin: Path, taille_max: int) -> str:
    """Lit un source Python selon son encodage déclaré ; refuse le binaire."""
    try:
        taille = chemin.stat().st_size
        if taille > taille_max:
            raise EntreeInvalide(f"{chemin} : {taille} octets, au-delà de --taille-max {taille_max}")
        donnees = chemin.read_bytes()
    except OSError as erreur:
        raise EntreeInvalide(f"{chemin} : lecture impossible ({erreur.strerror})") from erreur
    if b"\x00" in donnees:
        raise EntreeInvalide(f"{chemin} : contenu binaire (octet nul), pas un source Python")
    try:
        encodage, _ = tokenize.detect_encoding(io.BytesIO(donnees).readline)
        return donnees.decode(encodage)
    except (SyntaxError, UnicodeDecodeError, LookupError) as erreur:
        raise EntreeInvalide(f"{chemin} : décodage impossible ({erreur})") from erreur


def analyser_source(source: str, libelle: str) -> tuple[ast.Module, symtable.SymbolTable]:
    """Arbre ast et table des symboles ; lève EntreeInvalide si le source ne compile pas."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(source, filename=libelle), symtable.symtable(source, libelle, "exec")
        except (SyntaxError, ValueError, RecursionError, MemoryError) as erreur:
            raise EntreeInvalide(f"{libelle} : non analysable ({erreur})") from erreur


# --------------------------------------------------------------------------- #
# Tables de symboles
# --------------------------------------------------------------------------- #

def indexer_tables(racine: symtable.SymbolTable) -> dict[tuple[str, str, int], list[symtable.SymbolTable]]:
    """Index des tables de fonctions et de classes par (type, nom, ligne), dans l'ordre."""
    index: dict[tuple[str, str, int], list[symtable.SymbolTable]] = {}
    pile = [racine]
    while pile:
        table = pile.pop(0)
        cle = (str(table.get_type()), table.get_name(), table.get_lineno())
        index.setdefault(cle, []).append(table)
        pile[0:0] = table.get_children()
    return index


def symbole(table: symtable.SymbolTable | None, nom: str) -> symtable.Symbol | None:
    """Symbole d'une table, ou None s'il n'y figure pas."""
    if table is None:
        return None
    try:
        return table.lookup(nom)
    except KeyError:
        return None


def libres_descendants(table: symtable.SymbolTable | None) -> set[str]:
    """Noms qu'une portée imbriquée (hors classe) lit comme variables libres."""
    libres: set[str] = set()
    if table is None:
        return libres
    for enfant in table.get_children():
        libres.update(s.get_name() for s in enfant.get_symbols() if s.is_free())
        libres.update(libres_descendants(enfant))
    return libres


def definis_au_module(table: symtable.SymbolTable) -> set[str]:
    """Noms liés au niveau module, y compris par `global` dans une fonction."""
    definis = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    pile = list(table.get_children())
    while pile:
        enfant = pile.pop()
        definis.update(s.get_name() for s in enfant.get_symbols()
                       if s.is_declared_global() and (s.is_assigned() or s.is_imported()))
        pile.extend(enfant.get_children())
    return definis


# --------------------------------------------------------------------------- #
# Parcours
# --------------------------------------------------------------------------- #

def branches_exclusives(a: tuple[tuple[int, str], ...], b: tuple[tuple[int, str], ...]) -> bool:
    """Vrai si deux chemins de branches divergent sur un même if/try/match."""
    for pas_a, pas_b in zip(a, b):
        if pas_a != pas_b:
            return pas_a[0] == pas_b[0]
    return False


def decorateur_surcharge(decorateurs: list[ast.expr]) -> bool:
    """Vrai pour @overload ou @typing.overload."""
    for deco in decorateurs:
        nom = deco.attr if isinstance(deco, ast.Attribute) else getattr(deco, "id", "")
        if nom == "overload":
            return True
    return False


def attrape_nameerror(gestionnaires: list[ast.ExceptHandler]) -> bool:
    """Vrai si un gestionnaire rattrape NameError."""
    for gestionnaire in gestionnaires:
        genre = gestionnaire.type
        noms = genre.elts if isinstance(genre, ast.Tuple) else [genre]
        if any(isinstance(n, ast.Name) and n.id in EXCEPTIONS_NOM for n in noms):
            return True
    return False


def attrape_absence(gestionnaires: list[ast.ExceptHandler]) -> bool:
    """Vrai si un gestionnaire rattrape l'absence d'un attribut ou d'un module."""
    for gestionnaire in gestionnaires:
        genre = gestionnaire.type
        noms = genre.elts if isinstance(genre, ast.Tuple) else [genre]
        if genre is None or any(getattr(n, "id", getattr(n, "attr", "")) in EXCEPTIONS_ATTRIBUT for n in noms):
            return True
    return False


def test_de_plateforme(test: ast.expr) -> bool:
    """Vrai si un if teste la plateforme, la version ou la présence d'un attribut."""
    for noeud in ast.walk(test):
        if isinstance(noeud, ast.Call) and getattr(noeud.func, "id", "") in ("hasattr", "getattr"):
            return True
        if isinstance(noeud, ast.Attribute) and noeud.attr in ("platform", "name", "implementation",
                                                               "version_info", "system", "TYPE_CHECKING"):
            return True
        if isinstance(noeud, ast.Name) and noeud.id == "TYPE_CHECKING":
            return True
    return False


def noms_cibles(cible: ast.AST) -> list[ast.Name]:
    """Noms liés par une cible de déballage (a, (b, *c))."""
    return [n for n in ast.walk(cible) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]


class Analyseur:
    """Parcourt l'arbre en suivant les portées ; accumule liaisons, lectures et constats."""

    def __init__(self, table_module: symtable.SymbolTable) -> None:
        self.index = indexer_tables(table_module)
        self.module = Portee("module", table_module)
        self.pile: list[Portee] = [self.module]
        self.toutes: list[Portee] = [self.module]
        self.chemin: list[tuple[int, str]] = []
        self.boucles: list[int] = []
        self.annotation = 0
        self.garde_nom = 0
        self.garde_attr = 0
        self.lectures_globales: list[tuple[str, int, bool, bool]] = []
        self.differees: list[tuple[Portee, str]] = []
        self.constats: list[Constat] = []
        self.chaines: list[tuple[str, list[str], int]] = []
        self.exports: list[tuple[str, int]] = []
        self.etoile = False

    @property
    def portee(self) -> Portee:
        return self.pile[-1]

    def parcourir(self, noeud: ast.AST) -> None:
        methode = getattr(self, "voir_" + type(noeud).__name__, None)
        if methode is not None and methode(noeud):
            return
        for enfant in ast.iter_child_nodes(noeud):
            self.parcourir(enfant)

    def parcourir_tous(self, noeuds: list[ast.AST]) -> None:
        for noeud in noeuds:
            self.parcourir(noeud)

    def dans_branche(self, noeuds: list[ast.AST], cle: int, branche: str) -> None:
        self.chemin.append((cle, branche))
        self.parcourir_tous(noeuds)
        self.chemin.pop()

    # ----- liaisons -------------------------------------------------------- #

    def lier(self, nom: str, noeud: ast.AST, genre: str, **details: object) -> None:
        portee = self.portee
        if nom in portee.declares:
            return
        position = (getattr(noeud, "end_lineno", None) or noeud.lineno,
                    getattr(noeud, "end_col_offset", None) or 0) if genre == "augassign" else (
            noeud.lineno, noeud.col_offset)
        liaison = Liaison(nom, genre, position, tuple(self.chemin), tuple(self.boucles), **details)
        precedentes = portee.liaisons.setdefault(nom, [])
        if genre in LIAISONS_REDEFINISSABLES and precedentes:
            self.verifier_redefinition(precedentes[-1], liaison)
        precedentes.append(liaison)

    def verifier_redefinition(self, ancienne: Liaison, nouvelle: Liaison) -> None:
        if ancienne.genre not in LIAISONS_REDEFINISSABLES or ancienne.utilisee or nouvelle.nom == "_":
            return
        if branches_exclusives(ancienne.chemin, nouvelle.chemin) or ancienne.surcharge or nouvelle.surcharge:
            return
        if ancienne.module_racine and ancienne.module_racine == nouvelle.module_racine \
                and ancienne.detail != nouvelle.detail:
            return
        self.constats.append(Constat("redefinition_inutilisee", nouvelle.nom, nouvelle.position[0],
                                     f"« {nouvelle.nom} » redéfini avant tout usage de la liaison "
                                     f"de la ligne {ancienne.position[0]}"))

    # ----- lectures -------------------------------------------------------- #

    def lire(self, nom: str, noeud: ast.AST) -> None:
        if any(nom in noms for noms in self.portee.comprehensions):
            return
        cible = self.resoudre(nom)
        position = (noeud.lineno, noeud.col_offset)
        if cible is self.module:
            self.lectures_globales.append((nom, noeud.lineno, self.annotation > 0, self.garde_nom > 0))
        if cible is None:
            return
        cible.lus.add(nom)
        if cible is self.portee:
            if not self.portee.comprehensions:
                cible.lectures.append(Lecture(nom, position, tuple(self.boucles)))
            self.marquer(cible, nom, position)
        else:
            self.differees.append((cible, nom))

    def marquer(self, portee: Portee, nom: str, position: tuple[int, int] | None) -> None:
        liaisons = portee.liaisons.get(nom, [])
        avant = [l for l in liaisons if position is None or l.position <= position] or liaisons[-1:]
        if not avant:
            return
        retenue = avant[-1]
        retenue.utilisee = True
        for autre in liaisons:
            meme_module = bool(retenue.module_racine) and autre.module_racine == retenue.module_racine
            alternative = autre.genre in LIAISONS_REDEFINISSABLES and branches_exclusives(autre.chemin, retenue.chemin)
            if meme_module or alternative:
                autre.utilisee = True

    def resoudre(self, nom: str) -> Portee | None:
        portee = self.portee
        if portee.genre == "typeparams" and nom in portee.noms_types:
            return portee
        if portee.genre == "module":
            return portee
        sym = symbole(portee.table, nom)
        if sym is not None and sym.is_local():
            return portee
        if sym is not None and (sym.is_declared_global() or (sym.is_global() and not sym.is_local())):
            return self.module
        return self.resoudre_par_chaine(nom, libre=sym is not None and sym.is_free())

    def resoudre_par_chaine(self, nom: str, libre: bool) -> Portee | None:
        for rang in range(len(self.pile) - 1, -1, -1):
            portee = self.pile[rang]
            if portee.genre == "module":
                return None if libre else portee
            if portee.genre == "class" and rang != len(self.pile) - 1:
                continue
            if nom in portee.noms_types or nom in portee.liaisons or nom in portee.annotes:
                return portee
            sym = symbole(portee.table, nom)
            if sym is not None and sym.is_local() and portee.genre != "class":
                return portee
        return self.module

    # ----- instructions ----------------------------------------------------- #

    def voir_Name(self, noeud: ast.Name) -> bool:
        if isinstance(noeud.ctx, (ast.Load, ast.Del)):
            self.lire(noeud.id, noeud)
        elif isinstance(noeud.ctx, ast.Store):
            self.lier(noeud.id, noeud, "autre")
        return True

    def voir_Assign(self, noeud: ast.Assign) -> bool:
        self.parcourir(noeud.value)
        for cible in noeud.targets:
            self.cible(cible, "assign")
        self.noter_exports(noeud.targets, noeud.value)
        return True

    def cible(self, cible: ast.expr, genre: str) -> None:
        if isinstance(cible, ast.Name):
            self.lier(cible.id, cible, genre)
        elif isinstance(cible, (ast.Tuple, ast.List)):
            for element in cible.elts:
                self.cible(element, "tuple")
        elif isinstance(cible, ast.Starred):
            self.cible(cible.value, "tuple")
        else:
            self.parcourir(cible)

    def voir_AnnAssign(self, noeud: ast.AnnAssign) -> bool:
        self.annoter(noeud.annotation)
        if noeud.value is not None:
            self.parcourir(noeud.value)
            self.cible(noeud.target, "assign")
        elif isinstance(noeud.target, ast.Name):
            self.portee.annotes.add(noeud.target.id)
        else:
            self.parcourir(noeud.target)
        return True

    def voir_AugAssign(self, noeud: ast.AugAssign) -> bool:
        self.parcourir(noeud.value)
        if isinstance(noeud.target, ast.Name):
            self.lire(noeud.target.id, noeud.target)
            self.lier(noeud.target.id, noeud, "augassign")
        else:
            self.parcourir(noeud.target)
        if isinstance(noeud.target, ast.Name):
            self.noter_exports([noeud.target], noeud.value)
        return True

    def voir_NamedExpr(self, noeud: ast.NamedExpr) -> bool:
        self.parcourir(noeud.value)
        self.lier(noeud.target.id, noeud.target, "assign")
        return True

    def voir_For(self, noeud: ast.For) -> bool:
        self.parcourir(noeud.iter)
        self.boucles.append(id(noeud))
        self.cible(noeud.target, "for")
        self.parcourir_tous(noeud.body)
        self.boucles.pop()
        self.parcourir_tous(noeud.orelse)
        return True

    def voir_AsyncFor(self, noeud: ast.AsyncFor) -> bool:
        return self.voir_For(noeud)

    def voir_While(self, noeud: ast.While) -> bool:
        self.boucles.append(id(noeud))
        self.parcourir(noeud.test)
        self.parcourir_tous(noeud.body)
        self.boucles.pop()
        self.parcourir_tous(noeud.orelse)
        return True

    def voir_withitem(self, noeud: ast.withitem) -> bool:
        self.parcourir(noeud.context_expr)
        if noeud.optional_vars is not None:
            self.cible(noeud.optional_vars, "with")
        return True

    def voir_If(self, noeud: ast.If) -> bool:
        self.parcourir(noeud.test)
        garde = test_de_plateforme(noeud.test)
        self.garde_attr += garde
        self.dans_branche(noeud.body, id(noeud), "si")
        self.dans_branche(noeud.orelse, id(noeud), "sinon")
        self.garde_attr -= garde
        return True

    def voir_Try(self, noeud: ast.Try) -> bool:
        garde, garde_attr = attrape_nameerror(noeud.handlers), attrape_absence(noeud.handlers)
        self.garde_nom += garde
        self.garde_attr += garde_attr
        self.dans_branche(noeud.body + noeud.orelse, id(noeud), "corps")
        self.garde_nom -= garde
        for rang, gestionnaire in enumerate(noeud.handlers):
            self.dans_branche([gestionnaire], id(noeud), f"except{rang}")
        self.garde_attr -= garde_attr
        self.parcourir_tous(noeud.finalbody)
        return True

    def voir_TryStar(self, noeud: ast.Try) -> bool:
        return self.voir_Try(noeud)

    def voir_ExceptHandler(self, noeud: ast.ExceptHandler) -> bool:
        if noeud.type is not None:
            self.parcourir(noeud.type)
        if noeud.name:
            self.lier(noeud.name, noeud, "except")
        self.parcourir_tous(noeud.body)
        return True

    def voir_match_case(self, noeud: ast.match_case) -> bool:
        self.chemin.append((id(noeud), "case"))
        self.parcourir(noeud.pattern)
        if noeud.guard is not None:
            self.parcourir(noeud.guard)
        self.parcourir_tous(noeud.body)
        self.chemin.pop()
        return True

    def voir_MatchAs(self, noeud: ast.MatchAs) -> bool:
        if noeud.pattern is not None:
            self.parcourir(noeud.pattern)
        if noeud.name:
            self.lier(noeud.name, noeud, "tuple")
        return True

    def voir_MatchStar(self, noeud: ast.MatchStar) -> bool:
        if noeud.name:
            self.lier(noeud.name, noeud, "tuple")
        return True

    def voir_MatchMapping(self, noeud: ast.MatchMapping) -> bool:
        self.parcourir_tous(noeud.keys + noeud.patterns)
        if noeud.rest:
            self.lier(noeud.rest, noeud, "tuple")
        return True

    def voir_Global(self, noeud: ast.Global) -> bool:
        self.portee.declares.update(noeud.names)
        return True

    def voir_Nonlocal(self, noeud: ast.Nonlocal) -> bool:
        self.portee.declares.update(noeud.names)
        return True

    def voir_Call(self, noeud: ast.Call) -> bool:
        if isinstance(noeud.func, ast.Name) and noeud.func.id == "locals":
            self.portee.utilise_locals = True
        return False

    # ----- imports ---------------------------------------------------------- #

    def voir_Import(self, noeud: ast.Import) -> bool:
        for alias in noeud.names:
            local = alias.asname or alias.name.split(".")[0]
            self.lier(local, noeud, "import", detail=alias.name, reexport=alias.asname == alias.name,
                      module_racine="" if alias.asname else local, objet=alias.asname and alias.name or local)
        return True

    def voir_ImportFrom(self, noeud: ast.ImportFrom) -> bool:
        if noeud.module == "__future__":
            return True
        module = "." * noeud.level + (noeud.module or "")
        for alias in noeud.names:
            if alias.name == "*":
                self.etoile = True
                self.constats.append(Constat("import_etoile", module, noeud.lineno,
                                             f"from {module} import * : les noms indéfinis deviennent indétectables"))
                continue
            local = alias.asname or alias.name
            complet = f"{module}.{alias.name}" if noeud.module else f"{module}{alias.name}"
            self.lier(local, noeud, "import", detail=complet, reexport=alias.asname == alias.name,
                      objet=complet if noeud.level == 0 else "")
            if noeud.level == 0 and noeud.module and not self.garde_attr:
                self.chaines.append((noeud.module, [alias.name], noeud.lineno))
        return True

    # ----- définitions ------------------------------------------------------ #

    def voir_FunctionDef(self, noeud: ast.FunctionDef) -> bool:
        self.parcourir_tous(noeud.decorator_list)
        self.parcourir_tous(noeud.args.defaults + [d for d in noeud.args.kw_defaults if d is not None])
        self.entrer_types(noeud)
        for argument in self.arguments(noeud.args):
            if argument.annotation is not None:
                self.annoter(argument.annotation)
        if noeud.returns is not None:
            self.annoter(noeud.returns)
        self.sortir_types(noeud)
        self.lier(noeud.name, noeud, "def", surcharge=decorateur_surcharge(noeud.decorator_list))
        self.corps_fonction(noeud, "function", noeud.name, noeud.body)
        return True

    def voir_AsyncFunctionDef(self, noeud: ast.AsyncFunctionDef) -> bool:
        return self.voir_FunctionDef(noeud)

    def voir_Lambda(self, noeud: ast.Lambda) -> bool:
        self.parcourir_tous(noeud.args.defaults + [d for d in noeud.args.kw_defaults if d is not None])
        self.corps_fonction(noeud, "function", "lambda", [noeud.body])
        return True

    def arguments(self, args: ast.arguments) -> list[ast.arg]:
        tous = args.posonlyargs + args.args + args.kwonlyargs
        return tous + [a for a in (args.vararg, args.kwarg) if a is not None]

    def corps_fonction(self, noeud: ast.AST, genre: str, nom: str, corps: list[ast.AST]) -> None:
        table = self.prendre_table(genre, nom, noeud.lineno)
        portee = Portee("function", table)
        self.entrer(portee, noeud)
        for argument in self.arguments(noeud.args):
            self.lier(argument.arg, argument, "param")
        boucles, self.boucles = self.boucles, []
        self.parcourir_tous(corps)
        self.boucles = boucles
        self.sortir(noeud)

    def voir_ClassDef(self, noeud: ast.ClassDef) -> bool:
        self.parcourir_tous(noeud.decorator_list)
        self.entrer_types(noeud)
        self.parcourir_tous(noeud.bases + noeud.keywords)
        portee = Portee("class", self.prendre_table("class", noeud.name, noeud.lineno))
        self.entrer(portee, noeud)
        self.parcourir_tous(noeud.body)
        self.sortir(noeud)
        self.sortir_types(noeud)
        self.lier(noeud.name, noeud, "class")
        return True

    def voir_TypeAlias(self, noeud: ast.AST) -> bool:
        self.lier(noeud.name.id, noeud.name, "assign")
        self.entrer_types(noeud)
        self.annoter(noeud.value)
        self.sortir_types(noeud)
        return True

    def entrer_types(self, noeud: ast.AST) -> None:
        parametres = getattr(noeud, "type_params", None) or []
        if parametres:
            noms = frozenset(p.name for p in parametres)
            self.pile.append(Portee("typeparams", None, noms_types=noms))
            for param in parametres:
                for borne in (getattr(param, "bound", None), getattr(param, "default_value", None)):
                    if borne is not None:
                        self.annoter(borne)

    def sortir_types(self, noeud: ast.AST) -> None:
        if getattr(noeud, "type_params", None):
            self.pile.pop()

    def prendre_table(self, genre: str, nom: str, ligne: int) -> symtable.SymbolTable | None:
        candidates = self.index.get((genre, nom, ligne))
        return candidates.pop(0) if candidates else None

    def entrer(self, portee: Portee, noeud: ast.AST) -> None:
        self.pile.append(portee)
        self.toutes.append(portee)
        self.chemin.append((id(noeud), "portee"))

    def sortir(self, noeud: ast.AST) -> None:
        self.chemin.pop()
        self.pile.pop()

    # ----- compréhensions --------------------------------------------------- #

    def comprehension(self, noeud: ast.AST, resultats: list[ast.AST]) -> bool:
        noms: set[str] = set()
        self.portee.comprehensions.append(noms)
        for generateur in noeud.generators:
            self.parcourir(generateur.iter)
            noms.update(n.id for n in noms_cibles(generateur.target))
            for noeud_cible in ast.walk(generateur.target):
                if isinstance(noeud_cible, (ast.Attribute, ast.Subscript)):
                    self.parcourir(noeud_cible)
            self.parcourir_tous(generateur.ifs)
        self.parcourir_tous(resultats)
        self.portee.comprehensions.pop()
        return True

    def voir_ListComp(self, noeud: ast.ListComp) -> bool:
        return self.comprehension(noeud, [noeud.elt])

    def voir_SetComp(self, noeud: ast.SetComp) -> bool:
        return self.comprehension(noeud, [noeud.elt])

    def voir_GeneratorExp(self, noeud: ast.GeneratorExp) -> bool:
        return self.comprehension(noeud, [noeud.elt])

    def voir_DictComp(self, noeud: ast.DictComp) -> bool:
        return self.comprehension(noeud, [noeud.key, noeud.value])

    # ----- annotations, attributs, exports ---------------------------------- #

    def annoter(self, expr: ast.expr) -> None:
        self.annotation += 1
        self.parcourir(expr)
        self.annotation -= 1

    def voir_Subscript(self, noeud: ast.Subscript) -> bool:
        if not self.annotation:
            return False
        tete = noeud.value.attr if isinstance(noeud.value, ast.Attribute) else getattr(noeud.value, "id", "")
        if tete not in ("Literal", "Annotated"):
            return False
        self.parcourir(noeud.value)
        elements = noeud.slice.elts if isinstance(noeud.slice, ast.Tuple) else [noeud.slice]
        if tete == "Annotated":
            self.parcourir(elements[0])
            elements = elements[1:]
        sauve, self.annotation = self.annotation, 0
        self.parcourir_tous(elements)
        self.annotation = sauve
        return True

    def voir_Constant(self, noeud: ast.Constant) -> bool:
        if self.annotation and isinstance(noeud.value, str):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    expr = ast.parse(noeud.value.strip(), mode="eval").body
                except (SyntaxError, ValueError):
                    return True
            for sous in ast.walk(expr):
                if isinstance(sous, (ast.Name, ast.Attribute)):
                    ast.copy_location(sous, noeud)
            self.parcourir(expr)
        return True

    def voir_Attribute(self, noeud: ast.Attribute) -> bool:
        if isinstance(noeud.ctx, ast.Load):
            chaine: list[str] = []
            base: ast.expr = noeud
            while isinstance(base, ast.Attribute):
                chaine.insert(0, base.attr)
                base = base.value
            if isinstance(base, ast.Name) and not self.annotation and not self.garde_attr:
                self.noter_chaine(base.id, chaine, noeud.lineno)
        return False

    def noter_chaine(self, nom: str, chaine: list[str], ligne: int) -> None:
        if any(nom in noms for noms in self.portee.comprehensions):
            return
        portee = self.resoudre(nom)
        liaisons = portee.liaisons.get(nom, []) if portee is not None else []
        objets = {l.objet if l.genre == "import" else "" for l in liaisons}
        if len(objets) == 1 and "" not in objets:
            self.chaines.append((objets.pop(), chaine, ligne))

    def noter_exports(self, cibles: list[ast.expr], valeur: ast.expr) -> None:
        if self.portee is not self.module or not any(isinstance(c, ast.Name) and c.id == "__all__" for c in cibles):
            return
        if isinstance(valeur, (ast.List, ast.Tuple)):
            for element in valeur.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    self.exports.append((element.value, cibles[0].lineno))


# --------------------------------------------------------------------------- #
# Synthèse des constats
# --------------------------------------------------------------------------- #

def constats_indefinis(analyseur: Analyseur, definis: set[str], init: bool) -> list[Constat]:
    """Lectures globales qui ne se résolvent ni au module ni dans les builtins.

    Dans un __init__.py, __all__ peut nommer des sous-modules : il n'est pas vérifié.
    """
    connus = definis | set(dir(builtins)) | NOMS_MAGIQUES
    categorie = "nom_peut_etre_indefini" if analyseur.etoile else "nom_indefini"
    constats = []
    for nom, ligne, annotation, garde in analyseur.lectures_globales:
        if nom in connus or garde or nom in NOMS_CLASSE:
            continue
        lieu = " (dans une annotation)" if annotation else ""
        constats.append(Constat(categorie, nom, ligne, f"nom « {nom} » lié nulle part{lieu}"))
    for nom, ligne in ([] if init else analyseur.exports):
        if nom not in definis:
            constats.append(Constat(categorie, nom, ligne, f"« {nom} » listé dans __all__ mais jamais défini"))
    return constats


def constats_imports(analyseur: Analyseur, init: bool) -> list[Constat]:
    """Imports jamais lus."""
    exportes = {nom for nom, _ in analyseur.exports}
    constats = []
    for portee in analyseur.toutes:
        if portee.genre == "class":
            continue
        for nom, liaisons in portee.liaisons.items():
            for liaison in liaisons:
                if liaison.genre != "import" or liaison.utilisee or liaison.reexport:
                    continue
                if portee is analyseur.module and nom in exportes:
                    continue
                note = " (réexportation possible : __init__.py)" if init else ""
                constats.append(Constat("import_inutilise", liaison.detail, liaison.position[0],
                                        f"« {liaison.detail} » importé mais jamais utilisé{note}"))
    return constats


def constats_variables(analyseur: Analyseur) -> list[Constat]:
    """Variables locales liées puis jamais lues ; une liaison except … as par gestionnaire."""
    constats = []
    for portee in analyseur.toutes:
        if portee.genre != "function" or portee.utilise_locals:
            continue
        libres = libres_descendants(portee.table)
        for nom, liaisons in portee.liaisons.items():
            if nom in libres or nom.startswith("_") or nom in portee.declares:
                continue
            for liaison in liaisons:
                if liaison.genre == "except" and not liaison.utilisee:
                    constats.append(Constat("variable_inutilisee", nom, liaison.position[0],
                                            f"exception « {nom} » nommée mais jamais lue"))
            if nom in portee.lus or any(l.genre not in LIAISONS_VARIABLES | {"autre", "param"} for l in liaisons):
                continue
            simples = [l for l in liaisons if l.genre in ("assign", "with")]
            if simples:
                constats.append(Constat("variable_inutilisee", nom, simples[-1].position[0],
                                        f"variable locale « {nom} » affectée mais jamais lue"))
    return constats


def constats_avant_affectation(analyseur: Analyseur) -> list[Constat]:
    """Lectures d'une locale avant sa première liaison, hors boucle qui la lie."""
    constats = []
    for portee in analyseur.toutes:
        if portee.genre != "function":
            continue
        signales: set[str] = set()
        for lecture in portee.lectures:
            liaisons = portee.liaisons.get(lecture.nom, [])
            if lecture.nom in signales or lecture.nom in portee.declares:
                continue
            if not liaisons and lecture.nom not in portee.annotes:
                continue
            premiere = min((l.position for l in liaisons), default=None)
            boucles_liees = {b for l in liaisons for b in l.boucles}
            if (premiere is None or lecture.position < premiere) and not boucles_liees & set(lecture.boucles):
                signales.add(lecture.nom)
                constats.append(Constat("usage_avant_affectation", lecture.nom, lecture.position[0],
                                        f"« {lecture.nom} » lu avant sa première affectation dans la "
                                        f"fonction (UnboundLocalError)"))
    return constats


def importer_stdlib(nom: str) -> types.ModuleType | None:
    """Importe un module de la stdlib (jamais le code analysé) ; None si interdit ou absent."""
    if nom.split(".")[0] not in sys.stdlib_module_names or nom.split(".")[0] in MODULES_NON_IMPORTES:
        return None
    with warnings.catch_warnings(), contextlib.redirect_stdout(sys.stderr):
        warnings.simplefilter("ignore")
        try:
            return importlib.import_module(nom)
        except (ImportError, OSError, ValueError, RuntimeError, AttributeError):
            return None


def sous_module(nom: str) -> bool:
    """Vrai si nom désigne un sous-module existant de la stdlib."""
    try:
        return importlib.util.find_spec(nom) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def objet_stdlib(chemin: str) -> object | None:
    """Objet désigné par un chemin pointé de la stdlib (module, classe, fonction…)."""
    module = importer_stdlib(chemin)
    if module is not None or "." not in chemin:
        return module
    parent, nom = chemin.rsplit(".", 1)
    conteneur = objet_stdlib(parent)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return getattr(conteneur, nom, None) if conteneur is not None else None


def attribut_manquant(chemin: str, chaine: list[str]) -> str | None:
    """Premier maillon introuvable d'une chaîne d'attributs, ou None si tout existe."""
    objet = objet_stdlib(chemin)
    qualifie = chemin
    for attribut in chaine:
        if objet is None or not isinstance(objet, (types.ModuleType, type)):
            return None
        qualifie = f"{qualifie}.{attribut}"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if hasattr(objet, attribut):
                objet = getattr(objet, attribut)
                continue
        if isinstance(objet, types.ModuleType) and sous_module(qualifie):
            objet = importer_stdlib(qualifie)
            continue
        return qualifie
    return None


def constats_attributs(analyseur: Analyseur) -> list[Constat]:
    """Attributs et from-imports absents des modules de la stdlib importés."""
    constats, vus = [], set()
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    for chemin, chaine, ligne in analyseur.chaines:
        complet = ".".join([chemin] + chaine)
        if complet in vus or chemin.split(".")[0] not in sys.stdlib_module_names:
            continue
        vus.add(complet)
        manquant = attribut_manquant(chemin, chaine)
        if manquant is None or manquant in ATTRIBUTS_DYNAMIQUES:
            continue
        if chemin.split(".")[0] in MODULES_PLATEFORME:
            constats.append(Constat("attribut_absent_plateforme", manquant, ligne,
                                    f"« {manquant} » absent de Python {version} sur {sys.platform} "
                                    f"(peut exister sur une autre plateforme)"))
        else:
            constats.append(Constat("attribut_inexistant", manquant, ligne,
                                    f"« {manquant} » n'existe pas dans la stdlib de Python {version}"))
    return constats


def analyser_fichier(chemin: Path, libelle: str, taille_max: int) -> tuple[list[Constat], str, ast.Module]:
    """Tous les constats d'un fichier, plus le source et l'arbre pour pyflakes."""
    source = lire_source(chemin, taille_max)
    arbre, table = analyser_source(source, libelle)
    analyseur = Analyseur(table)
    analyseur.parcourir_tous(arbre.body)
    for portee, nom in analyseur.differees:
        analyseur.marquer(portee, nom, None)
    for nom, _ in analyseur.exports:
        analyseur.marquer(analyseur.module, nom, None)
    constats = list(analyseur.constats)
    constats += constats_indefinis(analyseur, definis_au_module(table), chemin.name == "__init__.py")
    constats += constats_imports(analyseur, chemin.name == "__init__.py")
    constats += constats_variables(analyseur)
    constats += constats_avant_affectation(analyseur)
    constats += constats_attributs(analyseur)
    return sorted(set(constats), key=lambda c: (c.ligne, c.categorie, c.nom)), source, arbre


# --------------------------------------------------------------------------- #
# Recoupement optionnel avec pyflakes
# --------------------------------------------------------------------------- #

def constats_pyflakes(arbre: ast.Module, libelle: str) -> set[tuple[str, int, str]] | None:
    """Messages pyflakes des catégories communes : (catégorie, ligne, nom)."""
    if pyflakes_checker is None:
        return None
    correspondance = dict(CORRESPONDANCE_PYFLAKES)
    with warnings.catch_warnings(), contextlib.redirect_stdout(sys.stderr):
        warnings.simplefilter("ignore")
        verificateur = pyflakes_checker.Checker(arbre, filename=libelle)
    resultat = set()
    for message in verificateur.messages:
        categorie = correspondance.get(type(message).__name__)
        if categorie is not None:
            nom = str(message.message_args[0]).split(" as ")[0] if message.message_args else ""
            if categorie == "import_inutilise" and nom.endswith(".*"):
                categorie, nom = "import_etoile", nom[:-2]
            resultat.add((categorie, message.lineno, nom))
    return resultat


def recouper(miens: list[Constat], siens: set[tuple[str, int, str]]) -> dict[str, list[str]]:
    """Écarts entre l'outil et pyflakes, à la ligne et au nom près."""
    canon = lambda cat: "nom_indefini" if cat == "usage_avant_affectation" else cat
    a_moi = {(canon(c.categorie), c.ligne, c.nom) for c in miens if not c.categorie.startswith("attribut_")}
    siens = {(canon(cat), ligne, nom) for cat, ligne, nom in siens}
    clef = lambda t: f"{t[0]} ligne {t[1]} : {t[2]}"
    return {"communs": sorted(map(clef, a_moi & siens)),
            "seulement_outil": sorted(map(clef, a_moi - siens)),
            "seulement_pyflakes": sorted(map(clef, siens - a_moi))}


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #

def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait les intitulés du contrat de mesure depuis la docstring."""
    contrat: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if ligne[:1].strip() and tete in INTITULES:
            courant = tete
            contrat[courant] = []
        elif courant is not None and tete:
            contrat[courant].append(tete)
    return {cle: " ".join(valeur) for cle, valeur in contrat.items()}


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description=("Cherche, sans exécuter le code, les noms indéfinis, imports inutiles, variables "
                     "jamais lues, redéfinitions, import *, usages avant affectation et attributs "
                     "inexistants de la stdlib."),
        epilog=(f"Exemple : python {RACINE / 'detecter_noms_indefinis.py'} src/ --ignorer variable_inutilisee --json\n"
                "Codes : 0 rien à signaler, 1 constat trouvé, 2 entrée invalide, 3 rien à examiner."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="+", type=Path, help="fichiers .py ou dossiers")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="base des chemins relatifs et de l'affichage (défaut : dossier courant)")
    parseur.add_argument("--ignorer", action="append", choices=CATEGORIES, default=[],
                         help="catégorie à ne pas rapporter (répétable)")
    parseur.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets maximum par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=MAX_FICHIERS_DEFAUT, help="nombre maximum de fichiers")
    return parseur


def libelle_chemin(chemin: Path, base: Path) -> str:
    """Chemin affiché, relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def examiner(fichiers: list[Path], explicites: set[Path], base: Path, args: argparse.Namespace
             ) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, int]]:
    """Analyse chaque fichier ; un fichier explicite illisible est une entrée invalide."""
    resultats, rejets = [], []
    bilan_pyflakes = {"communs": 0, "seulement_outil": 0, "seulement_pyflakes": 0}
    for chemin in fichiers:
        libelle = libelle_chemin(chemin, base)
        try:
            constats, _, arbre = analyser_fichier(chemin, libelle, args.taille_max)
        except EntreeInvalide as erreur:
            if chemin in explicites:
                raise
            print(f"non analysé : {erreur}", file=sys.stderr)
            rejets.append({"chemin": libelle, "raison": str(erreur)})
            continue
        constats = [c for c in constats if c.categorie not in args.ignorer]
        entree: dict[str, object] = {"chemin": libelle, "constats": [vars_constat(c) for c in constats]}
        siens = constats_pyflakes(arbre, libelle)
        if siens is not None:
            siens = {s for s in siens if s[0] not in args.ignorer}
            ecarts = recouper(constats, siens)
            entree["pyflakes"] = {cle: valeur[:MAX_ECARTS] for cle, valeur in ecarts.items()}
            for cle, valeur in ecarts.items():
                bilan_pyflakes[cle] += len(valeur)
        resultats.append(entree)
    return resultats, rejets, bilan_pyflakes


def vars_constat(constat: Constat) -> dict[str, object]:
    """Sérialise un constat."""
    return {"categorie": constat.categorie, "nom": constat.nom, "ligne": constat.ligne,
            "message": constat.message}


def afficher_humain(sortie: dict[str, object]) -> None:
    """Affichage lisible par un humain."""
    print(f"detecter_noms_indefinis — {sortie['denominateur']} fichier(s), "
          f"{sortie['nombre_constats']} constat(s), moteur {sortie['moteur']}")
    for fichier in sortie["fichiers"]:
        for constat in fichier["constats"]:
            print(f"  {fichier['chemin']}:{constat['ligne']} [{constat['categorie']}] {constat['message']}")
    print("Par catégorie : " + ", ".join(f"{k}={v}" for k, v in sortie["par_categorie"].items() if v))
    if "pyflakes" in sortie:
        print(f"Recoupement pyflakes : {sortie['pyflakes']}")


def afficher_json(sortie: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    base = args.racine or Path.cwd()
    if pyflakes_checker is None:
        print("pyflakes absent : recoupement omis, analyse stdlib seule (ast + symtable + builtins)",
              file=sys.stderr)
    try:
        chemins, explicites = collecter_cibles([c if c.is_absolute() else base / c for c in args.chemins],
                                               max(1, args.max_fichiers))
        resultats, rejets, bilan = examiner(chemins, explicites, base, args)
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    tous = [c for r in resultats for c in r["constats"]]
    examines = [r["chemin"] for r in resultats]
    sortie: dict[str, object] = {
        "outil": "detecter_noms_indefinis",
        "moteur": "stdlib" if pyflakes_checker is None else "stdlib+pyflakes",
        "denominateur": len(resultats),
        "examines": examines[:MAX_EXAMINES],
        "examines_tronques": len(examines) > MAX_EXAMINES,
        "non_analyses": rejets,
        "nombre_constats": len(tous),
        "par_categorie": {c: sum(1 for x in tous if x["categorie"] == c) for c in CATEGORIES},
        "fichiers": [r for r in resultats if r["constats"] or "pyflakes" in r],
        "contrat": extraire_contrat(__doc__ or ""),
    }
    if pyflakes_checker is not None:
        sortie["pyflakes"] = bilan
    if args.json:
        afficher_json(sortie)
    if not resultats:
        print("dénominateur nul : aucun fichier Python analysable, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    return 1 if tous else 0


if __name__ == "__main__":
    raise SystemExit(main())
