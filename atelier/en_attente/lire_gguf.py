"""Un modèle GGUF de plusieurs Gio ne se lit pas comme un texte, et la description
officielle du format retarde sur le code : gguf.md s'arrête au type ggml 39 (MXFP4)
quand ggml.h déclare jusqu'au type 42 (Q2_0), et le paquet gguf 0.19.0 de PyPI
donne 40 octets au bloc Q8_1 que sizeof(block_q8_1) compile à 36 (mesuré le
2026-10-02 sur la branche master de ggml, gcc). Un lecteur fondé sur la seule
documentation se trompe donc de taille ou refuse des fichiers valides.

QUESTION
    Que contient ce modèle GGUF : architecture, contexte, quantification, tenseurs,
    vocabulaire, et llama.cpp pourra-t-il le charger ?
MESURE
    Lecture bornée de l'en-tête seul (struct, jamais les poids) : magie, version
    (2 ou 3, petit ou grand boutiste), nombre de tenseurs et de paires clé-valeur,
    chaque métadonnée typée (tableaux tronqués à l'affichage), puis chaque info de
    tenseur (nom, dimensions, type ggml, décalage). Contrôles repris de la
    spécification gguf.md et du lecteur de référence ggml/src/gguf.cpp : clés
    vides ou dupliquées, booléens hors 0/1, general.alignment puissance de 2 et de
    type uint32, noms de tenseur de moins de 64 octets, au plus 4 dimensions, type
    ggml connu et non retiré, première dimension multiple du bloc, décalages
    alignés et contigus dans l'ordre déclaré, données entièrement présentes dans le
    fichier. Totaux : paramètres, octets et bits par poids par type ggml ;
    identifiants de jetons spéciaux confrontés au vocabulaire et à token_embd ;
    fragments gguf-split présents. Moteur gguf (GGUFReader) en comparaison s'il est
    installé.
HYPOTHÈSES
    Tailles de bloc et d'élément de chaque type ggml : ggml.h et sizeof() des
    structures de ggml-common.h (master du 2026-10-02). Le fichier commence à
    l'octet 0 (pas de GGUF enchâssé dans un autre conteneur). Le boutisme se déduit
    de la version, comme le fait GGUFReader.
LIMITES
    Ne lit aucun poids : une valeur corrompue au milieu des données passe. Un type
    ggml ajouté après le 2026-10-02 est signalé inconnu. Les clés propres à une
    architecture ne sont pas validées (seules les clés générales et
    {arch}.context_length, block_count, embedding_length, attention.head_count(_kv)
    sont résumées). La version 1 du format est refusée, comme par llama.cpp.
CONTRE-EXEMPLES
    ggml-vocab-qwen2.gguf (dépôt llama.cpp) déclare 151936 jetons et aucun tenseur :
    l'outil le dit intègre (code 0) alors qu'aucun modèle n'y est chargeable, c'est
    un fichier de vocabulaire seul. Une métadonnée booléenne de valeur 2 : l'outil
    arrête la lecture et rend un défaut (gguf.md la déclare invalide) alors que
    gguf.cpp (ggml 353b63b, compilé ici) et GGUFReader 0.19.0 ouvrent le fichier.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Fichiers .gguf v2 et v3 (llama.cpp, ollama, LM Studio), entiers ou découpés par
    gguf-split, avant chargement, copie ou publication.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import gguf  # type: ignore[import-not-found]
except ImportError:
    gguf = None

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
MAGIE = "GGUF"
TYPE_MXFP4 = "MXFP4"
TYPE_Q2_0 = "Q2_0"
TYPE_Q8_1 = "Q8_1"

# ggml_type : identifiant → (nom, éléments par bloc, octets par bloc).
# Identifiants : ggml.h ; tailles : sizeof(block_*) compilé depuis ggml-common.h.
TYPES_GGML: dict[int, tuple[str, int, int]] = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1), 25: ("I16", 1, 2),
    26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8), 29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17), 40: ("NVFP4", 64, 36), 41: ("Q1_0", 128, 18), 42: ("Q2_0", 64, 18),
}
TYPES_RETIRES = {4: "Q4_2", 5: "Q4_3", 31: "Q4_0_4_4", 32: "Q4_0_4_8", 33: "Q4_0_8_8",
                 36: "IQ4_NL_4_4", 37: "IQ4_NL_4_8", 38: "IQ4_NL_8_8"}
# gguf_metadata_value_type : identifiant → (nom, code struct ; "" = chaîne ou tableau).
TYPES_VALEUR: dict[int, tuple[str, str]] = {
    0: ("UINT8", "B"), 1: ("INT8", "b"), 2: ("UINT16", "H"), 3: ("INT16", "h"),
    4: ("UINT32", "I"), 5: ("INT32", "i"), 6: ("FLOAT32", "f"), 7: ("BOOL", "B"),
    8: ("STRING", ""), 9: ("ARRAY", ""), 10: ("UINT64", "Q"), 11: ("INT64", "q"), 12: ("FLOAT64", "d"),
}
TYPE_CHAINE, TYPE_TABLEAU, TYPE_BOOL, TYPE_UINT32 = 8, 9, 7, 4
# llama_ftype (include/llama.h) pour general.file_type.
TYPES_FICHIER = {
    0: "ALL_F32", 1: "MOSTLY_F16", 2: "MOSTLY_Q4_0", 3: "MOSTLY_Q4_1", 7: "MOSTLY_Q8_0",
    8: "MOSTLY_Q5_0", 9: "MOSTLY_Q5_1", 10: "MOSTLY_Q2_K", 11: "MOSTLY_Q3_K_S",
    12: "MOSTLY_Q3_K_M", 13: "MOSTLY_Q3_K_L", 14: "MOSTLY_Q4_K_S", 15: "MOSTLY_Q4_K_M",
    16: "MOSTLY_Q5_K_S", 17: "MOSTLY_Q5_K_M", 18: "MOSTLY_Q6_K", 19: "MOSTLY_IQ2_XXS",
    20: "MOSTLY_IQ2_XS", 21: "MOSTLY_Q2_K_S", 22: "MOSTLY_IQ3_XS", 23: "MOSTLY_IQ3_XXS",
    24: "MOSTLY_IQ1_S", 25: "MOSTLY_IQ4_NL", 26: "MOSTLY_IQ3_S", 27: "MOSTLY_IQ3_M",
    28: "MOSTLY_IQ2_S", 29: "MOSTLY_IQ2_M", 30: "MOSTLY_IQ4_XS", 31: "MOSTLY_IQ1_M",
    32: "MOSTLY_BF16", 36: "MOSTLY_TQ1_0", 37: "MOSTLY_TQ2_0", 38: "MOSTLY_MXFP4_MOE",
    39: "MOSTLY_NVFP4", 40: "MOSTLY_Q1_0", 41: "MOSTLY_Q2_0", 1024: "GUESSED",
}
CLES_JETONS = ("bos", "eos", "eot", "eom", "unknown", "seperator", "padding", "mask")
CLES_ARCHITECTURE = ("context_length", "block_count", "embedding_length", "feed_forward_length",
                     "attention.head_count", "attention.head_count_kv", "expert_count", "vocab_size")
VERSIONS_LUES = (2, 3)
ALIGNEMENT_DEFAUT = 32
LONGUEUR_CLE_MAX = 65535
NOM_TENSEUR_MAX = 64
DIMENSIONS_MAX = 4
ELEMENTS_TABLEAU_MAX = 1 << 30
PROFONDEUR_MAX = 4
PLAFOND_ENTETE = 1 << 30
LIMITE_EXAMINES = 200
LIMITE_APERCU = 8
LIMITE_DEFAUTS = 50
MOTIF_FRAGMENT = re.compile(r"^(?P<base>.+)-(?P<no>\d{5})-of-(?P<total>\d{5})\.gguf$")
MOTIF_BLOC = re.compile(r"^blk\.(\d+)\.")


class ErreurEntree(Exception):
    """Entrée invalide (chemin absent) : code 2."""


class FormatInvalide(Exception):
    """L'en-tête ne peut plus être lu au-delà de ce point."""


