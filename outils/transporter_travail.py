"""Transporte du travail Python entre processus ou interpréteurs.

QUESTION
    ce travail peut-il être exécuté ailleurs, et qu'est-ce qui traverse réellement ?
MESURE
    cloudpickle.dumps/loads, comparé à pickle ; transport réel vers un sous-processus
HYPOTHÈSES
    le receveur a la même version de Python et cloudpickle
LIMITES
    un verrou, un générateur en cours ne traversent PAS ; les modules traversent par RÉFÉRENCE sans erreur ; ce qui est capturé est COPIÉ, pas partagé
CONTRE-EXEMPLES
    cloudpickle.dumps(sys.stdout) rend 98 octets et « réussit » — le receveur obtient SON stdout. Plausible et faux.
INVOCATION
    {outil} classer lambda --json
DOMAINE
    code Python sans état vivant, même version des deux côtés
"""

from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import base64
import hashlib
import io
import json
import os
import pickle
import pickletools
import subprocess
import threading
import time
import traceback
import types
import warnings
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Optional

class Classification(NamedTuple):
    nom: str
    mode: str
    taille: Optional[int]
    detail: str

class ResultatEnvoi(NamedTuple):
    valeur: Any
    duree_ms: float
    transporteur: str
    cible: str

class RapportReception(NamedTuple):
    etages: list[dict[str, Any]]
    resultat: Any
    duree_ms: float

def importer_cloudpickle() -> Any:
    try:
        import cloudpickle
        return cloudpickle
    except ImportError:
        return None

def importer_dill() -> Any:
    try:
        import dill
        return dill
    except ImportError:
        return None

def importer_restrictedpython() -> Any:
    try:
        from RestrictedPython import compile_restricted
        return compile_restricted
    except ImportError:
        return None

def importer_interpreters() -> Any:
    try:
        from concurrent import interpreters
        return interpreters
    except ImportError:
        try:
            from test.support import interpreters
            return interpreters
        except ImportError:
            return None

def ecrire_diagnostic(message: str) -> None:
    print(message, file=sys.stderr)

def encoder_base64(octets: bytes) -> str:
    return base64.b64encode(octets).decode("ascii")

def decoder_base64(texte: str) -> bytes:
    return base64.b64decode(texte, validate=True)

