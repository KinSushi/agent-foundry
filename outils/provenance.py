"""
QUESTION      de quoi ce résultat dépend-il ?
MESURE        chaîne module → fichier → empreinte → binaires → chargement
HYPOTHÈSES    les métadonnées d'installation sont exactes et complètes
LIMITES       ne voit pas les imports dynamiques, ni les dépendances système hors Python (DLL de l'OS), ni les modules compilés dans l'interpréteur (origin = None, 71 modules)
CONTRE-EXEMPLES numpy/random/_generator.pyd charge isolément mais son module n'importe pas — la chaîne de dépendances diffère du fichier
DOMAINE       un interpréteur, à un instant donné
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import pathlib
import sys
from typing import Any, NamedTuple, Optional

# Encodage UTF‑8 pour les consoles Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Racine portable
RACINE = pathlib.Path(__file__).resolve().parent


class Binaire(NamedTuple):
    chemin: pathlib.Path
    charge_isolee: bool
    erreur: Optional[str] = None
    winerror: Optional[int] = None
    cause: Optional[str] = None


class Module(NamedTuple):
    nom: str
    origine: Optional[pathlib.Path]
    empreinte: Optional[str]
    distribution: Optional[str]
    version: Optional[str]
    binaires: list[Binaire]
    importe_chaine: bool
    erreur_import: Optional[str] = None
    winerror_import: Optional[int] = None
    cause_import: Optional[str] = None


class Provenance(NamedTuple):
    module: Module
    dependances: list[str]
    contrat: dict[str, str]


def empreinte_fichier(chemin: pathlib.Path) -> str:
    """Calcule l'empreinte SHA256 d'un fichier."""
    with chemin.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def charger_binaire(chemin: pathlib.Path) -> tuple[bool, Optional[str], Optional[int], Optional[str]]:
    """Tente de charger un binaire avec ctypes."""
    try:
        import ctypes

        if sys.platform == "win32":
            ctypes.WinDLL(str(chemin))
        else:
            ctypes.CDLL(str(chemin))
        return True, None, None, None
    except OSError as e:
        winerror = getattr(e, "winerror", None)
        cause = "politique_systeme" if winerror is not None else "autre"
        return False, str(e), winerror, cause


def _liste_binaires_dans_répertoire(répertoire: pathlib.Path) -> list[pathlib.Path]:
    """Retourne les fichiers binaires (.pyd/.so/.dll) présents dans le répertoire."""
    return [
        p
        for p in répertoire.iterdir()
        if p.suffix.lower() in (".pyd", ".so", ".dll") and p.is_file()
    ]


def trouver_binaires(module: str, origine: Optional[pathlib.Path]) -> list[Binaire]:
    """Trouve les binaires associés à un module."""
    if origine is None:
        return []

    try:
        dist = importlib.metadata.distribution(module.split(".")[0])
    except importlib.metadata.PackageNotFoundError:
        dist = None

    binaires: list[Binaire] = []

    if dist is not None:
        prefixe = origine.parent
        for fichier in dist.files or []:
            chemin = dist.locate_file(fichier)
            if chemin.suffix.lower() not in (".pyd", ".so", ".dll"):
                continue
            if not str(chemin).startswith(str(prefixe)):
                continue
            charge, erreur, winerror, cause = charger_binaire(chemin)
            binaires.append(Binaire(chemin, charge, erreur, winerror, cause))
    else:
        répertoires = [origine.parent]
        if sys.platform == "win32":
            dlls = pathlib.Path(sys.prefix) / "DLLs"
            if dlls.is_dir():
                répertoires.append(dlls)

        for répertoire in répertoires:
            for chemin in _liste_binaires_dans_répertoire(répertoire):
                charge, erreur, winerror, cause = charger_binaire(chemin)
                binaires.append(
                    Binaire(
                        chemin,
                        charge_isolee=charge,
                        erreur=erreur,
                        winerror=winerror,
                        cause=cause,
                    )
                )
    return binaires


def importer_module(nom: str) -> tuple[bool, Optional[str], Optional[int], Optional[str]]:
    """Tente d'importer un module."""
    try:
        importlib.import_module(nom)
        return True, None, None, None
    except ImportError as e:
        cause = e.__cause__
        if isinstance(cause, OSError):
            winerror = getattr(cause, "winerror", None)
            if winerror is not None:
                return (
                    False,
                    f"ImportError: DLL load failed (winerror {winerror})",
                    winerror,
                    "politique_systeme",
                )
        return False, str(e), None, "autre"