@dataclass
class Flux:
    """Fichier ouvert en lecture binaire, boutisme et bornes de lecture."""
    fichier: BinaryIO
    taille: int
    plafond: int
    ordre: str = "<"


@dataclass
class Rapport:
    """Ce qu'on sait d'un fichier GGUF après lecture de son en-tête."""
    fichier: str
    taille_octets: int = 0
    version: int | None = None
    boutisme: str = "petit"
    nombre_tenseurs_declare: int = 0
    nombre_metadonnees_declare: int = 0
    metadonnees: dict[str, Any] = field(default_factory=dict)
    jetons: list[str] | None = None
    tenseurs: list[dict[str, Any]] = field(default_factory=list)
    alignement: int = ALIGNEMENT_DEFAUT
    debut_donnees: int = 0
    defauts: list[str] = field(default_factory=list)
    incoherences: list[str] = field(default_factory=list)
    remarques: list[str] = field(default_factory=list)


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


def lire_brut(flux: Flux, nombre: int) -> bytes:
    """Lit exactement `nombre` octets, sans dépasser la fin du fichier ni le plafond."""
    position = flux.fichier.tell()
    if nombre > flux.taille - position:
        raise FormatInvalide(f"lecture de {nombre} octets en position {position} : au-delà de la fin "
                             f"du fichier ({flux.taille} octets, tronqué ou corrompu)")
    if position + nombre > flux.plafond:
        raise FormatInvalide(f"en-tête au-delà du plafond de {flux.plafond} octets (--max-entete)")
    return flux.fichier.read(nombre)


def lire_nombre(flux: Flux, code: str) -> Any:
    """Un scalaire au format struct dans le boutisme du fichier."""
    return struct.unpack(flux.ordre + code, lire_brut(flux, struct.calcsize(code)))[0]


