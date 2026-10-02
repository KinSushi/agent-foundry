"""Un modèle ONNX est un message protobuf binaire : sans le paquet onnx, un agent
ne sait ni ce qu'il attend en entrée ni ce qu'il pèse, et l'interpréteur propre de
cette boîte ne l'a pas (mesuré : `python3.14 -c 'import onnx'` rend
ModuleNotFoundError). Le gpt2-10.onnx du dépôt onnx/models (548227537 octets) se
lit ici en 0,1 s sans charger ses poids : 1 entrée, 13 sorties, opset 10, 2581
nœuds, 332 initialiseurs (mesuré avec cet outil, confirmé par onnx 1.23.1).

QUESTION
    Que contient ce modèle ONNX : entrées, sorties, opset, opérateurs, taille des
    initialiseurs, et le graphe est-il cohérent ?
MESURE
    Décodeur protobuf minimal (varint, 64 bits, longueur délimitée, 32 bits) sur
    le fichier projeté en mémoire (mmap), sans copier les poids : ModelProto
    (ir_version, opset_import, producer, domain, metadata_props, functions),
    GraphProto (nœuds et leur op_type, entrées et sorties avec type d'élément et
    forme, initialiseurs : nombre, éléments, octets logiques, octets stockés,
    données externes), sous-graphes des attributs (If, Loop, Scan). Contrôles :
    message bien formé, graphe et ir_version présents, opset_import déclaré,
    raw_data de la taille attendue (dims x bits du type), fichiers de données
    externes présents et assez longs (stat seul), chaque entrée de nœud définie
    plus haut (tri topologique), sorties du graphe produites, noms de sorties
    uniques. Moteur onnx (onnx.load sans données externes, puis onnx.checker) en
    comparaison s'il est installé.
HYPOTHÈSES
    Numéros de champs et types d'éléments : onnx-ml.proto du paquet onnx 1.23.1
    (types d'éléments 0 à 28). Un fichier de données externes se trouve à l'emplacement
    relatif indiqué par « location », à côté du modèle.
LIMITES
    Ne vérifie ni les schémas d'opérateurs, ni les attributs, ni l'inférence de
    formes (c'est le travail d'onnx.checker, appelé seulement si onnx est
    installé). Ne lit pas le contenu des données externes. Les entrées des nœuds
    de sous-graphes peuvent venir de la portée englobante : elles ne sont pas
    contrôlées. Un champ inconnu (version future du format) est ignoré sans bruit.
CONTRE-EXEMPLES
    Un nœud dont l'op_type n'existe dans aucun opset (faute de frappe « ReLU » au
    lieu de « Relu », opset 17) : le moteur stdlib rend code 0 sans défaut, seul
    onnx.checker le refuse (« No Op registered for ReLU », mesuré avec onnx
    1.23.1). À l'inverse, un fichier de données externes amputé de 8 octets passe
    onnx.checker et l'outil le signale ; mais un fichier externe de bonne taille
    aux valeurs fausses passe les deux.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Fichiers .onnx (onnx, onnxruntime, exports PyTorch, TensorFlow, Optimum),
    avant déploiement, quantification ou publication.
"""

from __future__ import annotations

import argparse
import json
import math
import mmap
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import onnx  # type: ignore[import-not-found]
    from google.protobuf.message import DecodeError  # type: ignore[import-not-found]
except ImportError:
    onnx = None
    DecodeError = ValueError

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
FORMAT = "ONNX"

# TensorProto.DataType (onnx-ml.proto, onnx 1.23.1) : identifiant → (nom, bits par élément ; 0 = chaîne).
TYPES_ELEMENT: dict[int, tuple[str, int]] = {
    0: ("UNDEFINED", 0), 1: ("FLOAT", 32), 2: ("UINT8", 8), 3: ("INT8", 8), 4: ("UINT16", 16),
    5: ("INT16", 16), 6: ("INT32", 32), 7: ("INT64", 64), 8: ("STRING", 0), 9: ("BOOL", 8),
    10: ("FLOAT16", 16), 11: ("DOUBLE", 64), 12: ("UINT32", 32), 13: ("UINT64", 64),
    14: ("COMPLEX64", 64), 15: ("COMPLEX128", 128), 16: ("BFLOAT16", 16),
    17: ("FLOAT8E4M3FN", 8), 18: ("FLOAT8E4M3FNUZ", 8), 19: ("FLOAT8E5M2", 8),
    20: ("FLOAT8E5M2FNUZ", 8), 21: ("UINT4", 4), 22: ("INT4", 4), 23: ("FLOAT4E2M1", 4),
    24: ("FLOAT8E8M0", 8), 25: ("UINT2", 2), 26: ("INT2", 2), 27: ("FLOAT6E2M3", 6), 28: ("FLOAT6E3M2", 6),
}
# Numéros de champs utilisés (onnx-ml.proto).
MODELE = {"ir_version": 1, "producer_name": 2, "producer_version": 3, "domain": 4, "model_version": 5,
          "doc_string": 6, "graph": 7, "opset_import": 8, "metadata_props": 14, "functions": 25}
