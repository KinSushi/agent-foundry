"""Depuis Python 3.14 (PEP 649) les annotations ne sont évaluées que lorsqu'on
les lit : un nom importé sous TYPE_CHECKING ou mal orthographié passe l'import
sans bruit et lève NameError plus tard, dans pydantic, typer, singledispatch ou
typing.get_type_hints. Mesuré dans cette session :
@@FAIT_MESURE@@

QUESTION
    Ces annotations sont-elles évaluables à l'exécution, ou lèveront-elles
    NameError quand quelqu'un les lira ?
MESURE
    Analyse statique (défaut, n'exécute rien) : chaque annotation évaluée par
    Python (paramètres et retours de fonctions à toute profondeur, annotations
    de classe et de module, valeur des alias `type`, bornes des paramètres de
    type ; pas les annotations de variables locales, jamais évaluées) est
    parcourue, chaînes comprises (analysées comme expressions, hors Literal[…]
    et métadonnées d'Annotated). Chaque nom est résolu contre : paramètres de
    type, classe englobante, fonctions englobantes, liaisons du module (avec
    leur position et leur appartenance à un bloc if TYPE_CHECKING), builtins.
    Catégories : indefini (lié nulle part), type_checking_seulement (lié
    seulement sous TYPE_CHECKING : NameError à l'évaluation), chaine_invalide
    (chaîne qui n'est pas une expression), peut_etre_indefini (import * présent),
    reference_en_avant (nom lié plus bas, sans guillemets ni from __future__
    import annotations : NameError à l'import avant 3.14, défaut si --cible < 3.14).
    Mode --executer (désactivé par défaut) : importe le module puis lit chaque
    annotation avec annotationlib.get_annotations(…, format=Format.FORWARDREF)
    (module annotationlib, nouveau en 3.14) ; les chaînes sont évaluées par
    ForwardRef.evaluate(format=FORWARDREF) ; les ForwardRef restés non résolus
    sont listés et recoupés avec l'analyse statique.
HYPOTHÈSES
    Le source est du Python que la grammaire de l'interpréteur accepte ; les
    builtins sont ceux de cet interpréteur ; TYPE_CHECKING vaut False à
    l'exécution. Pour --executer : CPython 3.14 et un module importable seul.
LIMITES
    Les noms créés dynamiquement (globals(), setattr, exec) sont déclarés
    indéfinis ; seule la base d'une chaîne d'attributs (typing.X) est résolue,
    pas X ; typing.get_type_hints ne voit pas la classe englobante pour une
    annotation en chaîne de méthode, alors que l'outil la compte comme visible.
    RISQUE DU MODE --executer : il exécute tout le code de niveau module du
    fichier et de ce qu'il importe (effets de bord, réseau, écritures,
    sys.exit) ; le dossier du fichier est ajouté provisoirement à sys.path. À
    réserver à du code de confiance, jamais au code d'un dépôt inconnu.
CONTRE-EXEMPLES
    @@CONTRE_EXEMPLE@@
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Sources Python 3 annotés (fichiers ou arbres de .py) ; code lu par des
    bibliothèques qui évaluent les annotations à l'exécution ; CPython 3.14
    (évaluation différée) et versions antérieures via --cible.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import importlib.util
import io
import json
import os
import sys
import tokenize
import typing
import warnings
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import annotationlib  # stdlib, nouveau en Python 3.14 : seulement pour --executer
except ImportError:
    annotationlib = None

RACINE = Path(__file__).resolve().parent

SECTION_HYPOTHESES = "HYPOTHÈSES"
SECTION_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", SECTION_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", SECTION_INVOCATION, "DOMAINE")
GARDE_TYPAGE = "TYPE_CHECKING"
MODE_EXECUTION = "FORWARDREF"
RISQUE_EXECUTION = "RISQUE"

CATEGORIES = ("indefini", "type_checking_seulement", "chaine_invalide", "peut_etre_indefini",
              "reference_en_avant")
CATEGORIES_DEFAUT = frozenset({"indefini", "type_checking_seulement", "chaine_invalide"})
NOMS_MAGIQUES = frozenset({"__file__", "__name__", "__doc__", "__package__", "__spec__",
                           "__loader__", "__builtins__", "__path__", "__debug__"})
TAILLE_MAX_DEFAUT = 5_000_000
MAX_FICHIERS_DEFAUT = 20_000
MAX_EXAMINES = 200
DOSSIERS_IGNORES = frozenset({
    "__pycache__", ".git", ".hg", ".svn", ".tox", ".nox", ".venv", "venv",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


class EntreeInvalide(Exception):
    """Entrée refusée : chemin absent, fichier illisible ou non analysable."""


@dataclass(frozen=True)
class LiaisonModule:
    """Liaison d'un nom au niveau module."""

    position: tuple[int, int]
    type_checking: bool


