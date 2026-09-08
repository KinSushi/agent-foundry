"""impact_tests.py — cartographie l'impact des changements sur les tests.

QUESTION       quels tests dois-je relancer après ce changement ?
MESURE         sys.monitoring PY_START/PY_RETURN pendant l'exécution de chaque
               test ; carte inversée ; sceau sha256 des sources ; surcoût mesuré
HYPOTHÈSES    les tests sont déterministes et indépendants ; le code testé
               est du Python (PY_START ne voit pas les extensions C)
LIMITES        une fonction appelée par réflexion n'apparaît pas ; un test qui
               échoue à l'import ne cartographie rien ; « aucun test » ne veut
               pas dire « code mort » ; carte périmée dès que le code change
CONTRE-EXEMPLES TDAD mesure que des instructions TDD SANS carte portent les
               régressions de 6,08 % à 9,94 % — la discipline seule NUIT
INVOCATION
    {outil} carte . --json
DOMAINE        code Python testé par des fonctions Python, sur cet interpréteur
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Racine portable, surchargeable via --racine
RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Import conditionnel de sys.monitoring (disponible à partir de Python 3.12)
try:
    import sys.monitoring as monitoring
except ImportError:  # pragma: no cover
    monitoring = None

# Variables globales pour le suivi d'exécution
_test_courant: Optional[str] = None
_profondeur: int = 0
_debut_test: float = 0.0
_durees: Dict[str, float] = {}
test_fonctions: Dict[str, Set[str]] = {}


def _charger_tests(racine: Path, repertoire: str) -> List[unittest.TestCase]:
    """Charge récursivement tous les tests du répertoire."""
    loader = unittest.TestLoader()
    suite = loader.discover(str(racine / repertoire), pattern="test_*.py")
    tests: List[unittest.TestCase] = []

    def collect(suite_: unittest.TestSuite) -> None:
        for test in suite_:
            if isinstance(test, unittest.TestSuite):
                collect(test)
            else:
                tests.append(test)

    collect(suite)
    return tests


def _calculer_empreinte(racine: Path, repertoire: str) -> str:
    """Calcule l'empreinte SHA256 de tous les fichiers .py du répertoire."""
    empreinte = hashlib.sha256()
    for fichier in (racine / repertoire).rglob("*.py"):
        if fichier.is_file():
            with open(fichier, "rb") as f:
                empreinte.update(f.read())
    return empreinte.hexdigest()


def _detecter_version_python() -> Tuple[int, int]:
    """Retourne la version majeure et mineure de Python."""
    v = sys.version_info
    return v.major, v.minor


def _toutes_fonctions(racine: Path, repertoire: str) -> Set[str]:
    """Retourne l'ensemble des noms de fonctions définies dans les fichiers .py
    du répertoire (hors fichiers de test)."""
    fonctions = set()
    for fichier in (racine / repertoire).rglob("*.py"):
        if fichier.name.startswith("test_"):
            continue
        try:
            source = fichier.read_text(encoding="utf-8")
            compile(source, str(fichier), "exec")
            arbre = ast.parse(source, filename=str(fichier))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for noeud in ast.walk(arbre):
            if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fonctions.add(noeud.name)
    return fonctions


def _trouver_tool_id(monitoring_module) -> int:
    """Trouve un ID de monitoring libre (0-5)."""
    for tool_id in range(6):
        if monitoring_module.get_tool(tool_id) is None:
            return tool_id
    raise RuntimeError("Aucun ID de monitoring libre")


def _callback_py_start(code, offset):  # pragma: no cover
    """Callback pour PY_START (monitoring)."""
    global _profondeur, _debut_test
    if _test_courant is None:
        return
    nom_fonction = code.co_name
    if nom_fonction == "<module>":
        return
    if _test_courant in test_fonctions:
        test_fonctions[_test_courant].add(nom_fonction)
    if _profondeur == 0:
        _debut_test = time.perf_counter()
    _profondeur += 1


def _callback_py_return(code, instruction_offset, retval):  # pragma: no cover
    """Callback pour PY_RETURN (monitoring)."""
    global _profondeur
    if _test_courant is None:
        return
    _profondeur -= 1
    if _profondeur == 0:
        _durees[_test_courant] = time.perf_counter() - _debut_test


