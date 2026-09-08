"""\
QUESTION
    L'outil doit déterminer si un agent IA lancé en isolation possède réellement toute la matière du dépôt ou s'il répond de mémoire.

MESURE
    Il compare le contenu réel du worktree avec la liste des fichiers suivis par Git, en tenant compte des fichiers non mesurables et du .gitignore.

HYPOTHESES
    - Le dépôt possède un répertoire racine contenant le .git et le .gitignore.
    - `git ls-files` renvoie la liste exhaustive des fichiers suivis.
    - Les chemins sont résolus de façon sensible à la casse du système.

LIMITES
    - Les fichiers non lisibles sont marqués NON MESURABLE et exclus du comptage.
    - Les répertoires vides ne sont pas considérés comme manquants.
    - Le volume copié est limité par `--plafond-mo`.

CONTRE-EXEMPLES
    "un agent lance en worktree isole a conclu sur un tiers de la matiere sans le savoir : doc/ etait dans .gitignore, et rien ne le signalait."

INVOCATION
    {outil} diagnostiquer {fichier} --json

DOMAINE
    Audit d'intégrité de dépôts Git en worktree isolé.
"""

from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Mapping, Tuple, Dict, Any

__all__ = [
    "diagnostiquer",
    "armer",
    "verifier",
]

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False