@dataclass(frozen=True)
class Constat:
    """Une référence d'annotation qui ne se résoudra pas."""

    categorie: str
    nom: str
    ligne: int
    site: str
    message: str


@dataclass
class Contexte:
    """Portées visibles depuis une annotation."""

    classes: list[frozenset[str]] = field(default_factory=list)
    fonctions: list[frozenset[str]] = field(default_factory=list)
    types: list[frozenset[str]] = field(default_factory=list)
    dans_fonction: bool = False


# --------------------------------------------------------------------------- #
# Lecture
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


def analyser_arbre(source: str, libelle: str) -> ast.Module:
    """Arbre ast ; lève EntreeInvalide si le source ne se lit pas."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(source, filename=libelle)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as erreur:
            raise EntreeInvalide(f"{libelle} : non analysable ({erreur})") from erreur


# --------------------------------------------------------------------------- #
# Liaisons
# --------------------------------------------------------------------------- #

def est_type_checking(test: ast.expr) -> bool:
    """Reconnaît `if TYPE_CHECKING` et `if typing.TYPE_CHECKING`."""
    return (isinstance(test, ast.Name) and test.id == GARDE_TYPAGE) or (
        isinstance(test, ast.Attribute) and test.attr == GARDE_TYPAGE)


def noms_d_instruction(instruction: ast.stmt) -> list[str]:
    """Noms liés par une instruction simple (hors portées imbriquées)."""
    if isinstance(instruction, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in instruction.names if a.name != "*"]
    if isinstance(instruction, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [instruction.name]
    if isinstance(instruction, ast.TypeAlias):
        return [instruction.name.id]
    noms = []
    for noeud in ast.walk(instruction):
        if isinstance(noeud, ast.Name) and isinstance(noeud.ctx, ast.Store):
            noms.append(noeud.id)
        elif isinstance(noeud, ast.ExceptHandler) and noeud.name:
            noms.append(noeud.name)
        elif isinstance(noeud, (ast.MatchAs, ast.MatchStar)) and noeud.name:
            noms.append(noeud.name)
    return noms


def sous_blocs(instruction: ast.stmt) -> list[tuple[list[ast.stmt], bool]]:
    """Blocs d'une instruction composée, avec le drapeau « sous TYPE_CHECKING »."""
    if isinstance(instruction, ast.If):
        return [(instruction.body, est_type_checking(instruction.test)), (instruction.orelse, False)]
    blocs = []
    for nom in ("body", "orelse", "finalbody"):
        blocs.append((getattr(instruction, nom, None) or [], False))
    for gestionnaire in getattr(instruction, "handlers", None) or []:
        blocs.append((gestionnaire.body, False))
    for cas in getattr(instruction, "cases", None) or []:
        blocs.append((cas.body, False))
    return blocs


def entete(instruction: ast.stmt) -> list[ast.AST]:
    """Parties d'une instruction composée qui lient des noms hors de ses blocs."""
    parties: list[ast.AST] = []
    for nom in ("target", "items", "handlers", "cases", "test", "iter", "subject"):
        valeur = getattr(instruction, nom, None)
        if isinstance(valeur, list):
            parties.extend(v.pattern if isinstance(v, ast.match_case) else v for v in valeur)
        elif valeur is not None:
            parties.append(valeur)
    return parties


