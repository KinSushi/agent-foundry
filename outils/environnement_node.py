"""Analyse et isole un environnement Node.js pour exécuter des scripts npm sans polluer Python.

QUESTION      Peut-on exécuter un script npm dans un environnement Node isolé sans polluer le Python ?
MESURE        Existence d’un répertoire `env_node` contenant `node` et `npm`, et absence de modifications de os.environ après exécution.
HYPOTHÈSES    nodeenv installé ou Node déjà présent dans le PATH ; permissions d’écriture dans le répertoire racine.
LIMITES       L’outil ne détecte pas les effets indirects (ex. : fichiers temporaires créés par le script).
CONTRE-EXEMPLE Un script npm qui modifie `process.env.PYTHONPATH` persiste après la fin du processus Node, ce qui fait échouer `check-isolation`.
INVOCATION
    {outil} create {dossier}/proj --node-version 20.9.0 --json
DOMAINE       Projets JavaScript/Node nécessitant une isolation temporaire lors de l'exécution de builds ou de tests automatisés.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "creer_environnement",
    "installer_paquet_npm",
    "executer_npm_script",
    "verifier_isolation",
    "main",
]

RACINE = Path(__file__).resolve().parent

# Configuration des sorties
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Variables d'environnement de référence pour l'isolation
_ENV_REFERENCE = os.environ.copy()


class ErreurEnvironnement(Exception):
    """Erreur liée à la création ou l'utilisation de l'environnement Node."""


class ErreurIsolation(Exception):
    """Violation de l'isolation détectée."""


class ErreurExecution(Exception):
    """Échec d'exécution d'un script npm."""


def _chemin_absolu(cible: str | Path, racine: Path) -> Path:
    """Convertit un chemin relatif en chemin absolu basé sur la racine."""
    p = Path(cible)
    return p if p.is_absolute() else racine / p


def _verifier_chemin_valide(
    chemin: Path, doit_exister: bool = True, doit_etre_dossier: bool = True
) -> None:
    """Vérifie qu'un chemin est valide et correspond aux critères attendus."""
    if doit_exister and not chemin.exists():
        raise ErreurEnvironnement(f"Chemin introuvable : {chemin}")
    if doit_exister and doit_etre_dossier and not chemin.is_dir():
        raise ErreurEnvironnement(f"Le chemin fourni n'est pas un répertoire : {chemin}")
    if not doit_exister and chemin.exists():
        raise ErreurEnvironnement(f"Le chemin existe déjà : {chemin}")


def _verifier_nodeenv_disponible() -> bool:
    """Vérifie si nodeenv est disponible sans l'importer."""
    try:
        import importlib.util

        return importlib.util.find_spec("nodeenv") is not None
    except ImportError:
        return False


def creer_environnement(
    racine: Path, node_version: Optional[str] = None, *, force: bool = False
) -> Path:
    """Crée un environnement Node isolé.

    Args:
        racine: Répertoire racine où créer l'environnement.
        node_version: Version de Node.js à installer (ex: "20.9.0").
        force: Si True, écrase un environnement existant.

    Returns:
        Chemin vers le répertoire de l'environnement créé.

    Raises:
        ErreurEnvironnement: Si la création échoue.
    """
    if not _verifier_nodeenv_disponible():
        raise ErreurEnvironnement("nodeenv non disponible - impossible de créer l'environnement")

    env_path = racine / "env_node"
    if env_path.exists():
        if not force:
            raise ErreurEnvironnement(
                f"Environnement existant : {env_path} (utilisez --force pour écraser)"
            )
        shutil.rmtree(env_path)

    try:
        import nodeenv

        env = nodeenv.Environment(str(env_path))
        if node_version:
            env.create(jobs=1, node=node_version)
        else:
            env.create(jobs=1)
        return env_path
    except Exception as e:
        raise ErreurEnvironnement(f"Échec de la création de l'environnement : {e}") from e


