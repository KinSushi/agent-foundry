"""La validité d'un notebook ne dit rien de sa santé : un .ipynb dont les cellules
ont été exécutées dans l'ordre 2, 1, 3, 5, qui porte une ZeroDivisionError en
sortie et une sortie texte de 300 000 caractères passe nbformat.validate sans
une seule erreur (mesuré avec nbformat 5.11.1 sur analyse.ipynb, fabriqué ici).
L'interpréteur propre n'a pas nbformat (mesuré : ModuleNotFoundError).

QUESTION
    Ce notebook est-il sain : cellules en erreur, exécution dans le désordre,
    sorties géantes ?
MESURE
    Lecture du JSON nbformat 4 (module json, taille plafonnée) : cellules par
    type, langage du noyau (kernelspec, language_info), sorties d'erreur
    (output_type « error » : ename, evalue), compteurs d'exécution des cellules de
    code dans l'ordre du document (non croissants, absents alors que d'autres
    cellules ont tourné, premier compteur différent de 1), taille sérialisée des
    sorties de chaque cellule comparée à --seuil-sortie, images embarquées
    (image/png, image/jpeg, image/gif, image/svg+xml, pièces jointes markdown).
    Conformité au schéma : nbformat.validator si nbformat est installé, sinon
    contrôle structurel stdlib des clés obligatoires.
HYPOTHÈSES
    Les compteurs d'exécution enregistrés sont ceux du dernier passage : un
    compteur plus petit que celui d'une cellule située au-dessus signale une
    exécution hors de l'ordre du document. Une cellule de code non vide sans
    compteur, dans un notebook où d'autres ont tourné, n'a pas été exécutée.
LIMITES
    N'exécute rien : un notebook propre peut échouer au prochain lancement
    (données absentes, dépendances). Ne voit ni l'état caché du noyau ni les
    erreurs avalées par try/except ; une trace écrite sur stderr sans sortie
    « error » n'est comptée qu'en « traces_stderr ». nbformat 3 (worksheets) est
    refusé. Le contrôle structurel stdlib est plus pauvre que le schéma nbformat.
CONTRE-EXEMPLES
    Un notebook lancé cellule par cellule, dans l'ordre, dans un noyau qui avait
    déjà servi, porte des compteurs 3, 4 : l'outil le déclare sain (premier
    compteur 3, séquence 1..n fausse) sans pouvoir le distinguer d'un notebook
    dont deux cellules exécutées ont ensuite été supprimées (vérifié sur
    propre.ipynb, compteurs passés à 3 et 4).
INVOCATION
    {outil} --texte '{"nbformat": 4, "nbformat_minor": 4, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}}, "cells": [{"cell_type": "code", "metadata": {}, "execution_count": 2, "source": "x = 1", "outputs": []}, {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": "1/0", "outputs": [{"output_type": "error", "ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": []}]}]}' --json
DOMAINE
    Notebooks Jupyter .ipynb au format nbformat 4, avant commit, revue ou
    publication : décider s'il faut les relancer de bout en bout ou les nettoyer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import nbformat  # type: ignore[import-not-found]
    from nbformat import validator as validateur_nbformat  # type: ignore[import-not-found]
except ImportError:
    nbformat = None
    validateur_nbformat = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)

TYPES_IMAGE = ("image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp", "application/pdf")
TYPES_SORTIE = MappingProxyType({
    "stream": ("name", "text"),
    "display_data": ("data", "metadata"),
    "execute_result": ("data", "metadata", "execution_count"),
    "error": ("ename", "evalue", "traceback"),
})
MOTIF_ID_CELLULE = re.compile(r"[a-zA-Z0-9_-]{1,64}")
LIMITE_LISTE = 50
LIMITE_EXAMINES = 200


class ErreurNotebook(Exception):
    """Entrée illisible ou refusée ; `code` est le code de sortie proposé."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Reglages:
    """Paramètres communs à tous les notebooks examinés."""

    moteur: str
    seuil_sortie: int
    max_octets: int


# --------------------------------------------------------------------------- #
# Outils de base

