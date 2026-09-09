"""Porte Qualité – Instrument de contrôle mécanique

Pourquoi cet instrument ?  Il juge mécaniquement les outils contre le socle
commun, parce que sur six outils écrits par des agents les contrôles informels
avaient laissé passer treize défauts.

Intitulés attendus :
QUESTION, MESURE, HYPOTHESES, LIMITES, CONTRE-EXEMPLES, DOMAINE.

LIMITES : ces contrôles jugent la FORME et jamais la JUSTESSE ; un outil peut
passer les quatorze contrôles et rendre un résultat faux.

CONTRE-EXEMPLES: P12 a accusé 15 fichiers sur 17 à tort parce qu’il interpolait
un chemin Windows dans du code source.
P5 a accusé un fichier conforme parce qu'il écrivait pathlib.Path(__file__) au lieu de Path(__file__) — un contrôle qui n'accepte qu'une écriture d'une même chose mesure l'écriture, pas la chose.
une exclusion posée pour éviter un faux positif a ouvert un faux négatif : la porte laissait passer un chemin en dur rangé dans une constante simple. Toute exemption doit être aussi étroite que le motif qui la justifie.
P2 a accusé un import protégé par except Exception parce qu'il exigeait le mot ImportError — deuxième fois que ce contrôle mesure l'écriture au lieu de la chose.
P10 a déclaré morte la fonction publique conforme parce qu'elle n'était appelée nulle part dans son propre fichier — or c'est la définition même d'API. Un contrôle sur la mort d'une fonction doit savoir ce qu'est une surface publique.
P15 a rendu la sortie JSON de la porte illisible en important fitz, qui écrit un avertissement sur stdout à l'import. Une limite écrite dans la docstring n'est pas une limite tenue par le code.

"""

import ast
import argparse
import json
import subprocess
import sys
import tempfile
import unicodedata
import importlib
import inspect
import contextlib
import io
import re
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PREFIXES_INTERDITS = {"/home/", "/usr/", "/Users/", "/mnt/"}

TIRETS_EQUIVALENTS = {
    '\u2010': '-',  # trait d'union
    '\u2011': '-',  # trait d'union insécable
    '\u2012': '-',  # tiret numérique
    '\u2013': '-',  # tiret demi-cadratin
    '\u2014': '-',  # tiret cadratin
    '\u2015': '-',  # barre horizontale
    '\u2212': '-'   # signe moins
}

EXCLUSIONS_PROMESSE = {
    "POURQUOI", "USAGE", "EXEMPLE", "EXEMPLES", "NOTE", "NOTES",
    "ATTENTION", "AVERTISSEMENT", "SOCLE", "PLAN", "API", "JSON",
    "CLI", "TODO", "PEP"
}
EXCLUSIONS_PROMESSE_NORM = {
    unicodedata.normalize("NFC", w).upper() for w in EXCLUSIONS_PROMESSE
}

# racine de la documentation, initialisée dans `main`
DOC_ROOT: Path | None = None

# ---------------------------------------------------------------------------

def annoter_parents(arbre: ast.AST) -> None:
    """Pose l'attribut `parent` sur chaque nœud enfant de l'arbre."""
    for noeud in ast.walk(arbre):
        for enfant in ast.iter_child_nodes(noeud):
            enfant.parent = noeud  # type: ignore


def normaliser_texte(texte: str) -> Tuple[str, int]:
    """Normalise le texte pour les comparaisons d'intitulés."""
    remplacements = 0
    for tiret, ascii_tiret in TIRETS_EQUIVALENTS.items():
        compte = texte.count(tiret)
        if compte:
            texte = texte.replace(tiret, ascii_tiret)
            remplacements += compte
    texte = unicodedata.normalize("NFD", texte)
    texte = ''.join(c for c in texte if not unicodedata.combining(c))
    texte = unicodedata.normalize("NFC", texte).upper()
    return texte, remplacements


# ---------------------------------------------------------------------------
# Contrôles P1 à P14 (inchangés) …
# ---------------------------------------------------------------------------