GRAPHE = {"node": 1, "name": 2, "initializer": 5, "input": 11, "output": 12, "sparse_initializer": 15}
NOEUD = {"input": 1, "output": 2, "name": 3, "op_type": 4, "attribute": 5, "domain": 7}
ATTRIBUT = {"g": 6, "graphs": 11}
TENSEUR = {"dims": 1, "data_type": 2, "float_data": 4, "int32_data": 5, "string_data": 6, "int64_data": 7,
           "name": 8, "raw_data": 9, "double_data": 10, "uint64_data": 11, "external_data": 13,
           "data_location": 14}
VARINT, FIXE64, DELIMITE, FIXE32 = 0, 1, 2, 5
DOMAINE_DEFAUT = "ai.onnx"
PROFONDEUR_MAX = 32
LIMITE_EXAMINES = 200
LIMITE_LISTE = 50


class ErreurEntree(Exception):
    """Entrée invalide (chemin absent) : code 2."""


class FormatInvalide(Exception):
    """Le message protobuf est mal formé à cette position."""


@dataclass
class Graphe:
    """Ce que l'outil retient d'un GraphProto."""
    nom: str = ""
    noeuds: list[dict[str, Any]] = field(default_factory=list)
    entrees: list[dict[str, Any]] = field(default_factory=list)
    sorties: list[dict[str, Any]] = field(default_factory=list)
    initialiseurs: list[dict[str, Any]] = field(default_factory=list)
    initialiseurs_creux: list[str] = field(default_factory=list)
    sous_graphes: list[Graphe] = field(default_factory=list)


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


def lire_varint(tampon: Any, position: int, fin: int) -> tuple[int, int]:
    """Entier varint (au plus 10 octets) → (valeur, position suivante)."""
    valeur, decalage = 0, 0
    for rang in range(10):
        if position + rang >= fin:
            raise FormatInvalide(f"varint coupé en position {position}")
        octet = tampon[position + rang]
        valeur |= (octet & 0x7F) << decalage
        if not octet & 0x80:
            return valeur, position + rang + 1
        decalage += 7
    raise FormatInvalide(f"varint de plus de 10 octets en position {position}")


def champs(tampon: Any, debut: int, fin: int) -> Iterator[tuple[int, int, Any]]:
    """Parcourt un message : (numéro, type de fil, valeur entière ou (début, fin) du bloc)."""
    position = debut
    while position < fin:
        cle, position = lire_varint(tampon, position, fin)
        numero, type_fil = cle >> 3, cle & 7
        if numero == 0:
            raise FormatInvalide(f"numéro de champ 0 en position {position}")
        if type_fil == VARINT:
            valeur, position = lire_varint(tampon, position, fin)
        elif type_fil in (FIXE64, FIXE32):
            taille = 8 if type_fil == FIXE64 else 4
            if position + taille > fin:
                raise FormatInvalide(f"champ {numero} coupé en position {position}")
            valeur, position = (position, position + taille), position + taille
        elif type_fil == DELIMITE:
            longueur, position = lire_varint(tampon, position, fin)
            if position + longueur > fin:
                raise FormatInvalide(f"champ {numero} de {longueur} octets dépasse son message (position {position})")
            valeur, position = (position, position + longueur), position + longueur
        else:
            raise FormatInvalide(f"type de fil {type_fil} (groupe obsolète ou octet parasite) en position {position}")
        yield numero, type_fil, valeur


def texte(tampon: Any, bloc: tuple[int, int]) -> str:
    """Chaîne UTF-8 d'un champ délimité."""
    return bytes(tampon[bloc[0]:bloc[1]]).decode("utf-8", errors="replace")


def signe64(valeur: int) -> int:
    """int64 encodé en varint (complément à deux sur 64 bits)."""
    return valeur - (1 << 64) if valeur >= 1 << 63 else valeur


def compter_varints(tampon: Any, bloc: tuple[int, int]) -> int:
    """Nombre de varints d'un champ empaqueté (octets sans bit de continuation)."""
    return sum(1 for i in range(bloc[0], bloc[1]) if not tampon[i] & 0x80)