def lire_chaine(flux: Flux, limite: int = ELEMENTS_TABLEAU_MAX) -> str:
    """gguf_string_t : longueur uint64 puis octets UTF-8 (remplacés si invalides)."""
    longueur = lire_nombre(flux, "Q")
    if longueur > limite:
        raise FormatInvalide(f"chaîne annoncée de {longueur} octets, plafond {limite}")
    return lire_brut(flux, longueur).decode("utf-8", errors="replace")


def lire_scalaire(flux: Flux, type_valeur: int) -> Any:
    """Valeur scalaire typée ; un booléen hors 0/1 est un défaut de format."""
    valeur = lire_nombre(flux, TYPES_VALEUR[type_valeur][1])
    if type_valeur == TYPE_BOOL:
        if valeur not in (0, 1):
            raise FormatInvalide(f"booléen de valeur {valeur} : invalide selon gguf.md (0 ou 1), lecture arrêtée")
        return bool(valeur)
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return str(valeur)
    return valeur


def lire_tableau(flux: Flux, profondeur: int, garder: bool, apercu: int) -> dict[str, Any]:
    """Tableau typé : longueur, aperçu, et la liste entière si `garder`."""
    sous_type = lire_nombre(flux, "I")
    longueur = lire_nombre(flux, "Q")
    if sous_type not in TYPES_VALEUR:
        raise FormatInvalide(f"tableau de type de valeur inconnu {sous_type}")
    if longueur > ELEMENTS_TABLEAU_MAX:
        raise FormatInvalide(f"tableau de {longueur} éléments, plafond {ELEMENTS_TABLEAU_MAX}")
    nom = TYPES_VALEUR[sous_type][0]
    if TYPES_VALEUR[sous_type][1] and sous_type != TYPE_BOOL:
        return lire_tableau_numerique(flux, sous_type, longueur, apercu)
    elements = [lire_valeur(flux, sous_type, profondeur + 1, apercu)["valeur"] if sous_type == TYPE_TABLEAU
                else (lire_chaine(flux) if sous_type == TYPE_CHAINE else lire_scalaire(flux, sous_type))
                for _ in range(longueur)]
    sortie = {"type": f"ARRAY[{nom}]", "longueur": longueur, "valeur": elements[:apercu]}
    if garder:
        sortie["complet"] = elements
    return sortie


def lire_tableau_numerique(flux: Flux, sous_type: int, longueur: int, apercu: int) -> dict[str, Any]:
    """Tableau de nombres lu d'un bloc ; seuls les premiers sont décodés."""
    code = TYPES_VALEUR[sous_type][1]
    brut = lire_brut(flux, longueur * struct.calcsize(code))
    tete = min(longueur, apercu)
    valeurs = list(struct.unpack_from(f"{flux.ordre}{tete}{code}", brut)) if tete else []
    return {"type": f"ARRAY[{TYPES_VALEUR[sous_type][0]}]", "longueur": longueur, "valeur": valeurs}


def lire_valeur(flux: Flux, type_valeur: int, profondeur: int, apercu: int, garder: bool = False) -> dict[str, Any]:
    """Valeur de métadonnée : {type, valeur} (+ longueur pour un tableau)."""
    if type_valeur not in TYPES_VALEUR:
        raise FormatInvalide(f"type de valeur inconnu {type_valeur}")
    if type_valeur == TYPE_TABLEAU:
        if profondeur >= PROFONDEUR_MAX:
            raise FormatInvalide(f"tableaux imbriqués sur plus de {PROFONDEUR_MAX} niveaux")
        return lire_tableau(flux, profondeur, garder, apercu)
    if type_valeur == TYPE_CHAINE:
        return {"type": "STRING", "valeur": lire_chaine(flux)}
    return {"type": TYPES_VALEUR[type_valeur][0], "valeur": lire_scalaire(flux, type_valeur)}


def lire_preambule(flux: Flux, rapport: Rapport) -> None:
    """Magie, version (et boutisme), nombres de tenseurs et de métadonnées."""
    magie = lire_brut(flux, 4)
    if magie != MAGIE.encode("ascii"):
        raise FormatInvalide(f"magie {magie!r} au lieu de {MAGIE!r} : pas un fichier GGUF")
    brut = lire_brut(flux, 4)
    version = struct.unpack("<I", brut)[0]
    if version & 0xFFFF == 0:
        flux.ordre, rapport.boutisme = ">", "grand"
        version = struct.unpack(">I", brut)[0]
    rapport.version = version
    if version not in VERSIONS_LUES:
        precision = " ; la version 1 est refusée par llama.cpp" if version == 1 else ""
        raise FormatInvalide(f"version {version} non lue (2 ou 3 attendue{precision})")
    rapport.nombre_tenseurs_declare = lire_nombre(flux, "Q")
    rapport.nombre_metadonnees_declare = lire_nombre(flux, "Q")
    restant = flux.taille - flux.fichier.tell()
    if rapport.nombre_metadonnees_declare * 13 > restant or rapport.nombre_tenseurs_declare * 33 > restant:
        raise FormatInvalide(f"{rapport.nombre_tenseurs_declare} tenseurs et {rapport.nombre_metadonnees_declare} "
                             f"métadonnées annoncés : impossible dans les {restant} octets restants")


