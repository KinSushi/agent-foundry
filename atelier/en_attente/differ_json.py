"""Ni `diff` ni les bibliothèques courantes ne disent juste ce qui a changé dans un
JSON : `diff` rend 1 et deux lignes changées pour {"a": 1, "b": [1, 2]} contre le
même objet aux clés permutées (mesuré), et sur {"l": [1, 2]} contre
{"l": [true, 2]}, jsonpatch 1.33 rend un patch vide et DeepDiff 9.1.0 un écart vide
(mesuré) : le passage de 1 à true dans un tableau leur échappe.

QUESTION
    Qu'est-ce qui a changé entre ces deux JSON, sous forme de patch applicable ?
MESURE
    Comparaison récursive typée (null, booléen, entier, décimal, chaîne, tableau,
    objet : 1, 1.0 et true sont trois valeurs différentes) ; objets comparés clé par
    clé, tableaux alignés par difflib.SequenceMatcher sur la forme canonique de
    chaque élément. Sortie : patch RFC 6902 (add, remove, replace) avec chemins
    JSON Pointer RFC 6901 (« ~ » écrit ~0, « / » écrit ~1), résumé par opération et
    par clé de premier niveau. Le patch est ensuite appliqué à la source par un
    applicateur stdlib (add, remove, replace, move, copy, test) et le résultat
    comparé strictement à la cible ; jsonpatch, s'il est installé, l'applique aussi
    (second applicateur), et DeepDiff donne un second avis sur « identiques ? ».
    Avec --patch, applique un patch donné et dit s'il reproduit la cible.
HYPOTHÈSES
    Les deux entrées sont du JSON (RFC 8259) ; l'ordre des clés d'un objet n'a pas
    de sens, l'ordre des éléments d'un tableau en a un. Des clés dupliquées sont
    signalées : la dernière valeur l'emporte, comme à la lecture par json.
LIMITES
    Le patch produit est correct (vérifié par application) mais pas toujours
    minimal : un élément déplacé dans un tableau devient remove + add, jamais move.
    Au-delà de --max-produit (taille source × taille cible) un tableau est comparé
    position par position. Les documents sont chargés entiers en mémoire (plafond
    --max-octets). Les nombres sont comparés après lecture par json : 1e400 et
    1e500 deviennent tous deux inf.
CONTRE-EXEMPLES
    Sur [1, 2, 3, 4] contre [4, 1, 2, 3], l'outil rend deux opérations (add /0 puis
    remove /4) là où un seul move suffit (vérifié) : le patch est juste, pas le plus
    court.
INVOCATION
    {outil} --texte-source '{"version": 1, "hotes": ["a", "b"], "tls": true}' --texte-cible '{"version": 2, "hotes": ["a", "c", "b"], "port": 443}' --json
DOMAINE
    Documents JSON tenant en mémoire (configurations, réponses d'API, manifestes,
    fichiers de verrouillage) dont on veut savoir ce qui a changé, ou vérifier qu'un
    patch RFC 6902 produit bien la version attendue.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import jsonpatch  # type: ignore[import-not-found]
except ImportError:
    jsonpatch = None

try:
    from deepdiff import DeepDiff  # type: ignore[import-not-found]
except ImportError:
    DeepDiff = None

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

OPERATIONS = MappingProxyType({"add": ("value",), "remove": (), "replace": ("value",), "move": ("from",),
                               "copy": ("from",), "test": ("value",)})
MOTIF_INDEX = re.compile(r"0|[1-9][0-9]*")
LIMITE_LISTE = 200
LIMITE_APERCU = 120


class ErreurEntree(Exception):
    """Entrée illisible ou patch mal formé (code 2), ou patch inapplicable (code 1)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Etat:
    """Opérations produites et nœuds examinés pendant la comparaison."""

    max_produit: int
    operations: list[dict[str, Any]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    noeuds: int = 0
    positionnels: int = 0
    chemins: list[str] = field(default_factory=list)

    def examiner(self, chemin: str, nombre: int = 1) -> None:
        """Compte un ou plusieurs nœuds examinés et retient les premiers chemins."""
        self.noeuds += nombre
        if len(self.chemins) < LIMITE_LISTE:
            self.chemins.append(chemin or "(racine)")

    def emettre(self, op: str, chemin: str, avant: Any = None, apres: Any = None, *, a_avant: bool, a_apres: bool) -> None:
        """Ajoute une opération RFC 6902 et sa ligne de détail."""
        operation: dict[str, Any] = {"op": op, "path": chemin}
        if a_apres:
            operation["value"] = apres
        self.operations.append(operation)
        detail: dict[str, Any] = {"op": op, "path": chemin}
        if a_avant:
            detail["avant"] = apercu(avant)
        if a_apres:
            detail["apres"] = apercu(apres)
        self.details.append(detail)


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


def apercu(valeur: Any) -> str:
    """Valeur JSON compacte et bornée pour l'affichage."""
    texte = json.dumps(valeur, ensure_ascii=False)
    return texte if len(texte) <= LIMITE_APERCU else texte[: LIMITE_APERCU - 1] + "…"


def categorie(valeur: Any) -> str:
    """Catégorie stricte : un booléen n'est pas un entier, un entier n'est pas un décimal."""
    if valeur is None:
        return "null"
    if isinstance(valeur, bool):
        return "booleen"
    if isinstance(valeur, int):
        return "entier"
    if isinstance(valeur, float):
        return "decimal"
    if isinstance(valeur, str):
        return "chaine"
    return "tableau" if isinstance(valeur, list) else "objet"


def egaux_stricts(gauche: Any, droite: Any) -> bool:
    """Égalité typée et récursive (NaN égal à NaN pour la comparaison de documents)."""
    if categorie(gauche) != categorie(droite):
        return False
    if isinstance(gauche, dict):
        return gauche.keys() == droite.keys() and all(egaux_stricts(gauche[k], droite[k]) for k in gauche)
    if isinstance(gauche, list):
        return len(gauche) == len(droite) and all(egaux_stricts(g, d) for g, d in zip(gauche, droite))
    if isinstance(gauche, float) and gauche != gauche:
        return droite != droite
    return bool(gauche == droite)


def egaux_rfc(gauche: Any, droite: Any) -> bool:
    """Égalité de l'opération test (RFC 6902 §4.6) : nombres comparés par valeur."""
    nombres = ("entier", "decimal")
    if categorie(gauche) in nombres and categorie(droite) in nombres:
        return bool(gauche == droite)
    if categorie(gauche) != categorie(droite):
        return False
    if isinstance(gauche, dict):
        return gauche.keys() == droite.keys() and all(egaux_rfc(gauche[k], droite[k]) for k in gauche)
    if isinstance(gauche, list):
        return len(gauche) == len(droite) and all(egaux_rfc(g, d) for g, d in zip(gauche, droite))
    return bool(gauche == droite)


def canonique(valeur: Any) -> str:
    """Forme canonique d'une valeur, qui distingue 1, 1.0 et true."""
    return json.dumps(valeur, sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# JSON Pointer (RFC 6901)

def echapper(jeton: str) -> str:
    """« ~ » -> ~0 puis « / » -> ~1."""
    return jeton.replace("~", "~0").replace("/", "~1")


def jetons_pointeur(pointeur: str) -> list[str]:
    """Découpe un pointeur ; refuse un échappement autre que ~0 et ~1."""
    if pointeur == "":
        return []
    if not pointeur.startswith("/"):
        raise ErreurEntree(f"pointeur JSON invalide {pointeur!r} : il doit commencer par « / »")
    jetons = []
    for brut in pointeur[1:].split("/"):
        if re.search(r"~(?![01])", brut):
            raise ErreurEntree(f"pointeur JSON invalide {pointeur!r} : « ~ » doit être suivi de 0 ou 1")
        jetons.append(brut.replace("~1", "/").replace("~0", "~"))
    return jetons


def index_tableau(jeton: str, tableau: list[Any], pointeur: str, ajout: bool = False) -> int:
    """Index d'un jeton dans un tableau ; « - » vaut la fin pour un ajout."""
    if ajout and jeton == "-":
        return len(tableau)
    if not MOTIF_INDEX.fullmatch(jeton):
        raise ErreurEntree(f"{pointeur!r} : « {jeton} » n'est pas un index de tableau", 1)
    index = int(jeton)
    if index > len(tableau) or (index == len(tableau) and not ajout):
        raise ErreurEntree(f"{pointeur!r} : index {index} hors du tableau de {len(tableau)} élément(s)", 1)
    return index


def atteindre(document: Any, jetons: list[str], pointeur: str) -> Any:
    """Valeur désignée par une suite de jetons ; erreur si elle n'existe pas."""
    courant = document
    for jeton in jetons:
        if isinstance(courant, dict):
            if jeton not in courant:
                raise ErreurEntree(f"{pointeur!r} : clé « {jeton} » absente", 1)
            courant = courant[jeton]
        elif isinstance(courant, list):
            courant = courant[index_tableau(jeton, courant, pointeur)]
        else:
            raise ErreurEntree(f"{pointeur!r} : « {jeton} » descend dans une valeur {categorie(courant)}", 1)
    return courant


# --------------------------------------------------------------------------- #
# Comparaison

def differ(source: Any, cible: Any, chemin: str, etat: Etat) -> None:
    """Émet les opérations qui transforment `source` en `cible` à l'emplacement `chemin`."""
    etat.examiner(chemin)
    if categorie(source) != categorie(cible):
        etat.emettre("replace", chemin, source, cible, a_avant=True, a_apres=True)
    elif isinstance(source, dict):
        differ_objets(source, cible, chemin, etat)
    elif isinstance(source, list):
        differ_tableaux(source, cible, chemin, etat)
    elif not egaux_stricts(source, cible):
        etat.emettre("replace", chemin, source, cible, a_avant=True, a_apres=True)


def differ_objets(source: dict[str, Any], cible: dict[str, Any], chemin: str, etat: Etat) -> None:
    """Clés retirées, clés communes comparées, clés ajoutées."""
    for cle, valeur in source.items():
        sous_chemin = f"{chemin}/{echapper(cle)}"
        if cle not in cible:
            etat.examiner(sous_chemin)
            etat.emettre("remove", sous_chemin, valeur, a_avant=True, a_apres=False)
        else:
            differ(valeur, cible[cle], sous_chemin, etat)
    for cle, valeur in cible.items():
        if cle not in source:
            etat.examiner(f"{chemin}/{echapper(cle)}")
            etat.emettre("add", f"{chemin}/{echapper(cle)}", apres=valeur, a_avant=False, a_apres=True)


def differ_tableaux(source: list[Any], cible: list[Any], chemin: str, etat: Etat) -> None:
    """Alignement par SequenceMatcher ; au début de chaque bloc, le tableau vaut cible[:j1] + source[i1:]."""
    if len(source) * len(cible) > etat.max_produit:
        etat.positionnels += 1
        differ_par_position(source, cible, chemin, etat)
        return
    alignement = difflib.SequenceMatcher(None, [canonique(e) for e in source], [canonique(e) for e in cible],
                                         autojunk=False)
    for etiquette, i1, i2, j1, j2 in alignement.get_opcodes():
        if etiquette == "equal":
            etat.examiner(f"{chemin}/{j1}" if i2 - i1 == 1 else f"{chemin}/{j1}..{j2 - 1}", i2 - i1)
        else:
            remplacer_bloc(source[i1:i2], cible[j1:j2], j1, chemin, etat)


def remplacer_bloc(anciens: list[Any], nouveaux: list[Any], debut: int, chemin: str, etat: Etat) -> None:
    """Compare paire à paire, puis retire l'excédent de la source ou ajoute celui de la cible."""
    commun = min(len(anciens), len(nouveaux))
    for k in range(commun):
        differ(anciens[k], nouveaux[k], f"{chemin}/{debut + k}", etat)
    for ancien in anciens[commun:]:
        etat.examiner(f"{chemin}/{debut + commun}")
        etat.emettre("remove", f"{chemin}/{debut + commun}", ancien, a_avant=True, a_apres=False)
    for k in range(commun, len(nouveaux)):
        etat.examiner(f"{chemin}/{debut + k}")
        etat.emettre("add", f"{chemin}/{debut + k}", apres=nouveaux[k], a_avant=False, a_apres=True)


def differ_par_position(source: list[Any], cible: list[Any], chemin: str, etat: Etat) -> None:
    """Repli pour les très grands tableaux : comparaison index par index."""
    commun = min(len(source), len(cible))
    for k in range(commun):
        differ(source[k], cible[k], f"{chemin}/{k}", etat)
    for k in range(len(source) - 1, commun - 1, -1):
        etat.examiner(f"{chemin}/{k}")
        etat.emettre("remove", f"{chemin}/{k}", source[k], a_avant=True, a_apres=False)
    for k in range(commun, len(cible)):
        etat.examiner(f"{chemin}/{k}")
        etat.emettre("add", f"{chemin}/{k}", apres=cible[k], a_avant=False, a_apres=True)


# --------------------------------------------------------------------------- #
# Application (RFC 6902)

def valider_patch(patch: Any) -> list[dict[str, Any]]:
    """Le patch est-il une liste d'opérations bien formées ?"""
    if not isinstance(patch, list):
        raise ErreurEntree("un patch RFC 6902 est un tableau JSON d'opérations")
    for rang, operation in enumerate(patch):
        if not isinstance(operation, dict) or operation.get("op") not in OPERATIONS:
            raise ErreurEntree(f"opération {rang} : « op » absent ou inconnu (attendu {', '.join(OPERATIONS)})")
        manquants = [c for c in ("path",) + OPERATIONS[operation["op"]] if c not in operation]
        pointeurs = [operation.get(c) for c in ("path", "from") if c in operation]
        if manquants or not all(isinstance(p, str) for p in pointeurs):
            raise ErreurEntree(f"opération {rang} ({operation['op']}) : membre(s) manquant(s) ou invalide(s) "
                               f"{', '.join(manquants) or 'path'}")
    return patch


def appliquer_patch(document: Any, patch: list[dict[str, Any]]) -> Any:
    """Applique le patch à une copie ; erreur (code 1) à la première opération impossible."""
    resultat = copy.deepcopy(document)
    for rang, operation in enumerate(patch):
        try:
            resultat = appliquer_operation(resultat, operation)
        except ErreurEntree as exc:
            raise ErreurEntree(f"opération {rang} ({operation['op']} {operation['path']}) : {exc}", exc.code) from exc
    return resultat


def appliquer_operation(document: Any, operation: dict[str, Any]) -> Any:
    """Une opération ; rend le document (la racine peut être remplacée)."""
    op, pointeur = operation["op"], operation["path"]
    if op == "test":
        if not egaux_rfc(atteindre(document, jetons_pointeur(pointeur), pointeur), operation["value"]):
            raise ErreurEntree("test échoué : la valeur diffère", 1)
        return document
    if op in ("move", "copy"):
        origine = operation["from"]
        if op == "move" and pointeur.startswith(origine + "/"):
            raise ErreurEntree("move vers un descendant de l'origine", 1)
        valeur = copy.deepcopy(atteindre(document, jetons_pointeur(origine), origine))
        if op == "move":
            document = retirer(document, origine)
        return ajouter(document, pointeur, valeur)
    if op == "remove":
        return retirer(document, pointeur)
    if op == "replace":
        atteindre(document, jetons_pointeur(pointeur), pointeur)
        document = retirer(document, pointeur) if pointeur else None
        return ajouter(document, pointeur, copy.deepcopy(operation["value"]))
    return ajouter(document, pointeur, copy.deepcopy(operation["value"]))


def ajouter(document: Any, pointeur: str, valeur: Any) -> Any:
    """add : crée une clé, insère dans un tableau, ou remplace la racine."""
    jetons = jetons_pointeur(pointeur)
    if not jetons:
        return valeur
    parent = atteindre(document, jetons[:-1], pointeur)
    dernier = jetons[-1]
    if isinstance(parent, dict):
        parent[dernier] = valeur
    elif isinstance(parent, list):
        parent.insert(index_tableau(dernier, parent, pointeur, ajout=True), valeur)
    else:
        raise ErreurEntree(f"{pointeur!r} : le parent est une valeur {categorie(parent)}", 1)
    return document


def retirer(document: Any, pointeur: str) -> Any:
    """remove : la cible doit exister ; la racine ne se retire pas."""
    jetons = jetons_pointeur(pointeur)
    if not jetons:
        raise ErreurEntree("remove de la racine du document", 1)
    parent = atteindre(document, jetons[:-1], pointeur)
    dernier = jetons[-1]
    if isinstance(parent, dict):
        if dernier not in parent:
            raise ErreurEntree(f"{pointeur!r} : clé « {dernier} » absente", 1)
        del parent[dernier]
    elif isinstance(parent, list):
        del parent[index_tableau(dernier, parent, pointeur)]
    else:
        raise ErreurEntree(f"{pointeur!r} : le parent est une valeur {categorie(parent)}", 1)
    return document


# --------------------------------------------------------------------------- #
# Bibliothèques optionnelles

def appliquer_jsonpatch(document: Any, patch: list[dict[str, Any]]) -> tuple[Any, str | None]:
    """Second applicateur ; rend (résultat, erreur)."""
    try:
        return jsonpatch.apply_patch(document, patch, in_place=False), None
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException, ValueError, TypeError, KeyError,
            IndexError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def patch_jsonpatch(source: Any, cible: Any) -> list[dict[str, Any]]:
    """Patch produit par jsonpatch.make_patch (moteur --moteur jsonpatch)."""
    return list(jsonpatch.make_patch(source, cible).patch)


def avis_deepdiff(source: Any, cible: Any, identiques: bool) -> dict[str, Any] | None:
    """Second avis de DeepDiff sur « identiques ? » (None s'il est absent)."""
    if DeepDiff is None:
        return None
    ecart = DeepDiff(source, cible)
    return {"identiques": not ecart, "accord": (not ecart) == identiques}


# --------------------------------------------------------------------------- #
# Entrées

def charger(texte: str, nom: str, alertes: list[str]) -> Any:
    """Décode un JSON ; relève clés dupliquées et constantes non standard."""
    def paires(liste: list[tuple[str, Any]]) -> dict[str, Any]:
        comptes = Counter(cle for cle, _v in liste)
        doublons = sorted(cle for cle, n in comptes.items() if n > 1)
        if doublons:
            alertes.append(f"{nom} : clé(s) dupliquée(s) {', '.join(doublons)} (la dernière valeur l'emporte)")
        return dict(liste)

    def constante(nom_constante: str) -> float:
        alertes.append(f"{nom} : constante non standard {nom_constante} acceptée")
        return float(nom_constante)

    try:
        return json.loads(texte, object_pairs_hook=paires, parse_constant=constante)
    except json.JSONDecodeError as exc:
        raise ErreurEntree(f"{nom} : JSON invalide ligne {exc.lineno} colonne {exc.colno} ({exc.msg})") from exc
    except RecursionError as exc:
        raise ErreurEntree(f"{nom} : JSON trop profondément imbriqué") from exc


def lire_entree(chemin: str | None, texte: str | None, role: str, args: argparse.Namespace, alertes: list[str]) -> tuple[Any, str] | None:
    """Document donné par chemin ou par texte ; None s'il n'est pas donné."""
    if chemin is not None and texte is not None:
        raise ErreurEntree(f"{role} donnée deux fois (chemin et texte)")
    if texte is not None:
        if len(texte.encode("utf-8")) > args.max_octets:
            raise ErreurEntree(f"{role} : texte au-delà de --max-octets {args.max_octets}")
        return charger(texte, f"({role})", alertes), f"({role})"
    if chemin is None:
        return None
    fichier = resoudre(chemin, args.racine)
    return charger(lire_fichier(fichier, args.max_octets), fichier.name, alertes), fichier.name


def lire_fichier(fichier: Path, plafond: int) -> str:
    """Texte utf-8 d'un fichier, borné en taille."""
    if not fichier.exists():
        raise ErreurEntree(f"chemin introuvable : {fichier}")
    if fichier.is_dir():
        raise ErreurEntree(f"{fichier} est un dossier : attendu un fichier JSON")
    taille = fichier.stat().st_size
    if taille > plafond:
        raise ErreurEntree(f"{fichier.name} pèse {taille} octets, au-delà de --max-octets {plafond}")
    try:
        return fichier.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{fichier.name} n'est pas du texte utf-8 (octet {exc.start}) : pas du JSON") from exc


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


# --------------------------------------------------------------------------- #
# Modes

def mode_differ(source: Any, cible: Any, moteur: str, max_produit: int) -> dict[str, Any]:
    """Patch de la source vers la cible, puis vérification par application."""
    etat = Etat(max_produit)
    differ(source, cible, "", etat)
    patch = etat.operations
    details = etat.details
    if moteur == "jsonpatch":
        try:
            patch = patch_jsonpatch(source, cible)
            details = [{"op": o["op"], "path": o["path"]} for o in patch]
        except (jsonpatch.JsonPatchException, TypeError, ValueError, KeyError, IndexError) as exc:
            print(f"jsonpatch.make_patch a échoué ({type(exc).__name__}: {exc}) ; patch produit par la stdlib",
                  file=sys.stderr)
            moteur = "stdlib"
    verification = verifier(source, cible, patch)
    identiques = egaux_stricts(source, cible)
    return {
        "mode": "differ",
        "moteur": moteur,
        "identiques": identiques,
        "operations": len(patch),
        "par_operation": dict(Counter(o["op"] for o in patch)),
        "cles_racine_touchees": sorted({premier_jeton(o["path"]) for o in patch}),
        "noeuds_compares": etat.noeuds,
        "chemins_examines": etat.chemins,
        "tableaux_compares_par_position": etat.positionnels,
        "verification": verification,
        "avis_deepdiff": avis_deepdiff(source, cible, identiques),
        "detail": details[:LIMITE_LISTE],
        "detail_tronque": len(details) > LIMITE_LISTE,
        "patch": patch,
    }


def premier_jeton(pointeur: str) -> str:
    """Clé de premier niveau touchée (« (racine) » pour le document entier)."""
    jetons = jetons_pointeur(pointeur)
    return jetons[0] if jetons else "(racine)"


def verifier(source: Any, cible: Any, patch: list[dict[str, Any]]) -> dict[str, Any]:
    """Le patch reproduit-il la cible ? Applicateur stdlib, puis jsonpatch s'il est là."""
    try:
        stdlib = egaux_stricts(appliquer_patch(source, patch), cible)
        erreur = None
    except ErreurEntree as exc:
        stdlib, erreur = False, str(exc)
    verification: dict[str, Any] = {"stdlib": stdlib, "erreur_stdlib": erreur, "jsonpatch": None}
    if jsonpatch is not None:
        resultat, erreur_jp = appliquer_jsonpatch(source, patch)
        verification["jsonpatch"] = erreur_jp is None and egaux_stricts(resultat, cible)
        verification["erreur_jsonpatch"] = erreur_jp
    return verification


def mode_appliquer(source: Any, cible: tuple[Any, str] | None, patch: list[dict[str, Any]], max_produit: int) -> dict[str, Any]:
    """Applique un patch donné ; compare au besoin le résultat à la cible."""
    resultat = appliquer_patch(source, patch)
    rapport: dict[str, Any] = {"mode": "appliquer", "moteur": "stdlib", "operations": len(patch),
                               "par_operation": dict(Counter(o["op"] for o in patch)), "resultat": resultat}
    if jsonpatch is not None:
        autre, erreur = appliquer_jsonpatch(source, patch)
        rapport["accord_jsonpatch"] = erreur is None and egaux_stricts(autre, resultat)
        rapport["erreur_jsonpatch"] = erreur
    if cible is not None:
        etat = Etat(max_produit)
        differ(resultat, cible[0], "", etat)
        rapport["reproduit_la_cible"] = not etat.operations
        rapport["noeuds_compares"] = etat.noeuds
        rapport["chemins_examines"] = etat.chemins
        rapport["ecart_restant"] = etat.operations[:LIMITE_LISTE]
    return rapport


# --------------------------------------------------------------------------- #
# Sortie

def assembler(rapport: dict[str, Any], noms: list[str], patch: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Objet JSON final ; `denominateur` en tête : nœuds comparés (et opérations appliquées)."""
    chemins = rapport.pop("chemins_examines", [])
    operations = [f"{rang}:{o['op']} {o['path']}" for rang, o in enumerate(patch or [])]
    denominateur = rapport.get("noeuds_compares", 0) + (rapport["operations"] if patch is not None else 0)
    examines = (operations + chemins)[:LIMITE_LISTE]
    return {"denominateur": denominateur, "examines": examines, "examines_tronques": denominateur > len(examines),
            "documents": noms, **rapport, "contrat": extraire_contrat(__doc__ or "")}


def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Résumé lisible : une ligne par opération."""
    if sortie["mode"] == "differ":
        resume = ", ".join(f"{n} {op}" for op, n in sortie["par_operation"].items()) or "aucune différence"
        print(f"{' contre '.join(sortie['documents'])} : {resume} ({sortie['noeuds_compares']} nœuds comparés)")
        for detail in sortie["detail"]:
            avant = f" {detail['avant']}" if "avant" in detail else ""
            apres = f" -> {detail['apres']}" if "apres" in detail else ""
            print(f"  {detail['op']:<7} {detail['path'] or '(racine)'} :{avant}{apres}")
        print(f"vérification par application : stdlib {sortie['verification']['stdlib']}, "
              f"jsonpatch {sortie['verification']['jsonpatch']}")
        return
    print(f"patch de {sortie['operations']} opération(s) appliqué à {sortie['documents'][0]}")
    if "reproduit_la_cible" in sortie:
        print(f"reproduit la cible : {sortie['reproduit_la_cible']}")
        for operation in sortie["ecart_restant"]:
            print(f"  écart restant : {operation['op']} {operation['path']}")


def ecrire_sortie(chemin: str, valeur: Any, racine: str | None) -> None:
    """Écrit le patch (ou le document patché) dans le fichier explicitement demandé."""
    cible = resoudre(chemin, racine)
    if cible.is_dir():
        raise ErreurEntree(f"--sortie {cible} est un dossier")
    try:
        cible.write_text(json.dumps(valeur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ErreurEntree(f"écriture impossible dans {cible} : {exc}") from exc


# --------------------------------------------------------------------------- #
# Interface

def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    analyseur = argparse.ArgumentParser(
        description="Compare deux documents JSON et rend un patch RFC 6902 vérifié par application "
                    "(code 1 s'ils diffèrent) ; avec --patch, applique un patch et dit s'il "
                    "reproduit la cible (code 1 sinon).",
        epilog="Exemple : python3 differ_json.py config_avant.json config_apres.json --json",
    )
    analyseur.add_argument("source", nargs="?", help="document JSON de départ")
    analyseur.add_argument("cible", nargs="?", help="document JSON d'arrivée")
    analyseur.add_argument("--texte-source", help="document de départ passé directement")
    analyseur.add_argument("--texte-cible", help="document d'arrivée passé directement")
    analyseur.add_argument("--patch", help="fichier patch RFC 6902 à appliquer à la source")
    analyseur.add_argument("--texte-patch", help="patch RFC 6902 passé directement")
    analyseur.add_argument("--sortie", help="fichier où écrire le patch (ou, avec --patch, le document patché)")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "jsonpatch"), default="auto",
                           help="générateur du patch ; auto = stdlib, vérifié aussi par jsonpatch s'il est là")
    analyseur.add_argument("--max-octets", type=int, default=256 * 1024 * 1024, help="taille maximale d'une entrée")
    analyseur.add_argument("--max-produit", type=int, default=4_000_000,
                           help="au-delà de (taille source × taille cible), un tableau est comparé par position")
    return analyseur


def annoncer_replis(moteur: str) -> None:
    """Une ligne sur stderr pour les bibliothèques optionnelles absentes."""
    if moteur == "jsonpatch" and jsonpatch is None:
        raise ErreurEntree("--moteur jsonpatch demandé mais jsonpatch n'est pas installé")
    manquantes = [nom for nom, module in (("jsonpatch", jsonpatch), ("deepdiff", DeepDiff)) if module is None]
    if manquantes:
        print(f"{' et '.join(manquantes)} absent(s) : patch vérifié par l'applicateur stdlib seul, "
              "sans second applicateur ni second avis", file=sys.stderr)


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Lit les entrées, choisit le mode, rend (sortie, code)."""
    annoncer_replis(args.moteur)
    args.max_octets = max(args.max_octets, 1024)
    alertes: list[str] = []
    source = lire_entree(args.source, args.texte_source, "source", args, alertes)
    cible = lire_entree(args.cible, args.texte_cible, "cible", args, alertes)
    patch = lire_entree(args.patch, args.texte_patch, "patch", args, alertes)
    for alerte in alertes:
        print(alerte, file=sys.stderr)
    if source is None:
        raise ErreurEntree("source manquante (chemin ou --texte-source)")
    noms = [source[1]] + ([cible[1]] if cible else [])
    if patch is not None:
        operations = valider_patch(patch[0])
        rapport = mode_appliquer(source[0], cible, operations, max(args.max_produit, 1))
        code = 0 if rapport.get("reproduit_la_cible", True) and rapport.get("accord_jsonpatch", True) else 1
        a_ecrire = rapport["resultat"]
    elif cible is None:
        raise ErreurEntree("cible manquante (chemin ou --texte-cible), ou --patch à appliquer")
    else:
        rapport = mode_differ(source[0], cible[0], "jsonpatch" if args.moteur == "jsonpatch" else "stdlib",
                              max(args.max_produit, 1))
        verification = rapport["verification"]
        code = 0 if rapport["identiques"] and verification["stdlib"] else 1
        if not verification["stdlib"]:
            print(f"le patch NE reproduit PAS la cible : {verification['erreur_stdlib'] or 'résultat différent'}",
                  file=sys.stderr)
        avis = rapport["avis_deepdiff"]
        if avis is not None and not avis["accord"]:
            print(f"second avis DeepDiff en désaccord : il juge les documents "
                  f"{'identiques' if avis['identiques'] else 'différents'} (comparaison typée : "
                  f"{'identiques' if rapport['identiques'] else 'différents'})", file=sys.stderr)
        if verification["jsonpatch"] is False:
            print(f"second applicateur jsonpatch en désaccord : {verification.get('erreur_jsonpatch') or 'résultat différent'}",
                  file=sys.stderr)
        a_ecrire = rapport["patch"]
    if args.sortie:
        ecrire_sortie(args.sortie, a_ecrire, args.racine)
    return assembler(rapport, noms, operations if patch is not None else None), code


def main() -> int:
    """Point d'entrée : 0 identiques (ou patch conforme), 1 différences, 2 entrée invalide, 3 rien à examiner."""
    args = construire_analyseur().parse_args()
    try:
        sortie, code = executer(args)
    except ErreurEntree as exc:
        print(f"differ_json : {exc}", file=sys.stderr)
        if args.json:
            afficher_json({"denominateur": 0, "examines": [], "moteur": "stdlib", "erreur": str(exc), "code": exc.code})
        return exc.code
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("dénominateur nul : patch vide et aucune cible, rien à examiner", file=sys.stderr)
        return 3
    return code


if __name__ == "__main__":
    raise SystemExit(main())