def _git_tracked_files(root: Path) -> List[Path]:
    """Retourne la liste des chemins (relatifs à root) suivis par Git."""
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [Path(line) for line in cp.stdout.splitlines() if line]
    except subprocess.CalledProcessError as e:
        print(f"Erreur git : {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"Erreur inattendue lors de l'appel à git : {e}", file=sys.stderr)
        return []

def _read_gitignore(root: Path) -> List[str]:
    """Lit .gitignore et renvoie les motifs glob (sans commentaires)."""
    gi = root / ".gitignore"
    if not gi.is_file():
        return []
    try:
        lines = gi.read_text(encoding="utf-8").splitlines()
        return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
    except Exception:
        return []

def _matches_any(path: Path, patterns: Iterable[str]) -> bool:
    """Vrai si le chemin (string) correspond à l'un des motifs glob."""
    s = str(path)
    return any(fnmatch.fnmatch(s, pat) for pat in patterns)

def _creer_sortie_json_base(denominateur: int) -> Dict[str, Any]:
    """Crée le dictionnaire de base pour toute sortie JSON."""
    return {
        "denominateur": denominateur,
        "examines": [],
        "examines_tronques": False,
    }

def _afficher_sortie_json(sortie: Mapping[str, object]) -> None:
    """Affiche UNIQUEMENT le JSON sur stdout, sans autre texte."""
    json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

def _gerer_refus_legitime(message: str) -> int:
    """Gère le protocole de refus légitime (code 3)."""
    print(f"Denominateur nul : {message}", file=sys.stderr)
    return 3

# --------------------------------------------------------------------------- #
# Core functions (sans impression)
# --------------------------------------------------------------------------- #
def diagnostiquer(worktree: Path, racine: Path) -> Mapping[str, object]:
    tracked = _git_tracked_files(racine)
    if not tracked:
        return _creer_sortie_json_base(0)

    gitignore = _read_gitignore(racine)
    per_dir: dict[str, dict[str, int]] = {}
    present, missing, non_mesurable = 0, 0, 0

    for rel in tracked:
        src = racine / rel
        dst = worktree / rel
        try:
            if dst.is_file():
                size = dst.stat().st_size
                present += 1
                key = str(rel.parent) or "."
                per_dir.setdefault(key, {"present": 0, "missing": 0, "size": 0})
                per_dir[key]["present"] += 1
                per_dir[key]["size"] += size
            else:
                missing += 1
                key = str(rel.parent) or "."
                per_dir.setdefault(key, {"present": 0, "missing": 0, "size": 0})
                per_dir[key]["missing"] += 1
        except PermissionError:
            non_mesurable += 1
        except Exception:
            non_mesurable += 1

    # texte humain
    lines = [
        f"Analyse du worktree : {worktree}",
        f"Fichiers suivis par Git : {len(tracked)}",
        f"Présents : {present}",
        f"Manquants : {missing}",
    ]
    if non_mesurable:
        lines.append(f"Non mesurables (permission refusée) : {non_mesurable}")

    lines.append("\nDétails par dossier (triés par nombre de manquants décroissant) :")
    for d, stats in sorted(per_dir.items(), key=lambda kv: kv[1]["missing"], reverse=True):
        lines.append(
            f"{d}: présent={stats['present']}, manquant={stats['missing']}, poids={stats['size']} octets"
        )
    human = "\n".join(lines)

    examines = tracked[:200]
    sortie = _creer_sortie_json_base(len(tracked))
    sortie.update({
        "examines": [str(p) for p in examines],
        "examines_tronques": len(tracked) > 200,
        "missing": missing,
        "non_mesurables": non_mesurable,
        "human": human,
    })
    return sortie

def armer(
    worktree: Path,
    racine: Path,
    inclure: List[str],
    exclure: List[str],
    plafond: int,
    a_blanc: bool,
) -> Mapping[str, object]:
    tracked = _git_tracked_files(racine)
    if not tracked:
        return _creer_sortie_json_base(0)

    missing = [p for p in tracked if not (worktree / p).is_file()]

    # filtres inclure / exclure
    if inclure:
        missing = [p for p in missing if _matches_any(p, inclure)]
    if exclure:
        missing = [p for p in missing if not _matches_any(p, exclure)]

    truncated = len(missing) > plafond
    to_copy = missing[:plafond]

    actions = []
    for rel in to_copy:
        src = racine / rel
        dst = worktree / rel

        if not _is_path_inside(dst, worktree):
            actions.append((rel, "refusé (hors worktree)"))
            continue

        if a_blanc:
            actions.append((rel, "simulé"))
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            actions.append((rel, "copié"))
        except PermissionError:
            actions.append((rel, "non mesurable (permission)"))
        except Exception as e:
            actions.append((rel, f"erreur : {e}"))

    lines = [
        f"Armement du worktree : {worktree}",
        f"Fichiers manquants détectés : {len(missing)}",
        f"Plafond appliqué : {plafond} (truncation : {'oui' if truncated else 'non'})",
    ]
    for rel, status in actions:
        lines.append(f"{rel}: {status}")
    human = "\n".join(lines)

    examines = tracked[:200]
    sortie = _creer_sortie_json_base(len(tracked))
    sortie.update({
        "examines": [str(p) for p in examines],
        "examines_tronques": len(tracked) > 200,
        "missing": len(missing) - len(to_copy) if truncated else 0,
        "human": human,
        "actions": actions,
    })
    return sortie

def verifier(worktree: Path, racine: Path) -> Mapping[str, object]:
    tracked = _git_tracked_files(racine)
    if not tracked:
        return _creer_sortie_json_base(0)

    present_both, still_missing, non_mesurable = 0, 0, 0

    for rel in tracked:
        src = racine / rel
        dst = worktree / rel
        try:
            src_ok = src.is_file()
            dst_ok = dst.is_file()
        except PermissionError:
            non_mesurable += 1
            continue
        except Exception:
            non_mesurable += 1
            continue

        if src_ok and dst_ok:
            present_both += 1
        else:
            still_missing += 1

    lines = [
        f"Vérification du worktree : {worktree}",
        f"Fichiers présents des deux côtés : {present_both}",
        f"Fichiers encore manquants : {still_missing}",
    ]
    if non_mesurable:
        lines.append(f"Non mesurables : {non_mesurable}")
    human = "\n".join(lines)

    examines = tracked[:200]
    sortie = _creer_sortie_json_base(len(tracked))
    sortie.update({
        "examines": [str(p) for p in examines],
        "examines_tronques": len(tracked) > 200,
        "missing": still_missing,
        "human": human,
    })
    return sortie

# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #
def _determiner_code_sortie(res: Mapping[str, object]) -> int:
    """Retourne le code de sortie selon la présence de défauts."""
    denom = res.get("denominateur", 0)
    if denom == 0:
        return _gerer_refus_legitime("rien à examiner, refus de conclure.")
    return 0 if res.get("missing", 0) == 0 else 1

# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    # parent parser contenant les options communes
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du dépôt (défaut : répertoire du script).",
    )
    parent_parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON unique.",
    )

    parser = argparse.ArgumentParser(
        description="Outil d’audit d’un dépôt Git en worktree isolé.",
        parents=[parent_parser],
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    # diagnostique
    p_diag = sub.add_parser(
        "diagnostiquer",
        parents=[parent_parser],
        help="Comparer le worktree au dépôt.",
    )
    p_diag.add_argument("worktree", type=Path, help="Chemin du worktree à analyser.")

    # armer
    p_arm = sub.add_parser(
        "armer",
        parents=[parent_parser],
        help="Copier les fichiers manquants dans le worktree.",
    )
    p_arm.add_argument("worktree", type=Path, help="Chemin du worktree cible.")
    p_arm.add_argument(
        "--inclure",
        action="append",
        default=[],
        help="Motif glob à inclure (répétable).",
    )
    p_arm.add_argument(
        "--exclure",
        action="append",
        default=[],
        help="Motif glob à exclure (répétable).",
    )
    p_arm.add_argument(
        "--plafond-mo",
        type=int,
        default=200,
        help="Nombre maximal de fichiers à copier (défaut : 200).",
    )
    p_arm.add_argument(
        "--a-blanc",
        action="store_true",
        help="Mode simulation : n’écrit rien.",
    )

    # verifier
    p_ver = sub.add_parser(
        "verifier",
        parents=[parent_parser],
        help="Vérifier l’état après armement.",
    )
    p_ver.add_argument("worktree", type=Path, help="Chemin du worktree à vérifier.")

    args = parser.parse_args()

    racine = args.racine.resolve()
    worktree = args.worktree.resolve()

    # validations du worktree
    if not worktree.exists():
        sortie = _creer_sortie_json_base(0)
        sortie["erreur"] = f"le worktree « {worktree} » n’existe pas."
        if args.json:
            _afficher_sortie_json(sortie)
        else:
            print(f"Erreur : {sortie['erreur']}", file=sys.stderr)
        return _gerer_refus_legitime("worktree inexistant.")
    if not worktree.is_dir():
        sortie = _creer_sortie_json_base(0)
        sortie["erreur"] = f"le worktree « {worktree} » n’est pas un répertoire."
        if args.json:
            _afficher_sortie_json(sortie)
        else:
            print(f"Erreur : {sortie['erreur']}", file=sys.stderr)
        return _gerer_refus_legitime("worktree non répertoire.")
    if not os.access(worktree, os.R_OK):
        sortie = _creer_sortie_json_base(0)
        sortie["erreur"] = f"le worktree « {worktree} » n’est pas lisible."
        if args.json:
            _afficher_sortie_json(sortie)
        else:
            print(f"Erreur : {sortie['erreur']}", file=sys.stderr)
        return _gerer_refus_legitime("worktree illisible.")

    try:
        if args.commande == "diagnostiquer":
            res = diagnostiquer(worktree, racine)
        elif args.commande == "armer":
            res = armer(
                worktree,
                racine,
                args.inclure,
                args.exclure,
                args.plafond_mo,
                args.a_blanc,
            )
        elif args.commande == "verifier":
            res = verifier(worktree, racine)
        else:
            sortie = _creer_sortie_json_base(0)
            sortie["erreur"] = "commande inconnue."
            if args.json:
                _afficher_sortie_json(sortie)
            else:
                print(f"Erreur : {sortie['erreur']}", file=sys.stderr)
            return _gerer_refus_legitime("commande inconnue.")
    except Exception as e:
        sortie = _creer_sortie_json_base(0)
        sortie["erreur"] = f"erreur inattendue : {str(e)}"
        if args.json:
            _afficher_sortie_json(sortie)
        else:
            print(f"Erreur : {sortie['erreur']}", file=sys.stderr)
        return _gerer_refus_legitime("erreur inattendue.")

    # affichage
    if args.json:
        _afficher_sortie_json(res)
    else:
        print(res.get("human", ""), file=sys.stdout)

    return _determiner_code_sortie(res)

if __name__ == "__main__":
    raise SystemExit(main())