def lire_metadonnees(flux: Flux, rapport: Rapport, apercu: int) -> None:
    """Paires clé-valeur ; clés vides ou dupliquées refusées par gguf.cpp."""
    for rang in range(rapport.nombre_metadonnees_declare):
        cle = lire_chaine(flux, LONGUEUR_CLE_MAX)
        if not cle:
            rapport.defauts.append(f"métadonnée n°{rang} : clé vide")
        if cle in rapport.metadonnees:
            rapport.defauts.append(f"clé dupliquée : {cle}")
        type_valeur = lire_nombre(flux, "I")
        valeur = lire_valeur(flux, type_valeur, 0, apercu, garder=cle == "tokenizer.ggml.tokens")
        if valeur["type"].startswith("ARRAY[ARRAY"):
            rapport.defauts.append(f"{cle} : tableau de tableaux, conforme à gguf.md mais refusé par gguf.cpp")
        if cle == "tokenizer.ggml.tokens" and "complet" in valeur:
            rapport.jetons = valeur.pop("complet")
        rapport.metadonnees[cle] = valeur


def fixer_alignement(rapport: Rapport) -> None:
    """general.alignment : uint32, puissance de 2 non nulle (défaut 32)."""
    entree = rapport.metadonnees.get("general.alignment")
    if entree is None:
        return
    if entree["type"] != TYPES_VALEUR[TYPE_UINT32][0]:
        rapport.defauts.append(f"general.alignment de type {entree['type']} (UINT32 exigé par gguf.cpp)")
    valeur = entree["valeur"]
    if not isinstance(valeur, int) or valeur <= 0 or valeur & (valeur - 1):
        rapport.defauts.append(f"general.alignment = {valeur!r} n'est pas une puissance de 2 : 32 retenu")
        return
    rapport.alignement = valeur


def lire_info_tenseur(flux: Flux, rapport: Rapport) -> dict[str, Any]:
    """gguf_tensor_info_t : nom, dimensions, type ggml, décalage."""
    nom = lire_chaine(flux, LONGUEUR_CLE_MAX)
    n_dims = lire_nombre(flux, "I")
    if n_dims > DIMENSIONS_MAX:
        rapport.defauts.append(f"{nom} : {n_dims} dimensions (au plus {DIMENSIONS_MAX})")
        if n_dims > 64:
            raise FormatInvalide(f"{nom} : {n_dims} dimensions, lecture abandonnée")
    dims = [lire_nombre(flux, "Q") for _ in range(n_dims)]
    type_ggml = lire_nombre(flux, "I")
    decalage = lire_nombre(flux, "Q")
    if len(nom.encode("utf-8")) >= NOM_TENSEUR_MAX:
        rapport.defauts.append(f"{nom[:40]}… : nom de {len(nom.encode('utf-8'))} octets, gguf.cpp exige moins de {NOM_TENSEUR_MAX}")
    return {"nom": nom, "dimensions": dims, "type_id": type_ggml, "decalage": decalage}


def mesurer_tenseur(tenseur: dict[str, Any], rapport: Rapport) -> None:
    """Type, éléments et octets d'un tenseur, d'après la table ggml."""
    type_id, dims, nom = tenseur["type_id"], tenseur["dimensions"], tenseur["nom"]
    tenseur["elements"] = math.prod(dims)
    if any(d >= 1 << 63 for d in dims):
        rapport.defauts.append(f"{nom} : dimension négative en int64 {dims}")
    if type_id in TYPES_RETIRES:
        tenseur["type"] = TYPES_RETIRES[type_id]
        rapport.defauts.append(f"{nom} : type ggml {type_id} ({TYPES_RETIRES[type_id]}) retiré des fichiers GGUF")
        return
    if type_id not in TYPES_GGML:
        tenseur["type"] = f"inconnu_{type_id}"
        rapport.defauts.append(f"{nom} : type ggml {type_id} inconnu (table au 2026-10-02 : 0 à 42)")
        return
    nom_type, bloc, octets = TYPES_GGML[type_id]
    tenseur["type"] = nom_type
    if dims and dims[0] % bloc:
        rapport.defauts.append(f"{nom} : première dimension {dims[0]} non multiple du bloc {nom_type} ({bloc})")
        return
    tenseur["octets"] = tenseur["elements"] // bloc * octets


def aligner(position: int, alignement: int) -> int:
    """align_offset de gguf.md."""
    return position + (alignement - position % alignement) % alignement


