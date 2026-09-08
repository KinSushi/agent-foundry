"""
QUESTION      Comment un code réagit-il à des erreurs système ?
MESURE        Simulation d'erreurs système (disque plein) lors de l'exécution d'un code Python,
              observation des exceptions levées et de leur gestion.
HYPOTHÈSES    Le code cible est valide syntaxiquement et sémantiquement en l'absence d'erreurs système.
              Les erreurs système simulées sont représentatives des conditions réelles (ex: ENOSPC pour disque plein).
LIMITES       Ne simule pas toutes les erreurs système possibles, seulement celles implémentables via les modules standard.
              La simulation dépend des permissions et de l'environnement d'exécution.
CONTRE-EXEMPLES Un code qui gère explicitement une erreur système peut ne pas lever d'exception malgré la simulation.
DOMAINE       Code Python exécuté dans un environnement où les permissions permettent la simulation d'erreurs système.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import argparse
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, Tuple

__all__ = [
    "simuler_erreur_disque_plein",
    "executer_avec_simulation",
    "analyser_resultat",
    "main",
]

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui renvoie une erreur JSON quand --json est présent."""
    def error(self, message: str) -> None:
        if "--json" in sys.argv:
            err_obj = {"error": message}
            json.dump(err_obj, sys.stdout, ensure_ascii=False)
            sys.exit(2)
        else:
            super().error(message)


