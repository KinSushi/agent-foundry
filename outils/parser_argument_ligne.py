"""
QUESTION      Comment interpréter ces arguments CLI ?
MESURE        Extraction statique des déclarations d'arguments via AST (argparse, click, typer) et validation par compilation.
HYPOTHÈSES    La cible est un fichier Python source valide et les arguments sont déclarés de manière standard.
LIMITES       Ne capture pas les arguments générés dynamiquement ou via des boucles complexes.
CONTRE-EXEMPLES Un script qui construit ses arguments avec une boucle `for` ne sera pas analysé correctement.
INVOCATION
    {outil} --cible {fichier} --json
DOMAINE       Fichiers Python source utilisant argparse, click ou typer pour leurs interfaces en ligne de commande.
"""
from __future__ import annotations

import sys
import ast
import json
import argparse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Tierces optionnelles
try:
    from rich.console import Console
    from rich.table import Table
    RICH_DISPONIBLE = True
except ImportError:
    RICH_DISPONIBLE = False

try:
    import click
    CLICK_DISPONIBLE = True
except ImportError:
    CLICK_DISPONIBLE = False

try:
    import typer
    TYPER_DISPONIBLE = True
except ImportError:
    TYPER_DISPONIBLE = False

RACINE = Path(__file__).resolve().parent

__all__ = ["VisiteurCLI", "interpreter_arguments_cli", "main"]


class CLIError(Exception):
    """Exception levée lorsque le parseur d'arguments rencontre une erreur en mode JSON."""
    pass


class VisiteurCLI(ast.NodeVisitor):
    """Visiteur AST pour extraire les déclarations d'arguments CLI."""

    def __init__(self) -> None:
        self.arguments: list[dict] = []

    def _extraire_kwargs(self, node: ast.Call) -> dict:
        kwargs = {}
        for kw in node.keywords:
            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                kwargs["aide"] = kw.value.value
            elif kw.arg == "type" and isinstance(kw.value, ast.Name):
                kwargs["type"] = kw.value.id
            elif kw.arg == "required" and isinstance(kw.value, ast.Constant):
                kwargs["requis"] = kw.value.value
        return kwargs

    def _extraire_nom(self, node: ast.Call) -> str | None:
        if node.args and isinstance(node.args[0], ast.Constant):
            return str(node.args[0].value)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("add_argument", "option", "argument"):
                nom = self._extraire_nom(node)
                if nom:
                    arg_dict = {"nom": nom}
                    arg_dict.update(self._extraire_kwargs(node))
                    self.arguments.append(arg_dict)
        elif isinstance(node.func, ast.Name):
            if node.func.id in ("Option", "Argument"):
                nom = self._extraire_nom(node)
                if nom:
                    arg_dict = {"nom": nom}
                    arg_dict.update(self._extraire_kwargs(node))
                    self.arguments.append(arg_dict)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        args = node.args.args
        defaults = node.args.defaults
        offset = len(args) - len(defaults)
        for i, default in enumerate(defaults):
            arg = args[offset + i]
            if isinstance(default, ast.Call):
                call = default
                is_cli = False
                if isinstance(call.func, ast.Attribute) and call.func.attr in ("Option", "Argument"):
                    is_cli = True
                elif isinstance(call.func, ast.Name) and call.func.id in ("Option", "Argument"):
                    is_cli = True
                if is_cli:
                    nom = self._extraire_nom(call)
                    if not nom:
                        nom = f"--{arg.arg.replace('_', '-')}"
                    arg_dict = {"nom": nom}
                    arg_dict.update(self._extraire_kwargs(call))
                    self.arguments.append(arg_dict)
        self.generic_visit(node)


def interpreter_arguments_cli(chemin_cible: Path, chemin_racine: Path) -> list[dict]:
    """Cœur de l'outil : lit, compile et extrait les arguments CLI d'un source Python."""
    if str(chemin_racine) not in sys.path:
        sys.path.insert(0, str(chemin_racine))

    if not chemin_cible.exists() or not chemin_cible.is_file():
        raise ValueError(f"Cible introuvable ou illisible: {chemin_cible}")

    source = chemin_cible.read_text(encoding="utf-8")
    compile(source, str(chemin_cible), "exec")
    arbre = ast.parse(source)
    visiteur = VisiteurCLI()
    visiteur.visit(arbre)
    return visiteur.arguments