def tracer_module(nom: str) -> Module:
    """Trace la provenance complète d'un module."""
    spec = importlib.util.find_spec(nom)
    origine = pathlib.Path(spec.origin) if spec and spec.origin else None
    empreinte = empreinte_fichier(origine) if origine else None

    try:
        dist = importlib.metadata.distribution(nom.split(".")[0])
        distribution = dist.name
        version = dist.version
    except (importlib.metadata.PackageNotFoundError, IndexError):
        distribution = None
        version = None

    binaires = trouver_binaires(nom, origine)
    importe, erreur_import, winerror_import, cause_import = importer_module(nom)

    return Module(
        nom=nom,
        origine=origine,
        empreinte=empreinte,
        distribution=distribution,
        version=version,
        binaires=binaires,
        importe_chaine=importe,
        erreur_import=erreur_import,
        winerror_import=winerror_import,
        cause_import=cause_import,
    )


def dependances_declarees(nom: str) -> list[str]:
    """Récupère les dépendances déclarées d'un module."""
    try:
        dist = importlib.metadata.distribution(nom.split(".")[0])
        return [
            req
            for req in importlib.metadata.requires(dist.name) or []
            if "extra ==" not in req
        ]
    except (importlib.metadata.PackageNotFoundError, IndexError):
        return []


def provenance_complete(nom: str) -> Provenance:
    """Rend la provenance complète d'un module."""
    module = tracer_module(nom)
    dependances = dependances_declarees(nom)
    contrat = {
        "QUESTION": "de quoi ce résultat dépend-il ?",
        "MESURE": "chaîne module → fichier → empreinte → binaires → chargement",
        "HYPOTHÈSES": "les métadonnées d'installation sont exactes et complètes",
        "LIMITES": "ne voit pas les imports dynamiques, ni les dépendances système hors Python (DLL de l'OS), ni les modules compilés dans l'interpréteur (origin = None, 71 modules)",
        "CONTRE-EXEMPLES": "numpy/random/_generator.pyd charge isolément mais son module n'importe pas — la chaîne de dépendances diffère du fichier",
        "DOMAINE": "un interpréteur, à un instant donné",
    }
    return Provenance(module, dependances, contrat)


def expliquer_echec(provenance: Provenance, _visites: Optional[set[str]] = None) -> str:
    """Explique le premier maillon rompu dans la chaîne de dépendances."""
    if _visites is None:
        _visites = set()
    module = provenance.module
    if module.nom in _visites:
        return f"Cycle détecté sur {module.nom}"
    _visites.add(module.nom)

    if module.origine is None:
        return f"Module {module.nom} est compilé dans l'interpréteur (origin = None) — chaîne arrêtée"
    if not module.importe_chaine:
        cause = f"Module {module.nom} ne s'importe pas : {module.erreur_import}"
        if module.cause_import:
            cause += f" (cause : {module.cause_import})"
        if module.winerror_import is not None:
            cause += f" (winerror {module.winerror_import})"
        return cause
    for binaire in module.binaires:
        if not binaire.charge_isolee:
            cause = f"Binaire {binaire.chemin.name} ne charge pas : {binaire.erreur}"
            if binaire.cause:
                cause += f" (cause : {binaire.cause})"
            if binaire.winerror:
                cause += f" (winerror {binaire.winerror})"
            return cause

    for dep in provenance.dependances:
        nom_dep = dep.split()[0].split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0].strip()
        if not nom_dep:
            continue
        try:
            dep_prov = provenance_complete(nom_dep)
            explication = expliquer_echec(dep_prov, _visites)
            if explication != "Aucun échec détecté":
                return explication
        except Exception:
            continue

    return "Aucun échec détecté"