def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring en sections selon les intitulés du contrat."""
    contrat: dict[str, str] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES:
            courant = tete
            contrat[courant] = ""
        elif courant is not None:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def texte_multiligne(valeur: Any) -> str:
    """Champ nbformat « multiline string » : chaîne ou liste de chaînes."""
    if isinstance(valeur, list):
        return "".join(v for v in valeur if isinstance(v, str))
    return valeur if isinstance(valeur, str) else ""


def taille_json(valeur: Any) -> int:
    """Octets occupés par une valeur une fois sérialisée en JSON (utf-8)."""
    return len(json.dumps(valeur, ensure_ascii=False).encode("utf-8"))


def charger_notebook(donnees: bytes, nom: str) -> dict[str, Any]:
    """Décode le JSON d'un notebook ; refuse ce qui n'en est pas un."""
    try:
        document = json.loads(donnees.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ErreurNotebook(f"{nom} n'est pas du texte utf-8 (octet {exc.start}) : pas un notebook") from exc
    except json.JSONDecodeError as exc:
        raise ErreurNotebook(f"{nom} : JSON invalide, ligne {exc.lineno} colonne {exc.colno} ({exc.msg})") from exc
    except RecursionError as exc:
        raise ErreurNotebook(f"{nom} : JSON trop profondément imbriqué") from exc
    if not isinstance(document, dict) or "cells" not in document and "worksheets" not in document:
        raise ErreurNotebook(f"{nom} : objet JSON sans « cells » : pas un notebook")
    if "worksheets" in document or document.get("nbformat") != 4:
        raise ErreurNotebook(f"{nom} : nbformat {document.get('nbformat')} non pris en charge (attendu 4)")
    return document


def lire_fichier(chemin: Path, plafond: int) -> bytes:
    """Lit un fichier entier après contrôle de sa taille."""
    taille = chemin.stat().st_size
    if taille > plafond:
        raise ErreurNotebook(f"{chemin.name} pèse {taille} octets, au-delà de --max-octets {plafond}", 1)
    return chemin.read_bytes()


# --------------------------------------------------------------------------- #
# Validation de structure

def valider_stdlib(document: dict[str, Any]) -> list[str]:
    """Contrôle structurel minimal du schéma nbformat 4."""
    erreurs = []
    for cle, attendu in (("cells", list), ("metadata", dict), ("nbformat", int), ("nbformat_minor", int)):
        if not isinstance(document.get(cle), attendu):
            erreurs.append(f"racine : « {cle} » absent ou de type incorrect")
    cellules = document.get("cells") if isinstance(document.get("cells"), list) else []
    exige_id = isinstance(document.get("nbformat_minor"), int) and document["nbformat_minor"] >= 5
    for index, cellule in enumerate(cellules):
        erreurs.extend(f"cells[{index}] : {e}" for e in erreurs_cellule(cellule, exige_id))
    return erreurs


def erreurs_cellule(cellule: Any, exige_id: bool) -> list[str]:
    """Clés obligatoires d'une cellule et de ses sorties."""
    if not isinstance(cellule, dict):
        return ["cellule qui n'est pas un objet"]
    erreurs = []
    genre = cellule.get("cell_type")
    if genre not in ("code", "markdown", "raw"):
        erreurs.append(f"cell_type inconnu {genre!r}")
    for cle in ("source", "metadata"):
        if cle not in cellule:
            erreurs.append(f"« {cle} » manquant")
    if exige_id and not (isinstance(cellule.get("id"), str) and MOTIF_ID_CELLULE.fullmatch(cellule["id"])):
        erreurs.append("« id » manquant ou invalide (exigé depuis nbformat 4.5)")
    if genre == "code":
        erreurs.extend(erreurs_code(cellule))
    elif genre in ("markdown", "raw") and "outputs" in cellule:
        erreurs.append(f"cellule {genre} avec « outputs »")
    return erreurs