class ArgumentParserJSON(argparse.ArgumentParser):
    """ArgumentParser qui lève une exception personnalisée en cas d'erreur si --json est présent."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._json_mode: bool = False

    def error(self, message: str) -> None:
        if self._json_mode:
            raise CLIError(message)
        super().error(message)


def main() -> int:
    json_mode = "--json" in sys.argv
    parser = ArgumentParserJSON(
        description="Analyse statiquement les déclarations d'arguments CLI dans un fichier Python.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemple réel: python parser_argument_ligne.py --cible mon_script.py --json"
    )
    parser._json_mode = json_mode
    parser.add_argument(
        "--cible",
        required=True,
        help="Chemin vers le fichier Python cible à analyser"
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help=f"Dossier racine à insérer en tête de sys.path (défaut: {RACINE})"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON unique sur stdout, rien d'autre"
    )

    try:
        args = parser.parse_args()
    except CLIError as err:
        resultat = {
            "erreur": f"Erreur d'argument: {err}",
            "denominateur": 0
        }
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        sys.stderr.write(f"Erreur d'argument: {err}\n")
        return 2

    chemin_cible = Path(args.cible).resolve()
    chemin_racine = Path(args.racine).resolve()

    contrat = {
        "QUESTION": "Comment interpréter ces arguments CLI ?",
        "MESURE": "Extraction statique des déclarations d'arguments via AST (argparse, click, typer).",
        "HYPOTHÈSES": "La cible est un fichier Python valide et les arguments sont déclarés de manière standard.",
        "LIMITES": "Ne capture pas les arguments générés dynamiquement ou via des boucles.",
        "CONTRE-EXEMPLES": "Un script qui construit ses arguments avec une boucle `for` ne sera pas analysé correctement.",
        "DOMAINE": "Fichiers Python source utilisant argparse, click ou typer pour leurs CLI."
    }

    try:
        arguments = interpreter_arguments_cli(chemin_cible, chemin_racine)
    except Exception as e:
        if args.json:
            resultat = {
                "contrat": contrat,
                "erreur": str(e),
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "tierces": {
                    "rich": RICH_DISPONIBLE,
                    "click": CLICK_DISPONIBLE,
                    "typer": TYPER_DISPONIBLE
                }
            }
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        print(f"Erreur lors de l'analyse: {e}", file=sys.stderr)
        return 2

    denominateur = len(arguments)
    examines = arguments[:200]
    examines_tronques = denominateur > 200

    if args.json:
        resultat = {
            "contrat": contrat,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
            "tierces": {
                "rich": RICH_DISPONIBLE,
                "click": CLICK_DISPONIBLE,
                "typer": TYPER_DISPONIBLE
            }
        }
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        if denominateur == 0:
            sys.stderr.write("denominateur nul : rien à examiner, refus de conclure.\n")
            return 3
        return 0

    if denominateur == 0:
        sys.stderr.write("denominateur nul : rien à examiner, refus de conclure.\n")
        return 3

    # Sortie humaine
    mode = "standard"
    if not RICH_DISPONIBLE and not CLICK_DISPONIBLE:
        mode = "dégradé (rich et click absents, coloration désactivée)"
    elif not RICH_DISPONIBLE and CLICK_DISPONIBLE:
        mode = "dégradé (rich absent, coloration via click)"

    print(f"Mode: {mode}")
    print(f"Question: {contrat['QUESTION']}")
    print(f"Mesure: {contrat['MESURE']}")
    print(f"Denominateur: {denominateur}")

    if RICH_DISPONIBLE:
        console = Console()
        table = Table(title="Arguments CLI examinés")
        table.add_column("Nom", style="cyan")
        table.add_column("Aide", style="green")
        table.add_column("Type", style="magenta")
        table.add_column("Requis", style="red")
        for arg in examines:
            table.add_row(
                arg.get("nom", ""),
                str(arg.get("aide", "")),
                str(arg.get("type", "")),
                str(arg.get("requis", ""))
            )
        console.print(table)
    elif CLICK_DISPONIBLE:
        for arg in examines:
            nom = click.style(arg.get("nom", ""), fg="cyan")
            aide = click.style(str(arg.get("aide", "")), fg="green")
            print(f"  - {nom}: {aide}")
    else:
        for arg in examines:
            print(f"  - {arg.get('nom', '')}: {arg.get('aide', 'Aucune aide')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())