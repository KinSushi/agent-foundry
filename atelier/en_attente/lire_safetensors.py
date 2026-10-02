"""Un fichier safetensors est binaire : un agent ne peut ni le lire ni le charger
sans bibliothèque, et l'interpréteur propre de cette boîte n'en a aucune (mesuré :
`python3.14 -c 'import safetensors'` rend ModuleNotFoundError). Comparer les tailles
de fichiers ne suffit pas : sur un Llama sauvegardé en 5 fragments par transformers
5.18.0, l'index déclare total_size = 528768 octets quand les fichiers en pèsent
531968 (mesuré : les en-têtes ne comptent pas dans total_size).

QUESTION
    Que contient ce fichier safetensors (tenseurs, types, formes, nombre de
    paramètres, métadonnées) et est-il intègre ?
MESURE
    Lecture bornée de l'en-tête seul : 8 octets little-endian de longueur N, puis N
    octets de JSON UTF-8. Pour chaque tenseur : dtype, forme, décalages ; contrôle
    que la taille déclarée vaut produit(forme) x bits(dtype) / 8, que les blocs sont
    contigus, sans trou ni chevauchement, et couvrent exactement le fichier
    (8 + N + fin des données = taille sur disque). Totaux : paramètres (éléments) et
    octets par dtype, __metadata__. Index model.safetensors.index.json : fragments
    présents, weight_map complet et exact, total_size et total_parameters recalculés.
    Aucun poids n'est lu. Moteur safetensors (safe_open) en comparaison s'il est
    installé.
HYPOTHÈSES
    Le format suit la spécification safetensors (dtypes et règles de validation de
    safetensors/src/tensor.rs : en-tête plafonné à 100000000 octets, tenseurs triés
    par décalage, couverture exacte). Les fragments d'un index sont dans le même
    dossier que l'index. Le nombre de paramètres est le nombre d'éléments stockés.
LIMITES
    Ne lit aucune valeur : un poids NaN, nul ou corrompu octet par octet passe. Les
    poids quantifiés empaquetés (GPTQ, AWQ : entiers I32 ou U8) comptent des
    éléments empaquetés, pas des paramètres du modèle d'origine ; l'outil le
    signale en remarque quand un tenseur s'appelle *.qweight. Les poids liés retirés à la
    sauvegarde ne sont pas comptés. Un fragment absent de l'index n'est pas cherché
    ailleurs que dans le dossier de l'index.
CONTRE-EXEMPLES
    Un en-tête où la clé « a » apparaît deux fois : l'outil le déclare défectueux
    (ambiguïté) alors que safetensors 0.8.0 l'ouvre sans erreur en gardant la
    dernière définition (mesuré, l'écart des deux moteurs est rendu). Une couche
    q_proj 4096 x 4096 au format GPTQ 4 bits (qweight I32 [512, 4096], qzeros,
    scales, g_idx) : l'outil rend 2248704 « paramètres » pour 16777216 poids réels ;
    il le signale en remarque mais ne corrige pas le compte.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Fichiers .safetensors et index model.safetensors.index.json (Hugging Face,
    diffusers, peft), avant chargement, copie ou publication d'un modèle.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import safetensors  # type: ignore[import-not-found]
    from safetensors import safe_open  # type: ignore[import-not-found]
except ImportError:
    safetensors = None
    safe_open = None

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
FORMAT_GPTQ = "GPTQ"
ENCODAGE_ENTETE = "UTF-8"
SUFFIXE_EMPAQUETE = "qweight"

# Bits par élément, d'après Dtype::bitsize de safetensors/src/tensor.rs.
BITS_PAR_DTYPE = {
    "BOOL": 8, "F4": 4, "F6_E2M3": 6, "F6_E3M2": 6, "U8": 8, "I8": 8,
    "F8_E5M2": 8, "F8_E4M3": 8, "F8_E8M0": 8, "F8_E4M3FNUZ": 8, "F8_E5M2FNUZ": 8,
    "I16": 16, "U16": 16, "F16": 16, "BF16": 16, "I32": 32, "U32": 32,
    "F32": 32, "C64": 64, "F64": 64, "I64": 64, "U64": 64,
}
CLE_METADONNEES = "__metadata__"
SUFFIXE_INDEX = ".safetensors.index.json"
SUFFIXE_POIDS = ".safetensors"
PLAFOND_ENTETE = 100_000_000
LIMITE_EXAMINES = 200
LIMITE_APERCU = 12
LIMITE_DEFAUTS_FICHIER = 50


class ErreurEntree(Exception):
    """Entrée invalide (chemin absent, illisible) : code 2."""


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


def refuser_doublons(paires: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook : une clé JSON répétée est une ambiguïté, pas un détail."""
    objet: dict[str, Any] = {}
    for cle, valeur in paires:
        if cle in objet:
            raise ValueError(f"clé JSON dupliquée : {cle!r}")
        objet[cle] = valeur
    return objet


