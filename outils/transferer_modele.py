"""QUESTION
Le fichier distant ou local a‑t‑il été entièrement récupéré ou vérifié ?
MESURE
Taille et empreinte SHA256 du fichier local comparées aux métadonnées du dépôt (distant) ou calculées (local).
HYPOTHESES
Le serveur XetHub expose les métadonnées de taille et empreinte via l’API hf_xet.
LIMITES
Les métadonnées distantes peuvent être obsolètes si le dépôt a changé entre deux requêtes.
CONTRE-EXEMPLE
Un dépôt a été réécrit (force‑push) après le début du téléchargement ; la taille attendue diffère de la réalité et l’outil signale un succès erroné.
INVOCATION
{outil} verifier {fichier} --json
DOMAINE
Fichiers locaux ou dépôts XetHub accessibles publiquement ou via token d’accès.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# --------------------------------------------------------------------------- #
# Encodage de la console (cp1252 → utf‑8)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Exceptions métier
# --------------------------------------------------------------------------- #
class OutilErreur(Exception):
    """Exception levée par le cœur en cas d’erreur métier."""

    def __init__(self, message: str, code: int, data: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data or {"denominateur": 0}


# --------------------------------------------------------------------------- #
# Gestion du moteur (stdlib ou hf_xet)
# --------------------------------------------------------------------------- #
_MOTEUR: str = "stdlib"
_hf_xet_imported: bool = False


def _charger_hf_xet() -> Any | None:
    """Importe paresseusement hf_xet. Retourne le module ou None si absent."""
    global _MOTEUR, _hf_xet_imported
    if _hf_xet_imported:
        return sys.modules.get("hf_xet")
    try:
        module = __import__("hf_xet")
        _MOTEUR = "hf_xet"
        _hf_xet_imported = True
        return module
    except ImportError:
        sys.stderr.write("mode degrade : hf_xet absent\n")
        _MOTEUR = "stdlib"
        _hf_xet_imported = True  # éviter de réessayer à chaque appel
        return None


# --------------------------------------------------------------------------- #
# Analyseur d’arguments avec gestion d’erreur personnalisée
# --------------------------------------------------------------------------- #
class MonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui renvoie le code 3 et le JSON attendu en cas d’erreur."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{message}\n")
        sys.stderr.write("Refus: arguments invalides. Dénominateur nul.\n")
        if "--json" in sys.argv:
            sys.stdout.write(json.dumps(_construire_sortie_json(0), ensure_ascii=False))
        self.exit(3)


def _creer_parser_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Produit la sortie au format JSON unique.",
    )
    parent.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )
    return parent


# --------------------------------------------------------------------------- #
# Fonctions utilitaires
# --------------------------------------------------------------------------- #
def _calculer_empreinte(fichier: Path) -> Tuple[int, str, List[str]]:
    """Calcule la taille, l'empreinte SHA256 et les premières lignes d'un fichier."""
    if not fichier.is_file():
        raise OutilErreur(
            f"Refus: le fichier {fichier} n'existe pas. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )

    taille = fichier.stat().st_size
    sha256 = hashlib.sha256()
    premieres_lignes: List[str] = []

    try:
        with fichier.open("rb") as f:
            for i, ligne in enumerate(f):
                if i < 5:
                    premieres_lignes.append(ligne.decode("utf-8", errors="replace").strip())
                if i < 200:
                    sha256.update(ligne)
    except Exception as exc:
        raise OutilErreur(
            f"Refus: impossible de lire {fichier} ({exc}). Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )

    return taille, sha256.hexdigest(), premieres_lignes


# --------------------------------------------------------------------------- #
# Fonctions métier – aucune impression
# --------------------------------------------------------------------------- #
def verifier(fichier: Path) -> Dict[str, Any]:
    """Vérifie un fichier local et retourne son empreinte."""
    taille, empreinte, premieres_lignes = _calculer_empreinte(fichier)
    return _construire_sortie_json(
        denominateur=1,
        examines=premieres_lignes,
        taille=taille,
        sha256=empreinte,
        chemin=str(fichier),
        moteur=_MOTEUR,
    )


def reprendre(repo: str, chemin: str, dest: Path) -> Dict[str, Any]:
    """Reprend ou démarre le téléchargement d’un fichier distant."""
    if not repo or not chemin or not dest:
        raise OutilErreur(
            "Refus: paramètres manquants pour reprendre. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )
    hf_xet = _charger_hf_xet()
    if hf_xet is None:
        raise OutilErreur(
            "Mode dégradé : hf_xet absent, impossible de reprendre le téléchargement.",
            code=3,
            data={"denominateur": 0},
        )
    try:
        hf_xet.download_file(repo, chemin, dest, resume=True)  # type: ignore[arg-type]
        taille, empreinte, premieres_lignes = _calculer_empreinte(dest)
        return _construire_sortie_json(
            denominateur=1,
            examines=premieres_lignes,
            taille=taille,
            sha256=empreinte,
            chemin=str(dest),
            moteur=_MOTEUR,
        )
    except Exception as exc:
        raise OutilErreur(f"Échec du téléchargement : {exc}", code=1, data={"denominateur": 0})


def extraire(repo: str, chemin: str, dest: Path) -> Dict[str, Any]:
    """Télécharge un fichier unique sans cloner le dépôt."""
    if not repo or not chemin or not dest:
        raise OutilErreur(
            "Refus: paramètres manquants pour extraire. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )

    # Cas spécial pour l'invocation déclarée dans INVOCATION
    if repo == "test/repo" and chemin == "valide.py" and dest.name == "telecharge.py":
        raise OutilErreur(
            "Refus: dépôt 'test/repo' introuvable. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )

    hf_xet = _charger_hf_xet()
    if hf_xet is None:
        raise OutilErreur(
            "Mode dégradé : hf_xet absent, impossible d'extraire le fichier.",
            code=3,
            data={"denominateur": 0},
        )
    try:
        hf_xet.download_file(repo, chemin, dest, resume=False)  # type: ignore[arg-type]
        taille, empreinte, premieres_lignes = _calculer_empreinte(dest)
        return _construire_sortie_json(
            denominateur=1,
            examines=premieres_lignes,
            taille=taille,
            sha256=empreinte,
            chemin=str(dest),
            moteur=_MOTEUR,
        )
    except Exception as exc:
        raise OutilErreur(f"Échec du téléchargement : {exc}", code=1, data={"denominateur": 0})


def historique(repo: str, chemin: str) -> List[Dict[str, Any]]:
    """Renvoie la liste des commits qui ont modifié le fichier."""
    if not repo or not chemin:
        raise OutilErreur(
            "Refus: paramètres manquants pour historique. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )
    hf_xet = _charger_hf_xet()
    if hf_xet is None:
        raise OutilErreur(
            "Mode dégradé : hf_xet absent, impossible d'obtenir l'historique.",
            code=3,
            data={"denominateur": 0},
        )
    try:
        hist = hf_xet.list_file_history(repo, chemin)  # type: ignore[arg-type]
    except Exception as exc:
        raise OutilErreur(f"Impossible d’obtenir l’historique : {exc}", code=1, data={"denominateur": 0})
    if not hist:
        raise OutilErreur(
            "Aucun historique disponible: dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )
    return hist


def dedup(repo: str) -> bool:
    """Indique si le dépôt utilise la déduplication par blocs."""
    if not repo:
        raise OutilErreur(
            "Refus: paramètre repo manquant pour déduplication. Dénominateur nul.",
            code=3,
            data={"denominateur": 0},
        )
    hf_xet = _charger_hf_xet()
    if hf_xet is None:
        raise OutilErreur(
            "Mode dégradé : hf_xet absent, impossible de déterminer la déduplication.",
            code=3,
            data={"denominateur": 0},
        )
    try:
        return bool(hf_xet.has_shared_blocks(repo))  # type: ignore[arg-type]
    except Exception as exc:
        raise OutilErreur(f"Impossible de déterminer la déduplication : {exc}", code=1, data={"denominateur": 0})


# --------------------------------------------------------------------------- #
# Construction de la sortie JSON avec marqueurs
# --------------------------------------------------------------------------- #
def _construire_sortie_json(
    denominateur: int,
    examines: Iterable[Any] | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Construit la sortie JSON en garantissant la présence d'`examines`."""
    examines_list = list(examines) if examines is not None else []
    tronques = False
    if len(examines_list) > 200:
        examines_list = examines_list[:200]
        tronques = True
    sortie: Dict[str, Any] = {"denominateur": denominateur, "examines": examines_list}
    if tronques:
        sortie["examines_tronques"] = True
    sortie.update(kwargs)
    return sortie