def erreurs_code(cellule: dict[str, Any]) -> list[str]:
    """Clés propres aux cellules de code et à leurs sorties."""
    erreurs = []
    compteur = cellule.get("execution_count", "absent")
    if compteur == "absent" or not (compteur is None or (isinstance(compteur, int) and compteur >= 0)):
        erreurs.append("« execution_count » absent ou invalide")
    sorties = cellule.get("outputs")
    if not isinstance(sorties, list):
        return erreurs + ["« outputs » absent ou qui n'est pas une liste"]
    for position, sortie in enumerate(sorties):
        genre = sortie.get("output_type") if isinstance(sortie, dict) else None
        if genre not in TYPES_SORTIE:
            erreurs.append(f"outputs[{position}] : output_type inconnu {genre!r}")
            continue
        manquantes = [c for c in TYPES_SORTIE[genre] if c not in sortie]
        erreurs.extend(f"outputs[{position}] : « {c} » manquant" for c in manquantes)
    return erreurs


def valider_nbformat(document: dict[str, Any]) -> list[str]:
    """Erreurs du schéma officiel, avec leur chemin dans le document."""
    erreurs = []
    for erreur in validateur_nbformat.iter_validate(json.loads(json.dumps(document))):
        chemin = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in erreur.absolute_path).lstrip(".")
        erreurs.append(f"{chemin or 'racine'} : {erreur.message[:200]}")
    return erreurs


# --------------------------------------------------------------------------- #
# Analyse

def analyser_notebook(document: dict[str, Any], reglages: Reglages) -> dict[str, Any]:
    """Rapport complet d'un notebook déjà décodé."""
    cellules = [c for c in document.get("cells", []) if isinstance(c, dict)]
    moteur = "nbformat" if reglages.moteur != "stdlib" and validateur_nbformat is not None else "stdlib"
    schema = valider_nbformat(document) if moteur == "nbformat" else valider_stdlib(document)
    rapport = {
        "moteur": moteur,
        "nbformat": f"{document.get('nbformat')}.{document.get('nbformat_minor')}",
        "noyau": decrire_noyau(document.get("metadata")),
        "cellules": dict(Counter(str(c.get("cell_type")) for c in cellules)),
        "cellules_vides": sum(1 for c in cellules if not texte_multiligne(c.get("source")).strip()),
        "execution": analyser_execution(cellules),
        "erreurs": erreurs_sortie(cellules),
        "traces_stderr": traces_stderr(cellules),
        "sorties_volumineuses": sorties_volumineuses(cellules, reglages.seuil_sortie),
        "images": inventaire_images(cellules),
        "taille_sorties_totale": sum(taille_json(c.get("outputs", [])) for c in cellules),
        "schema": {"valide": not schema, "erreurs": schema[:LIMITE_LISTE], "nombre": len(schema)},
    }
    rapport["anomalies"] = anomalies(rapport)
    return rapport


def decrire_noyau(metadonnees: Any) -> dict[str, Any]:
    """Noyau et langage déclarés dans les métadonnées."""
    metadonnees = metadonnees if isinstance(metadonnees, dict) else {}
    noyau = metadonnees.get("kernelspec") if isinstance(metadonnees.get("kernelspec"), dict) else {}
    langage = metadonnees.get("language_info") if isinstance(metadonnees.get("language_info"), dict) else {}
    return {
        "nom": noyau.get("name"),
        "affichage": noyau.get("display_name"),
        "langage": noyau.get("language") or langage.get("name"),
        "version_langage": langage.get("version"),
    }


def analyser_execution(cellules: list[dict[str, Any]]) -> dict[str, Any]:
    """Compteurs d'exécution des cellules de code non vides, dans l'ordre du document."""
    code = [(i, c) for i, c in enumerate(cellules)
            if c.get("cell_type") == "code" and texte_multiligne(c.get("source")).strip()]
    compteurs = [(i, c.get("execution_count")) for i, c in code]
    executees = [(i, n) for i, n in compteurs if isinstance(n, int) and not isinstance(n, bool)]
    desordre = [
        {"cellule": i, "compteur": n, "precedent": precedent}
        for (i, n), (_j, precedent) in zip(executees[1:], executees)
        if n <= precedent
    ]
    non_executees = [i for i, n in compteurs if n is None]
    valeurs = [n for _i, n in executees]
    return {
        "cellules_de_code": len(code),
        "executees": len(executees),
        "non_executees": non_executees[:LIMITE_LISTE] if executees else [],
        "partielle": bool(executees) and bool(non_executees),
        "desordre": desordre[:LIMITE_LISTE],
        "premier_compteur": valeurs[0] if valeurs else None,
        "sequence_1_a_n": valeurs == list(range(1, len(valeurs) + 1)) if valeurs else None,
    }