def liaisons_bloc(corps: list[ast.stmt], sous_tc: bool, liaisons: dict[str, list[LiaisonModule]]) -> bool:
    """Remplit les liaisons d'un bloc de niveau module ; rend True si un import * est vu."""
    etoile = False
    composees = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try,
                 ast.TryStar, ast.Match)
    for instruction in corps:
        if isinstance(instruction, ast.ImportFrom) and any(a.name == "*" for a in instruction.names):
            etoile = True
        if isinstance(instruction, composees):
            noms = [n for partie in entete(instruction) for n in noms_d_expression(partie)]
            for bloc, tc in sous_blocs(instruction):
                etoile |= liaisons_bloc(bloc, sous_tc or tc, liaisons)
        else:
            noms = noms_d_instruction(instruction)
        fin = isinstance(instruction, ast.ClassDef)
        position = (instruction.end_lineno or instruction.lineno, instruction.end_col_offset or 0) if fin else (
            instruction.lineno, instruction.col_offset)
        for nom in noms:
            liaisons.setdefault(nom, []).append(LiaisonModule(position, sous_tc))
    return etoile


def noms_d_expression(partie: ast.AST) -> list[str]:
    """Noms liés dans une partie d'en-tête (cible de for, with … as, except … as, motif)."""
    noms = []
    for noeud in ast.walk(partie):
        if isinstance(noeud, ast.Name) and isinstance(noeud.ctx, ast.Store):
            noms.append(noeud.id)
        elif isinstance(noeud, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and noeud.name:
            noms.append(noeud.name)
    return noms


def globales_declarees(arbre: ast.Module) -> set[str]:
    """Noms déclarés `global` dans une fonction (liés au module à l'exécution)."""
    return {nom for noeud in ast.walk(arbre) if isinstance(noeud, ast.Global) for nom in noeud.names}


def noms_de_portee(noeud: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda) -> frozenset[str]:
    """Noms liés dans une fonction ou une classe, sans entrer dans les portées imbriquées."""
    noms: set[str] = set()
    if not isinstance(noeud, ast.ClassDef):
        args = noeud.args
        noms.update(a.arg for a in args.posonlyargs + args.args + args.kwonlyargs)
        noms.update(a.arg for a in (args.vararg, args.kwarg) if a is not None)
    pile: list[ast.AST] = list(noeud.body) if isinstance(noeud.body, list) else []
    while pile:
        courant = pile.pop()
        if isinstance(courant, ast.stmt):
            noms.update(noms_d_instruction(courant) if not hasattr(courant, "body") else
                        [n for p in entete(courant) for n in noms_d_expression(p)])
        if isinstance(courant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms.add(courant.name)
            continue
        if isinstance(courant, (ast.NamedExpr,)):
            noms.add(courant.target.id)
        pile.extend(ast.iter_child_nodes(courant))
    return frozenset(noms)


# --------------------------------------------------------------------------- #
# Références des annotations
# --------------------------------------------------------------------------- #

def references(expr: ast.expr, en_chaine: bool = False) -> list[tuple[str, int, bool, str | None]]:
    """(nom, ligne, vient d'une chaîne, erreur de chaîne) pour une annotation."""
    trouves: list[tuple[str, int, bool, str | None]] = []
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return references_de_chaine(expr)
    if isinstance(expr, ast.Name):
        return [(expr.id, expr.lineno, en_chaine, None)]
    if isinstance(expr, ast.Attribute):
        return references(expr.value, en_chaine)
    if isinstance(expr, ast.Subscript):
        tete = expr.value.attr if isinstance(expr.value, ast.Attribute) else getattr(expr.value, "id", "")
        trouves.extend(references(expr.value, en_chaine))
        elements = expr.slice.elts if isinstance(expr.slice, ast.Tuple) else [expr.slice]
        if tete == "Literal":
            return trouves
        if tete == "Annotated":
            elements = elements[:1]
        for element in elements:
            trouves.extend(references(element, en_chaine))
        return trouves
    for enfant in ast.iter_child_nodes(expr):
        if isinstance(enfant, ast.expr):
            trouves.extend(references(enfant, en_chaine))
    return trouves


def references_de_chaine(constante: ast.Constant) -> list[tuple[str, int, bool, str | None]]:
    """Analyse une annotation en chaîne comme une expression."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            expr = ast.parse(constante.value.strip(), mode="eval").body
        except (SyntaxError, ValueError) as erreur:
            return [(constante.value, constante.lineno, True, str(erreur))]
    return [(nom, constante.lineno, True, None) for nom, _, _, _ in references(expr, True)]


@dataclass
class Module:
    """Ce que l'on sait du module pour résoudre un nom."""

    liaisons: dict[str, list[LiaisonModule]]
    globales: set[str]
    etoile: bool
    differe: bool


def resoudre(nom: str, ctx: Contexte, module: Module, position: tuple[int, int], en_chaine: bool
             ) -> tuple[str, str] | None:
    """Catégorie et explication si le nom ne se résoudra pas ; None sinon."""
    if any(nom in noms for noms in ctx.types + ctx.classes + ctx.fonctions):
        return None
    liaisons = module.liaisons.get(nom, [])
    executees = [l for l in liaisons if not l.type_checking]
    if executees or nom in module.globales:
        avant = any(l.position <= position for l in executees) or nom in module.globales
        if avant or en_chaine or module.differe or ctx.dans_fonction:
            return None
        return "reference_en_avant", (f"« {nom} » est lié plus bas (ligne {executees[0].position[0]}) : "
                                      f"NameError à l'import avant Python 3.14 sans from __future__ import annotations")
    if nom in dir(builtins) or nom in NOMS_MAGIQUES:
        return None
    if liaisons:
        return "type_checking_seulement", (f"« {nom} » n'est lié que sous if TYPE_CHECKING (ligne "
                                           f"{liaisons[0].position[0]}) : NameError quand l'annotation est évaluée")
    if module.etoile:
        return "peut_etre_indefini", f"« {nom} » n'est lié nulle part, sauf peut-être par un import *"
    return "indefini", f"« {nom} » n'est lié nulle part : NameError quand l'annotation est évaluée"


class Collecteur:
    """Parcourt le module et confronte chaque annotation évaluée aux portées visibles."""

    def __init__(self, module: Module) -> None:
        self.module = module
        self.ctx = Contexte()
        self.constats: list[Constat] = []
        self.examinees = 0

    def annotation(self, expr: ast.expr | None, site: str, position: tuple[int, int]) -> None:
        if expr is None:
            return
        self.examinees += 1
        for nom, ligne, en_chaine, erreur in references(expr):
            if erreur is not None:
                self.constats.append(Constat("chaine_invalide", nom, ligne, site,
                                             f"annotation en chaîne illisible ({erreur})"))
                continue
            verdict = resoudre(nom, self.ctx, self.module, position, en_chaine)
            if verdict is not None:
                self.constats.append(Constat(verdict[0], nom, ligne, site, verdict[1]))

    def bloc(self, corps: list[ast.stmt], prefixe: str) -> None:
        for instruction in corps:
            self.instruction(instruction, prefixe)

    def instruction(self, noeud: ast.stmt, prefixe: str) -> None:
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.fonction(noeud, prefixe)
        elif isinstance(noeud, ast.ClassDef):
            self.classe(noeud, prefixe)
        elif isinstance(noeud, ast.AnnAssign) and not self.ctx.dans_fonction:
            cible = ast.unparse(noeud.target)
            self.annotation(noeud.annotation, f"{prefixe or 'module'} : {cible}", (noeud.lineno, noeud.col_offset))
        elif isinstance(noeud, ast.TypeAlias):
            self.alias(noeud, prefixe)
        else:
            for bloc, _ in sous_blocs(noeud) if hasattr(noeud, "body") else []:
                self.bloc(bloc, prefixe)

    def parametres_type(self, noeud: ast.AST, site: str) -> frozenset[str]:
        parametres = getattr(noeud, "type_params", None) or []
        noms = frozenset(p.name for p in parametres)
        self.ctx.types.append(noms)
        for param in parametres:
            self.annotation(getattr(param, "bound", None), f"{site} : borne de {param.name}",
                            (noeud.lineno, noeud.col_offset))
        return noms

    def fonction(self, noeud: ast.FunctionDef | ast.AsyncFunctionDef, prefixe: str) -> None:
        nom = f"{prefixe}.{noeud.name}" if prefixe else noeud.name
        position = (noeud.lineno, noeud.col_offset)
        self.parametres_type(noeud, nom)
        args = noeud.args
        for argument in args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]:
            self.annotation(argument.annotation, f"{nom} : paramètre {argument.arg}", position)
        self.annotation(noeud.returns, f"{nom} : retour", position)
        classes, self.ctx.classes = self.ctx.classes, []
        dedans, self.ctx.dans_fonction = self.ctx.dans_fonction, True
        self.ctx.fonctions.append(noms_de_portee(noeud))
        self.bloc(noeud.body, nom)
        self.ctx.fonctions.pop()
        self.ctx.classes, self.ctx.dans_fonction = classes, dedans
        self.ctx.types.pop()

    def classe(self, noeud: ast.ClassDef, prefixe: str) -> None:
        nom = f"{prefixe}.{noeud.name}" if prefixe else noeud.name
        self.parametres_type(noeud, nom)
        dedans, self.ctx.dans_fonction = self.ctx.dans_fonction, False
        self.ctx.classes.append(noms_de_portee(noeud))
        self.bloc(noeud.body, nom)
        self.ctx.classes.pop()
        self.ctx.dans_fonction = dedans
        self.ctx.types.pop()

    def alias(self, noeud: ast.AST, prefixe: str) -> None:
        nom = f"{prefixe}.{noeud.name.id}" if prefixe else noeud.name.id
        self.parametres_type(noeud, f"type {nom}")
        differe, self.module.differe = self.module.differe, True
        self.annotation(noeud.value, f"type {nom} : valeur", (noeud.lineno, noeud.col_offset))
        self.module.differe = differe
        self.ctx.types.pop()


def annonce_differe(arbre: ast.Module) -> bool:
    """Vrai si le module déclare from __future__ import annotations."""
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in arbre.body)