def lire_octets_entete(chemin: Path, plafond: int) -> tuple[int, bytes, int, list[str]]:
    """Rend (N, octets de l'en-tête, taille du fichier, défauts bloquants)."""
    taille = chemin.stat().st_size
    if taille < 8:
        return 0, b"", taille, [f"fichier de {taille} octet(s) : moins que les 8 octets de longueur d'en-tête"]
    with chemin.open("rb") as flux:
        (longueur,) = struct.unpack("<Q", flux.read(8))
        if longueur > plafond:
            return longueur, b"", taille, [
                f"en-tête annoncé de {longueur} octets, au-delà du plafond {plafond} (pas un safetensors, ou corrompu)"]
        if 8 + longueur > taille:
            return longueur, b"", taille, [
                f"en-tête annoncé de {longueur} octets mais le fichier n'en a que {taille - 8} après la longueur (tronqué)"]
        return longueur, flux.read(longueur), taille, []


def decoder_entete(brut: bytes) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """JSON de l'en-tête → (objet, défauts, remarques)."""
    remarques: list[str] = []
    try:
        texte = brut.decode(ENCODAGE_ENTETE)
    except UnicodeDecodeError as exc:
        return None, [f"en-tête non {ENCODAGE_ENTETE} (octet {exc.start})"], remarques
    if not texte.startswith("{"):
        remarques.append("l'en-tête ne commence pas par « { » comme l'exige la spécification")
    try:
        objet = json.loads(texte, object_pairs_hook=refuser_doublons)
    except ValueError as exc:
        return None, [f"en-tête JSON invalide : {exc}"], remarques
    if not isinstance(objet, dict):
        return None, ["l'en-tête JSON n'est pas un objet"], remarques
    return objet, [], remarques


def valider_metadonnees(valeur: Any) -> tuple[dict[str, str], list[str]]:
    """__metadata__ doit être un objet de chaînes vers chaînes."""
    if not isinstance(valeur, dict):
        return {}, ["__metadata__ n'est pas un objet"]
    fautives = [cle for cle, val in valeur.items() if not isinstance(val, str)]
    if fautives:
        return valeur, [f"__metadata__ : valeurs non textuelles pour {fautives[:5]} (refusé par safetensors)"]
    return valeur, []


def nombre_elements(forme: list[int]) -> int:
    """Produit des dimensions (1 pour un scalaire)."""
    return math.prod(forme)