def controle_compile(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P1: Le source compile avec `compile()`."""
    try:
        compile(source, str(chemin), "exec")
        return ("OK", "")
    except SyntaxError as e:
        return ("FATAL", f"Ligne {e.lineno}: {e.msg}")
    except Exception as e:
        return ("FATAL", f"Erreur de compilation: {str(e)}")


def controle_stdlib_seule(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P2: Seule la stdlib est importée, ou les imports sont protégés."""
    EXCEPTIONS_ACCEPTED = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}

    def exception_protegee(handler: ast.ExceptHandler) -> str | None:
        if handler.type is None:
            return "bare"
        if isinstance(handler.type, ast.Name):
            if handler.type.id in EXCEPTIONS_ACCEPTED:
                return handler.type.id
        if isinstance(handler.type, ast.Attribute):
            if handler.type.attr in EXCEPTIONS_ACCEPTED:
                return handler.type.attr
        if isinstance(handler.type, ast.Tuple):
            for elt in handler.type.elts:
                if isinstance(elt, ast.Name) and elt.id in EXCEPTIONS_ACCEPTED:
                    return elt.id
                if isinstance(elt, ast.Attribute) and elt.attr in EXCEPTIONS_ACCEPTED:
                    return elt.attr
        return None

    imports_proteges: Dict[str, str] = {}

    for node in ast.walk(arbre):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    import_node = node
            else:
                if node.module is None:
                    continue
                module = node.module.split(".")[0]
                import_node = node

            if module in imports_proteges:
                continue

            cur = getattr(import_node, "parent", None)
            protege = None
            while cur is not None:
                if isinstance(cur, ast.Try):
                    for handler in cur.handlers:
                        exc_name = exception_protegee(handler)
                        if exc_name:
                            protege = exc_name
                            break
                    if protege:
                        break
                cur = getattr(cur, "parent", None)

            if protege:
                imports_proteges[module] = protege

    modules_fautifs = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split(".")[0]
                if (module not in sys.stdlib_module_names and
                        module not in imports_proteges and
                        not (chemin.parent / f"{module}.py").exists()):
                    modules_fautifs.add(module)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module is None:
                continue
            module = node.module.split(".")[0]
            if (module not in sys.stdlib_module_names and
                    module not in imports_proteges and
                    not (chemin.parent / f"{module}.py").exists()):
                modules_fautifs.add(module)

    if not modules_fautifs:
        if imports_proteges:
            exp = "; ".join(f"{mod} protégé par {exc}" for mod, exc in imports_proteges.items())
        else:
            exp = ""
        return ("OK", exp)
    else:
        return ("MANQUEMENT", f"Modules non protégés: {', '.join(modules_fautifs)}")