def verifier_donnees(rapport: Rapport) -> None:
    """Décalages alignés, contigus dans l'ordre déclaré (gguf.cpp), données dans le fichier."""
    attendu = 0
    for tenseur in rapport.tenseurs:
        if "octets" not in tenseur:
            return
        nom, decalage = tenseur["nom"], tenseur["decalage"]
        if decalage % rapport.alignement:
            rapport.defauts.append(f"{nom} : décalage {decalage} non aligné sur {rapport.alignement}")
        elif decalage != attendu:
            rapport.defauts.append(f"{nom} : décalage {decalage}, gguf.cpp attend {attendu} (ordre ou bourrage incohérent)")
        fin = rapport.debut_donnees + decalage + tenseur["octets"]
        if fin > rapport.taille_octets:
            rapport.defauts.append(f"{nom} : données jusqu'à l'octet {fin}, fichier de {rapport.taille_octets} "
                                   f"octets (tronqué, téléchargement incomplet ?)")
            return
        attendu = decalage + aligner(tenseur["octets"], rapport.alignement)
    fin_attendue = rapport.debut_donnees + attendu
    if rapport.tenseurs and rapport.taille_octets > fin_attendue:
        rapport.remarques.append(f"{rapport.taille_octets - fin_attendue} octets après le dernier tenseur")


def lire_en_tete(chemin: Path, plafond: int, apercu: int) -> Rapport:
    """Lit tout l'en-tête ; une erreur de format arrête la lecture et devient un défaut."""
    rapport = Rapport(fichier=str(chemin))
    try:
        rapport.taille_octets = chemin.stat().st_size
        with chemin.open("rb") as fichier:
            flux = Flux(fichier, rapport.taille_octets, plafond)
            lire_preambule(flux, rapport)
            lire_metadonnees(flux, rapport, apercu)
            fixer_alignement(rapport)
            for _ in range(rapport.nombre_tenseurs_declare):
                rapport.tenseurs.append(lire_info_tenseur(flux, rapport))
            rapport.debut_donnees = aligner(fichier.tell(), rapport.alignement)
    except FormatInvalide as exc:
        rapport.defauts.append(str(exc))
        return rapport
    except OSError as exc:
        rapport.defauts.append(f"illisible : {exc.strerror or exc}")
        return rapport
    verifier_tenseurs(rapport)
    return rapport


def verifier_tenseurs(rapport: Rapport) -> None:
    """Noms uniques, tailles, données, puis cohérence avec les métadonnées."""
    vus: set[str] = set()
    for tenseur in rapport.tenseurs:
        if tenseur["nom"] in vus:
            rapport.defauts.append(f"tenseur dupliqué : {tenseur['nom']}")
        vus.add(tenseur["nom"])
        mesurer_tenseur(tenseur, rapport)
    verifier_donnees(rapport)
    verifier_metadonnees(rapport)


def valeur_meta(rapport: Rapport, cle: str) -> Any:
    """Valeur d'une métadonnée scalaire, ou None."""
    entree = rapport.metadonnees.get(cle)
    return None if entree is None else entree["valeur"]


def verifier_metadonnees(rapport: Rapport) -> None:
    """Architecture présente, jetons spéciaux dans le vocabulaire, couches complètes."""
    if rapport.boutisme != ("petit" if sys.byteorder == "little" else "grand"):
        rapport.incoherences.append(f"fichier {rapport.boutisme}-boutiste sur une machine {sys.byteorder}-endian : "
                                    "gguf.cpp le refusera ici")
    if valeur_meta(rapport, "general.architecture") is None:
        rapport.incoherences.append("clé obligatoire general.architecture absente (llama.cpp refusera)")
    taille_vocab = len(rapport.jetons) if rapport.jetons is not None else None
    for role in CLES_JETONS:
        ident = valeur_meta(rapport, f"tokenizer.ggml.{role}_token_id")
        if isinstance(ident, int) and taille_vocab is not None and not 0 <= ident < taille_vocab:
            rapport.incoherences.append(f"tokenizer.ggml.{role}_token_id = {ident} hors du vocabulaire ({taille_vocab} jetons)")
    embarque = next((t for t in rapport.tenseurs if t["nom"] == "token_embd.weight"), None)
    if embarque and len(embarque["dimensions"]) == 2 and taille_vocab is not None:
        lignes = embarque["dimensions"][1]
        if taille_vocab > lignes:
            rapport.incoherences.append(f"{taille_vocab} jetons pour {lignes} lignes de token_embd.weight : identifiants hors matrice")
        elif taille_vocab < lignes:
            rapport.remarques.append(f"token_embd.weight a {lignes} lignes pour {taille_vocab} jetons (bourrage)")
    verifier_couches(rapport)
    rapport.incoherences.extend(verifier_fragment(rapport))