def valider_tenseur(nom: str, info: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Contrôle dtype, forme, décalages et taille d'un tenseur déclaré."""
    if not isinstance(info, dict):
        return None, [f"{nom} : entrée non objet"]
    dtype, forme, decalages = info.get("dtype"), info.get("shape"), info.get("data_offsets")
    if dtype not in BITS_PAR_DTYPE:
        return None, [f"{nom} : dtype inconnu {dtype!r}"]
    if not isinstance(forme, list) or not all(isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in forme):
        return None, [f"{nom} : forme invalide {forme!r}"]
    if (not isinstance(decalages, list) or len(decalages) != 2
            or not all(isinstance(d, int) and not isinstance(d, bool) and d >= 0 for d in decalages)
            or decalages[0] > decalages[1]):
        return None, [f"{nom} : data_offsets invalides {decalages!r}"]
    elements = nombre_elements(forme)
    bits = elements * BITS_PAR_DTYPE[dtype]
    tenseur = {"nom": nom, "dtype": dtype, "forme": forme, "debut": decalages[0],
               "fin": decalages[1], "elements": elements, "octets": decalages[1] - decalages[0]}
    if bits % 8:
        return tenseur, [f"{nom} : {elements} éléments {dtype} ne tombent pas sur un octet entier"]
    if bits // 8 != tenseur["octets"]:
        return tenseur, [f"{nom} : {tenseur['octets']} octets déclarés, {bits // 8} attendus pour {dtype} {forme}"]
    return tenseur, []


def verifier_couverture(tenseurs: list[dict[str, Any]], zone: int) -> list[str]:
    """Blocs contigus, sans trou ni chevauchement, couvrant exactement la zone de données."""
    defauts: list[str] = []
    curseur, precedent = 0, "début des données"
    for tenseur in sorted(tenseurs, key=lambda t: (t["debut"], t["fin"])):
        if tenseur["debut"] > curseur:
            defauts.append(f"trou de {tenseur['debut'] - curseur} octets entre {precedent} et {tenseur['nom']}")
        elif tenseur["debut"] < curseur:
            defauts.append(f"chevauchement de {curseur - tenseur['debut']} octets entre {precedent} et {tenseur['nom']}")
        curseur, precedent = max(curseur, tenseur["fin"]), tenseur["nom"]
    if curseur < zone:
        defauts.append(f"{zone - curseur} octets en fin de fichier ne sont couverts par aucun tenseur")
    elif curseur > zone:
        defauts.append(f"fichier tronqué : les tenseurs s'étendent {curseur - zone} octets au-delà de la fin")
    return defauts


def totaliser(tenseurs: list[dict[str, Any]]) -> dict[str, Any]:
    """Paramètres et octets, au total et par dtype."""
    par_dtype: dict[str, dict[str, int]] = {}
    for tenseur in tenseurs:
        cumul = par_dtype.setdefault(tenseur["dtype"], {"tenseurs": 0, "parametres": 0, "octets": 0})
        cumul["tenseurs"] += 1
        cumul["parametres"] += tenseur["elements"]
        cumul["octets"] += tenseur["octets"]
    return {
        "nombre_tenseurs": len(tenseurs),
        "parametres": sum(t["elements"] for t in tenseurs),
        "octets_donnees": sum(t["octets"] for t in tenseurs),
        "par_dtype": dict(sorted(par_dtype.items())),
    }


def analyser_entete(objet: dict[str, Any], zone: int) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Valide chaque entrée de l'en-tête puis la couverture de la zone de données."""
    defauts: list[str] = []
    metadonnees: dict[str, str] = {}
    tenseurs: list[dict[str, Any]] = []
    for nom, info in objet.items():
        if nom == CLE_METADONNEES:
            metadonnees, fautes = valider_metadonnees(info)
            defauts.extend(fautes)
            continue
        tenseur, fautes = valider_tenseur(nom, info)
        defauts.extend(fautes)
        if tenseur is not None:
            tenseurs.append(tenseur)
    if len(tenseurs) == len(objet) - (CLE_METADONNEES in objet):
        defauts.extend(verifier_couverture(tenseurs, zone))
    return tenseurs, metadonnees, defauts


def examiner_fichier(chemin: Path, plafond: int) -> dict[str, Any]:
    """Rapport complet d'un fichier safetensors, sans lire les poids."""
    rapport: dict[str, Any] = {"fichier": str(chemin), "defauts": [], "remarques": [], "tenseurs": []}
    try:
        longueur, brut, taille, bloquants = lire_octets_entete(chemin, plafond)
    except OSError as exc:
        rapport["defauts"].append(f"illisible : {exc.strerror or exc}")
        return rapport
    rapport.update({"taille_octets": taille, "entete_octets": longueur})
    if bloquants:
        rapport["defauts"].extend(bloquants)
        return rapport
    objet, defauts, remarques = decoder_entete(brut)
    rapport["defauts"].extend(defauts)
    rapport["remarques"].extend(remarques)
    if objet is None:
        return rapport
    tenseurs, metadonnees, defauts = analyser_entete(objet, taille - 8 - longueur)
    rapport["defauts"].extend(defauts)
    rapport.update({"metadonnees": metadonnees, "tenseurs": tenseurs, **totaliser(tenseurs)})
    rapport["remarques"].extend(signaler_empaquetage(tenseurs))
    return rapport


def signaler_empaquetage(tenseurs: list[dict[str, Any]]) -> list[str]:
    """Poids quantifiés empaquetés : le compte d'éléments n'est pas le compte de paramètres."""
    empaquetes = [t for t in tenseurs if t["nom"].endswith(SUFFIXE_EMPAQUETE)]
    if not empaquetes:
        return []
    return [f"{len(empaquetes)} tenseur(s) *.{SUFFIXE_EMPAQUETE} ({FORMAT_GPTQ}/AWQ ?) : plusieurs poids "
            f"par élément {empaquetes[0]['dtype']}, le nombre de paramètres rendu les sous-estime"]


def comparer_bibliotheque(rapport: dict[str, Any]) -> dict[str, Any]:
    """Rouvre le fichier avec safe_open et confronte noms, formes, dtypes, métadonnées."""
    ecarts: list[str] = []
    try:
        with safe_open(rapport["fichier"], framework="numpy") as poignee:
            noms = set(poignee.keys())
            meta = poignee.metadata() or {}
            formes = {nom: (list(poignee.get_slice(nom).get_shape()), poignee.get_slice(nom).get_dtype()) for nom in noms}
    except (safetensors.SafetensorError, OSError, ValueError, TypeError, RuntimeError) as exc:
        accepte_stdlib = not rapport["defauts"]
        if accepte_stdlib:
            ecarts.append(f"safetensors refuse le fichier ({type(exc).__name__}: {exc}) alors que la lecture stdlib l'accepte")
        return {"accepte": False, "erreur": f"{type(exc).__name__}: {exc}", "ecarts": ecarts}
    if rapport["defauts"]:
        return {"accepte": True, "ecarts": ["safetensors accepte le fichier alors que la lecture stdlib y trouve des défauts"]}
    attendus = {t["nom"]: (t["forme"], t["dtype"]) for t in rapport["tenseurs"]}
    if set(attendus) != noms:
        ecarts.append(f"noms différents : {sorted(set(attendus) ^ noms)[:5]}")
    ecarts.extend(f"{nom} : stdlib {attendus[nom]} / safetensors {formes[nom]}"
                  for nom in sorted(set(attendus) & noms) if tuple(attendus[nom]) != formes[nom])
    if meta != rapport.get("metadonnees", {}):
        ecarts.append("__metadata__ différent entre stdlib et safetensors")
    return {"accepte": True, "ecarts": ecarts}


def lire_index(chemin: Path, plafond: int) -> tuple[dict[str, Any] | None, list[str]]:
    """Charge model.safetensors.index.json (taille bornée)."""
    try:
        if chemin.stat().st_size > plafond:
            return None, [f"index de plus de {plafond} octets : non lu"]
        objet = json.loads(chemin.read_text(encoding="utf-8"), object_pairs_hook=refuser_doublons)
    except OSError as exc:
        return None, [f"index illisible : {exc.strerror or exc}"]
    except (UnicodeDecodeError, ValueError) as exc:
        return None, [f"index JSON invalide : {exc}"]
    if not isinstance(objet, dict) or not isinstance(objet.get("weight_map"), dict):
        return None, ["index sans « weight_map » objet"]
    return objet, []


def fragments_de_l_index(index: dict[str, Any], dossier: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Fragment → noms de tenseurs attendus ; refuse les chemins hors du dossier."""
    attendus: dict[str, list[str]] = {}
    defauts: list[str] = []
    for nom, fragment in index["weight_map"].items():
        if not isinstance(fragment, str) or not fragment:
            defauts.append(f"weight_map[{nom!r}] n'est pas un nom de fichier")
        elif Path(fragment).is_absolute() or ".." in Path(fragment).parts:
            defauts.append(f"weight_map[{nom!r}] pointe hors du dossier de l'index : {fragment}")
        else:
            attendus.setdefault(fragment, []).append(nom)
    return attendus, defauts


def confronter_index(index: dict[str, Any], attendus: dict[str, list[str]],
                     rapports: dict[str, dict[str, Any]]) -> list[str]:
    """weight_map exact, total_size et total_parameters recalculés."""
    defauts: list[str] = []
    for fragment, noms in attendus.items():
        rapport = rapports.get(fragment)
        if rapport is None:
            defauts.append(f"fragment absent : {fragment} ({len(noms)} tenseurs perdus)")
            continue
        presents = {t["nom"] for t in rapport["tenseurs"]}
        manquants = sorted(set(noms) - presents)
        orphelins = sorted(presents - set(noms))
        if manquants:
            defauts.append(f"{fragment} : {len(manquants)} tenseur(s) du weight_map absents, ex. {manquants[:3]}")
        if orphelins:
            defauts.append(f"{fragment} : {len(orphelins)} tenseur(s) hors weight_map (jamais chargés), ex. {orphelins[:3]}")
    if any(r["defauts"] for r in rapports.values()) or len(rapports) != len(attendus):
        return defauts
    metadonnees = index.get("metadata") if isinstance(index.get("metadata"), dict) else {}
    for cle, calcule in (("total_size", sum(r["octets_donnees"] for r in rapports.values())),
                         ("total_parameters", sum(r["parametres"] for r in rapports.values()))):
        declare = metadonnees.get(cle)
        if declare is not None and declare != calcule:
            defauts.append(f"{cle} déclaré {declare}, recalculé {calcule}")
    return defauts


def examiner_index(chemin: Path, plafond: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Un modèle fragmenté : l'index et chacun de ses fragments présents."""
    modele: dict[str, Any] = {"index": str(chemin), "defauts": [], "fragments": []}
    index, defauts = lire_index(chemin, plafond)
    modele["defauts"].extend(defauts)
    if index is None:
        return modele, []
    attendus, defauts = fragments_de_l_index(index, chemin.parent)
    modele["defauts"].extend(defauts)
    rapports: dict[str, dict[str, Any]] = {}
    for fragment in sorted(attendus):
        cible = chemin.parent / fragment
        if cible.is_file():
            rapports[fragment] = examiner_fichier(cible, plafond)
    modele["defauts"].extend(confronter_index(index, attendus, rapports))
    modele["fragments"] = sorted(attendus)
    modele["metadonnees_index"] = index.get("metadata", {})
    modele.update(totaliser([t for r in rapports.values() for t in r["tenseurs"]]))
    return modele, list(rapports.values())


def collecter(cibles: list[Path], recursif: bool, max_fichiers: int) -> tuple[list[Path], list[Path]]:
    """Sépare index et fichiers de poids ; un dossier est déplié, un fichier pris tel quel."""
    index: list[Path] = []
    poids: list[Path] = []
    for cible in cibles:
        if cible.is_dir():
            motif = cible.rglob if recursif else cible.glob
            index.extend(sorted(motif("*" + SUFFIXE_INDEX)))
            poids.extend(sorted(p for p in motif("*" + SUFFIXE_POIDS) if p.is_file()))
        elif cible.name.endswith(SUFFIXE_INDEX):
            index.append(cible)
        else:
            poids.append(cible)
    return index[:max_fichiers], poids[:max_fichiers]


def examiner(cibles: list[Path], reglages: argparse.Namespace) -> dict[str, Any]:
    """Examine index puis fichiers isolés ; un fragment couvert par un index n'est lu qu'une fois."""
    fichiers_index, fichiers_poids = collecter(cibles, reglages.recursif, reglages.max_fichiers)
    modeles: list[dict[str, Any]] = []
    fichiers: list[dict[str, Any]] = []
    for chemin in fichiers_index:
        modele, rapports = examiner_index(chemin, reglages.max_entete)
        modeles.append(modele)
        fichiers.extend(rapports)
    deja_lus = {Path(r["fichier"]).resolve() for r in fichiers}
    for chemin in fichiers_poids:
        if chemin.resolve() not in deja_lus:
            fichiers.append(examiner_fichier(chemin, reglages.max_entete))
    return {"modeles_fragmentes": modeles, "fichiers": fichiers, "index_lus": len(fichiers_index)}


def choisir_moteur(demande: str) -> str:
    """auto → safetensors s'il est importable, sinon stdlib (annoncé sur stderr)."""
    if demande == "safetensors" and safe_open is None:
        raise ErreurEntree("--moteur safetensors demandé mais la bibliothèque safetensors n'est pas installée")
    if demande == "auto" and safe_open is None:
        print("lire_safetensors : bibliothèque safetensors absente — moteur stdlib seul "
              "(lecture directe de l'en-tête, sans comparaison)", file=sys.stderr)
        return "stdlib"
    return "stdlib" if demande == "stdlib" else "safetensors"


def presenter_fichier(rapport: dict[str, Any], apercu: int) -> dict[str, Any]:
    """Version JSON d'un fichier : tenseurs tronqués à l'aperçu."""
    sortie = {cle: valeur for cle, valeur in rapport.items() if cle != "tenseurs"}
    tenseurs = sorted(rapport["tenseurs"], key=lambda t: t["nom"])
    sortie["apercu_tenseurs"] = [{k: t[k] for k in ("nom", "dtype", "forme", "octets")} for t in tenseurs[:apercu]]
    sortie["defauts"] = rapport["defauts"][:LIMITE_DEFAUTS_FICHIER]
    sortie["defauts_total"] = len(rapport["defauts"])
    return sortie


def assembler(resultat: dict[str, Any], moteur: str, apercu: int) -> dict[str, Any]:
    """Objet de sortie : dénominateur, examinés, modèles, fichiers, contrat."""
    noms = [m["index"] for m in resultat["modeles_fragmentes"]] + [f["fichier"] for f in resultat["fichiers"]]
    defauts = sum(len(f["defauts"]) for f in resultat["fichiers"])
    defauts += sum(len(m["defauts"]) for m in resultat["modeles_fragmentes"])
    ecarts = sum(len(f.get("comparaison", {}).get("ecarts", [])) for f in resultat["fichiers"])
    return {
        "outil": "lire_safetensors",
        "moteur": moteur,
        "denominateur": len(noms),
        "examines": noms[:LIMITE_EXAMINES],
        "examines_tronques": len(noms) > LIMITE_EXAMINES,
        "defauts": defauts,
        "ecarts_moteurs": ecarts,
        "modeles_fragmentes": resultat["modeles_fragmentes"],
        "fichiers": [presenter_fichier(f, apercu) for f in resultat["fichiers"]],
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


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Rapport lisible."""
    print(f"{sortie['denominateur']} élément(s) examiné(s), moteur {sortie['moteur']}, {sortie['defauts']} défaut(s), "
          f"{sortie['ecarts_moteurs']} écart(s) entre moteurs")
    for modele in sortie["modeles_fragmentes"]:
        print(f"\nModèle fragmenté {modele['index']} : {len(modele['fragments'])} fragment(s), "
              f"{modele.get('parametres', 0):,} paramètres, {formater_octets(modele.get('octets_donnees', 0))}")
        for defaut in modele["defauts"]:
            print(f"  DÉFAUT {defaut}")
    for fichier in sortie["fichiers"]:
        print(f"\n{fichier['fichier']} ({formater_octets(fichier.get('taille_octets', 0))}, "
              f"en-tête {fichier.get('entete_octets', 0)} o)")
        if "parametres" in fichier:
            print(f"  {fichier['nombre_tenseurs']} tenseurs, {fichier['parametres']:,} paramètres")
            for dtype, cumul in fichier["par_dtype"].items():
                print(f"    {dtype:<12} {cumul['tenseurs']:>6} tenseurs {cumul['parametres']:>16,} param. {formater_octets(cumul['octets'])}")
        for cle, valeur in fichier.get("metadonnees", {}).items():
            print(f"  métadonnée {cle} = {valeur[:120]}")
        for tenseur in fichier["apercu_tenseurs"]:
            print(f"    {tenseur['nom']} {tenseur['dtype']} {tenseur['forme']}")
        for defaut in fichier["defauts"]:
            print(f"  DÉFAUT {defaut}")
        for ecart in fichier.get("comparaison", {}).get("ecarts", []):
            print(f"  ÉCART MOTEURS {ecart}")
        for remarque in fichier["remarques"]:
            print(f"  remarque : {remarque}")


def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def construire_analyseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    analyseur = argparse.ArgumentParser(
        description="Lit l'en-tête de fichiers safetensors (tenseurs, dtypes, formes, paramètres, "
                    "métadonnées) et vérifie leur intégrité, sans charger les poids.",
        epilog="Exemple : python lire_safetensors.py modeles/llama/ --json   "
               "(ou un fichier model.safetensors, ou model.safetensors.index.json)",
    )
    analyseur.add_argument("chemins", nargs="+", help="fichier .safetensors, index .safetensors.index.json ou dossier")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--recursif", action="store_true", help="descendre dans les sous-dossiers")
    analyseur.add_argument("--max-fichiers", type=int, default=10000, help="plafond de fichiers examinés (défaut 10000)")
    analyseur.add_argument("--max-entete", type=int, default=PLAFOND_ENTETE,
                           help=f"plafond d'octets d'en-tête ou d'index lus (défaut {PLAFOND_ENTETE})")
    analyseur.add_argument("--apercu", type=int, default=LIMITE_APERCU, help="tenseurs listés par fichier (défaut 12)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "safetensors"), default="auto",
                           help="auto : comparer avec safetensors s'il est installé")
    return analyseur


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


def verifier_cibles(chemins: list[str], racine: str | None) -> list[Path]:
    """Chaque cible doit exister."""
    cibles = [resoudre(c, racine) for c in chemins]
    for cible in cibles:
        if not cible.exists():
            raise ErreurEntree(f"chemin introuvable : {cible}")
    return cibles


def main() -> int:
    """0 intègre, 1 défaut trouvé, 2 entrée invalide, 3 rien à examiner."""
    args = construire_analyseur().parse_args()
    try:
        cibles = verifier_cibles(args.chemins, args.racine)
        moteur = choisir_moteur(args.moteur)
        resultat = examiner(cibles, args)
    except ErreurEntree as exc:
        print(f"lire_safetensors : {exc}", file=sys.stderr)
        return 2
    if moteur == "safetensors":
        for rapport in resultat["fichiers"]:
            rapport["comparaison"] = comparer_bibliotheque(rapport)
    sortie = assembler(resultat, moteur, max(args.apercu, 0))
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("lire_safetensors : dénominateur nul — aucun .safetensors ni index trouvé, rien à examiner", file=sys.stderr)
        return 3
    if sortie["defauts"] or sortie["ecarts_moteurs"]:
        print(f"lire_safetensors : {sortie['defauts']} défaut(s) d'intégrité, {sortie['ecarts_moteurs']} écart(s) "
              f"entre moteurs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
