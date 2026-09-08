"""QUESTION       qu'est-ce qui a changé entre ces deux arbres ?
MESURE         dircmp pour la structure, sha256 pour le contenu, toujours
HYPOTHÈSES     les deux arbres sont lisibles ; le hachage suffit à établir
               l'identité (collision sha256 : non observée)
LIMITES        ne suit pas les renommages (un fichier renommé paraît
               supprimé + ajouté) ; ne compare pas les permissions ni les
               liens symboliques ; le hachage coûte une lecture complète
CONTRE-EXEMPLE dircmp rend « identiques » deux fichiers de même taille et même
               mtime au contenu DIFFÉRENT — mesuré, c'est la raison de l'outil
INVOCATION
    {outil} {dossier}/a {dossier}/b --json
DOMAINE        deux arborescences de fichiers, sur cette machine
"""
from __future__ import annotations

import argparse
import difflib
import filecmp
import hashlib
import json
import os
import sys
import unicodedata
from pathlib import Path

# Encodage UTF‑8 pour la console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent


def parcourir_arbre(racine: Path) -> tuple[dict[str, Path], list[str], list[str]]:
    """Parcourt un arbre et renvoie les chemins normalisés en NFC, les erreurs et les collisions."""
    chemins: dict[str, Path] = {}
    erreurs: list[str] = []
    collisions: list[str] = []

    def gestion_erreur(e: OSError) -> None:
        erreurs.append(str(e.filename))

    for racine_courante, dossiers, fichiers in os.walk(racine, onerror=gestion_erreur):
        dossiers.sort()
        fichiers.sort()
        for fic in fichiers:
            chemin_abs = Path(racine_courante) / fic
            chemin_rel = chemin_abs.relative_to(racine).as_posix()
            chemin_nfc = unicodedata.normalize("NFC", chemin_rel)
            if chemin_nfc in chemins and chemins[chemin_nfc] != chemin_abs:
                collisions.append(chemin_nfc)
            chemins[chemin_nfc] = chemin_abs

    return chemins, erreurs, collisions


def comparer_structure_dircmp(arbre_a: Path, arbre_b: Path) -> tuple[set[str], set[str], set[str], list[str]]:
    """Utilise filecmp.dircmp pour obtenir les différences de structure."""
    funny: list[str] = []
    absents_b: set[str] = set()
    absents_a: set[str] = set()
    communs: set[str] = set()

    def _parcourir_dircmp(dcmp: filecmp.dircmp, prefix: str = "") -> None:
        for nom in dcmp.left_only:
            rel = os.path.join(prefix, nom)
            absents_b.add(unicodedata.normalize("NFC", rel))
        for nom in dcmp.right_only:
            rel = os.path.join(prefix, nom)
            absents_a.add(unicodedata.normalize("NFC", rel))
        for nom in dcmp.common_files:
            rel = os.path.join(prefix, nom)
            communs.add(unicodedata.normalize("NFC", rel))
        for nom in dcmp.funny_files:
            rel = os.path.join(prefix, nom)
            funny.append(unicodedata.normalize("NFC", rel))
        for sous, sous_dcmp in dcmp.subdirs.items():
            _parcourir_dircmp(sous_dcmp, os.path.join(prefix, sous))

    dcmp = filecmp.dircmp(arbre_a, arbre_b)
    _parcourir_dircmp(dcmp, "")
    return absents_b, absents_a, communs, funny


