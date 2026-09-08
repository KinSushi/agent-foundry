"""traquer_mort – détection de fonctions mortes dans un scénario Python

QUESTION
    Quelles fonctions ce scénario n'entre‑t‑il jamais ?
MESURE
    trace.Trace(count=1) ou sys.monitoring (selon la version) croisé aux bornes
    AST de chaque fonction du module cible.
HYPOTHÈSES
    Le scénario est représentatif de l'usage réel du module.
LIMITES
    « morte pour CE scénario » ≠ « morte » en général ; les appels par
    réflexion ou depuis un autre processus ne sont pas détectés.
CONTRE‑EXEMPLES
    def f(): return 2          # corps sur la même ligne → INDÉCIDABLE
    Aucun ligne tracée       → instrument cassé, refus de conclure
INVOCATION
    {outil} {fichier} --scenario "import valide; valide.f()" --json
DOMAINE
    Un module Python et un scénario qui l'exerce.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Set, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
# Optional import of sys.monitoring (Python ≥3.12)                           #
# --------------------------------------------------------------------------- #
try:
    import sys.monitoring as _monitoring  # type: ignore
    _HAS_MONITORING = True
except Exception:  # pragma: no cover
    _HAS_MONITORING = False

# --------------------------------------------------------------------------- #
# Core logic – aucune dépendance à argparse, aucune impression                #
# --------------------------------------------------------------------------- #
def _lire_source(cible: Path) -> str:
    return cible.read_text(encoding="utf-8")

def _extraire_fonctions(source: str, cible: Path) -> List[Tuple[str, int, Set[int]]]:
    """
    Retourne une liste de tuples (nom, ligne_def, lignes_corps) pour chaque
    fonction de niveau top‑level du module.
    lignes_corps exclut la ligne du ``def``.
    """
    arbre = ast.parse(source, filename=str(cible))
    fonctions: List[Tuple[str, int, Set[int]]] = []
    for node in ast.iter_child_nodes(arbre):
        if isinstance(node, ast.FunctionDef):
            def_line = node.lineno
            corps: Set[int] = set()
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    corps.add(sub.lineno)
            corps.discard(def_line)
            fonctions.append((node.name, def_line, corps))
    return fonctions

def _executer_scenario_trace(cible: Path, scenario: str) -> Tuple[Set[int], Set[Tuple[str, int]], str]:
    from trace import Trace

    tracer = Trace(count=1, trace=0)
    exec_globals: Dict[str, object] = {"__file__": str(cible)}
    try:
        tracer.runctx(scenario, exec_globals, exec_globals)
    except Exception:  # pragma: no cover
        traceback.print_exc(file=sys.stderr)
        raise
    counts = tracer.results().counts  # type: ignore[attr-defined]
    lignes: Set[int] = set()
    for (fname, lineno), nb in counts.items():
        if Path(fname).resolve() == cible.resolve() and nb > 0:
            lignes.add(lineno)

    source = _lire_source(cible)
    fonctions = _extraire_fonctions(source, cible)
    funcs_exe: Set[Tuple[str, int]] = set()
    for nom, def_line, corps in fonctions:
        if corps & lignes:
            funcs_exe.add((nom, def_line))

    return lignes, funcs_exe, "trace"

def _executer_scenario_monitoring(cible: Path, scenario: str) -> Tuple[Set[int], Set[Tuple[str, int]], str]:
    executed_lines: Set[int] = set()
    entered_funcs: Set[Tuple[str, int]] = set()
    tool_id = _monitoring.use_tool_id()  # type: ignore[attr-defined]

    def _callback(frame, event, arg):
        if event == _monitoring.events.LINE:  # type: ignore[attr-defined]
            if Path(frame.f_code.co_filename).resolve() == cible.resolve():
                executed_lines.add(frame.f_lineno)
                return _monitoring.DISABLE  # type: ignore[attr-defined]
        elif event == _monitoring.events.PY_START:  # type: ignore[attr-defined]
            if Path(frame.f_code.co_filename).resolve() == cible.resolve():
                name = frame.f_code.co_name
                start_line = frame.f_code.co_firstlineno
                entered_funcs.add((name, start_line))
        return None

    try:
        _monitoring.register_callback(
            tool_id, _monitoring.events.LINE, _callback  # type: ignore[attr-defined]
        )
        _monitoring.register_callback(
            tool_id, _monitoring.events.PY_START, _callback  # type: ignore[attr-defined]
        )
        _monitoring.set_events(
            tool_id, _monitoring.events.LINE | _monitoring.events.PY_START  # type: ignore[attr-defined]
        )
        exec_globals: Dict[str, object] = {"__file__": str(cible)}
        exec(compile(scenario, "<scenario>", "exec"), exec_globals, exec_globals)
    finally:
        _monitoring.free_tool_id(tool_id)  # type: ignore[attr-defined]
    return executed_lines, entered_funcs, "sys.monitoring"

def _executer_scenario(cible: Path, scenario: str) -> Tuple[Set[int], Set[Tuple[str, int]], str]:
    if _HAS_MONITORING:
        return _executer_scenario_monitoring(cible, scenario)
    return _executer_scenario_trace(cible, scenario)

def _base_json_result(denominateur: int) -> Dict:
    """Structure de base pour toute sortie JSON avec dénominateur obligatoire"""
    return {
        "denominateur": denominateur,
        "examines": [],
        "contrat": {
            "QUESTION": "quelles fonctions ce scénario n'entre-t-il jamais ?",
            "MESURE": "trace.Trace(count=1) ou sys.monitoring croisé aux bornes AST",
            "HYPOTHÈSES": "le scénario est représentatif de l'usage réel",
            "LIMITES": "mortes pour CE scénario ≠ mortes en général ; appels par réflexion non détectés",
            "CONTRE-EXEMPLES": "def f(): return 2 → INDÉCIDABLE ; 0 ligne tracée → instrument cassé",
            "DOMAINE": "un module Python et un scénario qui l'exerce",
        }
    }

def analyser(
    cible: Path, scenario: str, exiger_lignes: int = 1
) -> Tuple[Dict, int, bool, str]:
    """
    Retourne (resultat_json, code_sortie, message_erreur, instrumentation)
    resultat_json est toujours un dict valide avec "denominateur"
    """
    if not cible.is_file():
        return _base_json_result(0), 3, f"Denominateur nul : fichier inexistant {cible}", ""

    try:
        source = _lire_source(cible)
    except Exception as e:
        return _base_json_result(0), 3, f"Denominateur nul : impossible de lire {cible}", ""

    try:
        fonctions = _extraire_fonctions(source, cible)
    except Exception as e:
        return _base_json_result(0), 3, f"Denominateur nul : erreur d'analyse AST {cible}", ""

    if not fonctions:
        return _base_json_result(0), 3, "Denominateur nul : aucune fonction trouvée", ""

    try:
        lignes_exe, funcs_exe, instrumentation = _executer_scenario(cible, scenario)
    except Exception as e:
        return _base_json_result(0), 3, f"Denominateur nul : échec scénario {str(e)}", ""

    nb_lignes = len(lignes_exe)
    if nb_lignes < exiger_lignes:
        return _base_json_result(0), 3, f"Denominateur nul : seulement {nb_lignes} lignes tracées", instrumentation

    result = {
        "vives": [],
        "mortes": [],
        "partielles": [],
        "indecidables": [],
    }

    for nom, def_line, corps in fonctions:
        if not corps:
            result["indecidables"].append((nom, def_line))
            continue
        entered = (nom, def_line) in funcs_exe
        if not entered:
            result["mortes"].append((nom, def_line))
            continue
        lignes_corps_exe = corps & lignes_exe
        if not lignes_corps_exe:
            result["indecidables"].append((nom, def_line))
        elif lignes_corps_exe == corps:
            result["vives"].append((nom, def_line))
        else:
            manquantes = sorted(corps - lignes_corps_exe)
            result["partielles"].append((nom, def_line, manquantes))

    json_result = _base_json_result(len(fonctions))
    json_result.update({
        "examines": sorted([{"nom": n, "def": l} for n, l, _ in fonctions], key=lambda x: x["nom"]),
        "scenario": scenario,
        "fichier": str(cible),
        "lignes_traces": nb_lignes,
        "instrument_ok": True,
        "instrumentation": instrumentation,
        "resultats": {
            "vives": sorted([{"nom": n, "def": l} for n, l in result["vives"]], key=lambda x: x["nom"]),
            "mortes": sorted([{"nom": n, "def": l} for n, l in result["mortes"]], key=lambda x: x["nom"]),
            "partielles": sorted(
                [{"nom": n, "def": l, "manquantes": m} for n, l, m in result["partielles"]],
                key=lambda x: x["nom"],
            ),
            "indecidables": sorted(
                [{"nom": n, "def": l} for n, l in result["indecidables"]], key=lambda x: x["nom"]
            ),
        },
    })
    return json_result, 0, "", instrumentation

# --------------------------------------------------------------------------- #
# CLI – impression uniquement ici                                          #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Détecte les fonctions jamais appelées par un scénario donné.",
        epilog='Exemple : python -m outils.traquer_mort mon_module.py --scenario "import mon_module; mon_module.main()"',
    )
    parser.add_argument(
        "cible",
        type=Path,
        help="Chemin du module Python à analyser (fichier .py).",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Code Python à exécuter pour exercer le module (entre guillemets).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )
    parser.add_argument(
        "--exiger-lignes",
        type=int,
        default=1,
        help="Nombre minimal de lignes exécutées dans le fichier cible (défaut : 1).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON (un seul objet).",
    )
    args = parser.parse_args()

    racine = args.racine.resolve()
    cible = (racine / args.cible).resolve()

    json_result, code_sortie, message_erreur, instrumentation = analyser(
        cible, args.scenario, exiger_lignes=args.exiger_lignes
    )

    if code_sortie == 3:
        sys.stderr.write(f"{message_erreur}\n")
        if args.json:
            json.dump(json_result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 3

    if args.json:
        json.dump(json_result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        _afficher_humain(
            {
                "vives": [(r["nom"], r["def"]) for r in json_result["resultats"]["vives"]],
                "mortes": [(r["nom"], r["def"]) for r in json_result["resultats"]["mortes"]],
                "partielles": [(r["nom"], r["def"], r["manquantes"]) for r in json_result["resultats"]["partielles"]],
                "indecidables": [(r["nom"], r["def"]) for r in json_result["resultats"]["indecidables"]],
            },
            json_result["lignes_traces"],
            Path(json_result["fichier"]),
            json_result["scenario"],
            json_result["instrumentation"],
        )

    return 1 if (json_result["resultats"]["mortes"] or
                 json_result["resultats"]["partielles"] or
                 json_result["resultats"]["indecidables"]) else 0

def _afficher_humain(
    result: Dict[str, List[Tuple]],
    nb_lignes: int,
    cible: Path,
    scenario: str,
    instrumentation: str,
) -> None:
    out = [
        f"Scénario : {scenario}",
        f"Fichier  : {cible}",
        f"Lignes exécutées (cible) : {nb_lignes}",
        f"Instrumentation : {instrumentation}",
        "",
        f"VIVES        {len(result['vives'])}",
    ]
    for nom, ligne in result["vives"]:
        out.append(f"  {nom} (l.{ligne})")
    out.append(f"MORTES       {len(result['mortes'])}")
    for nom, ligne in result["mortes"]:
        out.append(f"  {nom} (l.{ligne})")
    out.append(f"PARTIELLES   {len(result['partielles'])}")
    for nom, ligne, manq in result["partielles"]:
        out.append(f"  {nom} (l.{ligne}) -> lignes non atteintes : {manq}")
    out.append(f"INDÉCIDABLES {len(result['indecidables'])}")
    for nom, ligne in result["indecidables"]:
        out.append(f"  {nom} (l.{ligne})")
    sys.stdout.write("\n".join(out) + "\n")

if __name__ == "__main__":
    raise SystemExit(main())