def _callback_trace(frame, event, arg):
    """Traceur pour sys.settrace (fallback Python < 3.12)."""
    global _profondeur, _debut_test
    if _test_courant is None:
        return _callback_trace
    if event == "call":
        nom_fonction = frame.f_code.co_name
        if nom_fonction != "<module>":
            if _test_courant in test_fonctions:
                test_fonctions[_test_courant].add(nom_fonction)
        if _profondeur == 0:
            _debut_test = time.perf_counter()
        _profondeur += 1
    elif event == "return":
        _profondeur -= 1
        if _profondeur == 0:
            _durees[_test_courant] = time.perf_counter() - _debut_test
    return _callback_trace


def _executer_tests(tests: List[unittest.TestCase], mode: str) -> None:
    """Exécute les tests avec le mode de suivi approprié."""
    global _test_courant, _profondeur, _debut_test
    for test in tests:
        _test_courant = str(test)
        _profondeur = 0
        _debut_test = 0.0
        try:
            if mode == "trace":
                sys.settrace(_callback_trace)
                try:
                    test.run()
                finally:
                    sys.settrace(None)
            else:
                test.run()
        except Exception:  # pragma: no cover
            pass
        _test_courant = None


def carte(racine: Path, repertoire: str) -> Dict[str, Any]:
    """Construit la carte de couverture test → fonctions et fonction → tests."""
    global test_fonctions, _durees
    test_fonctions = {}
    _durees = {}

    major, minor = _detecter_version_python()
    mode = "monitoring" if (major, minor) >= (3, 12) else "trace"
    if mode == "trace":
        sys.stderr.write(
            "Attention : Python < 3.12, utilisation de sys.settrace (surcoût ×9,4)\n"
        )

    empreinte = _calculer_empreinte(racine, repertoire)

    tests = _charger_tests(racine, repertoire)
    if not tests:
        sys.stderr.write(
            "Denominateur nul : rien a examiner, refus de conclure.\n"
        )
        raise ValueError("denominateur_zero")

    for test in tests:
        test_fonctions[str(test)] = set()

    debut_baseline = time.perf_counter()
    _executer_tests(tests, "none")
    duree_baseline = time.perf_counter() - debut_baseline

    fonction_tests: Dict[str, Set[str]] = {}
    tool_id = None

    try:
        if mode == "monitoring":
            if monitoring is None:
                raise RuntimeError(
                    "sys.monitoring indisponible malgré la version"
                )
            tool_id = _trouver_tool_id(monitoring)
            monitoring.use_tool_id(tool_id, "impact_tests")
            monitoring.register_callback(
                tool_id, monitoring.events.PY_START, _callback_py_start
            )
            monitoring.register_callback(
                tool_id, monitoring.events.PY_RETURN, _callback_py_return
            )
            monitoring.set_events(
                tool_id, monitoring.events.PY_START | monitoring.events.PY_RETURN
            )

        debut_total = time.perf_counter()
        _executer_tests(tests, mode)
        duree_totale = time.perf_counter() - debut_total

        surcout = duree_totale - duree_baseline

        for test_nom, fonctions in test_fonctions.items():
            for fonction in fonctions:
                fonction_tests.setdefault(fonction, set()).add(test_nom)

    finally:
        if mode == "monitoring" and tool_id is not None and monitoring is not None:
            monitoring.set_events(tool_id, 0)
            monitoring.free_tool_id(tool_id)

    return {
        "denominateur": len(tests),
        "empreinte": empreinte,
        "test_fonctions": test_fonctions,
        "fonction_tests": fonction_tests,
        "durees": _durees,
        "surcout": surcout,
        "mode": mode,
        "contrat": {
            "QUESTION": "quels tests dois-je relancer après ce changement ?",
            "MESURE": "sys.monitoring PY_START/PY_RETURN pendant l'exécution de chaque test ; carte inversée ; sceau sha256 des sources ; surcoût mesuré",
            "HYPOTHÈSES": "les tests sont déterministes et indépendants ; le code testé est du Python (PY_START ne voit pas les extensions C)",
            "LIMITES": "une fonction appelée par réflexion n'apparaît pas ; un test qui échoue à l'import ne cartographie rien ; « aucun test » ne veut pas dire « code mort » ; carte périmée dès que le code change",
            "CONTRE-EXEMPLES": "TDAD mesure que des instructions TDD SANS carte portent les régressions de 6,08 % à 9,94 % — la discipline seule NUIT",
            "DOMAINE": "code Python testé par des fonctions Python, sur cet interpréteur",
        },
    }


def impact(carte_resultat: Dict[str, Any], fonction: str) -> Set[str]:
    """Retourne l'ensemble des tests qui touchent une fonction donnée."""
    return set(carte_resultat["fonction_tests"].get(fonction, []))