def verifier_couches(rapport: Rapport) -> None:
    """block_count face aux tenseurs blk.N présents."""
    architecture = valeur_meta(rapport, "general.architecture")
    blocs = valeur_meta(rapport, f"{architecture}.block_count")
    indices = {int(m.group(1)) for t in rapport.tenseurs if (m := MOTIF_BLOC.match(t["nom"]))}
    if not isinstance(blocs, int) or not indices or valeur_meta(rapport, "split.count"):
        return
    manquants = sorted(set(range(blocs)) - indices)
    if manquants:
        rapport.remarques.append(f"{architecture}.block_count = {blocs} mais aucun tenseur pour les couches {manquants[:8]}")
    if max(indices) >= blocs:
        rapport.remarques.append(f"tenseurs blk.{max(indices)} au-delà de block_count = {blocs}")


def resumer(rapport: Rapport) -> dict[str, Any]:
    """Architecture, contexte, quantification, vocabulaire."""
    architecture = valeur_meta(rapport, "general.architecture")
    resume: dict[str, Any] = {
        "architecture": architecture,
        "nom": valeur_meta(rapport, "general.name"),
        "type_fichier": TYPES_FICHIER.get(valeur_meta(rapport, "general.file_type"), valeur_meta(rapport, "general.file_type")),
        "version_quantification": valeur_meta(rapport, "general.quantization_version"),
        "alignement": rapport.alignement,
    }
    for cle in CLES_ARCHITECTURE:
        resume[cle.replace(".", "_")] = valeur_meta(rapport, f"{architecture}.{cle}")
    resume["tokenizer"] = valeur_meta(rapport, "tokenizer.ggml.model")
    resume["jetons"] = len(rapport.jetons) if rapport.jetons is not None else None
    for role in ("bos", "eos", "eot", "padding"):
        ident = valeur_meta(rapport, f"tokenizer.ggml.{role}_token_id")
        texte = rapport.jetons[ident] if isinstance(ident, int) and rapport.jetons and 0 <= ident < len(rapport.jetons) else None
        resume[f"{role}_token"] = None if ident is None else {"id": ident, "texte": texte}
    gabarit = valeur_meta(rapport, "tokenizer.chat_template")
    resume["gabarit_chat_octets"] = len(gabarit.encode("utf-8")) if isinstance(gabarit, str) else None
    return resume


def quantification(rapport: Rapport) -> dict[str, Any]:
    """Tenseurs, paramètres, octets et bits par poids, par type ggml."""
    par_type: dict[str, dict[str, Any]] = {}
    for tenseur in rapport.tenseurs:
        cumul = par_type.setdefault(tenseur.get("type", "?"), {"tenseurs": 0, "parametres": 0, "octets": 0})
        cumul["tenseurs"] += 1
        cumul["parametres"] += tenseur.get("elements", 0)
        cumul["octets"] += tenseur.get("octets", 0)
    for cumul in par_type.values():
        cumul["bits_par_poids"] = round(cumul["octets"] * 8 / cumul["parametres"], 3) if cumul["parametres"] else None
    parametres = sum(c["parametres"] for c in par_type.values())
    octets = sum(c["octets"] for c in par_type.values())
    return {"parametres": parametres, "octets_tenseurs": octets,
            "bits_par_poids": round(octets * 8 / parametres, 3) if parametres else None,
            "par_type": dict(sorted(par_type.items(), key=lambda kv: -kv[1]["octets"]))}


def comparer_bibliotheque(rapport: Rapport) -> dict[str, Any]:
    """Relit le fichier avec gguf.GGUFReader et confronte métadonnées et tenseurs."""
    try:
        lecteur = gguf.GGUFReader(rapport.fichier)
    except (ValueError, KeyError, OSError, IndexError, TypeError) as exc:
        ecarts = [] if rapport.defauts else [f"GGUFReader refuse le fichier ({type(exc).__name__}: {exc}) alors que la lecture stdlib l'accepte"]
        return {"accepte": False, "erreur": f"{type(exc).__name__}: {exc}", "ecarts": ecarts}
    if rapport.defauts:
        return {"accepte": True, "ecarts": ["GGUFReader accepte le fichier alors que la lecture stdlib y trouve des défauts"]}
    ecarts: list[str] = []
    cles = {cle for cle in lecteur.fields if not cle.startswith("GGUF.")}
    if cles != set(rapport.metadonnees):
        ecarts.append(f"clés différentes : {sorted(cles ^ set(rapport.metadonnees))[:5]}")
    attendus = {t["nom"]: t for t in rapport.tenseurs}
    for tenseur in lecteur.tensors:
        mien = attendus.get(tenseur.name)
        lu = (int(tenseur.tensor_type), [int(d) for d in tenseur.shape], int(tenseur.n_bytes),
              int(tenseur.data_offset))
        if mien is None:
            ecarts.append(f"{tenseur.name} : absent de la lecture stdlib")
        elif lu != (mien["type_id"], mien["dimensions"], mien.get("octets"), rapport.debut_donnees + mien["decalage"]):
            ecarts.append(f"{tenseur.name} : gguf {lu} / stdlib {(mien['type_id'], mien['dimensions'], mien.get('octets'))}")
    if len(lecteur.tensors) != len(rapport.tenseurs):
        ecarts.append(f"{len(lecteur.tensors)} tenseurs pour gguf, {len(rapport.tenseurs)} pour stdlib")
    return {"accepte": True, "ecarts": ecarts[:LIMITE_DEFAUTS]}


