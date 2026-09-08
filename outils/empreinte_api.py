"""empreinte_api.py
Outil d’analyse d’empreinte d’API Python.

CONTRAT DE MESURE
-----------------
QUESTION       qu'est-ce qui a cassé entre ces deux versions ?
MESURE         API publique extraite par vars() + inspect.signature,
               normalisée, triée, empreinte SHA‑256 ; changements classés
               par effet sur l'appelant
HYPOTHÈSES    le module s'importe sans effet de bord ; l'API publique est
               ce qui ne commence pas par « _ »
LIMITES        ne voit pas un changement de COMPORTEMENT à signature égale ;
               isfunction|isclass rate les formes spéciales et les fonctions C ;
               un __getattr__ dynamique n'est pas énumérable
CONTRE-EXEMPLE une empreinte calculée sur un ordre non trié diverge sans
               qu'aucune API n'ait changé — faux positif qui décrédibilise
DOMAINE        modules Python importables, API publique par convention
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import sys
import typing
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 2. Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Optionnel : docstring_parser
try:
    from docstring_parser import parse as parse_docstring  # type: ignore
except ImportError:  # pragma: no cover
    parse_docstring = None  # type: ignore

RACINE_DEFAUT = Path(__file__).resolve().parent


def _charger_module(nom: str, racine: Path) -> Any:
    """Importe un module à partir de son nom ou d’un chemin de fichier.

    Gère les cas d’inexistence et de fichiers illisibles en renvoyant
    une exception claire.
    """
    chemin = (racine / nom).with_suffix(".py")
    if chemin.exists():
        if chemin.is_dir():
            raise IsADirectoryError(f"{chemin} est un répertoire, pas un fichier module")
        # Détection d’un fichier binaire ou contenant des octets nuls
        try:
            with chemin.open("rb") as f:
                contenu = f.read()
        except OSError as exc:
            raise RuntimeError(f"Erreur : impossible de lire le fichier {chemin}") from exc
        if b"\x00" in contenu:
            raise RuntimeError(f"Erreur : le fichier {chemin} contient des octets nuls")
        # Lecture texte avec gestion d’erreurs d’encodage
        try:
            texte = contenu.decode("utf-8", errors="replace")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"Erreur : décodage du fichier {chemin} en UTF‑8") from exc
        # Chargement du module à partir du texte lu
        spec = importlib.util.spec_from_loader(f"_tmp_{nom}", loader=None)
        if spec is None:
            raise RuntimeError(f"Erreur : impossible de créer la spécification pour {nom}")
        mod = importlib.util.module_from_spec(spec)
        exec(texte, mod.__dict__)  # noqa: S102
        sys.modules[spec.name] = mod
        return mod
    # sinon, on tente d’importer par nom (module ou package)
    try:
        return importlib.import_module(nom)
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"Erreur : module introuvable '{nom}'") from exc


def _signature_texte(obj: Any) -> str:
    """Retourne la signature sous forme de chaîne ou '(?)' en cas d’erreur."""
    try:
        sig = inspect.signature(obj)
        try:
            hints = typing.get_type_hints(obj)
        except Exception:
            hints = {}
        if hints:
            new_params = []
            for p in sig.parameters.values():
                if p.name in hints:
                    new_params.append(p.replace(annotation=hints[p.name]))
                else:
                    new_params.append(p)
            sig = sig.replace(parameters=new_params)
        return str(sig)
    except (ValueError, TypeError):
        return "(?)"


def _extraire_api(module: Any) -> Dict[str, str]:
    """Extrait l’API publique du module sous forme {qualname: signature}."""
    api: Dict[str, str] = {}
    for name, member in vars(module).items():
        if name.startswith("_"):
            continue
        if inspect.isfunction(member) or inspect.isclass(member):
            api[name] = _signature_texte(member)
    return api


def _empreinte(api: Dict[str, str]) -> str:
    """Calcule l’empreinte SHA‑256 d’une API triée, incluant la version Python."""
    lignes = [f"{k}:{v}" for k, v in sorted(api.items())]
    payload = "\n".join(lignes)
    hash_hex = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    version = f"{sys.version_info[0]}.{sys.version_info[1]}"
    return f"{version}\n{hash_hex}"


def empreindre(
    nom_module: str, racine: Path = RACINE_DEFAUT
) -> Tuple[str, List[Tuple[str, str]]]:
    """Calcule l’empreinte d’un module."""
    module = _charger_module(nom_module, racine)
    api = _extraire_api(module)
    if hasattr(module, "__getattr__") and callable(getattr(module, "__getattr__")):
        sys.stderr.write(
            f"Warning: module {nom_module} defines __getattr__; API may be incomplete\n"
        )
    return _empreinte(api), [(k, v) for k, v in sorted(api.items())]


def _classer_changement(ancien_obj: Any, nouveau_obj: Any) -> Tuple[str, str]:
    """Classe un changement de signature entre deux objets (fonctions ou classes)."""
    try:
        sig_old = inspect.signature(ancien_obj)
    except (ValueError, TypeError):
        sig_old = None
    try:
        sig_new = inspect.signature(nouveau_obj)
    except (ValueError, TypeError):
        sig_new = None

    if sig_old is None or sig_new is None:
        return "INDÉTERMINÉ", "signature indéterminée"

    if sig_old.return_annotation != sig_new.return_annotation:
        return "TYPE DE RETOUR CHANGÉ", "annotation de retour différente"

    old_params = sig_old.parameters
    new_params = sig_new.parameters

    suppr = [n for n in old_params if n not in new_params]
    if suppr:
        return "PARAMÈTRE RETIRÉ", f"paramètre(s) {', '.join(suppr)} retiré(s)"

    ajout_defaut = [
        n
        for n in new_params
        if n not in old_params and new_params[n].default is not inspect._empty
    ]
    if ajout_defaut:
        return (
            "PARAMÈTRE À DÉFAUT AJOUTÉ",
            f"paramètre(s) {', '.join(ajout_defaut)} ajouté(s) avec défaut",
        )

    rendu_obligatoire = [
        n
        for n in old_params
        if n in new_params
        and old_params[n].default is not inspect._empty
        and new_params[n].default is inspect._empty
    ]
    if rendu_obligatoire:
        return (
            "PARAMÈTRE RENDU OBLIGATOIRE",
            f"paramètre(s) {', '.join(rendu_obligatoire)} rendu(s) obligatoire(s)",
        )

    return "COMPATIBLE", "modifications compatibles"


def comparer(
    avant: str,
    apres: str,
    racine: Path = RACINE_DEFAUT,
    verifier_docstring: bool = False,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], str, int]:
    """Compare deux versions d’un même module."""
    emp_av, api_av = empreindre(avant, racine)
    emp_ap, api_ap = empreindre(apres, racine)

    if not api_av and not api_ap:
        return [], [], "AUCUNE API", 0

    ver_av = emp_av.splitlines()[0] if "\n" in emp_av else ""
    ver_ap = emp_ap.splitlines()[0] if "\n" in emp_ap else ""
    if ver_av != ver_ap:
        return [], [], "VERSIONS DIFFÉRENTES — comparaison non concluante", 0

    ruptures: List[Dict[str, str]] = []
    compatibles: List[Dict[str, str]] = []

    noms_av = {n for n, _ in api_av}
    noms_ap = {n for n, _ in api_ap}
    dict_av = dict(api_av)
    dict_ap = dict(api_ap)

    for nom in sorted(noms_av - noms_ap):
        ruptures.append(
            {"nom": nom, "ancienne": dict_av[nom], "nouvelle": "", "classement": "SUPPRIMÉ"}
        )
    for nom in sorted(noms_ap - noms_av):
        compatibles.append(
            {"nom": nom, "ancienne": "", "nouvelle": dict_ap[nom], "classement": "AJOUTÉ"}
        )

    mod_av = _charger_module(avant, racine)
    mod_ap = _charger_module(apres, racine)

    for nom in sorted(noms_av & noms_ap):
        anc_sig = dict_av[nom]
        nouv_sig = dict_ap[nom]
        if anc_sig == nouv_sig:
            continue
        ancien_obj = getattr(mod_av, nom)
        nouveau_obj = getattr(mod_ap, nom)
        classement, description = _classer_changement(ancien_obj, nouveau_obj)
        if classement == "INDÉTERMINÉ":
            sys.stderr.write(f"Warning: signature indeterminate for {nom}\n")
            continue
        entry = {
            "nom": nom,
            "ancienne": anc_sig,
            "nouvelle": nouv_sig,
            "classement": classement,
            "description": description,
        }
        if classement in ("SUPPRIMÉ", "PARAMÈTRE RETIRÉ", "PARAMÈTRE RENDU OBLIGATOIRE"):
            ruptures.append(entry)
        else:
            compatibles.append(entry)

        if verifier_docstring and parse_docstring:
            try:
                doc = parse_docstring(inspect.getdoc(nouveau_obj) or "")
                if doc.params:
                    doc_params = {p.arg_name for p in doc.params}
                    sig_params = set(inspect.signature(nouveau_obj).parameters)
                    if not doc_params.issubset(sig_params):
                        sys.stderr.write(
                            f"Warning: docstring incoherente for {nom}: docstring décrit des paramètres inexistants\n"
                        )
            except Exception:  # pragma: no cover
                pass

    verdict = "IDENTIQUE" if not ruptures and not compatibles else "DIVERGENTE"
    denominateur = len(api_av) + len(api_ap)
    return ruptures, compatibles, verdict, denominateur


def _afficher_humain(
    avant: str,
    apres: str,
    ruptures: List[Dict[str, str]],
    compatibles: List[Dict[str, str]],
    verdict: str,
) -> None:
    print(f"Comparaison : {avant} ↔ {apres}")
    print(f"Verdict : {verdict}\n")
    if ruptures:
        print("RUPTURES :")
        for r in ruptures:
            print(f"  {r['nom']}{r['ancienne']} → {r['nouvelle']}  {r['classement']}")
        print()
    if compatibles:
        print("COMPATIBLES :")
        for c in compatibles:
            print(f"  {c['nom']}{c['ancienne']} → {c['nouvelle']}  {c['classement']}")
        print()


def _json_sort_key(item: Dict[str, str]) -> str:
    return item.get("nom", "")


def _sort_result(ruptures, compatibles):
    return sorted(ruptures, key=_json_sort_key), sorted(compatibles, key=_json_sort_key)


def _verifier_cible(cible: str, racine: Path) -> None:
    """Vérifie que la cible n’est pas un répertoire lorsqu’un fichier est attendu."""
    chemin_brut = racine / cible
    if chemin_brut.is_dir():
        raise IsADirectoryError(f"{chemin_brut} est un répertoire, pas un fichier module")
    chemin = (racine / cible).with_suffix(".py")
    if chemin.exists() and chemin.is_dir():
        raise IsADirectoryError(f"{chemin} est un répertoire, pas un fichier module")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="empreinte_api.py",
        usage="empreinte_api.py [options] module1 [module2]",
        description="Analyse d’empreinte d’API et comparaison entre deux versions.",
        epilog="Exemple : python empreinte_api.py monmodule_v1.py monmodule_v2.py --json",
        add_help=False,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-h", "--help", action="help", help="Affiche cette aide et quitte")
    parser.add_argument(
        "modules",
        nargs="+",
        help="Nom ou chemin du module à analyser (un seul → empreinte, deux → comparaison).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE_DEFAUT,
        help="Chemin racine à utiliser pour les chemins relatifs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    parser.add_argument(
        "--docstring",
        action="store_true",
        help="Vérifier la cohérence des docstrings (requiert docstring_parser).",
    )
    args = parser.parse_args()

    try:
        for cible in args.modules:
            _verifier_cible(cible, args.racine)
    except IsADirectoryError as exc:
        sys.stderr.write(f"Erreur : {exc}\n")
        return 2

    try:
        if len(args.modules) == 1:
            empreinte, api = empreindre(args.modules[0], args.racine)
            denominateur = len(api)
            if denominateur == 0:
                sys.stderr.write("Erreur : aucune API publique détectée.\n")
                return 2
            if args.json:
                out = {"denominateur": denominateur, "empreinte": empreinte, "api": api}
                json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
                return 0
            print(f"Empreinte de {args.modules[0]} : {empreinte}")
            for nom, sig in api:
                print(f"{nom}{sig}")
            return 0

        if len(args.modules) != 2:
            parser.error("Deux modules sont requis pour la comparaison.")
            return 1

        avant, apres = args.modules
        ruptures, compatibles, verdict, denominateur = comparer(
            avant,
            apres,
            racine=args.racine,
            verifier_docstring=args.docstring,
        )
        ruptures, compatibles = _sort_result(ruptures, compatibles)

        if denominateur == 0:
            sys.stderr.write("Erreur : aucune API publique à comparer.\n")
            return 2

        if args.json:
            out = {
                "denominateur": denominateur,
                "verdict": verdict,
                "ruptures": ruptures,
                "compatibles": compatibles,
            }
            json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            _afficher_humain(avant, apres, ruptures, compatibles, verdict)

        if verdict == "IDENTIQUE":
            return 0
        if verdict == "VERSIONS DIFFÉRENTES — comparaison non concluante":
            return 3
        if verdict == "AUCUNE API":
            return 2
        return 1 if ruptures else 0

    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())