def calculer_hash(chemin: Path) -> str:
    """Calcule le hash sha256 d'un fichier."""
    with open(chemin, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def generer_diff(pa: Path, pb: Path) -> str:
    """Génère un diff unifié pour les fichiers texte, ou un résumé pour les binaires."""
    try:
        texte_a = pa.read_text(encoding="utf-8").splitlines(keepends=True)
        texte_b = pb.read_text(encoding="utf-8").splitlines(keepends=True)
        return "".join(difflib.unified_diff(texte_a, texte_b, fromfile=str(pa), tofile=str(pb)))
    except UnicodeDecodeError:
        ha = calculer_hash(pa)
        hb = calculer_hash(pb)
        ta = pa.stat().st_size
        tb = pb.stat().st_size
        return f"Binaire: taille {ta} -> {tb}, hash {ha} -> {hb}"


def comparer(
    arbre_a: Path,
    arbre_b: Path,
    rapide: bool = False,
    contenu: bool = False,
) -> dict:
    """Compare deux arbres et renvoie un dictionnaire de résultat enrichi."""
    filecmp.clear_cache()
    absents_b, absents_a, communs, funny = comparer_structure_dircmp(arbre_a, arbre_b)
    chemins_a, erreurs_a, coll_a = parcourir_arbre(arbre_a)
    chemins_b, erreurs_b, coll_b = parcourir_arbre(arbre_b)

    erreurs = erreurs_a + erreurs_b
    identiques: list[str] = []
    modifies: list[str] = []
    illisibles: list[str] = list(funny)
    diffs: dict[str, str] = {}

    funny_set = set(funny)
    communs -= funny_set

    for cle in sorted(communs):
        pa = chemins_a[cle]
        pb = chemins_b[cle]
        try:
            if rapide:
                ta = pa.stat()
                tb = pb.stat()
                if ta.st_size == tb.st_size and int(ta.st_mtime) == int(tb.st_mtime):
                    identiques.append(cle)
                else:
                    modifies.append(cle)
                    if contenu:
                        diffs[cle] = generer_diff(pa, pb)
            else:
                ha = calculer_hash(pa)
                hb = calculer_hash(pb)
                if ha == hb:
                    identiques.append(cle)
                else:
                    modifies.append(cle)
                    if contenu:
                        diffs[cle] = generer_diff(pa, pb)
        except OSError:
            illisibles.append(cle)

    denominateur = len(communs) + len(absents_b) + len(absents_a) + len(funny_set)

    # « examines » = tous les chemins effectivement parcourus
    examines = sorted(list(communs) + list(absents_b) + list(absents_a) + list(funny_set))

    resultat = {
        "denominateur": denominateur,
        "examines": examines,
        "identiques": sorted(identiques),
        "modifies": sorted(modifies),
        "absents_b": sorted(absents_b),
        "absents_a": sorted(absents_a),
        "illisibles": sorted(illisibles),
        "non_lus": len(erreurs),
        "normalisation": sorted(set(coll_a + coll_b)),
        "diffs": diffs,
        "contrat": {
            "QUESTION": "qu'est-ce qui a changé entre ces deux arbres ?",
            "MESURE": "dircmp pour la structure, sha256 pour le contenu, toujours",
            "HYPOTHÈSES": "les deux arbres sont lisibles ; le hachage suffit à établir l'identité (collision sha256 : non observée)",
            "LIMITES": "ne suit pas les renommages (un fichier renommé paraît supprimé + ajouté) ; ne compare pas les permissions ni les liens symboliques ; le hachage coûte une lecture complète",
            "CONTRE-EXEMPLE": "dircmp rend « identiques » deux fichiers de même taille et même mtime au contenu DIFFÉRENT — mesuré, c'est la raison de l'outil",
            "DOMAINE": "deux arborescences de fichiers, sur cette machine",
        },
    }

    return resultat


def _json_erreur(message: str) -> dict:
    """Structure d'erreur JSON conforme au contrat."""
    return {
        "erreur": message,
        "denominateur": 0,
        "examines": [],
        "contrat": {
            "QUESTION": "qu'est-ce qui a changé entre ces deux arbres ?",
            "MESURE": "dircmp pour la structure, sha256 pour le contenu, toujours",
            "HYPOTHÈSES": "les deux arbres sont lisibles ; le hachage suffit à établir l'identité (collision sha256 : non observée)",
            "LIMITES": "ne suit pas les renommages ; ne compare pas les permissions ni les liens symboliques",
            "CONTRE-EXEMPLE": "dircmp rend « identiques » deux fichiers de même taille et même mtime au contenu DIFFÉRENT",
            "DOMAINE": "deux arborescences de fichiers, sur cette machine",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare deux arbres de fichiers en profondeur.",
        epilog="Exemple: python outils/comparer_arbres.py v1 v2 --json",
    )
    parser.add_argument("arbre_a", type=Path, help="Premier arbre à comparer")
    parser.add_argument("arbre_b", type=Path, help="Deuxième arbre à comparer")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    parser.add_argument("--rapide", action="store_true", help="Comparaison par taille et date (peut manquer des modifications)")
    parser.add_argument("--contenu", action="store_true", help="Affiche le contenu des changements (diff)")
    parser.add_argument("--partiel", action="store_true", help="Accepte un résultat partiel si des erreurs de lecture surviennent")
    parser.add_argument("--racine", type=Path, default=RACINE, help="Surcharge la racine du projet")

    try:
        args = parser.parse_args()
    except SystemExit:
        # Argument manquant ou invalide → refus légitime
        if "--json" in sys.argv:
            print(json.dumps(_json_erreur("Arguments manquants ou invalides"), ensure_ascii=False, indent=2))
            sys.exit(3)
        raise

    if not args.arbre_a.exists() or not args.arbre_b.exists():
        err = _json_erreur("Chemin inexistant")
        if args.json:
            print(json.dumps(err, ensure_ascii=False, indent=2))
        print("ERREUR: Chemin inexistant", file=sys.stderr)
        # Refus légitime
        print("denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    if args.rapide:
        print(
            "MODE RAPIDE : comparaison par taille et date. Un fichier modifié à taille égale ne sera PAS détecté. Pour un verdict fiable : sans --rapide.",
            file=sys.stderr,
        )

    resultat = comparer(args.arbre_a, args.arbre_b, rapide=args.rapide, contenu=args.contenu)

    if resultat["non_lus"] > 0 and not args.partiel:
        print(
            f"ERREUR: {resultat['non_lus']} chemins non lus. Résultat partiel refusé. Utilisez --partiel pour forcer.",
            file=sys.stderr,
        )
        if args.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 2

    if resultat["denominateur"] == 0:
        # Refus légitime avec message conforme
        print("denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 3

    if args.json:
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 0

    # Sortie lisible
    print(f"IDENTIQUE      {len(resultat['identiques'])}")
    print(f"MODIFIÉ        {len(resultat['modifies'])}   {'  '.join(resultat['modifies'])}")
    print(f"ABSENT DE B    {len(resultat['absents_b'])}   {'  '.join(resultat['absents_b'])}")
    print(f"ABSENT DE A    {len(resultat['absents_a'])}   {'  '.join(resultat['absents_a'])}")
    print(f"ILLISIBLE      {len(resultat['illisibles'])}   {'  '.join(resultat['illisibles'])}")
    print(f"Dénominateur   {resultat['denominateur']}")

    print(f"NON LUS        {resultat['non_lus']}", file=sys.stderr)
    if resultat["normalisation"]:
        print(f"Normalisation  {len(resultat['normalisation'])} entrées coïncidées après NFC", file=sys.stderr)

    if args.contenu:
        for cle, diff in resultat["diffs"].items():
            print(f"\n--- {cle} ---")
            print(diff)

    if resultat["modifies"] or resultat["absents_b"] or resultat["absents_a"] or resultat["illisibles"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())