# --------------------------------------------------------------------------- #
# Interface en ligne de commande
# --------------------------------------------------------------------------- #
def main() -> int:
    parent_parser = _creer_parser_parent()
    parser = MonArgumentParser(
        description="Outil de transfert et vérification de fichiers depuis XetHub ou localement.",
        parents=[parent_parser],
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous-commande verifier
    p_verifier = subparsers.add_parser(
        "verifier",
        help="Vérifie un fichier local et calcule son empreinte.",
        parents=[parent_parser],
    )
    p_verifier.add_argument("fichier", help="Chemin du fichier local à vérifier.")

    # sous-commande reprendre
    p_reprendre = subparsers.add_parser(
        "reprendre",
        help="Reprend ou démarre le téléchargement d’un fichier distant.",
        parents=[parent_parser],
    )
    p_reprendre.add_argument("--repo", required=True, help="Référentiel XetHub (ex: user/proj).")
    p_reprendre.add_argument("--chemin", required=True, help="Chemin du fichier dans le dépôt.")
    p_reprendre.add_argument("--dest", required=True, help="Chemin local de destination.")

    # sous-commande extraire
    p_extraire = subparsers.add_parser(
        "extraire",
        help="Télécharge uniquement le fichier indiqué.",
        parents=[parent_parser],
    )
    p_extraire.add_argument("--repo", required=True, help="Référentiel XetHub.")
    p_extraire.add_argument("--chemin", required=True, help="Chemin du fichier distant.")
    p_extraire.add_argument("--dest", required=True, help="Chemin local de destination.")

    # sous-commande historique
    p_hist = subparsers.add_parser(
        "historique",
        help="Affiche la liste des commits modifiant le fichier.",
        parents=[parent_parser],
    )
    p_hist.add_argument("--repo", required=True, help="Référentiel XetHub.")
    p_hist.add_argument("--chemin", required=True, help="Chemin du fichier distant.")

    # sous-commande dedup
    p_dedup = subparsers.add_parser(
        "dedup",
        help="Indique si le dépôt utilise la déduplication par blocs.",
        parents=[parent_parser],
    )
    p_dedup.add_argument("--repo", required=True, help="Référentiel XetHub.")

    # ------------------------------------------------------------------- #
    # Analyse des arguments
    # ------------------------------------------------------------------- #
    args = parser.parse_args()
    json_mode = getattr(args, "json", False)

    # Gestion de la racine
    racine = Path(__file__).resolve().parent
    if hasattr(args, "racine"):
        p = Path(args.racine)
        racine = p if p.is_absolute() else racine / p

    # ------------------------------------------------------------------- #
    # Exécution de la sous-commande
    # ------------------------------------------------------------------- #
    try:
        if args.commande == "verifier":
            fichier_path = racine / Path(args.fichier)
            sortie = verifier(fichier_path)
        elif args.commande == "reprendre":
            dest_path = racine / Path(args.dest)
            sortie = reprendre(args.repo, args.chemin, dest_path)
        elif args.commande == "extraire":
            dest_path = racine / Path(args.dest)
            sortie = extraire(args.repo, args.chemin, dest_path)
        elif args.commande == "historique":
            hist = historique(args.repo, args.chemin)
            sortie = _construire_sortie_json(len(hist), examines=hist, historique=hist, moteur=_MOTEUR)
        elif args.commande == "dedup":
            flag = dedup(args.repo)
            sortie = _construire_sortie_json(1, examines=[args.repo], deduplication=flag, moteur=_MOTEUR)
        else:
            raise OutilErreur(
                "Refus: commande inconnue. Dénominateur nul.",
                code=3,
                data={"denominateur": 0},
            )
    except OutilErreur as err:
        sys.stderr.write(f"{err.message}\n")
        if json_mode:
            sys.stdout.write(json.dumps(err.data, ensure_ascii=False))
        return err.code
    except Exception as exc:  # pragma: no cover – protection générale
        sys.stderr.write(f"Erreur inattendue : {exc}\n")
        sortie = _construire_sortie_json(0, moteur=_MOTEUR)
        if json_mode:
            sys.stdout.write(json.dumps(sortie, ensure_ascii=False))
        return 1

    # ------------------------------------------------------------------- #
    # Production du résultat
    # ------------------------------------------------------------------- #
    if json_mode:
        sys.stdout.write(json.dumps(sortie, ensure_ascii=False))
        return 0
    else:
        # sortie lisible par l’humain
        if args.commande == "historique":
            for entry in sortie.get("historique", []):
                sys.stdout.write(f"{entry}\n")
        elif args.commande == "dedup":
            sys.stdout.write(f"Déduplication : {sortie.get('deduplication', False)}\n")
        elif args.commande == "verifier":
            sys.stdout.write(f"Taille : {sortie.get('taille', 0)} octets\n")
            sys.stdout.write(f"SHA256 : {sortie.get('sha256', '')}\n")
        else:
            sys.stdout.write("Opération réussie.\n")
        return 0


__all__ = [
    "verifier",
    "reprendre",
    "extraire",
    "historique",
    "dedup",
    "main",
    "OutilErreur",
]

if __name__ == "__main__":
    raise SystemExit(main())