def calculer_sceau(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()

def choisir_transporteur(compact: bool) -> tuple[Optional[Any], str]:
    cloudpickle = importer_cloudpickle()
    dill = importer_dill()
    if compact:
        if dill is not None:
            return dill, "dill"
        if cloudpickle is not None:
            ecrire_diagnostic("Avertissement : dill demandé mais absent, cloudpickle utilisé.")
            return cloudpickle, "cloudpickle"
        return None, ""
    if cloudpickle is not None:
        return cloudpickle, "cloudpickle"
    if dill is not None:
        ecrire_diagnostic("Avertissement : cloudpickle absent, dill utilisé.")
        return dill, "dill"
    return None, ""

def mesurer_serialization(objet: Any, module: Any) -> tuple[Optional[bytes], Optional[str]]:
    try:
        octets = module.dumps(objet)
        return octets, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

def determiner_mode(objet: Any, octets: bytes, module: Any) -> tuple[str, str]:
    try:
        recharge = module.loads(octets)
    except Exception as exc:
        return "PAR VALEUR", f"sérialisé {len(octets)} o, relecture locale impossible ({type(exc).__name__})"
    if recharge is objet:
        return "PAR RÉFÉRENCE", f"{len(octets)} o — résolu chez le receveur, `is` l'original ici"
    return "PAR VALEUR", f"{len(octets)} o — copie indépendante"

def classer_objet(objet: Any, nom: str = "objet", compact: bool = False) -> list[Classification]:
    resultats: list[Classification] = []
    module, nom_module = choisir_transporteur(compact)
    if module is None:
        resultats.append(Classification(nom, "INDISPONIBLE", None, "ni cloudpickle ni dill n'est installé"))
        return resultats

    # Vérification spécifique pour les générateurs en cours
    if isinstance(objet, types.GeneratorType):
        resultats.append(Classification(nom, "IMPOSSIBLE", None, f"{nom_module} — générateur en cours"))
        return resultats

    # pickle standard
    octets_pickle, erreur_pickle = mesurer_serialization(objet, pickle)
    if octets_pickle is not None:
        mode, detail = determiner_mode(objet, octets_pickle, pickle)
        resultats.append(Classification(nom, mode, len(octets_pickle), f"pickle — {detail}"))
    else:
        # cloudpickle / dill
        octets, erreur = mesurer_serialization(objet, module)
        if octets is None:
            resultats.append(Classification(nom, "IMPOSSIBLE", None, f"{nom_module} — {erreur}"))
        else:
            mode, detail = determiner_mode(objet, octets, module)
            resultats.append(Classification(nom, mode, len(octets), f"{nom_module} — {detail}"))

    # Classifications des variables capturées (closures)
    if hasattr(objet, "__closure__") and objet.__closure__:
        freevars = objet.__code__.co_freevars if hasattr(objet, "__code__") else ()
        for cell, varname in zip(objet.__closure__, freevars):
            try:
                cell_value = cell.cell_contents
            except ValueError:
                continue
            # Vérification spécifique pour les générateurs en cours dans les cellules
            if isinstance(cell_value, types.GeneratorType):
                resultats.append(Classification(f"  {varname}", "IMPOSSIBLE", None, f"{nom_module} — générateur en cours"))
                continue
            sous_nom = f"  {varname}={cell_value!r}"
            sous_octets, sous_erreur = mesurer_serialization(cell_value, module)
            if sous_octets is not None:
                sous_mode = "PAR VALEUR"
                sous_detail = f"{len(sous_octets)} o — copie indépendante"
                resultats.append(Classification(sous_nom, sous_mode, len(sous_octets), f"{nom_module} — {sous_detail}"))
            else:
                resultats.append(Classification(sous_nom, "IMPOSSIBLE", None, f"{nom_module} — {sous_erreur}"))

    # Classifications des objets référencés (sys, sys.stdout, threading.Lock)
    for ref_nom, ref_objet in [("sys", sys), ("sys.stdout", sys.stdout), ("threading.Lock", threading.Lock())]:
        if isinstance(ref_objet, types.GeneratorType):
            resultats.append(Classification(ref_nom, "IMPOSSIBLE", None, f"{nom_module} — générateur en cours"))
            continue
        ref_octets, ref_erreur = mesurer_serialization(ref_objet, module)
        if ref_octets is not None:
            if isinstance(ref_objet, types.ModuleType) or ref_objet is sys.stdout:
                ref_mode = "PAR RÉFÉRENCE"
                ref_detail = f"{len(ref_octets)} o — résolu chez le receveur"
            else:
                ref_mode, ref_detail = determiner_mode(ref_objet, ref_octets, module)
            resultats.append(Classification(ref_nom, ref_mode, len(ref_octets), f"{nom_module} — {ref_detail}"))
        else:
            resultats.append(Classification(ref_nom, "IMPOSSIBLE", None, f"{nom_module} — {ref_erreur}"))

    return resultats

def creer_objets_demo() -> dict[str, Any]:
    def fabrique(mult: int) -> Callable[[int], int]:
        seuil = 42
        def f(x: int) -> int:
            return x * mult + seuil
        return f

    def closure_stringio() -> str:
        contenu = io.StringIO("contenu")
        def lire() -> str:
            return contenu.read()
        return lire

    return {
        "lambda": lambda x: x * 2,
        "closure": fabrique(3),
        "classe_locale": type("Locale", (), {"valeur": 7}),
        "fonction_module": len,
        "sys": sys,
        "sys_stdout": sys.stdout,
        "lock": threading.Lock(),
        "generateur": (x for x in range(3)),
        "stringio_capture": closure_stringio(),
    }

def executer_dans_processus(fonction: Callable[..., Any], transporteur: Any, nom_transporteur: str) -> ResultatEnvoi:
    octets = transporteur.dumps(fonction)
    flux = encoder_base64(octets)
    script = f"""
import sys
import base64
import {nom_transporteur} as tp
flux = sys.stdin.readline().strip()
octets = base64.b64decode(flux, validate=True)
obj = tp.loads(octets)
resultat = obj(10)
sortie = base64.b64encode(tp.dumps(resultat)).decode("ascii")
print(sortie)
"""
    debut = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-"],
            input=script + flux + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("le sous-processus a dépassé le délai")
    duree_ms = (time.perf_counter() - debut) * 1000
    if proc.returncode != 0:
        raise RuntimeError(f"sous-processus échoué : {proc.stderr.strip()}")
    ligne = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    resultat_octets = decoder_base64(ligne)
    resultat = transporteur.loads(resultat_octets)
    return ResultatEnvoi(resultat, duree_ms, nom_transporteur, "processus")

def executer_dans_interpreteur(fonction: Callable[..., Any], transporteur: Any, nom_transporteur: str) -> ResultatEnvoi:
    interpreters = importer_interpreters()
    if interpreters is None:
        raise RuntimeError("concurrent.interpreters n'est pas disponible")
    octets = transporteur.dumps(fonction)
    flux = encoder_base64(octets)
    interp = interpreters.create()
    shared: dict[str, Any] = {"flux": flux, "transporteur": nom_transporteur}
    script = """
import base64
import sys
flux = shared['flux']
nom_transporteur = shared['transporteur']
if nom_transporteur == 'cloudpickle':
    import cloudpickle as tp
else:
    import dill as tp
octets = base64.b64decode(flux, validate=True)
obj = tp.loads(octets)
resultat = obj(10)
shared['resultat'] = base64.b64encode(tp.dumps(resultat)).decode('ascii')
"""
    debut = time.perf_counter()
    try:
        interpreters.run_string(interp, script, shared=shared)
    finally:
        interpreters.destroy(interp)
    duree_ms = (time.perf_counter() - debut) * 1000
    if "resultat" not in shared:
        raise RuntimeError("l'interpréteur n'a pas rendu de résultat")
    resultat_octets = decoder_base64(shared["resultat"])
    resultat = transporteur.loads(resultat_octets)
    return ResultatEnvoi(resultat, duree_ms, nom_transporteur, "interpreteur")

def envoyer_fonction(
    fonction: Callable[..., Any],
    cible: str,
    par_valeur_modules: Optional[list[str]] = None,
    compact: bool = False,
) -> ResultatEnvoi:
    transporteur, nom_transporteur = choisir_transporteur(compact)
    if transporteur is None:
        raise RuntimeError("aucun transporteur disponible (cloudpickle ou dill requis)")

    if isinstance(fonction, types.GeneratorType):
        raise TypeError("IMPOSSIBLE — générateur en cours")

    if par_valeur_modules:
        for nom_module in par_valeur_modules:
            try:
                module = __import__(nom_module)
                transporteur.register_pickle_by_value(module)
            except Exception as exc:
                ecrire_diagnostic(f"Impossible d'enregistrer {nom_module} par valeur : {exc}")

    if cible == "processus":
        return executer_dans_processus(fonction, transporteur, nom_transporteur)
    if cible == "interpreteur":
        return executer_dans_interpreteur(fonction, transporteur, nom_transporteur)
    raise ValueError(f"cible inconnue : {cible}")

def recevoir_octets(enveloppe: dict[str, Any], delai_max: float = 5.0) -> RapportReception:
    etages: list[dict[str, Any]] = []
    debut_total = time.perf_counter()

    # Étape 0 : version Python
    version_attendue = enveloppe.get("version_python", "")
    version_ok = version_attendue == f"{sys.version_info.major}.{sys.version_info.minor}"
    etages.append({
        "etage": "version",
        "statut": "OK" if version_ok else "REFUSÉ",
        "detail": f"attendue {version_attendue}, actuelle {sys.version_info.major}.{sys.version_info.minor}",
    })
    if not version_ok:
        return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)

    # Étape 1 : sceau + base64
    flux_base64 = enveloppe.get("flux_base64", "")
    sceau_attendu = enveloppe.get("sceau", "")
    try:
        octets_bruts = decoder_base64(flux_base64)
        sceau_calcule = calculer_sceau(octets_bruts)
        sceau_ok = sceau_calcule == sceau_attendu
    except Exception as exc:
        etages.append({
            "etage": "nom",
            "statut": "REFUSÉ",
            "detail": f"flux illisible : {type(exc).__name__}: {exc}",
        })
        return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)

    etages.append({
        "etage": "nom",
        "statut": "OK" if sceau_ok else "REFUSÉ",
        "detail": f"sceau attendu {sceau_attendu[:16]}…, calculé {sceau_calcule[:16]}…",
    })
    if not sceau_ok:
        return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)

    # Inspection pickletools
    noms_interdits = {"eval", "exec", "__import__", "open", "os.system"}
    noms_trouves: set[str] = set()
    try:
        for opcode, arg, _ in pickletools.genops(io.BytesIO(octets_bruts)):
            if isinstance(arg, str):
                noms_trouves.add(arg)
    except Exception as exc:
        etages.append({
            "etage": "nom",
            "statut": "REFUSÉ",
            "detail": f"pickletools a échoué : {exc}",
        })
        return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)

    noms_hostiles = noms_trouves & noms_interdits
    if noms_hostiles:
        etages.append({
            "etage": "nom",
            "statut": "REFUSÉ",
            "detail": f"noms en liste noire : {sorted(noms_hostiles)}",
        })
        return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)

    # Étape 2 : appel (RestrictedPython) — applicable seulement à du source
    compile_restricted = importer_restrictedpython()
    if compile_restricted is not None:
        source = enveloppe.get("source_python")
        if source:
            try:
                code_bride = compile_restricted(source, "<reçu>", "exec")
                if code_bride is None:
                    raise ValueError("compilation bridée refusée")
                etages.append({
                    "etage": "appel",
                    "statut": "OK",
                    "detail": "source acceptée par RestrictedPython",
                })
            except Exception as exc:
                etages.append({
                    "etage": "appel",
                    "statut": "REFUSÉ",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
                return RapportReception(etages, None, (time.perf_counter() - debut_total) * 1000)
        else:
            etages.append({
                "etage": "appel",
                "statut": "NON APPLICABLE",
                "detail": "pas de source Python fournie, le flux est un pickle",
            })
    else:
        etages.append({
            "etage": "appel",
            "statut": "DÉSACTIVÉ",
            "detail": "RestrictedPython non installé",
        })

    # Étape 3 : mémoire (interpréteur isolé)
    interpreters = importer_interpreters()
    transporteur_nom = enveloppe.get("transporteur", "cloudpickle")
    if interpreters is not None:
        interp = interpreters.create()
        shared: dict[str, Any] = {"flux": flux_base64, "transporteur": transporteur_nom}
        script = """
import base64
flux = shared['flux']
nom_transporteur = shared['transporteur']
if nom_transporteur == 'cloudpickle':
    import cloudpickle as tp
else:
    import dill as tp
octets = base64.b64decode(flux, validate=True)
obj = tp.loads(octets)
resultat = obj(10)
shared['resultat'] = base64.b64encode(tp.dumps(resultat)).decode('ascii')
"""
        try:
            interpreters.run_string(interp, script, shared=shared)
            resultat_b64 = shared.get("resultat")
            if resultat_b64 is None:
                raise RuntimeError("aucun résultat de l'interpréteur")
            transporteur, _ = choisir_transporteur(transporteur_nom == "dill")
            if transporteur is None:
                raise RuntimeError("transporteur manquant pour relire le résultat")
            resultat = transporteur.loads(decoder_base64(resultat_b64))
            etages.append({
                "etage": "mémoire",
                "statut": "OK",
                "detail": "exécution isolée dans un sous-interpréteur",
            })
        except Exception as exc:
            etages.append({
                "etage": "mémoire",
                "statut": "REFUSÉ",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            resultat = None
        finally:
            interpreters.destroy(interp)
    else:
        etages.append({
            "etage": "mémoire",
            "statut": "DÉSACTIVÉ",
            "detail": "concurrent.interpreters non disponible",
        })
        resultat = None

    # Étape 4 : temps
    duree_ms = (time.perf_counter() - debut_total) * 1000
    etages.append({
        "etage": "temps",
        "statut": "OK" if duree_ms <= delai_max * 1000 else "REFUSÉ",
        "detail": f"{duree_ms:.2f} ms (limite {delai_max * 1000:.0f} ms)",
    })

    return RapportReception(etages, resultat, duree_ms)

def formatter_classifications_humain(classifications: list[Classification]) -> str:
    lignes: list[str] = []
    for cls in classifications:
        taille = f"{cls.taille} o" if cls.taille is not None else "—"
        lignes.append(f"{cls.nom:20} {cls.mode:15} {taille:10} {cls.detail}")
    return "\n".join(lignes)

def formatter_envoi_humain(resultat: ResultatEnvoi) -> str:
    return (
        f"Résultat : {resultat.valeur}\n"
        f"Durée    : {resultat.duree_ms:.2f} ms\n"
        f"Transporteur : {resultat.transporteur}\n"
        f"Cible    : {resultat.cible}"
    )

def formatter_reception_humain(rapport: RapportReception) -> str:
    lignes: list[str] = []
    for etage in rapport.etages:
        lignes.append(f"{etage['etage']:10} {etage['statut']:12} {etage['detail']}")
    lignes.append(f"Résultat : {rapport.resultat}")
    lignes.append(f"Durée    : {rapport.duree_ms:.2f} ms")
    return "\n".join(lignes)

def construire_parseur() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transporte du travail Python entre processus ou interpréteurs.",
        epilog="Exemple : python outils/transporter_travail.py classer closure",
    )
    parser.add_argument("--racine", type=Path, default=None, help="répertoire racine du projet")
    parser.add_argument("--json", action="store_true", help="rendre un objet JSON unique sur stdout")
    parser.add_argument("--compact", action="store_true", help="utiliser dill à la place de cloudpickle")

    sous = parser.add_subparsers(dest="commande", required=True)

    p_classer = sous.add_parser("classer", help="classer comment un objet traverse")
    p_classer.add_argument("objet", help="nom de l'objet de démonstration (lambda, closure, sys_stdout, lock, ...)")

    p_envoyer = sous.add_parser("envoyer", help="envoyer une fonction et récupérer le résultat")
    p_envoyer.add_argument("--vers", choices=["processus", "interpreteur"], default="processus", help="cible d'exécution")
    p_envoyer.add_argument("--par-valeur", action="append", default=None, help="module à forcer par valeur (répétable)")

    p_recevoir = sous.add_parser("recevoir", help="recevoir un flux sérialisé et enchaîner les étages de sûreté")
    p_recevoir.add_argument("fichier", nargs="?", help="fichier JSON contenant l'enveloppe (sinon stdin)")

    return parser

def lire_enveloppe(chemin: Optional[Path]) -> dict[str, Any]:
    if chemin is None:
        texte = sys.stdin.read()
    else:
        texte = chemin.read_text(encoding="utf-8")
    return json.loads(texte)

def main() -> int:
    parser = construire_parseur()
    args = parser.parse_args()

    racine = args.racine if args.racine else Path(__file__).resolve().parent
    os.chdir(racine)

    contrat = {
        "QUESTION": "ce travail peut-il être exécuté ailleurs, et qu'est-ce qui traverse réellement ?",
        "MESURE": "cloudpickle.dumps/loads, comparé à pickle ; transport réel vers un sous-processus",
        "HYPOTHÈSES": "le receveur a la même version de Python et cloudpickle",
        "LIMITES": "un verrou, un générateur en cours ne traversent PAS ; les modules traversent par RÉFÉRENCE sans erreur ; ce qui est capturé est COPIÉ, pas partagé",
        "CONTRE-EXEMPLES": "cloudpickle.dumps(sys.stdout) rend 98 octets et « réussit » — le receveur obtient SON stdout. Plausible et faux.",
        "DOMAINE": "code Python sans état vivant, même version des deux côtés",
    }

    objets = creer_objets_demo()
    code_sortie = 0
    resultat_json: dict[str, Any] = {
        "contrat": contrat,
        "commande": args.commande,
        "denominateur": 0  # Initialisé à 0, mis à jour selon le cas
    }

    try:
        if args.commande == "classer":
            if not objets:
                ecrire_diagnostic("Dénominateur nul : aucun objet de démonstration disponible, refus de conclure.")
                resultat_json["denominateur"] = 0
                if args.json:
                    print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
                return 3

            if args.objet not in objets:
                ecrire_diagnostic(f"Dénominateur nul : objet inconnu {args.objet}, refus de conclure.")
                resultat_json["denominateur"] = 0
                if args.json:
                    print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
                return 3

            objet = objets[args.objet]
            classifications = classer_objet(objet, args.objet, compact=args.compact)
            resultat_json["denominateur"] = 1
            resultat_json["classifications"] = [cls._asdict() for cls in classifications]
            modes = {cls.mode for cls in classifications}
            if "IMPOSSIBLE" in modes or "INDISPONIBLE" in modes:
                code_sortie = 1
            if args.json:
                print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
            else:
                print(formatter_classifications_humain(classifications))

        elif args.commande == "envoyer":
            transporteur, _ = choisir_transporteur(args.compact)
            if transporteur is None:
                ecrire_diagnostic("Dénominateur nul : aucun transporteur disponible (cloudpickle ou dill requis), refus de conclure.")
                resultat_json["denominateur"] = 0
                if args.json:
                    print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
                return 3

            fonction = objets.get("closure")
            if fonction is None:
                ecrire_diagnostic("Dénominateur nul : aucune fonction de démonstration disponible, refus de conclure.")
                resultat_json["denominateur"] = 0
                if args.json:
                    print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
                return 3

            resultat = envoyer_fonction(
                fonction,
                args.vers,
                par_valeur_modules=args.par_valeur,
                compact=args.compact,
            )
            resultat_json["denominateur"] = 1
            resultat_json["resultat"] = {
                "valeur": resultat.valeur,
                "duree_ms": resultat.duree_ms,
                "transporteur": resultat.transporteur,
                "cible": resultat.cible,
            }
            if args.json:
                print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
            else:
                print(formatter_envoi_humain(resultat))

        elif args.commande == "recevoir":
            chemin = Path(args.fichier) if args.fichier else None
            try:
                enveloppe = lire_enveloppe(chemin)
            except Exception:
                ecrire_diagnostic("Dénominateur nul : enveloppe invalide ou absente, refus de conclure.")
                resultat_json["denominateur"] = 0
                if args.json:
                    print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
                return 3

            rapport = recevoir_octets(enveloppe)
            resultat_json["denominateur"] = 1
            resultat_json["rapport"] = {
                "etages": rapport.etages,
                "resultat": rapport.resultat,
                "duree_ms": rapport.duree_ms,
            }
            if any(etage["statut"].startswith("REFUSÉ") for etage in rapport.etages):
                code_sortie = 1
            if args.json:
                print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
            else:
                print(formatter_reception_humain(rapport))

    except Exception as exc:
        code_sortie = 1
        resultat_json["erreur"] = f"{type(exc).__name__}: {exc}"
        if args.json:
            print(json.dumps(resultat_json, ensure_ascii=False, indent=2))
        else:
            ecrire_diagnostic(f"Échec : {type(exc).__name__}: {exc}")
            ecrire_diagnostic(traceback.format_exc())

    return code_sortie

if __name__ == "__main__":
    raise SystemExit(main())