def verifier_fragment(rapport: Rapport) -> list[str]:
    """gguf-split : voisins -0000k-of-0000n présents (stat seul) et split.no / split.count cohérents."""
    chemin = Path(rapport.fichier)
    correspondance = MOTIF_FRAGMENT.match(chemin.name)
    if correspondance is None:
        return []
    numero, total = int(correspondance["no"]), int(correspondance["total"])
    manquants = [k for k in range(1, total + 1)
                 if not (chemin.parent / f"{correspondance['base']}-{k:05d}-of-{total:05d}.gguf").is_file()]
    incoherences = [f"fragments absents du dossier : {manquants} sur {total}"] if manquants else []
    compte, rang = valeur_meta(rapport, "split.count"), valeur_meta(rapport, "split.no")
    if compte is not None and compte != total:
        incoherences.append(f"split.count = {compte} mais le nom annonce {total} fragments")
    if rang is not None and rang != numero - 1:
        incoherences.append(f"split.no = {rang} mais le nom annonce le fragment {numero} (split.no attendu {numero - 1})")
    return incoherences


def collecter(cibles: list[Path], recursif: bool, max_fichiers: int) -> list[Path]:
    """Un dossier donne ses .gguf ; un fichier est pris tel quel."""
    fichiers: list[Path] = []
    for cible in cibles:
        if cible.is_dir():
            motif = cible.rglob if recursif else cible.glob
            fichiers.extend(sorted(p for p in motif("*.gguf") if p.is_file()))
        else:
            fichiers.append(cible)
    return fichiers[:max_fichiers]


def presenter_rapport(rapport: Rapport, apercu: int, comparaison: dict[str, Any] | None) -> dict[str, Any]:
    """Rapport JSON d'un fichier."""
    tenseurs = [{k: t.get(k) for k in ("nom", "type", "dimensions", "octets", "decalage")} for t in rapport.tenseurs[:apercu]]
    sortie = {
        "fichier": rapport.fichier, "taille_octets": rapport.taille_octets, "version": rapport.version,
        "boutisme": rapport.boutisme, "tenseurs_declares": rapport.nombre_tenseurs_declare,
        "metadonnees_declarees": rapport.nombre_metadonnees_declare, "debut_donnees": rapport.debut_donnees,
        "resume": resumer(rapport), "quantification": quantification(rapport),
        "metadonnees": rapport.metadonnees, "apercu_tenseurs": tenseurs,
        "defauts": rapport.defauts[:LIMITE_DEFAUTS], "defauts_total": len(rapport.defauts),
        "incoherences": rapport.incoherences, "remarques": rapport.remarques,
    }
    if comparaison is not None:
        sortie["comparaison"] = comparaison
    return sortie


def choisir_moteur(demande: str) -> str:
    """auto → gguf s'il est importable, sinon stdlib (annoncé sur stderr)."""
    if demande == "gguf" and gguf is None:
        raise ErreurEntree("--moteur gguf demandé mais le paquet gguf n'est pas installé")
    if demande == "auto" and gguf is None:
        print("lire_gguf : paquet gguf absent — moteur stdlib seul (lecture struct de l'en-tête, sans comparaison)",
              file=sys.stderr)
        return "stdlib"
    return "stdlib" if demande == "stdlib" else "gguf"