def analyser_statique(arbre: ast.Module) -> tuple[list[Constat], int]:
    """Constats statiques et nombre d'annotations examinées."""
    liaisons: dict[str, list[LiaisonModule]] = {}
    etoile = liaisons_bloc(arbre.body, False, liaisons)
    collecteur = Collecteur(Module(liaisons, globales_declarees(arbre), etoile, annonce_differe(arbre)))
    collecteur.bloc(arbre.body, "")
    return sorted(set(collecteur.constats), key=lambda c: (c.ligne, c.nom, c.site)), collecteur.examinees


# --------------------------------------------------------------------------- #
# Mode --executer (annotationlib, Python 3.14)
# --------------------------------------------------------------------------- #

def references_restees(valeur: object, vues: set[int] | None = None) -> list[str]:
    """Noms des ForwardRef non résolus, y compris dans les arguments génériques."""
    vues = set() if vues is None else vues
    if id(valeur) in vues:
        return []
    vues.add(id(valeur))
    if isinstance(valeur, annotationlib.ForwardRef):
        return [valeur.__forward_arg__]
    trouves = []
    for argument in typing.get_args(valeur):
        trouves.extend(references_restees(argument, vues))
    return trouves


def evaluer(valeur: object, proprietaire: object) -> tuple[list[str], str | None]:
    """ForwardRef restants d'une annotation (les chaînes sont évaluées en FORWARDREF)."""
    if isinstance(valeur, str):
        try:
            valeur = annotationlib.ForwardRef(valeur, owner=proprietaire).evaluate(
                format=annotationlib.Format.FORWARDREF)
        except (SyntaxError, TypeError, ValueError, AttributeError, NameError) as erreur:
            return [], f"{type(erreur).__name__}: {erreur}"
    return references_restees(valeur), None


