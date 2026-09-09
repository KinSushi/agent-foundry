"""QUESTION      Peut-on inspecter/modifier du bytecode Python sans outils natifs ?
MESURE        Existence d'une API tierce (`depyf`) ou désassemblage brut via `dis`.
HYPOTHESES    Le bytecode est valide et conforme à la version de Python utilisée.
LIMITES       Aucune garantie sur la sémantique du code reconstitué (ex: noms de variables perdus).
CONTRE-EXEMPLES Un fichier `.pyc` généré avec une version de Python différente (ex: 3.8 vs 3.14).
INVOCATION    {outil} optimiser {fichier} --json
DOMAINE       Fichiers `.pyc` et `.py` valides, plateformes Windows/Linux.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import marshal
import sys
from pathlib import Path
from types import CodeType
from typing import Any, Dict, List, Sequence

# ---------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------

class OutilErreur(Exception):
    """Exception levée par le cœur en cas d’erreur contrôlée."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code

def _configurer_encodage() -> None:
    """Force l’encodage UTF‑8 sur stdout et stderr si possible."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

def _chemin_resolu(cible: str | Path, racine: Path) -> Path:
    p = Path(cible)
    return p if p.is_absolute() else racine / p

def _lire_fichier_texte(chemin: Path) -> str:
    try:
        with chemin.open("r", encoding="utf-8", errors="replace") as f:
            texte = f.read()
        if chr(0) in texte:
            raise OutilErreur(f"Contenu binaire détecté dans {chemin}", code=3)
        return texte
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise OutilErreur(f"Impossible de lire {chemin} : {exc}", code=1) from exc

def _charger_code_pyc(chemin: Path) -> CodeType:
    try:
        with chemin.open("rb") as f:
            f.read(16)  # ignore l’en‑tête .pyc
            code = marshal.load(f)
        if not isinstance(code, CodeType):
            raise OutilErreur(f"Objet chargé depuis {chemin} n’est pas du code.", code=3)
        return code
    except (OSError, EOFError, ValueError) as exc:
        raise OutilErreur(f"Erreur de lecture du .pyc {chemin} : {exc}", code=3) from exc

def _charger_code_py(chemin: Path) -> CodeType:
    source = _lire_fichier_texte(chemin)
    try:
        return compile(source, str(chemin), "exec")
    except Exception as exc:
        raise OutilErreur(f"Compilation du source {chemin} échouée : {exc}", code=3) from exc

def _decompiler_brut(code: CodeType) -> str:
    import dis  # import standard, safe

    lignes = [
        instr.opname + (" " + str(instr.argval) if instr.argval is not None else "")
        for instr in dis.get_instructions(code)
    ]
    return "\n".join(lignes)

def _construire_sortie_json(denominateur: int, examines: List[str], **kwargs: Any) -> Dict[str, Any]:
    """Construit systématiquement la sortie JSON avec les clés obligatoires."""
    base = {
        "denominateur": denominateur,
        "examines": examines
    }
    base.update(kwargs)
    return base

def _decompiler_core(chemin: Path, racine: Path) -> Dict[str, Any]:
    code = _charger_code_pyc(chemin)
    depyf_spec = None
    if importlib.util.find_spec("depyf"):
        try:
            import depyf.decompiler  # type: ignore
            depyf_spec = depyf.decompiler.decompile
        except Exception:
            depyf_spec = None
    if depyf_spec:
        try:
            source = depyf_spec(code)  # type: ignore
        except Exception as exc:
            raise OutilErreur(f"Décompilation via depyf échouée : {exc}", code=3) from exc
    else:
        source = _decompiler_brut(code)

    instructions = source.splitlines()
    denominateur = len(instructions)
    examines = instructions[:200]
    return _construire_sortie_json(
        denominateur=denominateur,
        examines=examines,
        source=source,
        examines_tronques=len(instructions) > 200
    )

def _transformer_core(
    chemin: Path, remplacements: Sequence[str], racine: Path
) -> Dict[str, Any]:
    if not importlib.util.find_spec("depyf"):
        sys.stderr.write("denominateur nul – dépendance 'depyf' absente.\n")
        return _construire_sortie_json(denominateur=0, examines=[])

    code = _charger_code_py(chemin)

    try:
        import depyf.code_transform  # type: ignore
    except Exception as exc:
        raise OutilErreur(f"Import de depyf.code_transform échoué : {exc}", code=1) from exc

    changes: Dict[str, str] = {}
    for rep in remplacements:
        if "=" not in rep:
            raise OutilErreur(
                f"Remplacement mal formé '{rep}'. Utiliser OPCODE=VALEUR.", code=2
            )
        op, val = rep.split("=", 1)
        changes[op.strip()] = val.strip()

    try:
        nouveau_code = depyf.code_transform.transform_code(code, **changes)  # type: ignore
    except Exception as exc:
        raise OutilErreur(f"Transformation du code échouée : {exc}", code=3) from exc

    co_code_hex = nouveau_code.co_code.hex()
    denominateur = len(nouveau_code.co_code)
    examines = [
        co_code_hex[i : i + 2] for i in range(0, min(len(co_code_hex), 400), 2)
    ]
    return _construire_sortie_json(
        denominateur=denominateur,
        examines=examines,
        co_code=co_code_hex,
        examines_tronques=len(co_code_hex) > 400
    )

def _optimiser_core(chemin: Path, racine: Path) -> Dict[str, Any]:
    if not importlib.util.find_spec("depyf"):
        # mode dégradé : désassemblage brut
        code = _charger_code_py(chemin)
        disassembly = _decompiler_brut(code)
        instructions = disassembly.splitlines()
        denominateur = len(instructions)
        examines = instructions[:200]
        return _construire_sortie_json(
            denominateur=denominateur,
            examines=examines,
            diff="optimisation non disponible (depyf absent)",
            examines_tronques=len(instructions) > 200
        )

    try:
        import depyf.optimization  # type: ignore
    except Exception as exc:
        raise OutilErreur(f"Import de depyf.optimization échoué : {exc}", code=1) from exc

    code = _charger_code_py(chemin)
    try:
        code_opt = depyf.optimization.optimize_code(code)  # type: ignore
    except Exception as exc:
        raise OutilErreur(f"Optimisation du code échouée : {exc}", code=3) from exc

    co_code_hex = code_opt.co_code.hex()
    denominateur = len(code_opt.co_code)
    examines = [
        co_code_hex[i : i + 2] for i in range(0, min(len(co_code_hex), 400), 2)
    ]
    return _construire_sortie_json(
        denominateur=denominateur,
        examines=examines,
        co_code=co_code_hex,
        examines_tronques=len(co_code_hex) > 400
    )

def _comparer_core(chemin1: Path, chemin2: Path, racine: Path) -> Dict[str, Any]:
    code1 = _charger_code_py(chemin1)
    code2 = _charger_code_py(chemin2)

    if importlib.util.find_spec("depyf"):
        try:
            import depyf.utils  # type: ignore
            diff = depyf.utils.diff_code(code1, code2)  # type: ignore
        except Exception:
            diff = None
    else:
        diff = None

    if diff is None:
        diff = "\n".join(
            [f"- {b1}" for b1 in code1.co_code.hex().split()]
            + [f"+ {b2}" for b2 in code2.co_code.hex().split()]
        )

    lignes = diff.splitlines()
    denominateur = len(lignes)
    examines = lignes[:200]
    return _construire_sortie_json(
        denominateur=denominateur,
        examines=examines,
        diff=diff,
        examines_tronques=len(lignes) > 200
    )

def main() -> int:
    _configurer_encodage()

    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit la sortie au format JSON unique.",
    )
    parent_parser.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )

    parser = argparse.ArgumentParser(
        description="Outil d’inspection et de transformation du bytecode Python."
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # decompiler
    p_decomp = subparsers.add_parser(
        "decompiler",
        parents=[parent_parser],
        help="Décompile un fichier .pyc en source lisible.",
    )
    p_decomp.add_argument("cible", help="Fichier .pyc à décompiler.")

    # transformer
    p_trans = subparsers.add_parser(
        "transformer",
        parents=[parent_parser],
        help="Transforme le bytecode d’un fichier .py selon des remplacements.",
    )
    p_trans.add_argument("cible", help="Fichier .py source.")
    p_trans.add_argument(
        "--remplacer",
        action="append",
        default=[],
        metavar="OPCODE=VALEUR",
        help="Remplacement d’un opcode par une nouvelle valeur (répétable).",
    )

    # optimiser
    p_opt = subparsers.add_parser(
        "optimiser",
        parents=[parent_parser],
        help="Optimise le bytecode d’un fichier .py.",
    )
    p_opt.add_argument("cible", help="Fichier .py à optimiser.")

    # comparer
    p_comp = subparsers.add_parser(
        "comparer",
        parents=[parent_parser],
        help="Compare deux fichiers .py et affiche les différences d’instructions.",
    )
    p_comp.add_argument("cible1", help="Premier fichier .py.")
    p_comp.add_argument("cible2", help="Second fichier .py.")

    args = parser.parse_args()

    racine = getattr(args, "racine", RACINE)

    try:
        if args.commande == "decompiler":
            cible = _chemin_resolu(args.cible, racine)
            if not cible.is_file():
                sys.stderr.write("denominateur nul – fichier introuvable.\n")
                if getattr(args, "json", False):
                    json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                return 3
            result = _decompiler_core(cible, racine)

        elif args.commande == "transformer":
            cible = _chemin_resolu(args.cible, racine)
            if not cible.is_file():
                sys.stderr.write("denominateur nul – fichier introuvable.\n")
                if getattr(args, "json", False):
                    json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                return 3
            result = _transformer_core(cible, args.remplacer, racine)

        elif args.commande == "optimiser":
            cible = _chemin_resolu(args.cible, racine)
            if not cible.is_file():
                sys.stderr.write("denominateur nul – fichier introuvable.\n")
                if getattr(args, "json", False):
                    json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                return 3
            result = _optimiser_core(cible, racine)

        elif args.commande == "comparer":
            cible1 = _chemin_resolu(args.cible1, racine)
            cible2 = _chemin_resolu(args.cible2, racine)
            if not cible1.is_file() or not cible2.is_file():
                sys.stderr.write("denominateur nul – un ou plusieurs fichiers introuvables.\n")
                if getattr(args, "json", False):
                    json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                return 3
            result = _comparer_core(cible1, cible2, racine)

        else:
            raise OutilErreur("Commande inconnue.", code=2)

        # Production JSON systématique quand --json est demandé
        if getattr(args, "json", False):
            json.dump(result, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        # Affichage humain
        denominateur = result["denominateur"]
        if denominateur == 0:
            sys.stderr.write("denominateur nul – aucun élément à examiner.\n")
            return 3

        if args.commande == "decompiler":
            sys.stdout.write(result["source"] + "\n")
        elif args.commande == "transformer":
            sys.stdout.write(result["co_code"] + "\n")
        elif args.commande == "optimiser":
            sys.stdout.write(result.get("co_code", result.get("diff", "")) + "\n")
        elif args.commande == "comparer":
            sys.stdout.write(result["diff"] + "\n")

        return 0

    except OutilErreur as err:
        sys.stderr.write(f"{err}\n")
        if getattr(args, "json", False):
            json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return err.code
    except Exception as exc:
        sys.stderr.write(f"Erreur interne inattendue : {exc}\n")
        if getattr(args, "json", False):
            json.dump(_construire_sortie_json(denominateur=0, examines=[]), sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 3

__all__ = [
    "main",
    "OutilErreur",
    "RACINE",
]

if __name__ == "__main__":
    raise SystemExit(main())