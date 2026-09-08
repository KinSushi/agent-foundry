"""livrer_autonome.py
Outil d'empaquetage autonome d'un script Python en zipapp.

CONTRAT DE MESURE
QUESTION       cet outil peut-il voyager en un seul fichier ?
MESURE         imports par ast + binaires par distribution.files + exécution réelle
HYPOTHÈSES     la machine cible a un interpréteur ≥ 3.14 et sa stdlib complète
LIMITES        ne voit pas les imports dynamiques ; ne teste pas la machine cible
CONTRE-EXEMPLE une archive peut se construire et ne pas s'exécuter — d'où
               la vérification d'exécution obligatoire
DOMAINE       cet interpréteur, pour une cible de même version majeure
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import subprocess
import tempfile
import zipfile
import zipapp
import zipimport
import importlib.util
import importlib.metadata
from pathlib import Path
from typing import Iterable, Set, Dict, List, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# --------------------------------------------------------------------------- #
#                         COEUR DE L'OUTIL (sans argparse)                  #
# --------------------------------------------------------------------------- #

def _lire_source(fichier: Path) -> str:
    """Lit le fichier source en UTF‑8."""
    return fichier.read_text(encoding="utf-8")


def analyser_imports(fichier: Path) -> Set[str]:
    """
    Retourne l'ensemble des noms de modules importés (premier composant)
    dans le fichier Python indiqué.
    Vérifie la validité du code avec ``compile`` avant l'analyse AST.
    """
    source = _lire_source(fichier)
    try:
        compile(source, str(fichier), "exec")
    except SyntaxError as exc:
        raise ValueError(f"Syntaxe invalide dans {fichier}: {exc}") from exc

    arbre = ast.parse(source, filename=str(fichier))
    imports: Set[str] = set()
    for node in ast.walk(arbre):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".", 1)[0])
    return imports


def _est_stdlib(module: str) -> bool:
    """Détermine grossièrement si *module* appartient à la stdlib."""
    # Les modules intégrés sont toujours trouvables sans distribution.
    spec = importlib.util.find_spec(module)
    if spec is None:
        return False
    # Si le spec provient d'un fichier dans le répertoire de la stdlib, on le considère std.
    origin = spec.origin or ""
    return "site-packages" not in origin.replace("\\", "/").lower()


def _modules_vers_distributions(module: str) -> List[str]:
    """Mappe un nom de module vers ses noms de distribution via packages_distributions()."""
    mapping = importlib.metadata.packages_distributions()
    return mapping.get(module, [])


def _distribution_fichiers(dist_name: str) -> List[Path]:
    """Renvoie la liste des fichiers d'une distribution, vide si introuvable."""
    try:
        dist = importlib.metadata.distribution(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return []
    return [Path(p) for p in (dist.files or [])]


def dependance_embarquable(dep: str) -> Tuple[bool, str]:
    """
    Indique si la dépendance *dep* (nom de module) est embarquable.
    Mappe le nom de module vers sa distribution via packages_distributions(),
    puis vérifie les binaires de cette distribution.
    Retourne (True, "") si oui, sinon (False, motif).
    """
    dist_names = _modules_vers_distributions(dep)
    if not dist_names:
        return False, (
            f"`{dep}` ne correspond à aucune distribution installée "
            f"(packages_distributions() ne liste pas ce module) ; "
            f"impossible de vérifier l'embarquabilité."
        )

    bin_ext = {".so", ".pyd", ".dll"}
    total_binaires = 0
    for dist_name in dist_names:
        fichiers = _distribution_fichiers(dist_name)
        binaires = [f for f in fichiers if f.suffix.lower() in bin_ext]
        total_binaires += len(binaires)

    if total_binaires:
        motif = (
            f"`{dep}` porte {total_binaires} extension(s) C ; "
            f"la doc `zipapp` interdit de les exécuter depuis une archive. "
            f"Options : exclure la dépendance et l'exiger sur la machine cible, "
            f"ou livrer autrement."
        )
        return False, motif
    return True, ""


def carte_embarquable() -> Dict[str, List[str]]:
    """
    Retourne un dictionnaire avec deux listes :
    - "embarquables" : noms des paquets sans binaire natif.
    - "non_embarquables" : noms des paquets contenant au moins un binaire.
    """
    embarquables: List[str] = []
    non_embarquables: List[str] = []
    bin_ext = {".so", ".pyd", ".dll"}
    for dist in importlib.metadata.distributions():
        nom = dist.metadata["Name"]
        fichiers = _distribution_fichiers(nom)
        binaires = [f for f in fichiers if f.suffix.lower() in bin_ext]
        (embarquables if not binaires else non_embarquables).append(nom)
    return {"embarquables": sorted(embarquables), "non_embarquables": sorted(non_embarquables)}


def _creer_archive(source_dir: Path, cible: Path, main_mod: str) -> None:
    """Crée une archive zipapp compressée."""
    zipapp.create_archive(
        source=str(source_dir),
        target=str(cible),
        interpreter=f"{sys.executable}",
        main=main_mod,
        compressed=True,
    )


def verifier_execution(archive: Path) -> int:
    """
    Exécute l'archive avec l'interpréteur courant.
    Retourne le code de sortie du processus.
    """
    proc = subprocess.run([sys.executable, str(archive)], capture_output=True, text=True)
    return proc.returncode


def emballer(outil: Path, racine: Path) -> Tuple[bool, str]:
    """
    Tente d'empaqueter *outil* (fichier .py) en zipapp autonome.
    Retourne (True, chemin_archive) en cas de succès,
    sinon (False, message_d_erreur).
    """
    if not outil.is_file() or outil.suffix != ".py":
        return False, "Le chemin fourni n'est pas un fichier Python (.py)."

    imports = analyser_imports(outil)
    if not imports:
        return False, "Aucun import détecté ; dénominateur nul – l'outil ne peut être analysé."

    # Vérification des dépendances tierces
    for dep in imports:
        if _est_stdlib(dep):
            continue
        # Vérifier que le module est installé avant toute chose
        if importlib.util.find_spec(dep) is None:
            return False, (
                f"REFUS : la dépendance `{dep}` n'est pas installée "
                f"(find_spec retourne None) ; l'archive échouerait à l'exécution."
            )
        ok, motif = dependance_embarquable(dep)
        if not ok:
            return False, f"REFUS : {motif}"

    # Construction de l'archive
    module_name = outil.stem
    archive_path = racine / f"{module_name}.pyz"
    try:
        _creer_archive(source_dir=outil.parent, cible=archive_path, main_mod=module_name)
    except Exception as exc:
        return False, f"Échec de création d'archive : {exc}"

    # Vérification d'exécution
    code = verifier_execution(archive_path)
    if code != 0:
        return False, f"L'archive s'est exécutée avec le code de sortie {code} (défaut détecté)."
    return True, str(archive_path)


def verifier_archive(archive: Path) -> Tuple[bool, int]:
    """
    Exécute *archive* et renvoie (True, code) si le code est 0,
    sinon (False, code).
    """
    if not archive.is_file():
        raise FileNotFoundError(f"Archive introuvable : {archive}")
    code = verifier_execution(archive)
    return (code == 0, code)


def eprouver_archive(archive: Path) -> Tuple[bool, str]:
    """
    Importe chaque module depuis l'archive via zipimport et vérifie qu'aucun
    fichier n'est écrit sur disque.
    zipimport.zipimporter.load_module est déprécié, retrait prévu en 3.15 :
    employer exec_module() via find_spec.

    La surveillance des écritures disque se fait via un répertoire temporaire
    isolé (TMPDIR/TEMP/TMP) : on enregistre son contenu avant l'import, puis
    on vérifie qu'aucun fichier n'y a été créé pendant l'import.
    """
    if not archive.is_file():
        raise FileNotFoundError(f"Archive introuvable : {archive}")

    modules: Set[str] = set()
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if name.endswith('.py'):
                mod = name.replace('/', '.').replace('\\', '.')
                if mod.endswith('.py'):
                    mod = mod[:-3]
                if mod.endswith('.__init__'):
                    mod = mod[:-9]
                if mod:
                    modules.add(mod)

    importer = zipimport.zipimporter(str(archive))
    details = []
    succes = True

    for mod in modules:
        with tempfile.TemporaryDirectory(prefix="eprouver_") as tmpdir:
            # Rediriger les répertoires temporaires vers le répertoire isolé
            old_env: Dict[str, str | None] = {}
            for var in ("TMPDIR", "TEMP", "TMP"):
                old_env[var] = os.environ.get(var)
                os.environ[var] = tmpdir

            fichiers_avant = set(Path(tmpdir).rglob('*'))

            try:
                spec = importer.find_spec(mod)
                if spec is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod] = module
                spec.loader.exec_module(module)

                # Vérifier qu'aucun fichier n'a été écrit dans le répertoire temporaire
                fichiers_apres = set(Path(tmpdir).rglob('*'))
                nouveaux = fichiers_apres - fichiers_avant
                if nouveaux:
                    succes = False
                    details.append(
                        f"Le module {mod} a écrit des fichiers sur disque : "
                        f"{[str(f) for f in sorted(nouveaux)]}."
                    )

                # Vérifier que l'origine du module est bien l'archive
                origin = getattr(module, '__file__', '') or ''
                if str(archive) not in origin:
                    succes = False
                    details.append(
                        f"Le module {mod} n'a pas été chargé depuis l'archive "
                        f"(origine: {origin})."
                    )
            except Exception as exc:
                succes = False
                details.append(f"Échec d'import du module {mod} depuis l'archive : {exc}")
            finally:
                # Restaurer les variables d'environnement
                for var, val in old_env.items():
                    if val is not None:
                        os.environ[var] = val
                    else:
                        os.environ.pop(var, None)

    details.append(
        "Contrôle via exec_module() et find_spec() "
        "(load_module déprécié, retrait prévu en 3.15)."
    )
    details.append(
        "Surveillance des écritures disque via répertoire temporaire isolé "
        "(TMPDIR/TEMP/TMP)."
    )

    if succes:
        return True, (
            "Tous les modules ont été importés depuis l'archive "
            "sans écriture sur disque. " + " | ".join(details)
        )
    else:
        return False, (
            "Échec de l'épreuve d'import depuis l'archive. " + " | ".join(details)
        )