def orphelines(
    carte_resultat: Dict[str, Any], racine: Path, repertoire: str
) -> Set[str]:
    """Retourne l'ensemble des fonctions non couvertes par aucun test."""
    toutes = _toutes_fonctions(racine, repertoire)
    couvertes = set()
    for fonctions in carte_resultat["test_fonctions"].values():
        couvertes.update(fonctions)
    return toutes - couvertes


def main() -> int:
    # Options communes, propagées aux sous‑commandes
    commun = argparse.ArgumentParser(add_help=False)
    commun.add_argument(
        "--racine",
        default=RACINE,
        help="Répertoire racine du projet (par défaut : répertoire de l'outil)",
        dest="racine",
    )
    commun.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON",
        dest="json",
    )
    # Désactiver les valeurs par défaut afin que les sous‑commandes ne les
    # écrasent pas.
    for action in commun._actions:
        action.default = argparse.SUPPRESS

    parser = argparse.ArgumentParser(
        description="Cartographie l'impact des changements sur les tests.",
        epilog="Exemple : python impact_tests.py carte . --json",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande carte
    parser_carte = subparsers.add_parser(
        "carte", parents=[commun], help="Génère la carte d'impact"
    )
    parser_carte.add_argument(
        "--repertoire",
        default=".",
        help="Répertoire contenant les tests (par défaut : .)",
        dest="repertoire",
    )

    # sous‑commande impact
    parser_impact = subparsers.add_parser(
        "impact", parents=[commun], help="Trouve les tests impactés par une fonction"
    )
    parser_impact.add_argument(
        "--repertoire",
        default=".",
        help="Répertoire contenant les tests (par défaut : .)",
        dest="repertoire",
    )
    parser_impact.add_argument(
        "--fonction",
        required=True,
        help="Nom de la fonction pour 'impact'",
        dest="fonction",
    )
    parser_impact.add_argument(
        "--depuis",
        help="Empreinte de la carte précédente",
        dest="depuis",
    )

    # sous‑commande orphelines
    parser_orphelines = subparsers.add_parser(
        "orphelines", parents=[commun], help="Trouve les fonctions orphelines"
    )
    parser_orphelines.add_argument(
        "--repertoire",
        default=".",
        help="Répertoire contenant les tests (par défaut : .)",
        dest="repertoire",
    )

    args = parser.parse_args()

    # Gestion des options communes
    racine = Path(getattr(args, "racine", RACINE))
    json_mode = getattr(args, "json", False)

    try:
        if args.commande == "carte":
            resultat = carte(racine, args.repertoire)
            if json_mode:
                json.dump(resultat, sys.stdout)
            else:
                # sortie lisible (hors JSON) – aucune contrainte supplémentaire
                print(f"Denominateur : {resultat['denominateur']}")
                print(f"Empreinte : {resultat['empreinte']}")
                print(f"Mode : {resultat['mode']}")
                print(f"Surcoût : {resultat['surcout']:.4f} s")
        elif args.commande == "impact":
            resultat = carte(racine, args.repertoire)
            if args.depuis and resultat["empreinte"] != args.depuis:
                sys.stderr.write("PÉRIMÉE\n")
                return 2
            tests_impactes = impact(resultat, args.fonction)
            if json_mode:
                json.dump(list(tests_impactes), sys.stdout)
            else:
                if not tests_impactes:
                    print("AUCUN TEST")
                    return 1
                print(f"{args.fonction} -> {', '.join(tests_impactes)}")
        elif args.commande == "orphelines":
            resultat = carte(racine, args.repertoire)
            orphelines_set = orphelines(resultat, racine, args.repertoire)
            if json_mode:
                json.dump(list(orphelines_set), sys.stdout)
            else:
                if not orphelines_set:
                    print("AUCUNE ORPHELINE")
                else:
                    for fonction in orphelines_set:
                        print(fonction)

    except ValueError as e:
        if str(e) == "denominateur_zero":
            # Refus légitime : aucun test à examiner
            if json_mode:
                json.dump({"denominateur": 0}, sys.stdout)
            return 3
        sys.stderr.write(f"Erreur : {e}\n")
        if json_mode:
            json.dump({"erreur": str(e), "denominateur": 0}, sys.stdout)
        return 1
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"Erreur : {e}\n")
        if json_mode:
            json.dump({"erreur": str(e), "denominateur": 0}, sys.stdout)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())