def entiers_empaquetes(tampon: Any, bloc: tuple[int, int]) -> list[int]:
    """Liste d'int64 d'un champ empaqueté."""
    valeurs, position = [], bloc[0]
    while position < bloc[1]:
        valeur, position = lire_varint(tampon, position, bloc[1])
        valeurs.append(signe64(valeur))
    return valeurs


def lire_paire(tampon: Any, bloc: tuple[int, int]) -> tuple[str, str]:
    """StringStringEntryProto → (clé, valeur)."""
    cle = valeur = ""
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if type_fil == DELIMITE and numero == 1:
            cle = texte(tampon, contenu)
        elif type_fil == DELIMITE and numero == 2:
            valeur = texte(tampon, contenu)
    return cle, valeur


def lire_forme(tampon: Any, bloc: tuple[int, int]) -> list[Any]:
    """TensorShapeProto → dimensions (entier, nom symbolique ou « ? »)."""
    dims: list[Any] = []
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if numero != 1 or type_fil != DELIMITE:
            continue
        dim: Any = "?"
        for sous, type_sous, valeur in champs(tampon, *contenu):
            if sous == 1 and type_sous == VARINT:
                dim = signe64(valeur)
            elif sous == 2 and type_sous == DELIMITE:
                dim = texte(tampon, valeur)
        dims.append(dim)
    return dims


def lire_type(tampon: Any, bloc: tuple[int, int], profondeur: int = 0) -> dict[str, Any]:
    """TypeProto → {genre, element, forme}."""
    if profondeur > PROFONDEUR_MAX:
        raise FormatInvalide("types imbriqués trop profondément")
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if type_fil != DELIMITE:
            continue
        if numero in (1, 8):
            element, forme = 0, None
            for sous, type_sous, valeur in champs(tampon, *contenu):
                if sous == 1 and type_sous == VARINT:
                    element = valeur
                elif sous == 2 and type_sous == DELIMITE:
                    forme = lire_forme(tampon, valeur)
            return {"genre": "tenseur" if numero == 1 else "tenseur_creux",
                    "element": TYPES_ELEMENT.get(element, (f"inconnu_{element}", 0))[0], "forme": forme}
        if numero in (4, 9):
            interne = next((v for s, t, v in champs(tampon, *contenu) if s == 1 and t == DELIMITE), None)
            sous_type = lire_type(tampon, interne, profondeur + 1) if interne else None
            return {"genre": "sequence" if numero == 4 else "optionnel", "element": sous_type}
        if numero == 5:
            return {"genre": "dictionnaire"}
    return {"genre": "inconnu"}


def lire_valeur_info(tampon: Any, bloc: tuple[int, int]) -> dict[str, Any]:
    """ValueInfoProto → {nom, type}."""
    sortie: dict[str, Any] = {"nom": "", "type": None}
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if numero == 1 and type_fil == DELIMITE:
            sortie["nom"] = texte(tampon, contenu)
        elif numero == 2 and type_fil == DELIMITE:
            sortie["type"] = lire_type(tampon, contenu)
    return sortie