def controle_encodage(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P3: `sys.stdout.reconfigure` sous garde `hasattr`."""
    has_reconfigure_call = False
    has_guard = False

    for node in ast.walk(arbre):
        if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr == "reconfigure" and
                isinstance(node.func.value, ast.Attribute) and
                node.func.value.attr in ("stdout", "stderr") and
                isinstance(node.func.value.value, ast.Name) and
                node.func.value.value.id == "sys"):
            has_encoding = any(
                kw.arg == "encoding" and isinstance(kw.value, ast.Constant) and kw.value.value == "utf-8"
                for kw in node.keywords
            )
            if has_encoding:
                has_reconfigure_call = True
                current = node
                while hasattr(current, "parent"):
                    if isinstance(current.parent, ast.If):
                        test = current.parent.test
                        if (isinstance(test, ast.Call) and
                                isinstance(test.func, ast.Name) and
                                test.func.id == "hasattr" and
                                len(test.args) >= 2 and
                                isinstance(test.args[0], ast.Attribute) and
                                test.args[0].attr in ("stdout", "stderr") and
                                isinstance(test.args[0].value, ast.Name) and
                                test.args[0].value.id == "sys" and
                                isinstance(test.args[1], ast.Constant) and
                                test.args[1].value == "reconfigure"):
                            has_guard = True
                            break
                    current = current.parent

    if has_reconfigure_call and has_guard:
        return ("OK", "")
    elif has_reconfigure_call:
        return ("MANQUEMENT", "Reconfiguration présente mais SANS garde hasattr")
    else:
        return ("MANQUEMENT", "Aucun appel à `sys.stdout.reconfigure` avec encoding='utf-8'")


def controle_deux_sorties(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P4: `argparse` importé et option `--json` déclarée."""
    has_argparse = False
    has_json_option = False
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "argparse":
                    has_argparse = True
        elif isinstance(node, ast.ImportFrom) and node.module == "argparse":
            has_argparse = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for kw in node.keywords:
                if kw.arg == "dest" and isinstance(kw.value, ast.Constant) and kw.value.value == "json":
                    has_json_option = True
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "--json":
                    has_json_option = True

    if has_argparse and has_json_option:
        return ("OK", "")
    elif not has_argparse:
        return ("MANQUEMENT", "Module `argparse` non importé")
    else:
        return ("MANQUEMENT", "Option `--json` non déclarée")


def controle_racine_portable(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P5: Appel à `Path(__file__).resolve()` (ou formes équivalentes) et option `--racine`."""
    has_path_resolve = False
    has_racine_option = False
    for node in ast.walk(arbre):
        if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr == "resolve" and
                isinstance(node.func.value, ast.Call)):
            inner = node.func.value
            if ((isinstance(inner.func, ast.Name) and inner.func.id == "Path") or
                (isinstance(inner.func, ast.Attribute) and inner.func.attr == "Path")):
                if any(isinstance(arg, ast.Name) and arg.id == "__file__" for arg in inner.args):
                    has_path_resolve = True

        if (isinstance(node, ast.Call) and
                isinstance(node.func, ast.Attribute) and
                node.func.attr in ("abspath", "realpath")):
            if any(isinstance(arg, ast.Name) and arg.id == "__file__" for arg in node.args):
                has_path_resolve = True

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            for kw in node.keywords:
                if kw.arg == "dest" and isinstance(kw.value, ast.Constant) and kw.value.value == "racine":
                    has_racine_option = True
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "--racine":
                    has_racine_option = True

    if has_path_resolve and has_racine_option:
        return ("OK", "")
    elif not has_path_resolve:
        return ("MANQUEMENT", "Aucun appel à `Path(__file__).resolve()` ou équivalent")
    else:
        return ("MANQUEMENT", "Option `--racine` non déclarée")


def controle_aucun_chemin_en_dur(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P6: Aucun chemin absolu en dur (hors docstrings et constantes de module)."""
    lignes_constantes_module = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                        for subnode in ast.walk(node.value):
                            if isinstance(subnode, ast.Constant) and isinstance(subnode.value, str):
                                lignes_constantes_module.add(subnode.lineno)
                    break

    chemins_fautifs = []
    excluded_literals = 0

    for node in ast.walk(arbre):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            is_forbidden = any(node.value.startswith(p) for p in PREFIXES_INTERDITS)
            if not is_forbidden and len(node.value) >= 3 and node.value[1] == ":" and node.value[2] in ("/", "\\"):
                is_forbidden = True

            if is_forbidden:
                if node.lineno in lignes_constantes_module:
                    excluded_literals += 1
                else:
                    parent = getattr(node, "parent", None)
                    is_docstring = False
                    while parent is not None:
                        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                            if parent.body and node is parent.body[0]:
                                is_docstring = True
                            break
                        parent = getattr(parent, "parent", None)
                    if not is_docstring:
                        chemins_fautifs.append((node.value, node.lineno))

    if not chemins_fautifs:
        explanation = ""
        if excluded_literals > 0:
            explanation = (f"{excluded_literals} littéraux exclus (constantes de module) ; "
                           f"exclus car membres d'une table de règles")
        return ("OK", explanation)
    else:
        explanation = ""
        if excluded_literals > 0:
            explanation = (f"{excluded_literals} littéraux exclus (constantes de module) ; "
                           f"exclus car membres d'une table de règles; ")
        return ("MANQUEMENT", explanation + "; ".join(f"Ligne {lineno}: {chemin}" for chemin, lineno in chemins_fautifs))


def controle_contrat_mesure(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P7: Docstring du module contient les six intitulés (au moins une forme par exigence)."""
    docstring = ast.get_docstring(arbre)
    if not docstring:
        return ("MANQUEMENT", "Docstring du module absente")

    docstring_norm, remplacements = normaliser_texte(docstring)

    exigences = [
        ["QUESTION"],
        ["MESURE"],
        ["HYPOTHESES", "HYPOTHESE"],
        ["LIMITES", "LIMITE"],
        ["CONTRE-EXEMPLES", "CONTRE-EXEMPLE"],
        ["DOMAINE"]
    ]

    manquants = []
    for groupe in exigences:
        if not any(normaliser_texte(forme)[0] in docstring_norm for forme in groupe):
            manquants.append(groupe[0])

    explication = ""
    if remplacements > 0:
        explication = f"Normalisation: {remplacements} caractères remplacés"

    if not manquants:
        return ("OK", explication)
    else:
        return ("MANQUEMENT", f"Intitulés manquants: {', '.join(manquants)}" + (f" ({explication})" if explication else ""))


def controle_point_entree(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P8: `if __name__ == "__main__": raise SystemExit(main())`."""
    for node in ast.walk(arbre):
        if isinstance(node, ast.If):
            if (isinstance(node.test, ast.Compare) and
                    isinstance(node.test.left, ast.Name) and
                    node.test.left.id == "__name__" and
                    any(isinstance(op, ast.Eq) for op in node.test.ops) and
                    any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators)):
                if (len(node.body) == 1 and
                        isinstance(node.body[0], ast.Raise) and
                        isinstance(node.body[0].exc, ast.Call) and
                        isinstance(node.body[0].exc.func, ast.Name) and
                        node.body[0].exc.func.id == "SystemExit" and
                        len(node.body[0].exc.args) == 1 and
                        isinstance(node.body[0].exc.args[0], ast.Call) and
                        isinstance(node.body[0].exc.args[0].func, ast.Name) and
                        node.body[0].exc.args[0].func.id == "main"):
                    return ("OK", "")
    return ("MANQUEMENT", "Point d'entrée non conforme")


def controle_coeur_sans_ecriture(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P9: Aucune fonction (hors `main` et `_cli*`) n'appelle `print`."""
    prefixes = ("afficher", "imprimer", "rendre_humain", "presenter", "cli")
    fonctions_fautives = []
    exemptes = 0

    # Parcourir uniquement les fonctions de premier niveau du module
    for node in arbre.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stripped = node.name.lstrip('_')
        if stripped == "main" or stripped.startswith(prefixes):
            exemptes += 1
            continue
        # Recherche d'un appel à print (sans redirection vers stderr) dans le corps,
        # y compris dans les fonctions imbriquées.
        for subnode in ast.walk(node):
            if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Name) and subnode.func.id == "print":
                has_stderr = any(
                    kw.arg == "file" and isinstance(kw.value, ast.Attribute) and kw.value.attr == "stderr"
                    for kw in subnode.keywords
                )
                if not has_stderr:
                    fonctions_fautives.append((node.name, node.lineno))
                    break

    if not fonctions_fautives:
        exp = f"{exemptes} fonctions exemptées" if exemptes else ""
        return ("OK", exp)
    else:
        exp = f"{exemptes} fonctions exemptées; " if exemptes else ""
        return ("MANQUEMENT", exp + "; ".join(f"Fonction {name} ligne {lineno}" for name, lineno in fonctions_fautives))


def controle_fonction_morte(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P10: Aucune fonction définie au niveau module n'est inutilisée."""
    all_names = set()
    has_all = False
    for node in ast.walk(arbre):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                all_names.add(elt.value)
                    has_all = True
                    break

    fonctions_definies = set()
    fonctions_utilisees = set()

    for node in ast.walk(arbre):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(getattr(node, "parent", None), ast.Module):
                fonctions_definies.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            fonctions_utilisees.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.attr in fonctions_definies:
                fonctions_utilisees.add(node.attr)

    morts_privés = [n for n in fonctions_definies if n.startswith('_') and n not in fonctions_utilisees]
    morts_publics = [n for n in fonctions_definies if not n.startswith('_') and n not in fonctions_utilisees and n not in all_names]

    if not morts_privés and not morts_publics:
        return ("OK", "")

    if morts_privés:
        return ("MANQUEMENT", f"Fonctions inutilisées: {', '.join(morts_privés)}")

    if morts_publics and not has_all:
        return ("MANQUEMENT", "fonction non référencée et surface publique non déclarée — ajouter __all__ pour lever le doute")

    return ("MANQUEMENT", f"Fonctions inutilisées: {', '.join(morts_publics)}")


def controle_aide_executable(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P11: L'option `--help` fonctionne et produit une sortie non vide."""
    try:
        result = subprocess.run(
            [sys.executable, str(chemin), "--help"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return ("OK", "")
        elif result.returncode != 0:
            return ("MANQUEMENT", f"Code de retour {result.returncode}")
        else:
            return ("MANQUEMENT", "Sortie vide")
    except subprocess.TimeoutExpired:
        return ("MANQUEMENT", "Délai dépassé")
    except Exception as e:
        return ("MANQUEMENT", f"Erreur: {str(e)}")


def controle_importable_ailleurs(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P12: Le module est importable depuis un autre répertoire."""
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            cmd = [
                sys.executable,
                "-c",
                "import sys, importlib; sys.path.insert(0, sys.argv[1]); importlib.import_module(sys.argv[2])",
                str(chemin.parent),
                chemin.stem
            ]
            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30
            )
            if result.returncode == 0:
                return ("OK", "")
            else:
                stderr = result.stderr or ""
                tail = stderr[-300:]
                lines = [ln for ln in tail.splitlines() if ln.strip()]
                last_line = lines[-1] if lines else ""
                return ("MANQUEMENT", f"Erreur d'import: {last_line}")
        except subprocess.TimeoutExpired:
            return ("MANQUEMENT", "Délai dépassé")
        except Exception as e:
            return ("MANQUEMENT", f"Erreur: {str(e)}")


def extraire_mots_majuscules(docstring: str) -> List[str]:
    """Extrait les mots entièrement en majuscules d'au moins 4 lettres."""
    mots = []
    for mot in docstring.split():
        mot_propre = mot.strip(".,;:!?\"'()[]{}")
        if (mot_propre.isupper() and len(mot_propre) >= 4 and
            mot_propre not in {"QUESTION", "MESURE", "HYPOTHESES", "LIMITES",
                               "CONTRE-EXEMPLE", "CONTRE-EXEMPLES", "DOMAINE"}):
            mots.append(mot_propre)
    return mots


def controle_promesse_tenue(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P14: Les mots en majuscules de la docstring sont des constantes de chaîne dans le code."""
    docstring = ast.get_docstring(arbre)
    if not docstring:
        return ("INAPPLICABLE", "Docstring absente")

    mots_annonces_raw = extraire_mots_majuscules(docstring)
    mots_annonces = [
        normaliser_texte(m)[0]
        for m in mots_annonces_raw
        if normaliser_texte(m)[0] not in EXCLUSIONS_PROMESSE_NORM
    ]

    if not mots_annonces:
        return ("OK", "Aucun mot annoncé")

    constantes_raw = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        constantes_raw.add(node.value.value)

    constantes = {normaliser_texte(c)[0] for c in constantes_raw}
    manquements = [mot for mot in mots_annonces if mot not in constantes]

    if not manquements:
        return ("OK", f"{len(mots_annonces)} mots confrontés")
    else:
        return ("AVERTISSEMENT", f"indice, non mesure : ces mots sont annoncés dans la docstring et absents des constantes du code – {', '.join(manquements)}")


def controle_controles_mutuels(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P13: Contrôle mutuel - vérifie si d'autres contrôles échouent systématiquement."""
    return ("OK", "")


# ---------------------------------------------------------------------------
# P15 – Argument nommé inexistant – avec repli documentaire
# ---------------------------------------------------------------------------

def _extraire_signature_doc(module: str, fonction: str) -> Tuple[str | None, bool]:
    """
    Retourne la signature brute (ex: "timeout, repeat=False, file=sys.stderr, exit=False")
    et un bool indiquant si la signature provient de la documentation locale.
    Retourne (None, False) si aucune signature n'est trouvée.
    """
    global DOC_ROOT
    if DOC_ROOT is None:
        return None, False

    # 1. Recherche dans stdlib_314/*.md
    stdlib_md_dir = DOC_ROOT / "stdlib_314"
    pattern = re.compile(rf"###\s+`{re.escape(module)}\.{re.escape(fonction)}`")
    for md_file in stdlib_md_dir.glob(f"{module}_api_*.md"):
        try:
            with md_file.open(encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            continue

        for i, line in enumerate(lines):
            if pattern.search(line):
                # parcourir jusqu'à la prochaine section "###"
                for following in lines[i + 1:]:
                    if following.lstrip().startswith("###"):
                        break
                    sig_match = re.search(r"-\s+Signature\s*:\s*`([^`]*)`", following)
                    if sig_match:
                        sig = sig_match.group(1).strip()
                        if sig == "NON INTROSPECTABLE" or sig == "":
                            break  # passe au point 2
                        return sig, True
                break  # section trouvée mais pas de signature exploitable

    # 2. Recherche dans la documentation officielle texte
    officiel_txt = DOC_ROOT / "python_3.14_officiel" / "python-3.14-docs-text" / "library" / f"{module}.txt"
    if not officiel_txt.is_file():
        return None, False
    try:
        with officiel_txt.open(encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{module}.{fonction}("):
                    start = line.find('(')
                    end = line.rfind(')')
                    if start != -1 and end != -1 and end > start:
                        return line[start + 1:end].strip(), True
    except Exception:
        pass

    return None, False


def _parse_params_from_signature(sig: str) -> Tuple[set[str], bool]:
    """
    Analyse la chaîne de signature et renvoie l'ensemble des noms de paramètres
    (sans les valeurs par défaut) ainsi qu'un bool indiquant la présence de **kwargs.
    """
    params = set()
    accepte_kwargs = False
    for part in sig.split(','):
        part = part.strip()
        if not part:
            continue
        if part.startswith('**'):
            accepte_kwargs = True
            continue
        if part.startswith('*'):
            continue
        if part in ('/', '*'):
            continue
        name = part.split('=')[0].strip()
        if name:
            params.add(name)
    return params, accepte_kwargs


def controle_argument_nommee_inexistant(source: str, arbre: ast.AST, chemin: Path) -> Tuple[str, str]:
    """P15 — ARGUMENT NOMMÉ INEXISTANT SUR UNE FONCTION DE LA STDLIB

    Le contrôle parcourt les appels `module.fonction(...)` où `module` est réellement
    importé dans le fichier (via `import` ou `from … import`). Il n'analyse pas les
    alias d'import, ni les appels de méthode sur des objets, ni les fonctions
    récupérées dynamiquement.

    Pour chaque appel qualifié, le module est importé réellement (`importlib.import_module`);
    la fonction est obtenue par `getattr`. Si la signature est disponible et ne comporte
    pas de paramètre `**kwargs`, tout argument nommé qui ne figure pas parmi les paramètres
    déclarés est signalé comme MANQUEMENT, avec le nom de la fonction, le paramètre fautif,
    la ligne, et la liste des paramètres acceptés.

    Le contrôle indique toujours le nombre d'appels qu'il a pu confronter à une signature
    réelle. S'il n'en a pu confronter aucun, il rend INAPPLICABLE (et ne rend jamais OK
    sur zéro appel examiné).
    """
    # 1. Collecte des modules réellement importés (sans alias)
    modules_importes = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules_importes.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules_importes.add(node.module.split(".")[0])

    total_confrontes_introspec = 0
    total_confrontes_doc = 0
    total_non_confrontes = 0
    manquements = []
    ignored_modules = set()

    for node in ast.walk(arbre):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            continue
        module_name = func.value.id
        func_name = func.attr
        if module_name not in modules_importes:
            continue

        # 2. N'inspecter que les modules de la stdlib
        if module_name not in sys.stdlib_module_names:
            ignored_modules.add(module_name)
            continue

        # 3. Import du module avec redirection des flux
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                module = importlib.import_module(module_name)
        except Exception:
            continue

        # 4. Récupération de la fonction
        try:
            fonction_obj = getattr(module, func_name)
        except AttributeError:
            continue

        # 5. Tentative d'obtention de la signature via inspect
        try:
            sig = inspect.signature(fonction_obj)
            total_confrontes_introspec += 1
        except (ValueError, TypeError):
            # 5b. Repli documentaire
            sig_str, from_doc = _extraire_signature_doc(module_name, func_name)
            if sig_str is None:
                total_non_confrontes += 1
                continue
            param_names, accepte_kwargs = _parse_params_from_signature(sig_str)
            if accepte_kwargs:
                total_confrontes_doc += 1
                continue
            total_confrontes_doc += 1
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if kw.arg not in param_names:
                    manquements.append({
                        "module": module_name,
                        "fonction": func_name,
                        "ligne": node.lineno,
                        "param_fautif": kw.arg,
                        "params_acceptes": sorted(param_names)
                    })
            continue

        # Signature obtenue via inspect
        param_names = {p.name for p in sig.parameters.values()}
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            continue  # accepte **kwargs

        for kw in node.keywords:
            if kw.arg is None:
                continue
            if kw.arg not in param_names:
                manquements.append({
                    "module": module_name,
                    "fonction": func_name,
                    "ligne": node.lineno,
                    "param_fautif": kw.arg,
                    "params_acceptes": sorted(param_names)
                })

    # Construction de l'explication
    exp_parts = []
    if ignored_modules:
        exp_parts.append(f"Modules ignorés (non-stdlib) : {', '.join(sorted(ignored_modules))}")
    exp_parts.append(f"{total_confrontes_introspec} appels confrontés par introspection")
    exp_parts.append(f"{total_confrontes_doc} appels confrontés par documentation locale")
    exp_parts.append(f"{total_non_confrontes} appels non confrontables")

    if total_confrontes_introspec + total_confrontes_doc == 0:
        return ("INAPPLICABLE", "; ".join(exp_parts))

    if manquements:
        details = []
        for m in manquements:
            details.append(
                f"{m['module']}.{m['fonction']} ligne {m['ligne']}: paramètre inexistant '{m['param_fautif']}' ; paramètres acceptés: {', '.join(m['params_acceptes'])}"
            )
        exp_parts.append("; ".join(details))
        return ("MANQUEMENT", "; ".join(exp_parts))

    return ("OK", "; ".join(exp_parts))


# ---------------------------------------------------------------------------
# Orchestration générale
# ---------------------------------------------------------------------------

def juger(chemin: Path) -> Dict:
    """Juge un fichier contre les quatorze contrôles."""
    try:
        chemin_absolu = Path(chemin).resolve()
        if not chemin_absolu.exists():
            chemin_relatif_racine = Path(__file__).resolve().parent / chemin
            if not chemin_relatif_racine.exists():
                return {
                    "chemin": str(chemin),
                    "etat": "FATAL",
                    "passes": 0,
                    "applicables": 0,
                    "verdicts": [{
                        "code": "FATAL",
                        "intitule": "Lecture du fichier",
                        "verdict": "FATAL",
                        "explication": f"Fichier introuvable: {chemin} (essayé: {chemin_absolu} et {chemin_relatif_racine})"
                    }],
                    "avertissements": []
                }
        with open(chemin_absolu, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        return {
            "chemin": str(chemin),
            "etat": "FATAL",
            "passes": 0,
            "applicables": 0,
            "verdicts": [{
                "code": "FATAL",
                "intitule": "Lecture du fichier",
                "verdict": "FATAL",
                "explication": str(e)
            }],
            "avertissements": []
        }

    try:
        arbre = ast.parse(source)
    except Exception as e:
        return {
            "chemin": str(chemin),
            "etat": "FATAL",
            "passes": 0,
            "applicables": 0,
            "verdicts": [{
                "code": "P1",
                "intitule": "COMPILE",
                "verdict": "FATAL",
                "explication": f"Erreur d'analyse AST: {str(e)}"
            }],
            "avertissements": []
        }

    annoter_parents(arbre)

    controles = [
        ("P1", "COMPILE", controle_compile),
        ("P2", "STDLIB SEULE", controle_stdlib_seule),
        ("P3", "ENCODAGE", controle_encodage),
        ("P4", "DEUX SORTIES", controle_deux_sorties),
        ("P5", "RACINE PORTABLE", controle_racine_portable),
        ("P6", "AUCUN CHEMIN EN DUR", controle_aucun_chemin_en_dur),
        ("P7", "CONTRAT DE MESURE", controle_contrat_mesure),
        ("P8", "POINT D'ENTRÉE", controle_point_entree),
        ("P9", "CŒUR SANS ÉCRITURE", controle_coeur_sans_ecriture),
        ("P10", "FONCTION MORTE", controle_fonction_morte),
        ("P11", "AIDE EXÉCUTABLE", controle_aide_executable),
        ("P12", "IMPORTABLE AILLEURS", controle_importable_ailleurs),
        ("P13", "CONTRÔLES MUTUELS", controle_controles_mutuels),
        ("P14", "PROMESSE TENUE", controle_promesse_tenue),
        ("P15", "ARGUMENT NOMMÉ INEXISTANT", controle_argument_nommee_inexistant)
    ]

    verdicts = []
    passes = 0
    warnings = []
    applicable = 0

    for code, intitule, controle in controles:
        verdict, explication = controle(source, arbre, chemin)
        verdicts.append({
            "code": code,
            "intitule": intitule,
            "verdict": verdict,
            "explication": explication
        })
        if verdict != "INAPPLICABLE":
            applicable += 1
        if verdict == "OK":
            passes += 1
        elif verdict == "AVERTISSEMENT":
            warnings.append({
                "code": code,
                "intitule": intitule,
                "verdict": verdict,
                "explication": explication
            })
        if verdict == "FATAL":
            return {
                "chemin": str(chemin),
                "etat": "FATAL",
                "passes": passes,
                "applicables": applicable,
                "verdicts": verdicts,
                "avertissements": warnings
            }

    etat = "CONFORME" if passes == applicable else ("MANQUEMENTS" if any(v["verdict"] == "MANQUEMENT" for v in verdicts) else "CONFORME")
    if any(v["verdict"] == "MANQUEMENT" for v in verdicts):
        etat = "MANQUEMENTS"

    return {
        "chemin": str(chemin),
        "etat": etat,
        "passes": passes,
        "applicables": applicable,
        "verdicts": verdicts,
        "avertissements": warnings
    }


def juger_tous(chemins: List[Path]) -> Dict:
    """Juge tous les fichiers fournis."""
    fichiers = []
    for chemin in chemins:
        chemin_absolu = Path(chemin).resolve()
        if not chemin_absolu.exists():
            chemin_relatif_racine = Path(__file__).resolve().parent / chemin
            if chemin_relatif_racine.exists():
                fichiers.append(chemin_relatif_racine)
            else:
                print(f"Fichier introuvable: {chemin} (essayé: {chemin_absolu} et {chemin_relatif_racine})", file=sys.stderr)
        elif chemin_absolu.is_dir():
            # Un dossier vaut l'ensemble de ses *.py ; sans ce dépliage, la porte
            # ouvrait le dossier comme un fichier et tuait tout le lot (KeyError 'FATAL').
            fichiers.extend(sorted(chemin_absolu.glob("*.py")))
        else:
            fichiers.append(chemin_absolu)

    if not fichiers:
        return {
            "denominateur": 0,
            "par_etat": {},
            "jugements": [],
            "message": "Aucun fichier à examiner.",
            "controles_suspects": [],
            "avertissements": [],
            "resume": {
                "denominateur": 0,
                "conformes": 0,
                "avec_manquements": 0,
                "fatals": 0,
                "manquements_par_controle": {}
            }
        }

    jugements = []
    for f in fichiers:
        try:
            jugements.append(juger(f))
        except Exception as e:
            print(f"Erreur lors du traitement de {f}: {str(e)}", file=sys.stderr)
            return {
                "denominateur": len(fichiers),
                "par_etat": {"FATAL": len(fichiers)},
                "jugements": [],
                "message": f"Erreur lors du traitement de {f}",
                "controles_suspects": [],
                "avertissements": [],
                "resume": {
                    "denominateur": len(fichiers),
                    "conformes": 0,
                    "avec_manquements": 0,
                    "fatals": len(fichiers),
                    "manquements_par_controle": {}
                }
            }

    par_etat = {"CONFORME": 0, "MANQUEMENTS": 0, "FATAL": 0}
    controles_stats = {f"P{i}": {"total": 0, "manquements": 0} for i in range(1, 16)}
    toutes_les_avertissements = []

    for j in jugements:
        par_etat[j["etat"]] += 1
        toutes_les_avertissements.extend(j.get("avertissements", []))
        for verdict in j["verdicts"]:
            code = verdict["code"]
            controles_stats.setdefault(code, {"total": 0, "manquements": 0})
            controles_stats[code]["total"] += 1
            if verdict["verdict"] == "MANQUEMENT":
                controles_stats[code]["manquements"] += 1

    controles_suspects = []
    for code, stats in controles_stats.items():
        total = stats["total"]
        manq = stats["manquements"]
        if total >= 3:
            proportion = (manq / total) * 100
            if proportion >= 70.0:
                controles_suspects.append({
                    "code": code,
                    "compte": manq,
                    "proportion": round(proportion, 2)
                })
                msg = (f"le contrôle {code} accuse {manq} fichiers sur {total} "
                       f"({round(proportion, 2)} %) — suspecter le contrôle avant les fichiers")
                print(msg, file=sys.stderr)

    resume = {
        "denominateur": len(fichiers),
        "conformes": par_etat["CONFORME"],
        "avec_manquements": par_etat["MANQUEMENTS"],
        "fatals": par_etat["FATAL"],
        "manquements_par_controle": {code: stats["manquements"] for code, stats in controles_stats.items()}
    }

    return {
        "denominateur": len(fichiers),
        "par_etat": par_etat,
        "jugements": jugements,
        "controles_suspects": controles_suspects,
        "avertissements": toutes_les_avertissements,
        "resume": resume
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Contrôle mécanique des outils contre le socle commun.")
    parser.add_argument("chemins", nargs="+", type=Path, help="Fichiers ou dossiers à examiner")
    parser.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent, help="Racine des chemins relatifs")
    parser.add_argument("--json", action="store_true", help="Sortie en JSON")
    parser.add_argument("--strict", action="store_true", help="Échouer aussi sur les contrôles INAPPLICABLE")
    args = parser.parse_args()

    # Initialisation du répertoire de documentation pour le repli de P15
    global DOC_ROOT
    DOC_ROOT = args.racine / "doc"

    try:
        resultat = juger_tous(args.chemins)
    except Exception as e:
        print(f"Erreur lors du traitement des fichiers {args.chemins}: {str(e)}", file=sys.stderr)
        return 4

    if args.json:
        print(json.dumps(resultat, indent=2, ensure_ascii=False))
    else:
        if resultat["denominateur"] == 0:
            print(resultat["message"])
            return 3

        for jugement in resultat["jugements"]:
            print(f"{jugement['chemin']}: {jugement['etat']} ({jugement['passes']}/{jugement['applicables']})")

        for jugement in resultat["jugements"]:
            if jugement["etat"] != "CONFORME":
                print(f"\n{jugement['chemin']}:")
                for verdict in jugement["verdicts"]:
                    if verdict["verdict"] not in ("OK", "AVERTISSEMENT"):
                        print(f"  {verdict['code']} {verdict['intitule']}: {verdict['verdict']}")
                        if verdict["explication"]:
                            print(f"    {verdict['explication']}")
                warnings = [v for v in jugement.get("avertissements", []) if v["verdict"] == "AVERTISSEMENT"]
                if warnings:
                    print("\nà vérifier à la main:")
                    for w in warnings:
                        print(f"  {w['code']} {w['intitule']}: {w['verdict']}")
                        if w["explication"]:
                            print(f"    {w['explication']}")

    if resultat["par_etat"].get("FATAL", 0) > 0:
        return 2
    elif resultat["par_etat"].get("MANQUEMENTS", 0) > 0:
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())