def examiner(cibles: list[Path], args: argparse.Namespace, moteur: str) -> dict[str, Any]:
    """Lit chaque fichier, compare si demandé, assemble la sortie."""
    fichiers = collecter(cibles, args.recursif, args.max_fichiers)
    rapports = [lire_en_tete(chemin, args.max_entete, max(args.apercu_tableau, 0)) for chemin in fichiers]
    sorties = [presenter_rapport(r, max(args.apercu, 0), comparer_bibliotheque(r) if moteur == "gguf" else None)
               for r in rapports]
    defauts = sum(s["defauts_total"] + len(s["incoherences"]) for s in sorties)
    ecarts = sum(len(s.get("comparaison", {}).get("ecarts", [])) for s in sorties)
    noms = [r.fichier for r in rapports]
    return {
        "outil": "lire_gguf", "moteur": moteur, "denominateur": len(noms),
        "examines": noms[:LIMITE_EXAMINES], "examines_tronques": len(noms) > LIMITE_EXAMINES,
        "defauts": defauts, "ecarts_moteurs": ecarts, "fichiers": sorties,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def formater_octets(octets: int) -> str:
    """Octets en unité binaire lisible."""
    valeur = float(octets)
    for unite in ("o", "Kio", "Mio", "Gio", "Tio"):
        if valeur < 1024 or unite == "Tio":
            return f"{valeur:.0f} {unite}" if unite == "o" else f"{valeur:.2f} {unite}"
        valeur /= 1024
    return f"{octets} o"


def afficher_fichier(fichier: dict[str, Any]) -> None:
    """Bloc lisible d'un fichier."""
    print(f"\n{fichier['fichier']} ({formater_octets(fichier['taille_octets'])})")
    if fichier["version"] is not None:
        afficher_resume(fichier)
    for ligne in fichier["defauts"]:
        print(f"  DÉFAUT {ligne}")
    for ligne in fichier["incoherences"]:
        print(f"  INCOHÉRENCE {ligne}")
    for ligne in fichier.get("comparaison", {}).get("ecarts", []):
        print(f"  ÉCART MOTEURS {ligne}")
    for ligne in fichier["remarques"]:
        print(f"  remarque : {ligne}")


def afficher_resume(fichier: dict[str, Any]) -> None:
    """Version, architecture, vocabulaire, quantification, aperçu des tenseurs."""
    resume, quant = fichier["resume"], fichier["quantification"]
    print(f"  {MAGIE} v{fichier['version']}, {fichier['boutisme']} boutiste, {fichier['tenseurs_declares']} tenseurs, "
          f"{fichier['metadonnees_declarees']} métadonnées")
    print(f"  architecture {resume['architecture']} « {resume['nom']} », type {resume['type_fichier']}, "
          f"contexte {resume['context_length']}, couches {resume['block_count']}, "
          f"têtes {resume['attention_head_count']}/{resume['attention_head_count_kv']} (kv)")
    print(f"  vocabulaire {resume['jetons']} jetons ({resume['tokenizer']}), bos {resume['bos_token']}, "
          f"eos {resume['eos_token']}, gabarit de chat {resume['gabarit_chat_octets']} octets")
    if quant["parametres"]:
        print(f"  {quant['parametres']:,} paramètres, {formater_octets(quant['octets_tenseurs'])}, "
              f"{quant['bits_par_poids']} bits/poids")
        for nom, cumul in quant["par_type"].items():
            print(f"    {nom:<8} {cumul['tenseurs']:>5} tenseurs {cumul['parametres']:>15,} param. "
                  f"{formater_octets(cumul['octets']):>12} {cumul['bits_par_poids']} b/p")
    for tenseur in fichier["apercu_tenseurs"]:
        print(f"    {tenseur['nom']} {tenseur['type']} {tenseur['dimensions']}")


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Rapport lisible."""
    print(f"{sortie['denominateur']} fichier(s) examiné(s), moteur {sortie['moteur']}, {sortie['defauts']} défaut(s), "
          f"{sortie['ecarts_moteurs']} écart(s) entre moteurs")
    for fichier in sortie["fichiers"]:
        afficher_fichier(fichier)


def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def construire_analyseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    analyseur = argparse.ArgumentParser(
        description="Lit l'en-tête d'un modèle GGUF (architecture, contexte, quantification, tenseurs, "
                    "vocabulaire) et vérifie qu'il est chargeable, sans lire les poids.",
        epilog="Exemple : python lire_gguf.py modeles/qwen2.5-7b-instruct-q4_k_m.gguf --json   "
               "(ou un dossier : tous ses .gguf)",
    )
    analyseur.add_argument("chemins", nargs="+", help="fichier .gguf ou dossier")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--recursif", action="store_true", help="descendre dans les sous-dossiers")
    analyseur.add_argument("--max-fichiers", type=int, default=1000, help="plafond de fichiers examinés (défaut 1000)")
    analyseur.add_argument("--max-entete", type=int, default=PLAFOND_ENTETE,
                           help=f"plafond d'octets d'en-tête lus par fichier (défaut {PLAFOND_ENTETE})")
    analyseur.add_argument("--apercu", type=int, default=LIMITE_APERCU, help="tenseurs listés par fichier (défaut 8)")
    analyseur.add_argument("--apercu-tableau", type=int, default=LIMITE_APERCU,
                           help="éléments montrés par tableau de métadonnées (défaut 8)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "gguf"), default="auto",
                           help="auto : comparer avec le paquet gguf s'il est installé")
    return analyseur


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


def main() -> int:
    """0 chargeable, 1 défaut trouvé, 2 entrée invalide, 3 rien à examiner."""
    args = construire_analyseur().parse_args()
    cibles = [resoudre(c, args.racine) for c in args.chemins]
    try:
        absents = [str(c) for c in cibles if not c.exists()]
        if absents:
            raise ErreurEntree(f"chemin introuvable : {', '.join(absents)}")
        moteur = choisir_moteur(args.moteur)
    except ErreurEntree as exc:
        print(f"lire_gguf : {exc}", file=sys.stderr)
        return 2
    sortie = examiner(cibles, args, moteur)
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("lire_gguf : dénominateur nul — aucun fichier .gguf trouvé, rien à examiner", file=sys.stderr)
        return 3
    if sortie["defauts"] or sortie["ecarts_moteurs"]:
        print(f"lire_gguf : {sortie['defauts']} défaut(s), {sortie['ecarts_moteurs']} écart(s) entre moteurs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