def elements_stockes(tampon: Any, numero: int, bloc: tuple[int, int], type_fil: int) -> tuple[int, int]:
    """(éléments, octets) d'un champ de données typé, empaqueté ou non."""
    longueur = bloc[1] - bloc[0] if type_fil == DELIMITE else 0
    if numero == TENSEUR["float_data"]:
        return (longueur // 4, longueur) if type_fil == DELIMITE else (1, 4)
    if numero == TENSEUR["double_data"]:
        return (longueur // 8, longueur) if type_fil == DELIMITE else (1, 8)
    if numero == TENSEUR["string_data"]:
        return 1, longueur
    if type_fil == DELIMITE:
        return compter_varints(tampon, bloc), longueur
    return 1, 0


def lire_tenseur(tampon: Any, bloc: tuple[int, int]) -> dict[str, Any]:
    """TensorProto (initialiseur) sans copier ses données."""
    tenseur: dict[str, Any] = {"nom": "", "dims": [], "type_id": 0, "raw": None, "elements_types": 0,
                               "octets_stockes": 0, "externe": {}, "lieu": 0}
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if numero == TENSEUR["dims"]:
            tenseur["dims"] += entiers_empaquetes(tampon, contenu) if type_fil == DELIMITE else [signe64(contenu)]
        elif numero == TENSEUR["data_type"] and type_fil == VARINT:
            tenseur["type_id"] = contenu
        elif numero == TENSEUR["name"] and type_fil == DELIMITE:
            tenseur["nom"] = texte(tampon, contenu)
        elif numero == TENSEUR["raw_data"] and type_fil == DELIMITE:
            tenseur["raw"] = contenu[1] - contenu[0]
        elif numero == TENSEUR["external_data"] and type_fil == DELIMITE:
            cle, valeur = lire_paire(tampon, contenu)
            tenseur["externe"][cle] = valeur
        elif numero == TENSEUR["data_location"] and type_fil == VARINT:
            tenseur["lieu"] = contenu
        elif numero in (4, 5, 6, 7, 10, 11):
            elements, octets = elements_stockes(tampon, numero, contenu, type_fil)
            tenseur["elements_types"] += elements
            tenseur["octets_stockes"] += octets
    return tenseur


def lire_noeud(tampon: Any, bloc: tuple[int, int], profondeur: int) -> tuple[dict[str, Any], list[Graphe]]:
    """NodeProto → nœud et sous-graphes de ses attributs."""
    noeud: dict[str, Any] = {"op_type": "", "domaine": "", "nom": "", "entrees": [], "sorties": []}
    sous_graphes: list[Graphe] = []
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if type_fil != DELIMITE:
            continue
        if numero == NOEUD["input"]:
            noeud["entrees"].append(texte(tampon, contenu))
        elif numero == NOEUD["output"]:
            noeud["sorties"].append(texte(tampon, contenu))
        elif numero == NOEUD["op_type"]:
            noeud["op_type"] = texte(tampon, contenu)
        elif numero == NOEUD["domain"]:
            noeud["domaine"] = texte(tampon, contenu)
        elif numero == NOEUD["name"]:
            noeud["nom"] = texte(tampon, contenu)
        elif numero == NOEUD["attribute"]:
            sous_graphes += [lire_graphe(tampon, v, profondeur + 1) for s, t, v in champs(tampon, *contenu)
                             if s in ATTRIBUT.values() and t == DELIMITE]
    return noeud, sous_graphes


def lire_graphe(tampon: Any, bloc: tuple[int, int], profondeur: int = 0) -> Graphe:
    """GraphProto → Graphe (sous-graphes compris)."""
    if profondeur > PROFONDEUR_MAX:
        raise FormatInvalide(f"sous-graphes imbriqués sur plus de {PROFONDEUR_MAX} niveaux")
    graphe = Graphe()
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if type_fil != DELIMITE:
            continue
        if numero == GRAPHE["node"]:
            noeud, sous = lire_noeud(tampon, contenu, profondeur)
            graphe.noeuds.append(noeud)
            graphe.sous_graphes.extend(sous)
        elif numero == GRAPHE["name"]:
            graphe.nom = texte(tampon, contenu)
        elif numero == GRAPHE["initializer"]:
            graphe.initialiseurs.append(lire_tenseur(tampon, contenu))
        elif numero == GRAPHE["input"]:
            graphe.entrees.append(lire_valeur_info(tampon, contenu))
        elif numero == GRAPHE["output"]:
            graphe.sorties.append(lire_valeur_info(tampon, contenu))
        elif numero == GRAPHE["sparse_initializer"]:
            valeurs = next((v for s, t, v in champs(tampon, *contenu) if s == 1 and t == DELIMITE), None)
            graphe.initialiseurs_creux.append(lire_tenseur(tampon, valeurs)["nom"] if valeurs else "")
    return graphe


def lire_opset(tampon: Any, bloc: tuple[int, int]) -> dict[str, Any]:
    """OperatorSetIdProto → {domaine, version}."""
    domaine, version = "", None
    for numero, type_fil, contenu in champs(tampon, *bloc):
        if numero == 1 and type_fil == DELIMITE:
            domaine = texte(tampon, contenu)
        elif numero == 2 and type_fil == VARINT:
            version = signe64(contenu)
    return {"domaine": domaine or DOMAINE_DEFAUT, "version": version}


def lire_modele(tampon: Any, taille: int) -> tuple[dict[str, Any], Graphe | None]:
    """ModelProto → champs du modèle et graphe principal."""
    modele: dict[str, Any] = {"ir_version": None, "opset_import": [], "metadonnees": {}, "fonctions": 0}
    graphe: Graphe | None = None
    noms_texte = {MODELE["producer_name"]: "producer_name", MODELE["producer_version"]: "producer_version",
                  MODELE["domain"]: "domain", MODELE["doc_string"]: "doc_string"}
    for numero, type_fil, contenu in champs(tampon, 0, taille):
        if numero == MODELE["ir_version"] and type_fil == VARINT:
            modele["ir_version"] = signe64(contenu)
        elif numero == MODELE["model_version"] and type_fil == VARINT:
            modele["model_version"] = signe64(contenu)
        elif type_fil != DELIMITE:
            continue
        elif numero in noms_texte:
            modele[noms_texte[numero]] = texte(tampon, contenu)[:500]
        elif numero == MODELE["graph"]:
            graphe = lire_graphe(tampon, contenu)
        elif numero == MODELE["opset_import"]:
            modele["opset_import"].append(lire_opset(tampon, contenu))
        elif numero == MODELE["metadata_props"]:
            cle, valeur = lire_paire(tampon, contenu)
            modele["metadonnees"][cle] = valeur[:500]
        elif numero == MODELE["functions"]:
            modele["fonctions"] += 1
    return modele, graphe


def octets_logiques(tenseur: dict[str, Any]) -> int | None:
    """Taille en mémoire attendue : ceil(éléments x bits / 8) ; None pour les chaînes."""
    bits = TYPES_ELEMENT.get(tenseur["type_id"], ("", 0))[1]
    if not bits or any(d < 0 for d in tenseur["dims"]):
        return None
    return -(-math.prod(tenseur["dims"]) * bits // 8)


def verifier_externe(tenseur: dict[str, Any], dossier: Path, attendu: int | None) -> list[str]:
    """Données externes : fichier présent (stat seul) et assez long pour offset + length."""
    lieu = tenseur["externe"].get("location", "")
    nom = tenseur["nom"]
    if not lieu:
        return [f"{nom} : données externes sans « location »"]
    if Path(lieu).is_absolute() or ".." in Path(lieu).parts:
        return [f"{nom} : données externes hors du dossier du modèle ({lieu})"]
    cible = dossier / lieu
    if not cible.is_file():
        return [f"{nom} : fichier de données externes absent ({lieu})"]
    try:
        debut = int(tenseur["externe"].get("offset", "0") or 0)
        longueur = int(tenseur["externe"].get("length", "") or (attendu or 0))
    except ValueError:
        return [f"{nom} : offset ou length externes non entiers"]
    if debut + longueur > cible.stat().st_size:
        return [f"{nom} : {lieu} fait {cible.stat().st_size} octets, il en faut {debut + longueur}"]
    return []


def verifier_initialiseur(tenseur: dict[str, Any], dossier: Path) -> list[str]:
    """raw_data de la bonne taille, ou données externes présentes."""
    attendu = octets_logiques(tenseur)
    tenseur["octets"] = attendu
    if tenseur["lieu"] == 1:
        return verifier_externe(tenseur, dossier, attendu)
    if tenseur["raw"] is not None:
        tenseur["octets_stockes"] = tenseur["raw"]
        if attendu is not None and tenseur["raw"] != attendu:
            return [f"{tenseur['nom']} : raw_data de {tenseur['raw']} octets, {attendu} attendus "
                    f"({TYPES_ELEMENT.get(tenseur['type_id'], ('?', 0))[0]} {tenseur['dims']})"]
    return []


def verifier_graphe(graphe: Graphe) -> list[str]:
    """Tri topologique, entrées définies, sorties produites, sorties uniques."""
    definis = {e["nom"] for e in graphe.entrees} | {t["nom"] for t in graphe.initialiseurs}
    definis |= set(graphe.initialiseurs_creux)
    produits_plus_tard = {s for n in graphe.noeuds for s in n["sorties"]}
    defauts: list[str] = []
    vues: set[str] = set()
    for noeud in graphe.noeuds:
        for entree in noeud["entrees"]:
            if entree and entree not in definis:
                cause = "produite plus loin (graphe non trié topologiquement)" if entree in produits_plus_tard else "jamais définie"
                defauts.append(f"nœud {noeud['nom'] or noeud['op_type']} : entrée « {entree} » {cause}")
        for sortie in noeud["sorties"]:
            if sortie and sortie in vues:
                defauts.append(f"sortie « {sortie} » produite par deux nœuds")
            vues.add(sortie)
        definis.update(noeud["sorties"])
    defauts += [f"sortie du graphe « {s['nom']} » jamais produite" for s in graphe.sorties if s["nom"] not in definis]
    return defauts


def tous_les_noeuds(graphe: Graphe) -> Iterator[dict[str, Any]]:
    """Nœuds des sous-graphes, récursivement."""
    for sous in graphe.sous_graphes:
        yield from sous.noeuds
        yield from tous_les_noeuds(sous)


def resumer_initialiseurs(initialiseurs: list[dict[str, Any]]) -> dict[str, Any]:
    """Nombre, éléments, octets par type, octets externes."""
    par_type: dict[str, dict[str, int]] = {}
    for tenseur in initialiseurs:
        nom = TYPES_ELEMENT.get(tenseur["type_id"], (f"inconnu_{tenseur['type_id']}", 0))[0]
        cumul = par_type.setdefault(nom, {"nombre": 0, "elements": 0, "octets": 0})
        cumul["nombre"] += 1
        cumul["elements"] += math.prod(tenseur["dims"]) if all(d >= 0 for d in tenseur["dims"]) else 0
        cumul["octets"] += tenseur.get("octets") or 0
    externes = [t for t in initialiseurs if t["lieu"] == 1]
    return {
        "nombre": len(initialiseurs),
        "elements": sum(c["elements"] for c in par_type.values()),
        "octets": sum(c["octets"] for c in par_type.values()),
        "externes": len(externes),
        "octets_externes": sum(t.get("octets") or 0 for t in externes),
        "par_type": dict(sorted(par_type.items(), key=lambda kv: -kv[1]["octets"])),
    }


def analyser(chemin: Path) -> dict[str, Any]:
    """Rapport d'un fichier .onnx : lecture, résumé, défauts."""
    rapport: dict[str, Any] = {"fichier": str(chemin), "defauts": [], "remarques": []}
    try:
        taille = chemin.stat().st_size
        rapport["taille_octets"] = taille
        if taille == 0:
            rapport["defauts"].append("fichier vide")
            return rapport
        with chemin.open("rb") as fichier, mmap.mmap(fichier.fileno(), 0, access=mmap.ACCESS_READ) as tampon:
            modele, graphe = lire_modele(tampon, taille)
    except FormatInvalide as exc:
        rapport["defauts"].append(f"protobuf mal formé : {exc} — pas un ModelProto {FORMAT}")
        return rapport
    except (OSError, ValueError) as exc:
        rapport["defauts"].append(f"illisible : {exc}")
        return rapport
    rapport["modele"] = modele
    rapport["defauts"] += controler_modele(modele, graphe)
    if graphe is not None:
        rapport.update(decrire_graphe(graphe, chemin.parent, rapport["defauts"]))
    return rapport


def controler_modele(modele: dict[str, Any], graphe: Graphe | None) -> list[str]:
    """Champs obligatoires du ModelProto."""
    defauts = []
    if graphe is None:
        defauts.append(f"aucun graphe (champ 7) : pas un modèle {FORMAT}")
    if modele["ir_version"] is None:
        defauts.append("ir_version absent (onnx.checker le refuse)")
    elif modele["ir_version"] >= 3 and not modele["opset_import"]:
        defauts.append(f"ir_version {modele['ir_version']} sans opset_import")
    return defauts


def decrire_graphe(graphe: Graphe, dossier: Path, defauts: list[str]) -> dict[str, Any]:
    """Entrées, sorties, opérateurs, initialiseurs ; ajoute les défauts du graphe."""
    for tenseur in graphe.initialiseurs:
        defauts.extend(verifier_initialiseur(tenseur, dossier))
    defauts.extend(verifier_graphe(graphe))
    noms_init = {t["nom"] for t in graphe.initialiseurs}
    operateurs = Counter(f"{n['domaine']}.{n['op_type']}" if n["domaine"] not in ("", DOMAINE_DEFAUT) else n["op_type"]
                         for n in graphe.noeuds)
    internes = list(tous_les_noeuds(graphe))
    return {
        "graphe": graphe.nom,
        "entrees": [{"nom": e["nom"], **(e["type"] or {})} for e in graphe.entrees if e["nom"] not in noms_init][:LIMITE_LISTE],
        "entrees_initialiseurs": sum(1 for e in graphe.entrees if e["nom"] in noms_init),
        "sorties": [{"nom": s["nom"], **(s["type"] or {})} for s in graphe.sorties][:LIMITE_LISTE],
        "noeuds": len(graphe.noeuds),
        "operateurs": dict(operateurs.most_common()),
        "noeuds_sous_graphes": len(internes),
        "sous_graphes": len(graphe.sous_graphes),
        "initialiseurs": resumer_initialiseurs(graphe.initialiseurs),
        "initialiseurs_creux": len(graphe.initialiseurs_creux),
    }


def comparer_bibliotheque(rapport: dict[str, Any]) -> dict[str, Any]:
    """onnx.load (sans données externes) puis onnx.checker ; confronte les résumés."""
    try:
        modele = onnx.load(rapport["fichier"], load_external_data=False)
    except (DecodeError, ValueError, OSError, RuntimeError) as exc:
        refus = f"onnx.load refuse le fichier ({type(exc).__name__}: {str(exc)[:200]})"
        return {"accepte": False, "erreur": refus, "ecarts": [] if rapport["defauts"] else [refus]}
    if rapport["defauts"] and "modele" not in rapport:
        return {"accepte": True, "ecarts": ["onnx.load accepte le fichier alors que le décodeur stdlib le refuse"]}
    ecarts = confronter(modele, rapport)
    verdict = verifier_avec_onnx(rapport["fichier"])
    if verdict and not rapport["defauts"]:
        ecarts.append(f"onnx.checker refuse ce que le moteur stdlib accepte : {verdict}")
    return {"accepte": True, "checker": verdict or "ok", "ecarts": ecarts}


def confronter(modele: Any, rapport: dict[str, Any]) -> list[str]:
    """Écarts entre le ModelProto d'onnx et la lecture stdlib."""
    ecarts = []
    if modele.ir_version != (rapport["modele"]["ir_version"] or 0):
        ecarts.append(f"ir_version : onnx {modele.ir_version} / stdlib {rapport['modele']['ir_version']}")
    opsets = sorted((o.domain or DOMAINE_DEFAUT, o.version) for o in modele.opset_import)
    if opsets != sorted((o["domaine"], o["version"]) for o in rapport["modele"]["opset_import"]):
        ecarts.append(f"opset_import : onnx {opsets}")
    if len(modele.graph.node) != rapport.get("noeuds", 0):
        ecarts.append(f"nœuds : onnx {len(modele.graph.node)} / stdlib {rapport.get('noeuds')}")
    if len(modele.graph.initializer) != rapport.get("initialiseurs", {}).get("nombre", 0):
        ecarts.append(f"initialiseurs : onnx {len(modele.graph.initializer)}")
    sorties = [s.name for s in modele.graph.output]
    if sorties != [s["nom"] for s in rapport.get("sorties", [])][:LIMITE_LISTE] and len(sorties) <= LIMITE_LISTE:
        ecarts.append(f"sorties : onnx {sorties[:5]}")
    return ecarts


def verifier_avec_onnx(chemin: str) -> str:
    """onnx.checker.check_model sur le chemin (gère les données externes) ; '' si valide."""
    try:
        onnx.checker.check_model(chemin)
    except (onnx.checker.ValidationError, ValueError, OSError, RuntimeError) as exc:
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
    return ""


def collecter(cibles: list[Path], recursif: bool, max_fichiers: int) -> list[Path]:
    """Un dossier donne ses .onnx ; un fichier est pris tel quel."""
    fichiers: list[Path] = []
    for cible in cibles:
        if cible.is_dir():
            motif = cible.rglob if recursif else cible.glob
            fichiers.extend(sorted(p for p in motif("*.onnx") if p.is_file()))
        else:
            fichiers.append(cible)
    return fichiers[:max_fichiers]


def choisir_moteur(demande: str) -> str:
    """auto → onnx s'il est importable, sinon stdlib (annoncé sur stderr)."""
    if demande == "onnx" and onnx is None:
        raise ErreurEntree("--moteur onnx demandé mais le paquet onnx n'est pas installé")
    if demande == "auto" and onnx is None:
        print("lire_onnx : paquet onnx absent — moteur stdlib seul (décodeur protobuf minimal, sans onnx.checker)",
              file=sys.stderr)
        return "stdlib"
    return "stdlib" if demande == "stdlib" else "onnx"


def examiner(cibles: list[Path], args: argparse.Namespace, moteur: str) -> dict[str, Any]:
    """Analyse chaque fichier, compare si demandé, assemble la sortie."""
    rapports = [analyser(chemin) for chemin in collecter(cibles, args.recursif, args.max_fichiers)]
    if moteur == "onnx":
        for rapport in rapports:
            rapport["comparaison"] = comparer_bibliotheque(rapport)
    noms = [r["fichier"] for r in rapports]
    return {
        "outil": "lire_onnx", "moteur": moteur, "denominateur": len(noms),
        "examines": noms[:LIMITE_EXAMINES], "examines_tronques": len(noms) > LIMITE_EXAMINES,
        "defauts": sum(len(r["defauts"]) for r in rapports),
        "ecarts_moteurs": sum(len(r.get("comparaison", {}).get("ecarts", [])) for r in rapports),
        "fichiers": rapports, "contrat": extraire_contrat(__doc__ or ""),
    }


def formater_octets(octets: int) -> str:
    """Octets en unité binaire lisible."""
    valeur = float(octets)
    for unite in ("o", "Kio", "Mio", "Gio", "Tio"):
        if valeur < 1024 or unite == "Tio":
            return f"{valeur:.0f} {unite}" if unite == "o" else f"{valeur:.2f} {unite}"
        valeur /= 1024
    return f"{octets} o"


def decrire_valeur(valeur: dict[str, Any]) -> str:
    """« nom : FLOAT [batch, 3, 224, 224] »."""
    if valeur.get("genre") in ("tenseur", "tenseur_creux"):
        forme = valeur.get("forme")
        return f"{valeur['nom']} : {valeur['element']} {forme if forme is not None else '(forme inconnue)'}"
    return f"{valeur['nom']} : {valeur.get('genre', '?')}"


def afficher_fichier(rapport: dict[str, Any]) -> None:
    """Bloc lisible d'un fichier."""
    print(f"\n{rapport['fichier']} ({formater_octets(rapport.get('taille_octets', 0))})")
    modele = rapport.get("modele")
    if modele:
        opsets = ", ".join(f"{o['domaine']} {o['version']}" for o in modele["opset_import"])
        print(f"  ir_version {modele['ir_version']}, opset {opsets}, producteur "
              f"{modele.get('producer_name', '?')} {modele.get('producer_version', '')}")
    if "noeuds" in rapport:
        init = rapport["initialiseurs"]
        print(f"  {rapport['noeuds']} nœuds ({rapport['noeuds_sous_graphes']} dans {rapport['sous_graphes']} sous-graphe(s)), "
              f"{init['nombre']} initialiseurs, {init['elements']:,} éléments, {formater_octets(init['octets'])} "
              f"dont {formater_octets(init['octets_externes'])} externes")
        for entree in rapport["entrees"]:
            print(f"  entrée  {decrire_valeur(entree)}")
        for sortie in rapport["sorties"]:
            print(f"  sortie  {decrire_valeur(sortie)}")
        print("  opérateurs : " + ", ".join(f"{op}×{n}" for op, n in list(rapport["operateurs"].items())[:15]))
    for ligne in rapport["defauts"][:LIMITE_LISTE]:
        print(f"  DÉFAUT {ligne}")
    for ligne in rapport.get("comparaison", {}).get("ecarts", []):
        print(f"  ÉCART MOTEURS {ligne}")


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Rapport lisible."""
    print(f"{sortie['denominateur']} fichier(s) examiné(s), moteur {sortie['moteur']}, {sortie['defauts']} défaut(s), "
          f"{sortie['ecarts_moteurs']} écart(s) entre moteurs")
    for rapport in sortie["fichiers"]:
        afficher_fichier(rapport)


def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def construire_analyseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    analyseur = argparse.ArgumentParser(
        description="Lit un modèle ONNX sans le charger : entrées, sorties, opset, opérateurs, "
                    "initialiseurs, cohérence du graphe.",
        epilog="Exemple : python lire_onnx.py modeles/resnet50-v1-12.onnx --json   (ou un dossier : tous ses .onnx)",
    )
    analyseur.add_argument("chemins", nargs="+", help="fichier .onnx ou dossier")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--recursif", action="store_true", help="descendre dans les sous-dossiers")
    analyseur.add_argument("--max-fichiers", type=int, default=1000, help="plafond de fichiers examinés (défaut 1000)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "onnx"), default="auto",
                           help="auto : comparer avec onnx (et onnx.checker) s'il est installé")
    return analyseur


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


def main() -> int:
    """0 cohérent, 1 défaut trouvé, 2 entrée invalide, 3 rien à examiner."""
    args = construire_analyseur().parse_args()
    cibles = [resoudre(c, args.racine) for c in args.chemins]
    try:
        absents = [str(c) for c in cibles if not c.exists()]
        if absents:
            raise ErreurEntree(f"chemin introuvable : {', '.join(absents)}")
        moteur = choisir_moteur(args.moteur)
    except ErreurEntree as exc:
        print(f"lire_onnx : {exc}", file=sys.stderr)
        return 2
    sortie = examiner(cibles, args, moteur)
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("lire_onnx : dénominateur nul — aucun fichier .onnx trouvé, rien à examiner", file=sys.stderr)
        return 3
    if sortie["defauts"] or sortie["ecarts_moteurs"]:
        print(f"lire_onnx : {sortie['defauts']} défaut(s), {sortie['ecarts_moteurs']} écart(s) entre moteurs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