def objets_annotes(module: object) -> list[tuple[str, object]]:
    """Le module, ses fonctions et ses classes (et leurs méthodes) définies dans ce module."""
    objets: list[tuple[str, object]] = [("module", module)]
    for nom, valeur in vars(module).items():
        if getattr(valeur, "__module__", None) != module.__name__:
            continue
        if isinstance(valeur, type):
            objets.append((nom, valeur))
            for attribut, membre in vars(valeur).items():
                fonction = getattr(membre, "__func__", getattr(membre, "fget", membre))
                if callable(fonction) and hasattr(fonction, "__annotate__"):
                    objets.append((f"{nom}.{attribut}", fonction))
        elif callable(valeur) and hasattr(valeur, "__annotate__"):
            objets.append((nom, valeur))
    return objets


def importer_module(chemin: Path) -> object:
    """Importe le fichier comme module isolé. EXÉCUTE son code de niveau module."""
    nom = f"_resoudre_annotations_{abs(hash(str(chemin)))}"
    spec = importlib.util.spec_from_file_location(nom, chemin)
    if spec is None or spec.loader is None:
        raise ImportError(f"pas de chargeur pour {chemin}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    sys.path.insert(0, str(chemin.parent))
    try:
        with contextlib.redirect_stdout(sys.stderr), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(chemin.parent))
    return module