def erreurs_sortie(cellules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorties output_type « error » : cellule, ename, evalue."""
    erreurs = []
    for index, cellule in enumerate(cellules):
        for sortie in cellule.get("outputs", []) if isinstance(cellule.get("outputs"), list) else []:
            if isinstance(sortie, dict) and sortie.get("output_type") == "error":
                erreurs.append({
                    "cellule": index,
                    "compteur": cellule.get("execution_count"),
                    "ename": str(sortie.get("ename", "")),
                    "evalue": str(sortie.get("evalue", ""))[:300],
                })
    return erreurs


def traces_stderr(cellules: list[dict[str, Any]]) -> list[int]:
    """Cellules dont un flux stderr contient une trace Python."""
    trouvees = []
    for index, cellule in enumerate(cellules):
        for sortie in cellule.get("outputs", []) if isinstance(cellule.get("outputs"), list) else []:
            if (isinstance(sortie, dict) and sortie.get("output_type") == "stream" and sortie.get("name") == "stderr"
                    and "Traceback (most recent call last)" in texte_multiligne(sortie.get("text"))):
                trouvees.append(index)
                break
    return trouvees


def sorties_volumineuses(cellules: list[dict[str, Any]], seuil: int) -> list[dict[str, Any]]:
    """Cellules dont les sorties sérialisées dépassent le seuil."""
    grosses = []
    for index, cellule in enumerate(cellules):
        taille = taille_json(cellule.get("outputs", []))
        if taille > seuil:
            grosses.append({"cellule": index, "octets": taille})
    return grosses


def inventaire_images(cellules: list[dict[str, Any]]) -> dict[str, Any]:
    """Images embarquées dans les sorties et les pièces jointes markdown."""
    compte: Counter = Counter()
    octets = 0
    for cellule in cellules:
        paquets = [s.get("data") for s in cellule.get("outputs", []) if isinstance(s, dict)] \
            if isinstance(cellule.get("outputs"), list) else []
        if isinstance(cellule.get("attachments"), dict):
            paquets.extend(cellule["attachments"].values())
        for paquet in paquets:
            if not isinstance(paquet, dict):
                continue
            for type_mime in TYPES_IMAGE:
                if type_mime in paquet:
                    compte[type_mime] += 1
                    octets += len(texte_multiligne(paquet[type_mime]).encode("utf-8"))
    return {"nombre": sum(compte.values()), "par_type": dict(compte), "octets": octets}


def anomalies(rapport: dict[str, Any]) -> list[str]:
    """Défauts qui rendent le notebook non sain (code de sortie 1)."""
    trouvees = []
    if rapport["erreurs"]:
        trouvees.append(f"{len(rapport['erreurs'])} sortie(s) d'erreur")
    if rapport["execution"]["desordre"]:
        trouvees.append(f"exécution dans le désordre ({len(rapport['execution']['desordre'])} rupture(s))")
    if rapport["execution"]["partielle"]:
        trouvees.append(f"{len(rapport['execution']['non_executees'])} cellule(s) de code non exécutée(s)")
    if rapport["sorties_volumineuses"]:
        trouvees.append(f"{len(rapport['sorties_volumineuses'])} cellule(s) aux sorties volumineuses")
    if not rapport["schema"]["valide"]:
        trouvees.append(f"{rapport['schema']['nombre']} écart(s) au schéma")
    return trouvees


# --------------------------------------------------------------------------- #
# Parcours des entrées

def lister_notebooks(dossier: Path, plafond: int) -> tuple[list[Path], list[str]]:
    """Notebooks d'un dossier (récursif, trié), points de contrôle écartés."""
    retenus, ecartes = [], []
    for chemin in sorted(dossier.rglob("*.ipynb")):
        if not chemin.is_file():
            continue
        relatif = chemin.relative_to(dossier)
        if ".ipynb_checkpoints" in relatif.parts:
            ecartes.append(f"{relatif} (point de contrôle Jupyter)")
        elif len(retenus) < plafond:
            retenus.append(chemin)
        else:
            ecartes.append(f"{relatif} (au-delà de --max-fichiers)")
    return retenus, ecartes


def examiner_chemin(cible: Path, reglages: Reglages, max_fichiers: int) -> dict[str, Any]:
    """Examine un notebook ou tous ceux d'un dossier."""
    if not cible.exists():
        raise ErreurNotebook(f"chemin introuvable : {cible}")
    if cible.is_dir():
        fichiers, ecartes = lister_notebooks(cible, max_fichiers)
        base = cible
    else:
        if cible.suffix.lower() != ".ipynb":
            raise ErreurNotebook(f"{cible.name} : extension « {cible.suffix or '(aucune)'} », attendu .ipynb")
        fichiers, ecartes, base = [cible], [], cible.parent
    notebooks, illisibles = [], []
    for fichier in fichiers:
        nom = str(fichier.relative_to(base))
        try:
            document = charger_notebook(lire_fichier(fichier, reglages.max_octets), nom)
        except (OSError, ErreurNotebook) as exc:
            if not cible.is_dir():
                raise ErreurNotebook(str(exc), getattr(exc, "code", 2)) from exc
            illisibles.append({"fichier": nom, "erreur": str(exc)})
            continue
        notebooks.append({"fichier": nom, "octets": fichier.stat().st_size, **analyser_notebook(document, reglages)})
    return {"notebooks": notebooks, "illisibles": illisibles, "ecartes": ecartes}


def examiner_texte(texte: str, reglages: Reglages) -> dict[str, Any]:
    """Analyse un notebook passé en argument."""
    donnees = texte.encode("utf-8")
    if len(donnees) > reglages.max_octets:
        raise ErreurNotebook(f"--texte dépasse {reglages.max_octets} octets", 1)
    document = charger_notebook(donnees, "--texte")
    rapport = {"fichier": "(--texte)", "octets": len(donnees), **analyser_notebook(document, reglages)}
    return {"notebooks": [rapport], "illisibles": [], "ecartes": []}


def assembler(resultat: dict[str, Any]) -> dict[str, Any]:
    """Objet JSON final ; `denominateur` en tête (notebooks réellement examinés)."""
    notebooks = resultat["notebooks"]
    noms = [n["fichier"] for n in notebooks] + [i["fichier"] for i in resultat["illisibles"]]
    moteurs = sorted({n["moteur"] for n in notebooks}) or ["stdlib"]
    return {
        "denominateur": len(noms),
        "examines": noms[:LIMITE_EXAMINES],
        "examines_tronques": len(noms) > LIMITE_EXAMINES,
        "moteur": "+".join(moteurs),
        "notebooks_sains": sum(1 for n in notebooks if not n["anomalies"]),
        "notebooks_avec_anomalies": sum(1 for n in notebooks if n["anomalies"]),
        "illisibles": resultat["illisibles"],
        "ecartes": resultat["ecartes"],
        "notebooks": notebooks,
        "contrat": extraire_contrat(__doc__ or ""),
    }


# --------------------------------------------------------------------------- #
# Présentation

def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Résumé lisible, un bloc par notebook."""
    print(f"{sortie['denominateur']} notebook(s) examiné(s), moteur {sortie['moteur']} ; "
          f"{sortie['notebooks_sains']} sain(s), {sortie['notebooks_avec_anomalies']} avec anomalie(s)")
    for notebook in sortie["notebooks"]:
        afficher_notebook(notebook)
    for illisible in sortie["illisibles"]:
        print(f"\nILLISIBLE {illisible['fichier']} : {illisible['erreur']}")


def afficher_notebook(notebook: dict[str, Any]) -> None:
    """Bloc lisible d'un notebook."""
    noyau = notebook["noyau"]
    execution = notebook["execution"]
    cellules = ", ".join(f"{n} {t}" for t, n in notebook["cellules"].items()) or "aucune cellule"
    print(f"\n{notebook['fichier']} (nbformat {notebook['nbformat']}, noyau {noyau['nom'] or '?'}, "
          f"langage {noyau['langage'] or '?'} {noyau['version_langage'] or ''}) : {cellules}")
    print(f"  exécution : {execution['executees']}/{execution['cellules_de_code']} cellule(s) de code, "
          f"premier compteur {execution['premier_compteur']}, séquence 1..n : {execution['sequence_1_a_n']}")
    for rupture in execution["desordre"]:
        print(f"  DÉSORDRE cellule {rupture['cellule']} : compteur {rupture['compteur']} après {rupture['precedent']}")
    for erreur in notebook["erreurs"]:
        print(f"  ERREUR cellule {erreur['cellule']} : {erreur['ename']}: {erreur['evalue']}")
    for grosse in notebook["sorties_volumineuses"]:
        print(f"  SORTIE VOLUMINEUSE cellule {grosse['cellule']} : {grosse['octets']} octets")
    if notebook["images"]["nombre"]:
        print(f"  {notebook['images']['nombre']} image(s) embarquée(s), {notebook['images']['octets']} octets")
    for erreur in notebook["schema"]["erreurs"][:10]:
        print(f"  SCHÉMA {erreur}")
    print(f"  verdict : {'; '.join(notebook['anomalies']) or 'sain'}")


# --------------------------------------------------------------------------- #
# Interface

def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    analyseur = argparse.ArgumentParser(
        description="Contrôle la santé d'un notebook Jupyter (.ipynb) : sorties d'erreur, "
                    "exécution dans le désordre ou partielle, sorties volumineuses, images "
                    "embarquées, conformité au schéma. Code 1 si une anomalie est trouvée.",
        epilog="Exemple : python3 lire_notebook.py analyses/ --json",
    )
    analyseur.add_argument("chemin", nargs="?", help="notebook .ipynb, ou dossier où les chercher")
    analyseur.add_argument("--texte", help="contenu JSON d'un notebook (au lieu d'un chemin)")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "nbformat"), default="auto",
                           help="validation du schéma : nbformat s'il est installé (auto), sinon stdlib")
    analyseur.add_argument("--seuil-sortie", type=int, default=100_000,
                           help="octets de sorties sérialisées au-delà desquels une cellule est signalée")
    analyseur.add_argument("--max-octets", type=int, default=256 * 1024 * 1024, help="taille maximale d'un notebook lu")
    analyseur.add_argument("--max-fichiers", type=int, default=1000, help="plafond de notebooks examinés dans un dossier")
    return analyseur


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