def afficher_humain(provenance: Provenance) -> None:
    """Affiche la provenance de manière lisible (sur stderr)."""
    module = provenance.module
    print(f"module        {module.nom}", file=sys.stderr)
    if module.origine:
        print(f"  origine     {module.origine}", file=sys.stderr)
        print(f"  empreinte   {module.empreinte[:8]}…  (sha256)", file=sys.stderr)
    else:
        print("  origine     (compilé dans l'interpréteur) — chaîne arrêtée", file=sys.stderr)
    if module.distribution:
        print(f"  distribution {module.distribution} {module.version}", file=sys.stderr)
    print(
        f"  dépendances {module.distribution or module.nom} → {provenance.dependances or '(aucune atteinte)'}",
        file=sys.stderr,
    )
    print(f"  binaires    {len(module.binaires)}", file=sys.stderr)
    for binaire in module.binaires:
        charge = "OUI" if binaire.charge_isolee else "NON"
        print(f"    {binaire.chemin.name}", file=sys.stderr)
        print(f"      charge (ctypes)   {charge}", file=sys.stderr)
        if not binaire.charge_isolee:
            print(f"      erreur            {binaire.erreur}", file=sys.stderr)
            if binaire.cause:
                print(f"      cause            {binaire.cause}", file=sys.stderr)
            if binaire.winerror:
                print(f"      winerror         {binaire.winerror}", file=sys.stderr)
    print(f"  import réel       {'SUCCÈS' if module.importe_chaine else 'ÉCHEC'}", file=sys.stderr)
    if not module.importe_chaine:
        print(f"    erreur            {module.erreur_import}", file=sys.stderr)
        if module.cause_import:
            print(f"    cause             {module.cause_import}", file=sys.stderr)
        if module.winerror_import is not None:
            print(f"    winerror          {module.winerror_import}", file=sys.stderr)

    verdict = f"  ⇒ VERDICT   capacité {'DISPONIBLE' if module.importe_chaine else 'INDISPONIBLE'}"
    if not module.importe_chaine:
        verdict += f", cause : {expliquer_echec(provenance)}"
    print(verdict, file=sys.stderr)


def calcul_denominateur(provenance: Provenance) -> int:
    """Nombre d'éléments réellement examinés (module + dépendances)."""
    return 1 + len(provenance.dependances)


def vers_json(provenance: Provenance) -> dict[str, Any]:
    """Convertit la provenance en JSON, incluant le dénominateur."""
    module = provenance.module
    return {
        "denominateur": calcul_denominateur(provenance),
        "module": {
            "nom": module.nom,
            "origine": str(module.origine) if module.origine else None,
            "empreinte": module.empreinte,
            "distribution": module.distribution,
            "version": module.version,
            "binaires": [
                {
                    "chemin": str(b.chemin),
                    "charge_isolee": b.charge_isolee,
                    "erreur": b.erreur,
                    "winerror": b.winerror,
                    "cause": b.cause,
                }
                for b in module.binaires
            ],
            "importe_chaine": module.importe_chaine,
            "erreur_import": module.erreur_import,
            "winerror_import": module.winerror_import,
            "cause_import": module.cause_import,
        },
        "dependances": provenance.dependances,
        "contrat": provenance.contrat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace la provenance complète d'un module Python.",
        epilog="Exemple : provenance.py tracer numpy.fft",
    )
    parser.add_argument(
        "--racine",
        type=pathlib.Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil)",
    )
    sous_commandes = parser.add_subparsers(dest="commande", required=True)

    tracer = sous_commandes.add_parser("tracer", help="Trace la provenance complète")
    tracer.add_argument("nom", help="Nom du module à tracer")

    expliquer = sous_commandes.add_parser(
        "expliquer", help="Explique pourquoi un module ne fonctionne pas"
    )
    expliquer.add_argument("nom", help="Nom du module à expliquer")

    sceau = sous_commandes.add_parser("sceau", help="Fige la provenance en JSON")
    sceau.add_argument("nom", help="Nom du module à sceller")
    sceau.add_argument("--json", action="store_true", help="Sortie JSON")

    args = parser.parse_args()

    try:
        provenance = provenance_complete(args.nom)
    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 2

    # Auto‑audit : vérifier que l'outil dépend de _hashlib et _ctypes
    if args.nom in ("provenance", "__main__"):
        noms_binaires = {b.chemin.name for b in provenance.module.binaires}
        attendus = {"_hashlib.pyd", "_ctypes.pyd"}
        manquants = attendus - noms_binaires
        if manquants:
            print(
                f"Auto-audit : binaires attendus manquants : {', '.join(sorted(manquants))}",
                file=sys.stderr,
            )
        else:
            print(
                f"Auto-audit : binaires attendus présents : {', '.join(sorted(attendus))}",
                file=sys.stderr,
            )

    if args.commande == "tracer":
        afficher_humain(provenance)
        return 0 if provenance.module.importe_chaine else 1

    if args.commande == "expliquer":
        print(expliquer_echec(provenance), file=sys.stderr)
        return 0 if provenance.module.importe_chaine else 1

    if args.commande == "sceau":
        denom = calcul_denominateur(provenance)
        if denom == 0:
            print("Erreur : dénominateur nul, aucun élément examiné.", file=sys.stderr)
            return 3
        if args.json:
            json.dump(vers_json(provenance), sys.stdout, indent=2, ensure_ascii=False)
        else:
            afficher_humain(provenance)
        return 0 if provenance.module.importe_chaine else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())