# --------------------------------------------------------------------------- #
#                               INTERFACE CLI                               #
# --------------------------------------------------------------------------- #

def _afficher_humain(result: Dict) -> None:
    """Affiche le résultat de façon lisible pour un humain."""
    if result.get("succès"):
        print("✅ Succès :", result.get("message", ""))
    else:
        print("❌ Échec :", result.get("message", ""))
    if "details" in result:
        print("\nDétails :")
        for ligne in result["details"]:
            print("- " + ligne)


def _afficher_json(result: Dict) -> None:
    """Écrit le résultat complet au format JSON sur stdout."""
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="livrer_autonome",
        description="Emballage autonome d'un script Python en zipapp.",
        epilog="Exemple : python -m outils.livrer_autonome emballer mon_script.py --racine .",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à utiliser à la place du répertoire du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON unique.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande emballer
    p_emb = subparsers.add_parser("emballer", help="Emballer un script Python en zipapp.")
    p_emb.add_argument("outil", type=Path, help="Chemin du fichier .py à emballer.")

    # sous‑commande carte
    subparsers.add_parser("carte", help="Affiche la liste des paquets embarquables / non embarquables.")

    # sous‑commande verifier
    p_ver = subparsers.add_parser("verifier", help="Vérifie l'exécution d'une archive zipapp.")
    p_ver.add_argument("archive", type=Path, help="Chemin de l'archive .pyz à tester.")

    # sous‑commande eprouver
    p_epr = subparsers.add_parser("eprouver", help="Importe chaque module depuis l'archive via zipimport et vérifie qu'aucun fichier n'est écrit sur disque.")
    p_epr.add_argument("archive", type=Path, help="Chemin de l'archive .pyz à éprouver.")

    args = parser.parse_args()

    # Construction du dictionnaire de base du résultat
    resultat: Dict = {
        "contrat": {
            "QUESTION": "cet outil peut-il voyager en un seul fichier ?",
            "MESURE": "imports par ast + binaires par distribution.files + exécution réelle",
            "HYPOTHÈSES": "la machine cible a un interpréteur ≥ 3.14 et sa stdlib complète",
            "LIMITES": "ne voit pas les imports dynamiques ; ne teste pas la machine cible",
            "CONTRE-EXEMPLE": "une archive peut se construire et ne pas s'exécuter — d'où la vérification d'exécution obligatoire",
            "DOMAINE": "cet interpréteur, pour une cible de même version majeure",
        }
    }

    try:
        if args.commande == "emballer":
            imports = analyser_imports(args.outil)
            denominator = len(imports)
            if denominator == 0:
                print("Dénominateur nul : aucun élément détecté.", file=sys.stderr)
                sys.exit(3)
            ok, msg = emballer(args.outil, args.racine)
            resultat["succès"] = ok
            resultat["message"] = msg
            resultat["details"] = []
            code_sortie = 0 if ok else 1

        elif args.commande == "carte":
            dists = list(importlib.metadata.distributions())
            denominator = len(dists)
            if denominator == 0:
                print("Dénominateur nul : aucune distribution détectée.", file=sys.stderr)
                sys.exit(3)
            carte = carte_embarquable()
            resultat["succès"] = True
            resultat["message"] = "Cartographie des paquets obtenue."
            resultat["details"] = [
                f"{len(carte['embarquables'])} embarquables, {len(carte['non_embarquables'])} non embarquables."
            ]
            resultat["carte"] = carte
            code_sortie = 0

        elif args.commande == "verifier":
            denominator = 1  # une archive examinée
            ok, code = verifier_archive(args.archive)
            resultat["succès"] = ok
            resultat["message"] = f"Archive exécutée avec le code de sortie {code}."
            resultat["details"] = []
            code_sortie = 0 if ok else 1

        elif args.commande == "eprouver":
            denominator = 1  # une archive éprouvée
            ok, msg = eprouver_archive(args.archive)
            resultat["succès"] = ok
            resultat["message"] = msg
            resultat["details"] = []
            code_sortie = 0 if ok else 1

        else:
            # impossible grâce à argparse
            raise RuntimeError("Commande inconnue.")

    except ValueError as ve:
        # Erreur de validation (ex. syntaxe, dénominateur nul déjà traité ci-dessus)
        print(f"Erreur : {ve}", file=sys.stderr)
        resultat["succès"] = False
        resultat["message"] = str(ve)
        code_sortie = 2  # code dédié aux dénominateurs nuls ou erreurs de validation

    except Exception as exc:  # pragma: no cover – sécurité globale
        print(f"Erreur inattendue : {exc}", file=sys.stderr)
        resultat["succès"] = False
        resultat["message"] = f"Erreur inattendue : {exc}"
        code_sortie = 1

    # Ajout du dénominateur au résultat (déjà calculé dans chaque branche)
    # Pour les cas où le dénominateur n'a pas été défini (ex. erreur avant calcul),
    # on le met à 0 pour éviter KeyError, mais le traitement ci-dessus aura déjà
    # quitté avec code 3 si nécessaire.
    if "denominateur" not in resultat:
        resultat["denominateur"] = denominator if 'denominator' in locals() else 0

    # Impression du résultat
    if args.json:
        _afficher_json(resultat)
    else:
        _afficher_humain(resultat)

    return code_sortie


if __name__ == "__main__":
    raise SystemExit(main())