def analyser_execution(chemin: Path) -> dict[str, object]:
    """Lit chaque annotation en FORWARDREF après import ; liste les références non résolues."""
    try:
        module = importer_module(chemin)
    except (Exception, SystemExit) as erreur:  # code étranger : tout échec d'import est rapporté
        return {"import": "échec", "erreur": f"{type(erreur).__name__}: {erreur}", "non_resolues": []}
    restes = []
    for site, objet in objets_annotes(module):
        try:
            annotations = annotationlib.get_annotations(objet, format=annotationlib.Format.FORWARDREF)
        except (Exception, SystemExit) as erreur:  # __annotate__ arbitraire : rapporté, pas masqué
            restes.append({"site": site, "cle": None, "nom": None, "erreur": f"{type(erreur).__name__}: {erreur}"})
            continue
        for cle, valeur in annotations.items():
            noms, erreur = evaluer(valeur, objet)
            restes.extend({"site": site, "cle": cle, "nom": n} for n in noms)
            if erreur:
                restes.append({"site": site, "cle": cle, "nom": None, "erreur": erreur})
    return {"import": "réussi", "non_resolues": restes}


def recouper(constats: list[Constat], execution: dict[str, object]) -> dict[str, list[str]]:
    """Noms non résolus vus par l'exécution et par l'analyse statique."""
    statiques = {c.nom for c in constats if c.categorie in CATEGORIES_DEFAUT | {"peut_etre_indefini"}}
    dynamiques = {str(r["nom"]) for r in execution["non_resolues"] if r.get("nom")}
    return {"communs": sorted(statiques & dynamiques), "seulement_statique": sorted(statiques - dynamiques),
            "seulement_execution": sorted(dynamiques - statiques)}


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
        description=("Vérifie, sans exécuter le code, que chaque nom des annotations évaluées se "
                     "résoudra (sinon NameError à la lecture). --executer importe le module et lit "
                     "les annotations avec annotationlib (Format.FORWARDREF)."),
        epilog=(f"Exemple : python {RACINE / 'resoudre_annotations.py'} src/ --cible 3.12 --json\n"
                "Codes : 0 rien à signaler, 1 référence non résoluble, 2 entrée invalide, "
                "3 rien à examiner. --executer EXÉCUTE le code du module : code de confiance seulement."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="+", type=Path, help="fichiers .py ou dossiers")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="base des chemins relatifs et de l'affichage (défaut : dossier courant)")
    parseur.add_argument("--cible", default=None,
                         help="version visée (ex. 3.12) : sous 3.14, les références en avant deviennent des défauts")
    parseur.add_argument("--executer", action="store_true",
                         help="importer chaque module (EXÉCUTE son code) et lire ses annotations en FORWARDREF")
    parseur.add_argument("--taille-max", type=int, default=TAILLE_MAX_DEFAUT, help="octets maximum par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=MAX_FICHIERS_DEFAUT, help="nombre maximum de fichiers")
    return parseur


def lire_version(texte: str | None) -> tuple[int, int]:
    """Version cible (défaut : interpréteur courant)."""
    if texte is None:
        return sys.version_info[0], sys.version_info[1]
    morceaux = texte.strip().split(".")
    if len(morceaux) != 2 or not all(m.isdigit() for m in morceaux):
        raise EntreeInvalide(f"version cible illisible : {texte!r} (attendu par exemple 3.12)")
    return int(morceaux[0]), int(morceaux[1])


def libelle_chemin(chemin: Path, base: Path) -> str:
    """Chemin affiché, relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def examiner_fichier(chemin: Path, libelle: str, args: argparse.Namespace) -> dict[str, object]:
    """Analyse statique (et dynamique si demandée) d'un fichier."""
    arbre = analyser_arbre(lire_source(chemin, args.taille_max), libelle)
    constats, examinees = analyser_statique(arbre)
    entree: dict[str, object] = {
        "chemin": libelle, "annotations_examinees": examinees,
        "constats": [{"categorie": c.categorie, "nom": c.nom, "ligne": c.ligne, "site": c.site,
                      "message": c.message} for c in constats],
    }
    if args.executer:
        execution = analyser_execution(chemin.resolve())
        execution["recoupement"] = recouper(constats, execution)
        entree["execution"] = execution
    return entree


def examiner(fichiers: list[Path], explicites: set[Path], base: Path, args: argparse.Namespace
             ) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Examine chaque fichier ; un fichier explicite illisible est une entrée invalide."""
    resultats, rejets = [], []
    for chemin in fichiers:
        libelle = libelle_chemin(chemin, base)
        try:
            resultats.append(examiner_fichier(chemin, libelle, args))
        except EntreeInvalide as erreur:
            if chemin in explicites:
                raise
            print(f"non analysé : {erreur}", file=sys.stderr)
            rejets.append({"chemin": libelle, "raison": str(erreur)})
    return resultats, rejets


def defauts(resultats: list[dict[str, object]], cible: tuple[int, int]) -> list[dict[str, object]]:
    """Constats qui comptent comme défauts pour la version cible."""
    comptees = CATEGORIES_DEFAUT | ({"reference_en_avant"} if cible < (3, 14) else set())
    trouves = [c for r in resultats for c in r["constats"] if c["categorie"] in comptees]
    for r in resultats:
        execution = r.get("execution") or {}
        trouves.extend(x for x in execution.get("non_resolues", []))
        if execution.get("import") == "échec":
            trouves.append({"erreur": execution.get("erreur")})
    return trouves


def afficher_humain(sortie: dict[str, object]) -> None:
    """Affichage lisible par un humain."""
    print(f"resoudre_annotations — {sortie['denominateur']} fichier(s), "
          f"{sortie['annotations_examinees']} annotation(s) évaluée(s), {sortie['nombre_defauts']} défaut(s)")
    for fichier in sortie["fichiers"]:
        for c in fichier["constats"]:
            print(f"  {fichier['chemin']}:{c['ligne']} [{c['categorie']}] {c['site']} — {c['message']}")
        execution = fichier.get("execution")
        if execution:
            print(f"  {fichier['chemin']} --executer : import {execution['import']}, "
                  f"{len(execution['non_resolues'])} ForwardRef non résolu(s) ; recoupement {execution['recoupement']}")


def afficher_json(sortie: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def preparer(args: argparse.Namespace) -> tuple[tuple[int, int], Path]:
    """Valide les options ; lève EntreeInvalide."""
    if args.executer and annotationlib is None:
        raise EntreeInvalide("--executer exige annotationlib (Python 3.14 ou plus)")
    if args.executer:
        print(f"{RISQUE_EXECUTION} : --executer importe et exécute le code des modules analysés",
              file=sys.stderr)
    return lire_version(args.cible), args.racine or Path.cwd()


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args(argv)
    try:
        cible, base = preparer(args)
        chemins, explicites = collecter_cibles([c if c.is_absolute() else base / c for c in args.chemins],
                                               max(1, args.max_fichiers))
        resultats, rejets = examiner(chemins, explicites, base, args)
    except EntreeInvalide as erreur:
        print(f"entrée invalide : {erreur}", file=sys.stderr)
        return 2
    trouves = defauts(resultats, cible)
    examines = [r["chemin"] for r in resultats]
    sortie: dict[str, object] = {
        "outil": "resoudre_annotations", "moteur": "stdlib",
        "mode": f"statique+execution ({MODE_EXECUTION})" if args.executer else "statique",
        "cible": f"{cible[0]}.{cible[1]}", "denominateur": len(resultats),
        "examines": examines[:MAX_EXAMINES], "examines_tronques": len(examines) > MAX_EXAMINES,
        "non_analyses": rejets, "annotations_examinees": sum(int(r["annotations_examinees"]) for r in resultats),
        "par_categorie": {k: sum(1 for r in resultats for c in r["constats"] if c["categorie"] == k) for k in CATEGORIES},
        "nombre_defauts": len(trouves), "fichiers": [r for r in resultats if r["constats"] or "execution" in r],
        "contrat": extraire_contrat(__doc__ or ""),
    }
    if args.json:
        afficher_json(sortie)
    if not resultats:
        print("dénominateur nul : aucun fichier Python analysable, rien à examiner", file=sys.stderr)
        return 3
    if not args.json:
        afficher_humain(sortie)
    return 1 if trouves else 0


if __name__ == "__main__":
    raise SystemExit(main())
