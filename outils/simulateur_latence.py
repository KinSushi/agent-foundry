#!/usr/bin/env python3.14
"""
QUESTION      Comment un code réagit-il à une latence réseau ?
MESURE        Temps d'exécution d'un appel réseau simulé avec délais artificiels.
HYPOTHÈSES    Le code testé est synchrone et bloque sur les E/S réseau.
LIMITES       Ne mesure pas les latences réelles, seulement des délais fixes.
CONTRE-EXEMPLES Un code asynchrone ou multithreadé peut réagir différemment.
INVOCATION
    {outil} --code "import time; time.sleep(0.001)" --latences 50 --json
DOMAINE       Code synchrone bloquant sur des appels réseau simples.
"""

from __future__ import annotations

import argparse
import json
import sys
import timeit
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "simuler_latence",
    "analyser_resultats",
    "valider_code_python",
    "main",
]

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def generer_sortie_json(
    donnees: Dict[str, Any],
    erreur: Optional[str] = None,
    code_sortie: int = 0
) -> None:
    """Génère une sortie JSON valide avec dénominateur systématique."""
    base = {
        "denominateur": donnees.get("denominateur", 0),
        "contrat": {
            "QUESTION": "Comment un code réagit-il à une latence réseau ?",
            "MESURE": "Temps d'exécution d'un appel réseau simulé avec délais artificiels.",
            "HYPOTHÈSES": "Le code testé est synchrone et bloque sur les E/S réseau.",
            "LIMITES": "Ne mesure pas les latences réelles, seulement des délais fixes.",
            "CONTRE-EXEMPLES": "Un code asynchrone ou multithreadé peut réagir différemment.",
            "DOMAINE": "Code synchrone bloquant sur des appels réseau simples.",
        }
    }

    if erreur:
        base["erreur"] = erreur
    else:
        base.update(donnees)

    json.dump(base, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.exit(code_sortie)

def simuler_latence(
    code: str,
    latence_ms: float = 100.0,
    nombre_executions: int = 10,
    setup: str = "pass",
    avec_httpcore: bool = False,
) -> Dict[str, Any]:
    """Simule l'exécution d'un code avec une latence réseau artificielle."""
    if not avec_httpcore:
        stmt = f"""
import time
time.sleep({latence_ms / 1000.0})
{code}
"""
        temps = timeit.repeat(stmt=stmt, setup=setup, number=nombre_executions, repeat=3)
        return {
            "temps_min": min(temps),
            "temps_moyen": sum(temps) / len(temps),
            "temps_max": max(temps),
            "unites": "secondes",
            "methode": "time.sleep",
        }

    try:
        import httpcore  # type: ignore
    except ImportError:
        print(
            "httpcore non disponible, utilisation de time.sleep à la place.",
            file=sys.stderr,
        )
        return simuler_latence(
            code=code,
            latence_ms=latence_ms,
            nombre_executions=nombre_executions,
            setup=setup,
            avec_httpcore=False,
        )

    stmt = f"""
import httpcore
import time
time.sleep({latence_ms / 1000.0})
try:
    with httpcore.stream("GET", "https://www.example.com") as response:
        response.read()
except Exception:
    pass
{code}
"""
    temps = timeit.repeat(stmt=stmt, setup=setup, number=nombre_executions, repeat=3)
    return {
        "temps_min": min(temps),
        "temps_moyen": sum(temps) / len(temps),
        "temps_max": max(temps),
        "unites": "secondes",
        "methode": "httpcore + time.sleep",
    }

def analyser_resultats(
    code: str,
    latences: List[float],
    nombre_executions: int = 10,
    setup: str = "pass",
    avec_httpcore: bool = False,
) -> Dict[str, Any]:
    """Analyse la réaction du code à différentes latences réseau."""
    resultats = []
    for latence in latences:
        resultat = simuler_latence(
            code=code,
            latence_ms=latence,
            nombre_executions=nombre_executions,
            setup=setup,
            avec_httpcore=avec_httpcore,
        )
        resultat["latence_ms"] = latence
        resultats.append(resultat)

    return {
        "denominateur": len(resultats),
        "examines": resultats,
        "examines_tronques": False,
    }

def valider_code_python(code: str) -> bool:
    """Vérifie que le code Python est syntaxiquement correct."""
    try:
        compile(code, "<string>", "exec")
        return True
    except SyntaxError:
        return False

class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui gère les erreurs avec le protocole de refus."""

    def error(self, message: str) -> None:
        print(f"Denominateur nul : {message}", file=sys.stderr)
        generer_sortie_json({}, erreur=message, code_sortie=3)

def main() -> int:
    """Point d'entrée principal de l'outil."""
    parser = JsonArgumentParser(
        description="Simule l'impact de la latence réseau sur un code Python.",
        epilog=(
            "Exemple : simulateur_latence.py --code 'requests.get(\"https://example.com\")' "
            "--latences 50 100 200"
        ),
    )
    parser.add_argument(
        "--code",
        type=str,
        required=True,
        help="Code Python à analyser (doit contenir un appel réseau).",
    )
    parser.add_argument(
        "--latences",
        type=float,
        nargs="+",
        required=True,
        help="Latences réseau à simuler en millisecondes.",
    )
    parser.add_argument(
        "--nombre-executions",
        type=int,
        default=10,
        help="Nombre d'exécutions pour chaque latence (défaut : 10).",
    )
    parser.add_argument(
        "--setup",
        type=str,
        default="pass",
        help="Code d'initialisation à exécuter avant le code principal (défaut : 'pass').",
    )
    parser.add_argument(
        "--avec-httpcore",
        action="store_true",
        help="Utilise httpcore pour simuler un vrai appel réseau (si disponible).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine pour les imports relatifs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON.",
    )

    args = parser.parse_args()

    if args.racine:
        sys.path.insert(0, str(args.racine))

    if not valider_code_python(args.code):
        print("Denominateur nul : code syntaxiquement invalide", file=sys.stderr)
        generer_sortie_json({}, erreur="Le code fourni contient des erreurs de syntaxe.", code_sortie=3)

    if not valider_code_python(args.setup):
        print("Denominateur nul : setup syntaxiquement invalide", file=sys.stderr)
        generer_sortie_json({}, erreur="Le code d'initialisation contient des erreurs de syntaxe.", code_sortie=3)

    if not args.latences:
        print("Denominateur nul : aucune latence fournie", file=sys.stderr)
        generer_sortie_json({}, erreur="Aucune latence à simuler.", code_sortie=3)

    resultats = analyser_resultats(
        code=args.code,
        latences=args.latences,
        nombre_executions=args.nombre_executions,
        setup=args.setup,
        avec_httpcore=args.avec_httpcore,
    )

    if resultats["denominateur"] == 0:
        print("Denominateur nul : aucun résultat généré", file=sys.stderr)
        generer_sortie_json({}, erreur="Aucun résultat généré.", code_sortie=3)

    if args.json:
        generer_sortie_json({"resultats": resultats})
    else:
        for resultat in resultats["examines"]:
            print(
                f"Latence : {resultat['latence_ms']} ms | "
                f"Temps min : {resultat['temps_min']:.6f} s | "
                f"Temps moyen : {resultat['temps_moyen']:.6f} s | "
                f"Temps max : {resultat['temps_max']:.6f} s | "
                f"Méthode : {resultat['methode']}",
                file=sys.stdout,
            )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())