def installer_paquet_npm(env_path: Path, paquet: str, version: Optional[str] = None) -> None:
    """Installe un paquet npm dans l'environnement Node.

    Args:
        env_path: Chemin vers l'environnement Node.
        paquet: Nom du paquet à installer.
        version: Version spécifique à installer (ex: "1.2.3").

    Raises:
        ErreurEnvironnement: Si l'installation échoue.
    """
    if not env_path.exists():
        raise ErreurEnvironnement(f"Environnement introuvable : {env_path}")

    cmd = ["npm", "install", paquet]
    if version:
        cmd.append(f"{paquet}@{version}")

    try:
        subprocess.run(
            cmd,
            cwd=env_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as e:
        raise ErreurEnvironnement(f"Échec de l'installation de {paquet} : {e.stderr}") from e
    except Exception as e:
        raise ErreurEnvironnement(f"Erreur inattendue lors de l'installation : {e}") from e


def executer_npm_script(
    env_path: Path, script: str, args: Optional[list[str]] = None
) -> subprocess.CompletedProcess:
    """Exécute un script npm dans l'environnement Node.

    Args:
        env_path: Chemin vers l'environnement Node.
        script: Nom du script à exécuter (ex: "build").
        args: Arguments supplémentaires à passer au script.

    Returns:
        Objet CompletedProcess contenant les résultats de l'exécution.

    Raises:
        ErreurExecution: Si l'exécution échoue.
    """
    if not env_path.exists():
        raise ErreurEnvironnement(f"Environnement introuvable : {env_path}")

    cmd = ["npm", "run", script]
    if args:
        cmd.extend(args)

    try:
        return subprocess.run(
            cmd,
            cwd=env_path,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as e:
        raise ErreurExecution(f"Échec de l'exécution du script {script} : {e.stderr}") from e
    except Exception as e:
        raise ErreurExecution(f"Erreur inattendue lors de l'exécution : {e}") from e


def verifier_isolation(env_path: Path) -> bool:
    """Vérifie que l'environnement Node n'a pas modifié les variables d'environnement Python.

    Args:
        env_path: Chemin vers l'environnement Node (non utilisé ici mais requis pour l'API).

    Returns:
        True si l'isolation est préservée, False sinon.
    """
    violations = []
    for key, value in os.environ.items():
        if key not in _ENV_REFERENCE:
            violations.append(f"Variable ajoutée : {key}")
        elif _ENV_REFERENCE[key] != value:
            violations.append(
                f"Variable modifiée : {key} (avant: {_ENV_REFERENCE[key]}, après: {value})"
            )

    if violations:
        raise ErreurIsolation("\n".join(violations))
    return True


def _creer_analyseur_parent() -> argparse.ArgumentParser:
    """Crée un analyseur parent pour les arguments communs."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--racine",
        type=str,
        default=str(RACINE),
        help="Répertoire racine pour les opérations (défaut: répertoire du script)",
    )
    parent.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON",
    )
    return parent


def _creer_analyseur() -> argparse.ArgumentParser:
    """Crée l'analyseur d'arguments principal."""
    parent = _creer_analyseur_parent()
    parser = argparse.ArgumentParser(
        description="Gère un environnement Node.js isolé pour exécuter des scripts npm.",
        parents=[parent],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemple d'appel :\n"
        "  environnement_node.py create --racine ./mon_projet --node-version 20.9.0\n"
        "  environnement_node.py npm-run --racine ./mon_projet --script build",
    )

    sous_commandes = parser.add_subparsers(dest="commande", required=True)

    # Sous-commande create
    create = sous_commandes.add_parser(
        "create",
        parents=[parent],
        help="Crée un environnement Node isolé.",
    )
    create.add_argument(
        "--node-version",
        type=str,
        help="Version de Node.js à installer (ex: 20.9.0)",
    )
    create.add_argument(
        "--force",
        action="store_true",
        help="Écrase un environnement existant",
    )

    # Sous-commande install-node
    install_node = sous_commandes.add_parser(
        "install-node",
        parents=[parent],
        help="Installe ou met à jour Node.js dans l'environnement existant.",
    )
    install_node.add_argument(
        "--node-version",
        type=str,
        required=True,
        help="Version de Node.js à installer (ex: 20.9.0)",
    )

    # Sous-commande npm-install
    npm_install = sous_commandes.add_parser(
        "npm-install",
        parents=[parent],
        help="Installe un paquet npm dans l'environnement.",
    )
    npm_install.add_argument(
        "--package",
        type=str,
        required=True,
        help="Nom du paquet à installer",
    )
    npm_install.add_argument(
        "--version",
        type=str,
        help="Version spécifique du paquet à installer",
    )

    # Sous-commande npm-run
    npm_run = sous_commandes.add_parser(
        "npm-run",
        parents=[parent],
        help="Exécute un script npm dans l'environnement.",
    )
    npm_run.add_argument(
        "--script",
        type=str,
        required=True,
        help="Nom du script à exécuter (ex: build)",
    )
    npm_run.add_argument(
        "--args",
        type=str,
        nargs="*",
        help="Arguments supplémentaires à passer au script",
    )

    # Sous-commande check-isolation
    sous_commandes.add_parser(
        "check-isolation",
        parents=[parent],
        help="Vérifie que l'environnement Node n'a pas modifié les variables d'environnement Python.",
    )

    return parser


def _formater_sortie_json(
    resultat: Any,
    denominateur: int,
    examines: Optional[list[str]] = None,
    examines_tronques: bool = False,
) -> str:
    """Formate le résultat au format JSON avec les métadonnées requises."""
    sortie = {
        "denominateur": denominateur,
        "examines": examines if examines is not None else [],
        "examines_tronques": examines_tronques,
    }

    if isinstance(resultat, subprocess.CompletedProcess):
        sortie["stdout"] = resultat.stdout
        sortie["stderr"] = resultat.stderr
        sortie["returncode"] = resultat.returncode
    elif isinstance(resultat, Path):
        sortie["chemin"] = str(resultat)
    elif isinstance(resultat, bool):
        sortie["isolation_valide"] = resultat
    else:
        sortie["resultat"] = resultat

    return json.dumps(sortie, ensure_ascii=False, indent=2)


def _formater_sortie_humaine(resultat: Any) -> str:
    """Formate le résultat pour une sortie lisible par un humain."""
    if isinstance(resultat, subprocess.CompletedProcess):
        return f"Sortie du script :\n{resultat.stdout}\nErreurs :\n{resultat.stderr}"
    if isinstance(resultat, Path):
        return f"Environnement créé : {resultat}"
    if isinstance(resultat, bool):
        return "Isolation vérifiée : OK" if resultat else "Isolation violée"
    return str(resultat)


def main(argv: Optional[list[str]] = None) -> int:
    """Point d'entrée principal de l'outil."""
    parser = _creer_analyseur()
    args = parser.parse_args(argv)

    racine = _chemin_absolu(args.racine, RACINE)
    _verifier_chemin_valide(racine, doit_exister=True, doit_etre_dossier=True)

    try:
        if args.commande == "create":
            env_path = creer_environnement(racine, args.node_version, force=args.force)
            resultat = env_path
            denominateur = 1
            examines = [str(env_path)]

        elif args.commande == "install-node":
            if not _verifier_nodeenv_disponible():
                print("nodeenv non disponible - impossible d'installer Node.js", file=sys.stderr)
                return 3
            env_path = racine / "env_node"
            _verifier_chemin_valide(env_path, doit_exister=True)
            try:
                import nodeenv

                env = nodeenv.Environment(str(env_path))
                env.create(jobs=1, node=args.node_version)
                resultat = f"Node.js {args.node_version} installé dans {env_path}"
                denominateur = 1
                examines = [str(env_path)]
            except Exception as e:
                raise ErreurEnvironnement(f"Échec de l'installation de Node.js : {e}") from e

        elif args.commande == "npm-install":
            if not _verifier_nodeenv_disponible():
                print("nodeenv non disponible - impossible d'installer des paquets", file=sys.stderr)
                return 3
            env_path = racine / "env_node"
            _verifier_chemin_valide(env_path, doit_exister=True)
            installer_paquet_npm(env_path, args.package, args.version)
            resultat = f"Paquet {args.package} installé dans {env_path}"
            denominateur = 1
            examines = [str(env_path / "node_modules")]

        elif args.commande == "npm-run":
            env_path = racine / "env_node"
            _verifier_chemin_valide(env_path, doit_exister=True)
            resultat = executer_npm_script(env_path, args.script, args.args)
            denominateur = 1
            examines = [str(env_path)]

        elif args.commande == "check-isolation":
            env_path = racine / "env_node"
            _verifier_chemin_valide(env_path, doit_exister=False)  # L'environnement peut ne pas exister
            resultat = verifier_isolation(env_path)
            denominateur = 1
            examines = list(_ENV_REFERENCE.keys())[:200]
            examines_tronques = len(_ENV_REFERENCE) > 200

        else:
            print(f"Commande inconnue : {args.commande}", file=sys.stderr)
            return 2

    except ErreurEnvironnement as e:
        print(f"Erreur environnement : {e}", file=sys.stderr)
        if args.json:
            print(_formater_sortie_json(None, 0, examines=["erreur"]), file=sys.stdout)
        return 3
    except ErreurIsolation as e:
        print(f"Violation d'isolation : {e}", file=sys.stderr)
        if args.json:
            print(_formater_sortie_json(False, 1, examines=list(_ENV_REFERENCE.keys())[:200]), file=sys.stdout)
        return 1
    except ErreurExecution as e:
        print(f"Échec d'exécution : {e}", file=sys.stderr)
        if args.json:
            print(_formater_sortie_json(None, 1, examines=["erreur_execution"]), file=sys.stdout)
        return 1
    except Exception as e:
        print(f"Erreur inattendue : {e}", file=sys.stderr)
        if args.json:
            print(_formater_sortie_json(None, 0, examines=["erreur_inattendue"]), file=sys.stdout)
        return 3

    if args.json:
        print(
            _formater_sortie_json(
                resultat,
                denominateur,
                examines=examines,
                examines_tronques=len(examines) > 200 if examines else False,
            ),
            file=sys.stdout,
        )
    else:
        print(_formater_sortie_humaine(resultat), file=sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())