def main() -> int:
    """Point d'entrée : 0 sain, 1 anomalie, 2 entrée invalide, 3 rien à examiner."""
    args = construire_analyseur().parse_args()
    if (args.chemin is None) == (args.texte is None):
        print("donner soit un chemin, soit --texte (et pas les deux)", file=sys.stderr)
        return 2
    if args.moteur == "nbformat" and nbformat is None:
        print("--moteur nbformat demandé mais nbformat n'est pas installé", file=sys.stderr)
        return 2
    if args.moteur == "auto" and nbformat is None:
        print("nbformat absent : validation du schéma en mode dégradé stdlib (clés obligatoires seulement)",
              file=sys.stderr)
    reglages = Reglages(args.moteur, max(args.seuil_sortie, 0), max(args.max_octets, 1024))
    try:
        if args.texte is not None:
            resultat = examiner_texte(args.texte, reglages)
        else:
            resultat = examiner_chemin(resoudre(args.chemin, args.racine), reglages, max(args.max_fichiers, 1))
    except ErreurNotebook as exc:
        print(f"lire_notebook : {exc}", file=sys.stderr)
        if args.json:
            afficher_json({"denominateur": 0, "examines": [], "moteur": "stdlib", "erreur": str(exc), "code": exc.code})
        return exc.code
    sortie = assembler(resultat)
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("dénominateur nul : aucun notebook trouvé, rien à examiner", file=sys.stderr)
        return 3
    return 1 if sortie["notebooks_avec_anomalies"] or sortie["illisibles"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
