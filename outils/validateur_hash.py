"""\
QUESTION      Ce fichier correspond-il à ce hash ?
MESURE        Calcul du digest du fichier avec l'algorithme indiqué et comparaison avec le digest attendu.
HYPOTHESES   L'algorithme demandé est disponible dans hashlib et le fichier est lisible en mode binaire.
LIMITES       Ne supporte que les algorithmes fournis par hashlib ; aucun hash moderne non présent n'est calculé.
CONTRE-EXEMPLES  Un fichier illisible ou un algorithme absent entraîne un refus de conclure.
DOMAINE       Validation d'intégrité de fichiers simples dans un environnement standard Python.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# Réglage de l'encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__: List[str] = ["valider_hash", "main"]

RACINE = Path(__file__).resolve().parent

def _charger_hashlib() -> Tuple[bool, str]:
    """Essaye d'importer hashlib. Retourne (disponible, message d'erreur)."""
    try:
        import hashlib  # noqa: F401
        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, str(exc)

def _calculer_digest(fichier: Path, algo: str) -> str:
    """Calcule le digest hexadécimal du fichier avec l'algorithme demandé."""
    import hashlib

    h = hashlib.new(algo)
    with fichier.open("rb") as f:
        while True:
            bloc = f.read(8192)
            if not bloc:
                break
            h.update(bloc)
    return h.hexdigest()

def valider_hash(
    chemin: Path, algo: str, attendu: str
) -> Tuple[bool, str, str]:
    """
    Vérifie si le fichier *chemin* correspond au digest *attendu* avec *algo*.

    Retourne (match, digest_calculé, message_diagnostic).
    Le message d'erreur est vide en cas de succès.
    """
    if not chemin.is_file():
        return False, "", f"Le chemin '{chemin}' n'est pas un fichier lisible."
    try:
        digest = _calculer_digest(chemin, algo)
    except ValueError as ve:
        return False, "", f"Algorithme inconnu ou indisponible : {algo}"
    except OSError as oe:
        return False, "", f"Erreur d'accès au fichier '{chemin}' : {oe}"
    match = digest.lower() == attendu.lower()
    return match, digest, ""

def _construire_contrat() -> dict:
    """Construit la partie 'contrat' du JSON à partir du docstring."""
    lignes = __doc__.splitlines()
    sections = {}
    for ligne in lignes:
        if ligne.strip():
            # Séparer l'étiquette de la description sur le premier blanc
            parties = ligne.split(None, 1)
            if len(parties) == 2:
                cle, valeur = parties
                sections[cle.strip()] = valeur.strip()
    return sections

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie qu'un fichier correspond à un hash donné.",
        epilog=(
            "Exemple d'appel réel :\n"
            "  python validateur_hash.py --algo sha256 "
            "--hash 031edd7d41651593c5fe5c006fa5752b37fddff7bc4e843aa6af0c950f4b9406 "
            "chemin/vers/fichier.txt"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("cible", type=Path, help="Chemin du fichier à vérifier.")
    parser.add_argument(
        "--algo",
        default="sha256",
        help="Algorithme de hash (nom reconnu par hashlib).",
    )
    parser.add_argument(
        "--hash",
        dest="attendu",
        required=True,
        help="Digest hexadécimal attendu.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à préfixer à sys.path (défaut: répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique sur stdout.",
    )

    args = parser.parse_args()

    # Gestion du paramètre --racine
    racine = args.racine.resolve() if args.racine else RACINE
    if racine.is_dir():
        sys.path.insert(0, str(racine))
    else:
        print(f"Le répertoire racine indiqué n'existe pas : {racine}", file=sys.stderr)
        return 3

    disponible, err = _charger_hashlib()
    if not disponible:
        print(f"hashlib indisponible : {err}", file=sys.stderr)
        return 3

    match, digest, diagnostic = valider_hash(args.cible, args.algo, args.attendu)

    denominateur = 1 if diagnostic == "" else 0
    examines: List[dict] = []
    if denominateur:
        examines.append(
            {
                "nom": str(args.cible),
                "algo": args.algo,
                "digest_calcule": digest,
                "digest_attendu": args.attendu,
                "correspondance": match,
            }
        )

    if args.json:
        sortie = {
            "contrat": _construire_contrat(),
            "denominateur": denominateur,
            "examines": examines[:200],
            "examines_tronques": len(examines) > 200,
        }
        json.dump(sortie, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        if denominateur == 0:
            # Refus de conclure : message sur stderr
            msg = diagnostic if diagnostic else "Denominateur nul : aucun élément examiné"
            print(msg, file=sys.stderr)
            return 3
        return 0 if match else 1
    else:
        if diagnostic:
            print(diagnostic, file=sys.stderr)
        else:
            print(
                f"Correspondance : {'OUI' if match else 'NON'}\n"
                f"Digest calculé : {digest}",
                file=sys.stdout,
            )
        if denominateur == 0:
            return 3
        return 0 if match else 1

if __name__ == "__main__":
    raise SystemExit(main())