def simuler_erreur_disque_plein(chemin: Path) -> Tuple[bool, Optional[str]]:
    """
    Simule une erreur de disque plein en remplissant l'espace disponible.

    Args:
        chemin: Chemin du répertoire où simuler l'erreur.

    Returns:
        Tuple (succès, message d'erreur) où succès est True si la simulation a réussi.
    """
    try:
        with tempfile.NamedTemporaryFile(dir=chemin, delete=False) as f:
            tmp_path = f.name
        try:
            with open(tmp_path, "wb") as f:
                while True:
                    try:
                        f.write(b"x" * 1024 * 1024)  # 1 Mo par écriture
                        f.flush()
                        os.fsync(f.fileno())
                    except OSError as e:
                        if e.errno == errno.ENOSPC:
                            return (True, None)
                        raise
        except Exception as e:
            return (False, f"Échec de la simulation: {str(e)}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        return (False, f"Échec de la création du fichier temporaire: {str(e)}")


def executer_avec_simulation(
    source_code: str,
    nom_fichier: str,
    simuler_erreur: bool = False,
    chemin_simulation: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Exécute le code source avec une simulation d'erreur système si demandée.

    Args:
        source_code: Code source à exécuter.
        nom_fichier: Nom du fichier pour la compilation.
        simuler_erreur: Si True, simule une erreur de disque plein.
        chemin_simulation: Chemin où simuler l'erreur (défaut: répertoire temporaire).

    Returns:
        Dictionnaire contenant les résultats de l'exécution.
    """
    resultat: Dict[str, Any] = {
        "erreur": None,
        "sortie_stdout": "",
        "sortie_stderr": "",
        "traceback": None,
    }

    try:
        compile(source_code, nom_fichier, "exec")
    except SyntaxError as e:
        resultat["erreur"] = {
            "type": "SyntaxError",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
        return resultat

    if simuler_erreur:
        if chemin_simulation is None:
            with tempfile.TemporaryDirectory() as tmpdir:
                chemin_simulation = Path(tmpdir)
                succes, msg = simuler_erreur_disque_plein(chemin_simulation)
                if not succes:
                    resultat["erreur"] = {
                        "type": "SimulationError",
                        "message": msg or "Échec de la simulation",
                        "traceback": None,
                    }
                    return resultat

    class StdoutCapturer:
        def __init__(self) -> None:
            self.capture: List[str] = []

        def write(self, data: str) -> int:
            self.capture.append(data)
            return len(data)

        def flush(self) -> None:
            pass

        def getvalue(self) -> str:
            return "".join(self.capture)

    class StderrCapturer:
        def __init__(self) -> None:
            self.capture: List[str] = []

        def write(self, data: str) -> int:
            self.capture.append(data)
            return len(data)

        def flush(self) -> None:
            pass

        def getvalue(self) -> str:
            return "".join(self.capture)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_capturer = StdoutCapturer()
    stderr_capturer = StderrCapturer()

    sys.stdout = stdout_capturer  # type: ignore
    sys.stderr = stderr_capturer  # type: ignore

    try:
        exec(source_code, {"__name__": "__main__", "__file__": nom_fichier})
    except Exception as e:
        resultat["erreur"] = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        resultat["sortie_stdout"] = stdout_capturer.getvalue()
        resultat["sortie_stderr"] = stderr_capturer.getvalue()

    return resultat


def analyser_resultat(resultat: Dict[str, Any]) -> Dict[str, Union[bool, str, int]]:
    """
    Analyse le résultat de l'exécution pour détecter les erreurs système.

    Args:
        resultat: Résultat de l'exécution retourné par executer_avec_simulation.

    Returns:
        Dictionnaire contenant l'analyse du comportement.
    """
    analyse = {
        "erreur_systeme": False,
        "code_erreur": None,
        "gestion_erreur": False,
        "sortie_stderr_non_vide": bool(resultat["sortie_stderr"].strip()),
    }

    if resultat["erreur"]:
        analyse["erreur_systeme"] = True
        if "ENOSPC" in resultat["erreur"]["message"] or "No space left" in resultat["erreur"]["message"]:
            analyse["code_erreur"] = errno.ENOSPC
            analyse["gestion_erreur"] = True

    return analyse


def construire_cli() -> argparse.ArgumentParser:
    """Construit l'analyseur de ligne de commande."""
    parser = JsonArgumentParser(
        description="Simule des erreurs système et analyse la réaction d'un code Python.",
        epilog="Exemple: simulateur_erreurs.py --code 'with open(\"test.txt\", \"w\") as f: f.write(\"test\")' --erreur ENOSPC --json",
    )
    parser.add_argument(
        "--code",
        type=str,
        required=True,
        help="Code Python à analyser (entre guillemets).",
    )
    parser.add_argument(
        "--erreur",
        type=str,
        choices=["ENOSPC"],
        help="Erreur système à simuler (ex: ENOSPC pour disque plein).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour l'exécution (défaut: répertoire du script).",
    )
    return parser


def main() -> int:
    """Point d'entrée principal."""
    parser = construire_cli()
    args = parser.parse_args()

    if args.racine != RACINE:
        sys.path.insert(0, str(args.racine))

    simuler_erreur = args.erreur is not None
    resultat_execution = executer_avec_simulation(
        args.code,
        "code_analyse.py",
        simuler_erreur=simuler_erreur,
    )

    analyse = analyser_resultat(resultat_execution)

    examines = ["code_analyse.py"]
    denominateur = len(examines)
    if denominateur == 0:
        print("Aucun élément examiné", file=sys.stderr)
        return 3

    if args.json:
        sortie = {
            "contrat": {
                "QUESTION": "Comment un code réagit-il à des erreurs système ?",
                "MESURE": "Simulation d'erreurs système (disque plein) lors de l'exécution d'un code Python.",
                "HYPOTHESES": "Le code cible est valide syntaxiquement et sémantiquement en l'absence d'erreurs système.",
                "LIMITES": "Ne simule pas toutes les erreurs système possibles, seulement celles implémentables via les modules standard.",
                "CONTRE-EXEMPLES": "Un code qui gère explicitement une erreur système peut ne pas lever d'exception malgré la simulation.",
                "DOMAINE": "Code Python exécuté dans un environnement où les permissions permettent la simulation d'erreurs système.",
            },
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": False,
            "resultat": {
                "execution": resultat_execution,
                "analyse": analyse,
            },
        }
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        # Aucun caractère supplémentaire sur stdout
    else:
        if resultat_execution["erreur"]:
            print("Erreur détectée lors de l'exécution:", file=sys.stderr)
            print(f"Type: {resultat_execution['erreur']['type']}", file=sys.stderr)
            print(f"Message: {resultat_execution['erreur']['message']}", file=sys.stderr)
            if resultat_execution["erreur"]["traceback"]:
                print("Traceback:", file=sys.stderr)
                print(resultat_execution["erreur"]["traceback"], file=sys.stderr)
        else:
            print("Aucune erreur détectée lors de l'exécution.", file=sys.stdout)

        print("\nAnalyse du comportement:", file=sys.stderr)
        print(f"- Erreur système détectée: {'Oui' if analyse['erreur_systeme'] else 'Non'}", file=sys.stderr)
        if analyse["code_erreur"]:
            print(f"- Code d'erreur: {analyse['code_erreur']} ({errno.errorcode.get(analyse['code_erreur'], 'INCONNU')})", file=sys.stderr)
        print(f"- Gestion adéquate de l'erreur: {'Oui' if analyse['gestion_erreur'] else 'Non'}", file=sys.stderr)
        print(f"- Sortie stderr non vide: {'Oui' if analyse['sortie_stderr_non_vide'] else 'Non'}", file=sys.stderr)

    return 0 if not analyse["erreur_systeme"] else 2


if __name__ == "__main__":
    raise SystemExit(main())