r"""Lit l'historique d'un dépôt git directement dans .git, sans lancer le binaire git.

Mesuré le 2026-10-02 sur ce dépôt : `git count-objects -v` compte 520 objets en packfile
contre 20 isolés, et `git verify-pack -v` en montre 237 stockés en delta (chaînes jusqu'à 6) :
un lecteur qui ne déchiffre que les objets isolés ne voit pas l'historique, et sans binaire git
un agent n'en voit rien. Sur les 500 derniers commits de pallets/click, cet outil rend le même
ordre de commits que `git log` et le même nombre de commits pour chacun des 128 fichiers que
`git log --find-renames=100% --numstat`.

QUESTION
    Que dit l'historique de ce dépôt git : commits, auteurs, fichiers qui changent le plus ?
MESURE
    Lecture directe du dossier .git, en lecture seule : HEAD, références isolées et
    packed-refs (étiquettes annotées pelées), objets isolés (zlib) et packfiles (index v1 et
    v2, entrées OFS_DELTA et REF_DELTA, chaînes de deltas résolues sans récursion). Chaque
    objet lu est re-haché (sha1, ou sha256 si le dépôt le déclare) et comparé à son nom.
    Parcours des commits depuis HEAD (ou --ref) par date de commit décroissante, comme
    `git log` : parents, auteur, dates ISO avec fuseau, sujet. Churn par fichier sur les N
    commits examinés : arbre de chaque commit non-fusion comparé à celui de son premier
    parent (racine : arbre vide), renommages exacts appariés par empreinte de blob ; avec
    --lignes, lignes ajoutées/supprimées par difflib. Si dulwich est importable, la liste
    des commits est relue par dulwich et les écarts sont rapportés.
HYPOTHÈSES
    Le chemin donné est la racine d'un dépôt (dossier contenant .git, fichier .git d'un
    worktree, ou dépôt nu). Les parents de ce chemin ne sont jamais explorés. Un fichier .git
    « gitdir: », un commondir et objects/info/alternates sont suivis : ils font partie du
    dépôt. Le dépôt n'est pas modifié pendant la lecture.
LIMITES
    Format reftable et dépôts à extensions inconnues refusés (code 2). Pas de lecture du
    multi-pack-index ni du commit-graph (inutiles : chaque pack a son .idx). Renommages
    approximatifs (contenu modifié) non détectés : ils apparaissent en suppression + ajout.
    Les fusions ne comptent pas dans le churn (comme `git log --numstat` sans -m). Avec
    --lignes, difflib n'est pas l'algorithme de Myers de git : les comptes peuvent différer ;
    blobs de plus de 1 Mo ou binaires (octet nul dans les 8000 premiers) non comptés. Dans un
    clone superficiel, les commits de bord n'ont pas de parent et sont exclus du churn.
CONTRE-EXEMPLES
    Constaté sur un dépôt de test : `git log --numstat` (renommages détectés par défaut)
    montre « 1 1 principal.txt => coeur.txt » pour un renommage avec une ligne retouchée ;
    cet outil, qui n'apparie que les blobs identiques, y compte une suppression de
    principal.txt (-200) et un ajout de coeur.txt (+200). Avec --lignes, sur les 500 derniers
    commits de pallets/click, 13 fichiers sur 128 ont des comptes différents de git
    (.github/workflows/tests.yaml : +65/-55 ici, +64/-54 pour git).
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Dépôts git locaux au format de fichiers (sha1 ou sha256), y compris clones superficiels
    et worktrees ; audit d'activité, revue de l'historique récent, choix des fichiers à
    surveiller, là où le binaire git est absent, interdit ou non fiable.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import heapq
import json
import mmap
import re
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from dulwich import repo as dulwich_repo
except ImportError:
    dulwich_repo = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
NOM_HEAD = "HEAD"
NOM_OFS_DELTA = "OFS_DELTA"
NOM_REF_DELTA = "REF_DELTA"

TYPES_OBJET = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}
TYPE_OFS = 6
TYPE_REF = 7
MAGIQUE_IDX = b"\xfftOc"
MAX_EXAMINES = 200
MAX_CACHE_OCTETS = 64 * 1024 * 1024
MAX_BLOB_LIGNES = 1024 * 1024
MAX_PROFONDEUR_SYMREF = 10
MODE_ARBRE = "40000"
MODE_SOUS_MODULE = "160000"
EXTENSIONS_CONNUES = frozenset({"objectformat", "worktreeconfig", "preciousobjects",
                                "partialclone", "noop", "refstorage"})
MOTIF_PERSONNE = re.compile(rb"^(.*?) ?<([^>]*)> (-?\d+) ([+-]\d{4})\s*$")
MOTIF_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


class ErreurDepot(Exception):
    """Erreur de lecture du dépôt, portant le code de sortie à rendre."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class IndexPack:
    """Index (.idx) d'un packfile, projeté en mémoire."""

    chemin_pack: Path
    version: int
    nombre: int
    fanout: tuple[int, ...]
    projection: Any
    debut_noms: int
    debut_decalages: int
    debut_grands: int
    pack: Any = None


@dataclass
class Statistiques:
    """Compteurs de lecture, pour dire ce qui a réellement été lu."""

    objets_isoles_lus: int = 0
    objets_empaquetes_lus: int = 0
    deltas_ofs: int = 0
    deltas_ref: int = 0
    objets_verifies: int = 0
    anomalies: list[str] = field(default_factory=list)


@dataclass
class Depot:
    """Dépôt ouvert en lecture : dossiers, format, packs, cache borné."""

    chemin: Path
    gitdir: Path
    commondir: Path
    format_objets: str
    longueur_hash: int
    dossiers_objets: list[Path]
    packs: list[IndexPack]
    superficiels: frozenset[str]
    stats: Statistiques = field(default_factory=Statistiques)
    cache: dict[Any, tuple[str, bytes]] = field(default_factory=dict)
    taille_cache: int = 0


@dataclass
class Commit:
    """Commit décodé."""

    sha: str
    arbre: str
    parents: list[str]
    auteur: str
    courriel: str
    date_auteur: str
    horodatage_commit: int
    date_commit: str
    sujet: str


# ---------------------------------------------------------------------------
# Localisation du dépôt et configuration
# ---------------------------------------------------------------------------

def est_gitdir(dossier: Path) -> bool:
    """Un dossier est un gitdir s'il porte HEAD, objects/ et refs/ (ou un commondir)."""
    if not (dossier / NOM_HEAD).is_file():
        return False
    if (dossier / "commondir").is_file():
        return True
    return (dossier / "objects").is_dir() and (dossier / "refs").is_dir()


def lire_texte_court(chemin: Path, limite: int = 1 << 20) -> str:
    """Lit un petit fichier texte du dépôt (HEAD, ref, config), borné."""
    try:
        with chemin.open("rb") as flux:
            donnees = flux.read(limite)
    except OSError as exc:
        raise ErreurDepot(f"lecture impossible de {chemin} : {exc.strerror}") from exc
    return donnees.decode("utf-8", errors="replace")


def localiser_gitdir(chemin: Path) -> Path | None:
    """Trouve le gitdir du chemin donné, sans jamais remonter dans ses parents."""
    point_git = chemin / ".git"
    if point_git.is_dir() and est_gitdir(point_git):
        return point_git
    if point_git.is_file():
        texte = lire_texte_court(point_git).strip()
        if texte.startswith("gitdir:"):
            cible = Path(texte[len("gitdir:"):].strip())
            cible = cible if cible.is_absolute() else (chemin / cible)
            if est_gitdir(cible):
                return cible
        raise ErreurDepot(f"{point_git} existe mais ne désigne aucun gitdir lisible")
    if est_gitdir(chemin):
        return chemin
    return None


def localiser_commondir(gitdir: Path) -> Path:
    """Dossier commun (worktrees) : objets et références partagées."""
    fichier = gitdir / "commondir"
    if not fichier.is_file():
        return gitdir
    texte = lire_texte_court(fichier).strip()
    cible = Path(texte)
    return cible if cible.is_absolute() else (gitdir / cible)


def lire_config(commondir: Path) -> dict[str, str]:
    """Lit les clés « section.cle » de la config du dépôt (sans include, sans config globale)."""
    chemin = commondir / "config"
    if not chemin.is_file():
        return {}
    valeurs: dict[str, str] = {}
    section = ""
    for brute in lire_texte_court(chemin).splitlines():
        ligne = brute.strip()
        if not ligne or ligne[0] in "#;":
            continue
        entete = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"[^"]*")?\s*\]', ligne)
        if entete:
            section = entete.group(1).lower()
            continue
        cle, _, valeur = ligne.partition("=")
        valeurs[f"{section}.{cle.strip().lower()}"] = valeur.strip().strip('"')
    return valeurs


def controler_format(config: dict[str, str], commondir: Path) -> str:
    """Rend le format d'objets ; refuse reftable et les extensions inconnues."""
    if (commondir / "reftable").is_dir() or config.get("extensions.refstorage") == "reftable":
        raise ErreurDepot("dépôt au format reftable : non pris en charge par cet outil")
    if config.get("core.repositoryformatversion", "0") not in ("0", "1"):
        raise ErreurDepot("version de format de dépôt inconnue : "
                          f"{config.get('core.repositoryformatversion')}")
    for cle in config:
        if cle.startswith("extensions.") and cle.split(".", 1)[1] not in EXTENSIONS_CONNUES:
            raise ErreurDepot(f"extension de dépôt inconnue, lecture refusée : {cle}")
    format_objets = config.get("extensions.objectformat", "sha1").lower()
    if format_objets not in ("sha1", "sha256"):
        raise ErreurDepot(f"format d'objets inconnu : {format_objets}")
    return format_objets


def dossiers_alternates(objets: Path) -> list[Path]:
    """Dossiers d'objets supplémentaires déclarés par objects/info/alternates."""
    fichier = objets / "info" / "alternates"
    if not fichier.is_file():
        return []
    dossiers = []
    for ligne in lire_texte_court(fichier).splitlines():
        ligne = ligne.strip()
        if ligne and not ligne.startswith("#"):
            cible = Path(ligne)
            dossiers.append(cible if cible.is_absolute() else (objets / cible))
    return [d for d in dossiers if d.is_dir()]


def lire_superficiels(commondir: Path) -> frozenset[str]:
    """Commits de bord d'un clone superficiel (fichier shallow)."""
    fichier = commondir / "shallow"
    if not fichier.is_file():
        return frozenset()
    return frozenset(l.strip() for l in lire_texte_court(fichier).splitlines() if l.strip())


def ouvrir_depot(chemin: Path) -> Depot:
    """Ouvre le dépôt situé exactement à ce chemin, ou lève ErreurDepot."""
    gitdir = localiser_gitdir(chemin)
    if gitdir is None:
        raise ErreurDepot(f"aucun dépôt git à la racine de {chemin} (ni .git, ni dépôt nu) : "
                          "dénominateur nul, rien à examiner", code=3)
    commondir = localiser_commondir(gitdir)
    format_objets = controler_format(lire_config(commondir), commondir)
    objets = commondir / "objects"
    dossiers = [objets] + dossiers_alternates(objets)
    longueur = 20 if format_objets == "sha1" else 32
    packs = [index for dossier in dossiers for index in charger_index_packs(dossier, longueur)]
    return Depot(chemin=chemin, gitdir=gitdir, commondir=commondir, format_objets=format_objets,
                 longueur_hash=longueur, dossiers_objets=dossiers, packs=packs,
                 superficiels=lire_superficiels(commondir))


# ---------------------------------------------------------------------------
# Index de packs
# ---------------------------------------------------------------------------

def projeter(chemin: Path) -> Any:
    """Projette un fichier en mémoire, en lecture seule."""
    try:
        with chemin.open("rb") as flux:
            if flux.seek(0, 2) == 0:
                raise ErreurDepot(f"fichier vide : {chemin}", code=1)
            return mmap.mmap(flux.fileno(), 0, access=mmap.ACCESS_READ)
    except OSError as exc:
        raise ErreurDepot(f"lecture impossible de {chemin} : {exc.strerror}") from exc


def charger_index_packs(dossier_objets: Path, longueur: int) -> list[IndexPack]:
    """Charge tous les .idx de objects/pack dont le .pack existe."""
    dossier = dossier_objets / "pack"
    if not dossier.is_dir():
        return []
    index = []
    for chemin_idx in sorted(dossier.glob("pack-*.idx")):
        chemin_pack = chemin_idx.with_suffix(".pack")
        if chemin_pack.is_file():
            index.append(lire_index(chemin_idx, chemin_pack, longueur))
    return index


def lire_index(chemin_idx: Path, chemin_pack: Path, longueur: int) -> IndexPack:
    """Analyse l'en-tête d'un index de pack v1 ou v2."""
    proj = projeter(chemin_idx)
    if proj[:4] == MAGIQUE_IDX:
        version = struct.unpack(">I", proj[4:8])[0]
        if version != 2:
            raise ErreurDepot(f"version d'index de pack non prise en charge : {version}")
        fanout = struct.unpack(">256I", proj[8:8 + 1024])
        nombre = fanout[255]
        debut_noms = 8 + 1024
        debut_dec = debut_noms + nombre * longueur + nombre * 4
        attendu = debut_dec + nombre * 4 + 2 * longueur
        if len(proj) < attendu:
            raise ErreurDepot(f"index tronqué : {chemin_idx}", code=1)
        return IndexPack(chemin_pack, 2, nombre, fanout, proj, debut_noms, debut_dec,
                         debut_dec + nombre * 4)
    fanout = struct.unpack(">256I", proj[:1024])
    nombre = fanout[255]
    if len(proj) < 1024 + nombre * (4 + longueur):
        raise ErreurDepot(f"index v1 tronqué : {chemin_idx}", code=1)
    return IndexPack(chemin_pack, 1, nombre, fanout, proj, 1024, 1024, 0)


def nom_dans_index(index: IndexPack, rang: int, longueur: int) -> bytes:
    """Nom (empreinte brute) du rang-ième objet de l'index."""
    if index.version == 2:
        debut = index.debut_noms + rang * longueur
    else:
        debut = 1024 + rang * (4 + longueur) + 4
    return index.projection[debut:debut + longueur]


def decalage_dans_index(index: IndexPack, rang: int, longueur: int) -> int:
    """Décalage dans le .pack du rang-ième objet (grands décalages compris)."""
    proj = index.projection
    if index.version == 1:
        debut = 1024 + rang * (4 + longueur)
        return struct.unpack(">I", proj[debut:debut + 4])[0]
    valeur = struct.unpack(">I", proj[index.debut_decalages + rang * 4:
                                      index.debut_decalages + rang * 4 + 4])[0]
    if not valeur & 0x80000000:
        return valeur
    debut = index.debut_grands + (valeur & 0x7FFFFFFF) * 8
    return struct.unpack(">Q", proj[debut:debut + 8])[0]


def chercher_dans_index(index: IndexPack, brut: bytes, longueur: int) -> int | None:
    """Recherche dichotomique d'une empreinte ; rend son décalage dans le pack."""
    premier = brut[0]
    bas = index.fanout[premier - 1] if premier else 0
    haut = index.fanout[premier]
    while bas < haut:
        milieu = (bas + haut) // 2
        nom = nom_dans_index(index, milieu, longueur)
        if nom == brut:
            return decalage_dans_index(index, milieu, longueur)
        if nom < brut:
            bas = milieu + 1
        else:
            haut = milieu
    return None


# ---------------------------------------------------------------------------
# Lecture des objets
# ---------------------------------------------------------------------------

def decompresser(proj: Any, debut: int, taille: int, origine: str) -> bytes:
    """Décompresse un flux zlib commençant à `debut`, par blocs, et contrôle sa taille."""
    flux = zlib.decompressobj()
    morceaux = []
    position = debut
    try:
        while not flux.eof and position < len(proj):
            morceaux.append(flux.decompress(proj[position:position + 65536]))
            position += 65536
    except zlib.error as exc:
        raise ErreurDepot(f"flux zlib corrompu dans {origine} : {exc}", code=1) from exc
    donnees = b"".join(morceaux)
    if not flux.eof or len(donnees) != taille:
        raise ErreurDepot(f"objet tronqué dans {origine} (attendu {taille} octets, "
                          f"lu {len(donnees)})", code=1)
    return donnees


def lire_varint_delta(donnees: bytes, position: int) -> tuple[int, int]:
    """Entier de taille d'un delta (7 bits par octet, poids faible d'abord)."""
    valeur = 0
    decal = 0
    while True:
        octet = donnees[position]
        position += 1
        valeur |= (octet & 0x7F) << decal
        decal += 7
        if not octet & 0x80:
            return valeur, position


def copier_depuis_base(delta: bytes, position: int, operation: int) -> tuple[int, int, int]:
    """Décode une instruction de copie : (décalage, taille, position suivante)."""
    decalage = 0
    taille = 0
    for bit in range(4):
        if operation & (1 << bit):
            decalage |= delta[position] << (8 * bit)
            position += 1
    for bit in range(3):
        if operation & (0x10 << bit):
            taille |= delta[position] << (8 * bit)
            position += 1
    return decalage, taille or 0x10000, position


def appliquer_delta(base: bytes, delta: bytes) -> bytes:
    """Reconstruit un objet à partir de sa base et d'un delta git."""
    try:
        taille_source, position = lire_varint_delta(delta, 0)
        taille_cible, position = lire_varint_delta(delta, position)
        if taille_source != len(base):
            raise ErreurDepot("delta incohérent : taille de base inattendue", code=1)
        sortie = bytearray()
        while position < len(delta):
            operation = delta[position]
            position += 1
            if operation & 0x80:
                decalage, taille, position = copier_depuis_base(delta, position, operation)
                if decalage + taille > len(base):
                    raise ErreurDepot("delta incohérent : copie hors de la base", code=1)
                sortie += base[decalage:decalage + taille]
            elif operation:
                sortie += delta[position:position + operation]
                position += operation
            else:
                raise ErreurDepot("delta incohérent : instruction 0 réservée", code=1)
    except IndexError as exc:
        raise ErreurDepot("delta tronqué", code=1) from exc
    if len(sortie) != taille_cible:
        raise ErreurDepot("delta incohérent : taille reconstruite inattendue", code=1)
    return bytes(sortie)


def lire_entete_entree(proj: Any, position: int) -> tuple[int, int, int]:
    """En-tête d'une entrée de pack : (type, taille décompressée, position des données)."""
    octet = proj[position]
    position += 1
    type_num = (octet >> 4) & 7
    taille = octet & 0x0F
    decal = 4
    while octet & 0x80:
        octet = proj[position]
        position += 1
        taille |= (octet & 0x7F) << decal
        decal += 7
    return type_num, taille, position


def lire_decalage_ofs(proj: Any, position: int) -> tuple[int, int]:
    """Distance négative d'un OFS_DELTA vers sa base (codage « +1 » de git)."""
    octet = proj[position]
    position += 1
    distance = octet & 0x7F
    while octet & 0x80:
        octet = proj[position]
        position += 1
        distance = ((distance + 1) << 7) | (octet & 0x7F)
    return distance, position


def ouvrir_pack(index: IndexPack) -> Any:
    """Projette le .pack d'un index à la première utilisation et vérifie son en-tête."""
    if index.pack is None:
        proj = projeter(index.chemin_pack)
        if proj[:4] != b"PACK" or struct.unpack(">I", proj[4:8])[0] not in (2, 3):
            raise ErreurDepot(f"en-tête de pack invalide : {index.chemin_pack}", code=1)
        index.pack = proj
    return index.pack


def memoriser(depot: Depot, cle: Any, valeur: tuple[str, bytes]) -> None:
    """Cache borné en octets ; évince les entrées les plus anciennes."""
    depot.cache[cle] = valeur
    depot.taille_cache += len(valeur[1])
    while depot.taille_cache > MAX_CACHE_OCTETS and len(depot.cache) > 1:
        ancienne = next(iter(depot.cache))
        depot.taille_cache -= len(depot.cache.pop(ancienne)[1])


def localiser_objet(depot: Depot, sha: str) -> tuple[IndexPack, int] | Path | None:
    """Où vit l'objet : (pack, décalage) ou fichier isolé ; None s'il est absent."""
    for dossier in depot.dossiers_objets:
        isole = dossier / sha[:2] / sha[2:]
        if isole.is_file():
            return isole
    brut = bytes.fromhex(sha)
    for index in depot.packs:
        decalage = chercher_dans_index(index, brut, depot.longueur_hash)
        if decalage is not None:
            return index, decalage
    return None


def lire_objet_isole(depot: Depot, chemin: Path) -> tuple[str, bytes]:
    """Objet isolé : zlib, en-tête « type taille\\0 »."""
    flux = zlib.decompressobj()
    try:
        brut = flux.decompress(chemin.read_bytes())
    except (OSError, zlib.error) as exc:
        raise ErreurDepot(f"objet isolé illisible {chemin} : {exc}", code=1) from exc
    if not flux.eof or flux.unused_data:
        raise ErreurDepot(f"objet isolé tronqué ou suivi de données parasites : {chemin}", code=1)
    entete, sep, contenu = brut.partition(b"\x00")
    type_nom, _, taille = entete.decode("ascii", errors="replace").partition(" ")
    if not sep or type_nom not in TYPES_OBJET.values() or taille != str(len(contenu)):
        raise ErreurDepot(f"en-tête d'objet isolé invalide : {chemin}", code=1)
    depot.stats.objets_isoles_lus += 1
    return type_nom, contenu


def descendre_chaine(depot: Depot, index: IndexPack, decalage: int,
                     pile: list[tuple[Any, bytes]]) -> tuple[str, bytes]:
    """Suit une chaîne de deltas jusqu'à une base connue, en empilant les deltas."""
    while True:
        cle = (str(index.chemin_pack), decalage)
        if cle in depot.cache:
            return depot.cache[cle]
        proj = ouvrir_pack(index)
        type_num, taille, position = lire_entete_entree(proj, decalage)
        if type_num in TYPES_OBJET:
            contenu = decompresser(proj, position, taille, index.chemin_pack.name)
            depot.stats.objets_empaquetes_lus += 1
            memoriser(depot, cle, (TYPES_OBJET[type_num], contenu))
            return TYPES_OBJET[type_num], contenu
        if type_num == TYPE_OFS:
            distance, position = lire_decalage_ofs(proj, position)
            pile.append((cle, decompresser(proj, position, taille, index.chemin_pack.name)))
            depot.stats.deltas_ofs += 1
            decalage -= distance
            continue
        if type_num != TYPE_REF:
            raise ErreurDepot(f"type d'entrée de pack inconnu {type_num} dans "
                              f"{index.chemin_pack.name}", code=1)
        base = proj[position:position + depot.longueur_hash].hex()
        position += depot.longueur_hash
        pile.append((cle, decompresser(proj, position, taille, index.chemin_pack.name)))
        depot.stats.deltas_ref += 1
        lieu = localiser_objet(depot, base)
        if lieu is None:
            raise ErreurDepot(f"base de {NOM_REF_DELTA} absente : {base}", code=1)
        if isinstance(lieu, Path):
            return lire_objet_isole(depot, lieu)
        index, decalage = lieu


def lire_objet_empaquete(depot: Depot, index: IndexPack, decalage: int) -> tuple[str, bytes]:
    """Objet dans un pack, deltas OFS et REF résolus itérativement."""
    pile: list[tuple[Any, bytes]] = []
    type_nom, contenu = descendre_chaine(depot, index, decalage, pile)
    while pile:
        cle, delta = pile.pop()
        contenu = appliquer_delta(contenu, delta)
        depot.stats.objets_empaquetes_lus += 1
        memoriser(depot, cle, (type_nom, contenu))
    return type_nom, contenu


def verifier_empreinte(depot: Depot, sha: str, type_nom: str, contenu: bytes) -> None:
    """Re-hache l'objet : son nom doit être l'empreinte de « type taille\\0contenu »."""
    hacheur = hashlib.sha1 if depot.format_objets == "sha1" else hashlib.sha256
    calcule = hacheur(f"{type_nom} {len(contenu)}".encode("ascii") + b"\x00" + contenu)
    if calcule.hexdigest() != sha:
        raise ErreurDepot(f"empreinte incorrecte pour l'objet {sha} "
                          f"(calculée {calcule.hexdigest()})", code=1)
    depot.stats.objets_verifies += 1


def lire_objet(depot: Depot, sha: str) -> tuple[str, bytes]:
    """Lit et vérifie un objet par son empreinte complète."""
    lieu = localiser_objet(depot, sha)
    if lieu is None:
        raise ErreurDepot(f"objet absent du dépôt : {sha}", code=1)
    if isinstance(lieu, Path):
        type_nom, contenu = lire_objet_isole(depot, lieu)
    else:
        type_nom, contenu = lire_objet_empaquete(depot, lieu[0], lieu[1])
    verifier_empreinte(depot, sha, type_nom, contenu)
    return type_nom, contenu


# ---------------------------------------------------------------------------
# Références
# ---------------------------------------------------------------------------

def lire_packed_refs(commondir: Path) -> dict[str, tuple[str, str | None]]:
    """packed-refs : nom → (empreinte, empreinte pelée éventuelle)."""
    fichier = commondir / "packed-refs"
    if not fichier.is_file():
        return {}
    refs: dict[str, tuple[str, str | None]] = {}
    dernier = None
    for ligne in lire_texte_court(fichier, limite=1 << 28).splitlines():
        if ligne.startswith("#") or not ligne.strip():
            continue
        if ligne.startswith("^") and dernier:
            refs[dernier] = (refs[dernier][0], ligne[1:].strip())
            continue
        sha, _, nom = ligne.partition(" ")
        if MOTIF_SHA.match(sha):
            refs[nom.strip()] = (sha, None)
            dernier = nom.strip()
    return refs


def lister_refs_isolees(commondir: Path) -> dict[str, str]:
    """Références isolées sous refs/ : nom → contenu brut (empreinte ou « ref: »)."""
    racine = commondir / "refs"
    refs: dict[str, str] = {}
    if not racine.is_dir():
        return refs
    for fichier in sorted(racine.rglob("*")):
        if fichier.is_file() and not fichier.name.endswith(".lock"):
            nom = fichier.relative_to(commondir).as_posix()
            refs[nom] = lire_texte_court(fichier, limite=4096).strip()
    return refs


def resoudre_ref(depot: Depot, nom: str, packees: dict[str, tuple[str, str | None]]) -> str | None:
    """Résout une référence (symbolique ou non) en empreinte, ou None si elle n'existe pas."""
    for _ in range(MAX_PROFONDEUR_SYMREF):
        contenu = None
        for base in (depot.gitdir, depot.commondir):
            fichier = base / nom
            if fichier.is_file():
                contenu = lire_texte_court(fichier, limite=4096).strip()
                break
        if contenu is None:
            return packees[nom][0] if nom in packees else None
        if not contenu.startswith("ref:"):
            return contenu if MOTIF_SHA.match(contenu) else None
        nom = contenu[4:].strip()
    raise ErreurDepot(f"références symboliques en boucle à partir de {nom}")


def decrire_head(depot: Depot, packees: dict[str, tuple[str, str | None]]) -> dict[str, Any]:
    """HEAD : branche courante (ou tête détachée) et commit pointé."""
    contenu = lire_texte_court(depot.gitdir / NOM_HEAD, limite=4096).strip()
    branche = contenu[4:].strip() if contenu.startswith("ref:") else None
    return {"branche": branche, "detache": branche is None,
            "sha": resoudre_ref(depot, NOM_HEAD, packees)}


def peler(depot: Depot, sha: str) -> str:
    """Suit les étiquettes annotées jusqu'à l'objet visé."""
    for _ in range(MAX_PROFONDEUR_SYMREF):
        type_nom, contenu = lire_objet(depot, sha)
        if type_nom != "tag":
            return sha
        premiere = contenu.split(b"\n", 1)[0].decode("ascii", errors="replace")
        if not premiere.startswith("object "):
            raise ErreurDepot(f"étiquette annotée mal formée : {sha}", code=1)
        sha = premiere[len("object "):].strip()
    raise ErreurDepot(f"chaîne d'étiquettes trop longue depuis {sha}", code=1)


def inventorier_refs(depot: Depot, packees: dict[str, tuple[str, str | None]]) -> dict[str, Any]:
    """Branches et étiquettes (les isolées l'emportent sur packed-refs)."""
    toutes = {nom: valeur[0] for nom, valeur in packees.items()}
    for nom, contenu in lister_refs_isolees(depot.commondir).items():
        if MOTIF_SHA.match(contenu):
            toutes[nom] = contenu
    branches = sorted(n[len("refs/heads/"):] for n in toutes if n.startswith("refs/heads/"))
    etiquettes = [{"nom": n[len("refs/tags/"):], "sha": s,
                   "pelee": packees.get(n, (None, None))[1]}
                  for n, s in sorted(toutes.items()) if n.startswith("refs/tags/")]
    return {"branches": branches, "etiquettes": etiquettes, "toutes": toutes}


def resoudre_depart(depot: Depot, nom: str, refs: dict[str, Any],
                    packees: dict[str, tuple[str, str | None]]) -> str:
    """Nom de départ (--ref) → empreinte de commit."""
    candidats = [nom, f"refs/{nom}", f"refs/heads/{nom}", f"refs/tags/{nom}",
                 f"refs/remotes/{nom}"]
    for candidat in candidats:
        sha = refs["toutes"].get(candidat) or resoudre_ref(depot, candidat, packees)
        if sha:
            return peler(depot, sha)
    if re.fullmatch(r"[0-9a-f]{4,64}", nom):
        return peler(depot, completer_prefixe(depot, nom))
    raise ErreurDepot(f"référence inconnue : {nom}")


def completer_prefixe(depot: Depot, prefixe: str) -> str:
    """Complète un préfixe d'empreinte s'il est unique (objets isolés et packs)."""
    trouves = set()
    for dossier in depot.dossiers_objets:
        sous = dossier / prefixe[:2]
        if len(prefixe) >= 2 and sous.is_dir():
            trouves.update(prefixe[:2] + f.name for f in sous.iterdir()
                           if (prefixe[:2] + f.name).startswith(prefixe))
    for index in depot.packs:
        for rang in range(index.nombre):
            nom = nom_dans_index(index, rang, depot.longueur_hash).hex()
            if nom.startswith(prefixe):
                trouves.add(nom)
    if len(trouves) != 1:
        raise ErreurDepot(f"préfixe d'empreinte {prefixe} : {len(trouves)} objet(s) correspondant(s)")
    return trouves.pop()


# ---------------------------------------------------------------------------
# Commits et parcours
# ---------------------------------------------------------------------------

def decoder_personne(ligne: bytes, encodage: str) -> tuple[str, str, int, str]:
    """« Nom <courriel> horodatage fuseau » → (nom, courriel, horodatage, date ISO)."""
    trouve = MOTIF_PERSONNE.match(ligne)
    if not trouve:
        return ligne.decode(encodage, errors="replace"), "", 0, ""
    nom = trouve.group(1).decode(encodage, errors="replace")
    courriel = trouve.group(2).decode(encodage, errors="replace")
    horodatage = int(trouve.group(3))
    fuseau = trouve.group(4).decode("ascii")
    minutes = (int(fuseau[1:3]) * 60 + int(fuseau[3:5])) * (-1 if fuseau[0] == "-" else 1)
    try:
        date = datetime.fromtimestamp(horodatage, timezone(timedelta(minutes=minutes)))
        iso = date.isoformat()
    except (OverflowError, OSError, ValueError):
        iso = ""
    return nom, courriel, horodatage, iso


def separer_commit(contenu: bytes) -> tuple[dict[bytes, list[bytes]], bytes]:
    """En-têtes (lignes de continuation comprises) et message d'un commit."""
    entetes_bruts, _, message = contenu.partition(b"\n\n")
    entetes: dict[bytes, list[bytes]] = {}
    derniere = None
    for ligne in entetes_bruts.split(b"\n"):
        if ligne.startswith(b" ") and derniere is not None:
            entetes[derniere][-1] += b"\n" + ligne[1:]
            continue
        cle, _, valeur = ligne.partition(b" ")
        entetes.setdefault(cle, []).append(valeur)
        derniere = cle
    return entetes, message


def encodage_commit(entetes: dict[bytes, list[bytes]]) -> str:
    """Encodage déclaré par l'en-tête « encoding », s'il est connu de Python."""
    nom = entetes.get(b"encoding", [b"utf-8"])[0].decode("ascii", errors="replace")
    try:
        "".encode(nom)
    except LookupError:
        return "utf-8"
    return nom


def decoder_commit(sha: str, contenu: bytes) -> Commit:
    """Décode un objet commit."""
    entetes, message = separer_commit(contenu)
    encodage = encodage_commit(entetes)
    if b"tree" not in entetes:
        raise ErreurDepot(f"commit sans arbre : {sha}", code=1)
    auteur = decoder_personne(entetes.get(b"author", [b""])[0], encodage)
    commiteur = decoder_personne(entetes.get(b"committer", [b""])[0], encodage)
    texte = message.decode(encodage, errors="replace")
    sujet = texte.strip().split("\n", 1)[0] if texte.strip() else ""
    return Commit(sha=sha, arbre=entetes[b"tree"][0].decode("ascii"),
                  parents=[p.decode("ascii") for p in entetes.get(b"parent", [])],
                  auteur=auteur[0], courriel=auteur[1], date_auteur=auteur[3],
                  horodatage_commit=commiteur[2], date_commit=commiteur[3], sujet=sujet)


def lire_commit(depot: Depot, sha: str, memoire: dict[str, Commit]) -> Commit:
    """Lit un commit (avec mémoire locale)."""
    if sha not in memoire:
        type_nom, contenu = lire_objet(depot, sha)
        if type_nom != "commit":
            raise ErreurDepot(f"{sha} est un objet {type_nom}, pas un commit", code=1)
        memoire[sha] = decoder_commit(sha, contenu)
    return memoire[sha]


def parcourir(depot: Depot, departs: list[str], maximum: int,
              memoire: dict[str, Commit]) -> list[Commit]:
    """Parcours par date de commit décroissante (ordre par défaut de git log)."""
    tas: list[tuple[int, int, str]] = []
    vus: set[str] = set()
    ordre = 0
    for sha in departs:
        if sha not in vus:
            vus.add(sha)
            heapq.heappush(tas, (-lire_commit(depot, sha, memoire).horodatage_commit, ordre, sha))
            ordre += 1
    resultat = []
    while tas and len(resultat) < maximum:
        _, _, sha = heapq.heappop(tas)
        commit = lire_commit(depot, sha, memoire)
        resultat.append(commit)
        for parent in parents_presents(depot, commit, memoire):
            if parent not in vus:
                vus.add(parent)
                heapq.heappush(tas, (-memoire[parent].horodatage_commit, ordre, parent))
                ordre += 1
    return resultat


def parents_presents(depot: Depot, commit: Commit, memoire: dict[str, Commit]) -> list[str]:
    """Parents lisibles ; un parent absent hors clone superficiel est une anomalie."""
    if commit.sha in depot.superficiels:
        return []
    presents = []
    for parent in commit.parents:
        try:
            lire_commit(depot, parent, memoire)
            presents.append(parent)
        except ErreurDepot as exc:
            depot.stats.anomalies.append(f"parent illisible de {commit.sha[:12]} : {exc}")
    return presents


# ---------------------------------------------------------------------------
# Arbres et churn
# ---------------------------------------------------------------------------

def lire_arbre(depot: Depot, sha: str | None) -> dict[str, tuple[str, str]]:
    """Entrées d'un arbre : nom → (mode, empreinte). None = arbre vide."""
    if sha is None:
        return {}
    type_nom, contenu = lire_objet(depot, sha)
    if type_nom != "tree":
        raise ErreurDepot(f"{sha} n'est pas un arbre", code=1)
    entrees = {}
    position = 0
    while position < len(contenu):
        espace = contenu.index(b" ", position)
        nul = contenu.index(b"\x00", espace)
        mode = contenu[position:espace].decode("ascii")
        nom = contenu[espace + 1:nul].decode("utf-8", errors="surrogateescape")
        brut = contenu[nul + 1:nul + 1 + depot.longueur_hash]
        entrees[nom] = (mode, brut.hex())
        position = nul + 1 + depot.longueur_hash
    return entrees


def aplatir(depot: Depot, sha: str | None, prefixe: str) -> Iterator[tuple[str, str]]:
    """Tous les fichiers d'un sous-arbre : (chemin, empreinte de blob)."""
    for nom, (mode, objet) in lire_arbre(depot, sha).items():
        if mode == MODE_ARBRE:
            yield from aplatir(depot, objet, f"{prefixe}{nom}/")
        elif mode != MODE_SOUS_MODULE:
            yield f"{prefixe}{nom}", objet


def comparer_arbres(depot: Depot, avant: str | None, apres: str | None,
                    prefixe: str = "") -> Iterator[tuple[str, str | None, str | None]]:
    """Différences fichier par fichier : (chemin, blob avant, blob après)."""
    gauche = lire_arbre(depot, avant)
    droite = lire_arbre(depot, apres)
    for nom in sorted(set(gauche) | set(droite)):
        a = gauche.get(nom)
        b = droite.get(nom)
        if a == b:
            continue
        a_arbre = a is not None and a[0] == MODE_ARBRE
        b_arbre = b is not None and b[0] == MODE_ARBRE
        if a_arbre and b_arbre:
            yield from comparer_arbres(depot, a[1], b[1], f"{prefixe}{nom}/")
            continue
        if a is not None and b is not None and not (a_arbre or b_arbre) \
                and MODE_SOUS_MODULE not in (a[0], b[0]):
            if a[1] != b[1]:
                yield f"{prefixe}{nom}", a[1], b[1]
            continue
        yield from cote_unique(depot, a, a_arbre, f"{prefixe}{nom}", avant_cote=True)
        yield from cote_unique(depot, b, b_arbre, f"{prefixe}{nom}", avant_cote=False)


def cote_unique(depot: Depot, entree: tuple[str, str] | None, est_arbre: bool, chemin: str,
                avant_cote: bool) -> Iterator[tuple[str, str | None, str | None]]:
    """Un côté seul (ajout, suppression, ou changement de type fichier ↔ dossier)."""
    if entree is None or entree[0] == MODE_SOUS_MODULE:
        return
    fichiers = aplatir(depot, entree[1], chemin + "/") if est_arbre else [(chemin, entree[1])]
    for sous_chemin, blob in fichiers:
        yield (sous_chemin, blob, None) if avant_cote else (sous_chemin, None, blob)


def apparier_renommages(changements: list[tuple[str, str | None, str | None]]
                        ) -> tuple[list[tuple[str, str | None, str | None]], list[tuple[str, str]]]:
    """Apparie suppression et ajout d'un même blob : renommage exact."""
    supprimes: dict[str, list[str]] = {}
    for chemin, avant, apres in changements:
        if apres is None and avant is not None:
            supprimes.setdefault(avant, []).append(chemin)
    renommages = []
    retenus = []
    apparies: set[str] = set()
    for chemin, avant, apres in changements:
        if avant is None and apres is not None and supprimes.get(apres):
            ancien = supprimes[apres].pop(0)
            renommages.append((ancien, chemin))
            apparies.add(ancien)
            continue
        retenus.append((chemin, avant, apres))
    retenus = [c for c in retenus if not (c[2] is None and c[0] in apparies)]
    return retenus, renommages


def lignes_blob(depot: Depot, sha: str | None) -> list[bytes] | None:
    """Lignes d'un blob texte ; None si binaire ou trop gros (non mesurable)."""
    if sha is None:
        return []
    lieu = localiser_objet(depot, sha)
    if lieu is None:
        return None
    _, contenu = lire_objet(depot, sha)
    if len(contenu) > MAX_BLOB_LIGNES or b"\x00" in contenu[:8000]:
        return None
    return contenu.splitlines()


def compter_lignes(depot: Depot, avant: str | None, apres: str | None) -> tuple[int, int] | None:
    """(ajoutées, supprimées) entre deux blobs, par difflib ; None si non mesurable."""
    a = lignes_blob(depot, avant)
    b = lignes_blob(depot, apres)
    if a is None or b is None:
        return None
    ajouts = suppressions = 0
    comparateur = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for etiquette, i1, i2, j1, j2 in comparateur.get_opcodes():
        if etiquette != "equal":
            suppressions += i2 - i1
            ajouts += j2 - j1
    return ajouts, suppressions


@dataclass
class Churn:
    """Cumul par fichier sur les commits comparés."""

    commits: Counter = field(default_factory=Counter)
    ajouts: Counter = field(default_factory=Counter)
    suppressions: Counter = field(default_factory=Counter)
    non_mesures: Counter = field(default_factory=Counter)
    renommages: list[dict[str, str]] = field(default_factory=list)
    compares: int = 0
    fusions: int = 0
    bords: int = 0


def cumuler_commit(depot: Depot, commit: Commit, memoire: dict[str, Commit], churn: Churn,
                   avec_lignes: bool) -> None:
    """Ajoute au churn les fichiers touchés par un commit non-fusion."""
    if len(commit.parents) > 1:
        churn.fusions += 1
        return
    if commit.sha in depot.superficiels or (commit.parents and commit.parents[0] not in memoire):
        churn.bords += 1
        return
    arbre_parent = memoire[commit.parents[0]].arbre if commit.parents else None
    changements, renommages = apparier_renommages(
        list(comparer_arbres(depot, arbre_parent, commit.arbre)))
    churn.compares += 1
    for ancien, nouveau in renommages:
        churn.renommages.append({"commit": commit.sha[:12], "de": ancien, "vers": nouveau})
        churn.commits[nouveau] += 1
    for chemin, avant, apres in changements:
        churn.commits[chemin] += 1
        if avec_lignes:
            try:
                compte = compter_lignes(depot, avant, apres)
            except ErreurDepot as exc:
                depot.stats.anomalies.append(f"{chemin} dans {commit.sha[:12]} : {exc}")
                compte = None
            if compte is None:
                churn.non_mesures[chemin] += 1
            else:
                churn.ajouts[chemin] += compte[0]
                churn.suppressions[chemin] += compte[1]


def resumer_churn(churn: Churn, avec_lignes: bool, limite: int) -> dict[str, Any]:
    """Classement des fichiers les plus touchés."""
    fichiers = []
    for chemin, nombre in sorted(churn.commits.items(), key=lambda kv: (-kv[1], kv[0]))[:limite]:
        ligne: dict[str, Any] = {"chemin": chemin, "commits": nombre}
        if avec_lignes:
            ligne.update({"ajouts": churn.ajouts[chemin], "suppressions": churn.suppressions[chemin],
                          "non_mesures": churn.non_mesures[chemin]})
        fichiers.append(ligne)
    return {"commits_compares": churn.compares, "fusions_ignorees": churn.fusions,
            "bords_superficiels_ignores": churn.bords, "fichiers_distincts": len(churn.commits),
            "fichiers": fichiers, "renommages_exacts": churn.renommages[:MAX_EXAMINES],
            "lignes": "difflib" if avec_lignes else None}


def resumer_auteurs(commits: list[Commit]) -> list[dict[str, Any]]:
    """Auteurs par nombre de commits, avec premières et dernières dates."""
    groupes: dict[tuple[str, str], list[Commit]] = {}
    for commit in commits:
        groupes.setdefault((commit.auteur, commit.courriel), []).append(commit)
    lignes = []
    for (nom, courriel), liste in groupes.items():
        dates = sorted(c.date_auteur for c in liste if c.date_auteur)
        lignes.append({"auteur": nom, "courriel": courriel, "commits": len(liste),
                       "premier": dates[0] if dates else "", "dernier": dates[-1] if dates else ""})
    return sorted(lignes, key=lambda l: (-l["commits"], l["auteur"]))


# ---------------------------------------------------------------------------
# Contrôle croisé optionnel
# ---------------------------------------------------------------------------

def controler_avec_dulwich(chemin: Path, departs: list[str], maximum: int,
                           commits: list[Commit]) -> dict[str, Any]:
    """Relit la même liste de commits avec dulwich et rapporte les écarts."""
    if dulwich_repo is None:
        return {"bibliotheque": None}
    try:
        depot = dulwich_repo.Repo(str(chemin))
        marcheur = depot.get_walker(include=[s.encode("ascii") for s in departs],
                                    max_entries=maximum)
        lus = {e.commit.id.decode("ascii"): e.commit for e in marcheur}
    except (OSError, KeyError, ValueError) as exc:
        return {"bibliotheque": "dulwich", "erreur": f"{type(exc).__name__}: {exc}"}
    nos = {c.sha: c for c in commits}
    ecarts = sorted(set(nos) ^ set(lus))
    divergences = [sha for sha in set(nos) & set(lus)
                   if [p.decode("ascii") for p in lus[sha].parents] != nos[sha].parents
                   or lus[sha].tree.decode("ascii") != nos[sha].arbre]
    return {"bibliotheque": "dulwich", "commits_lus": len(lus), "concordance":
            not ecarts and not divergences, "absents_d_un_cote": ecarts[:MAX_EXAMINES],
            "metadonnees_divergentes": sorted(divergences)[:MAX_EXAMINES]}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def contrat() -> dict[str, str]:
    """Contrat de mesure extrait de la docstring du module."""
    texte = __doc__ or ""
    sections: dict[str, str] = {}
    for rang, titre in enumerate(TITRES_CONTRAT):
        suivants = "|".join(re.escape(t) for t in TITRES_CONTRAT[rang + 1:]) or r"\Z"
        trouve = re.search(rf"^{re.escape(titre)}\s*\n(.*?)(?=^(?:{suivants})\s*$|\Z)", texte,
                           re.MULTILINE | re.DOTALL)
        sections[titre] = " ".join(trouve.group(1).split()) if trouve else ""
    return sections


def decrire_stockage(depot: Depot) -> dict[str, Any]:
    """Ce que contient le stockage d'objets (comptage des index, sans tout lire)."""
    isoles = 0
    for dossier in depot.dossiers_objets:
        for sous in dossier.glob("[0-9a-f][0-9a-f]"):
            isoles += sum(1 for _ in sous.iterdir())
    s = depot.stats
    return {"objets_isoles_presents": isoles,
            "packs": [{"nom": i.chemin_pack.name, "objets": i.nombre, "version_index": i.version}
                      for i in depot.packs],
            "objets_empaquetes_presents": sum(i.nombre for i in depot.packs),
            "lus": {"isoles": s.objets_isoles_lus, "empaquetes": s.objets_empaquetes_lus,
                    NOM_OFS_DELTA: s.deltas_ofs, NOM_REF_DELTA: s.deltas_ref,
                    "empreintes_verifiees": s.objets_verifies}}


def analyser(chemin: Path, refs_depart: list[str], maximum: int, avec_lignes: bool,
             limite_fichiers: int) -> dict[str, Any]:
    """Ouvre le dépôt, parcourt l'historique et assemble le rapport."""
    depot = ouvrir_depot(chemin)
    packees = lire_packed_refs(depot.commondir)
    refs = inventorier_refs(depot, packees)
    head = decrire_head(depot, packees)
    noms = refs_depart or [NOM_HEAD]
    if noms == [NOM_HEAD] and head["sha"] is None:
        raise ErreurDepot(f"{NOM_HEAD} ne pointe sur aucun commit (dépôt vide) : dénominateur nul, "
                          "rien à examiner", code=3)
    departs = [resoudre_depart(depot, nom, refs, packees) for nom in noms]
    memoire: dict[str, Commit] = {}
    commits = parcourir(depot, departs, maximum, memoire)
    churn = Churn()
    for commit in commits:
        cumuler_commit(depot, commit, memoire, churn, avec_lignes)
    rapport = assembler(depot, head, refs, commits, churn, avec_lignes, limite_fichiers)
    rapport["departs"] = dict(zip(noms, departs))
    rapport["controle"] = controler_avec_dulwich(chemin, departs, maximum, commits)
    return rapport


def assembler(depot: Depot, head: dict[str, Any], refs: dict[str, Any], commits: list[Commit],
              churn: Churn, avec_lignes: bool, limite_fichiers: int) -> dict[str, Any]:
    """Rapport JSON (sans le contrôle croisé)."""
    return {
        "outil": "lire_historique_git", "moteur": "stdlib", "contrat": contrat(),
        "depot": str(depot.chemin), "gitdir": str(depot.gitdir),
        "format_objets": depot.format_objets, "head": head,
        "branches": refs["branches"], "etiquettes": refs["etiquettes"][:MAX_EXAMINES],
        "superficiel": bool(depot.superficiels),
        "alternates": [str(d) for d in depot.dossiers_objets[1:]],
        "denominateur": len(commits),
        "examines": [c.sha[:12] for c in commits[:MAX_EXAMINES]],
        "examines_tronques": len(commits) > MAX_EXAMINES,
        "commits": [{"sha": c.sha, "parents": c.parents, "auteur": c.auteur,
                     "courriel": c.courriel, "date": c.date_auteur, "date_commit": c.date_commit,
                     "fusion": len(c.parents) > 1, "sujet": c.sujet} for c in commits],
        "fusions": sum(1 for c in commits if len(c.parents) > 1),
        "auteurs": resumer_auteurs(commits),
        "churn": resumer_churn(churn, avec_lignes, limite_fichiers),
        "stockage": decrire_stockage(depot),
        "anomalies": depot.stats.anomalies,
    }


def code_de_sortie(rapport: dict[str, Any]) -> int:
    """1 si anomalie du dépôt (objet illisible ou corrompu, parent manquant) ; 0 sinon.

    Une divergence avec dulwich est rapportée (JSON et stderr) sans changer le code : le
    code de sortie ne doit pas dépendre des bibliothèques installées.
    """
    if rapport.get("controle", {}).get("concordance") is False:
        print("divergence avec dulwich : voir « controle » dans le rapport", file=sys.stderr)
    return 1 if rapport["anomalies"] else 0


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    head = rapport["head"]
    print(f"Dépôt : {rapport['depot']} ({rapport['format_objets']}"
          f"{', superficiel' if rapport['superficiel'] else ''})")
    print(f"{NOM_HEAD} : {head['branche'] or 'détaché'} → {head['sha']}")
    print(f"Branches : {len(rapport['branches'])}, étiquettes : {len(rapport['etiquettes'])}")
    stock = rapport["stockage"]
    print(f"Objets : {stock['objets_isoles_presents']} isolés, "
          f"{stock['objets_empaquetes_presents']} en {len(stock['packs'])} pack(s) ; lus : "
          f"{stock['lus']}")
    print(f"\n{rapport['denominateur']} commit(s) examiné(s), dont {rapport['fusions']} fusion(s) :")
    for c in rapport["commits"][:20]:
        print(f"  {c['sha'][:10]} {c['date'][:10]} {c['auteur'][:20]:20} {c['sujet'][:70]}")
    print("\nAuteurs :")
    for a in rapport["auteurs"][:10]:
        print(f"  {a['commits']:4} {a['auteur']} <{a['courriel']}>")
    afficher_churn(rapport["churn"])
    for anomalie in rapport["anomalies"]:
        print(f"ANOMALIE : {anomalie}")
    controle = rapport["controle"]
    if controle.get("bibliotheque"):
        print(f"\nContrôle dulwich : concordance = {controle.get('concordance')}")


def afficher_churn(churn: dict[str, Any]) -> None:
    """Fichiers les plus touchés."""
    print(f"\nChurn sur {churn['commits_compares']} commit(s) comparé(s) "
          f"({churn['fusions_ignorees']} fusion(s) ignorée(s)) :")
    for f in churn["fichiers"][:20]:
        lignes = f" +{f['ajouts']} -{f['suppressions']}" if churn["lignes"] else ""
        if churn["lignes"] and f["non_mesures"]:
            lignes += f" (non mesuré dans {f['non_mesures']} commit(s) : binaire ou > 1 Mo)"
        print(f"  {f['commits']:4} {f['chemin']}{lignes}")
    for r in churn["renommages_exacts"][:10]:
        print(f"  renommage {r['commit']} : {r['de']} → {r['vers']}")


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Lit l'historique d'un dépôt git directement dans .git (objets isolés, "
                    "packfiles et deltas), sans lancer git : commits, auteurs, fichiers les "
                    "plus modifiés.",
        epilog="Exemple : python outils/lire_historique_git.py . --nombre 50 --lignes --json")
    parseur.add_argument("depot", nargs="?", default=".",
                         help="racine du dépôt (dossier contenant .git, ou dépôt nu) ; défaut : .")
    parseur.add_argument("--ref", action="append", default=[],
                         help=f"point de départ (branche, étiquette, empreinte) ; répétable ; "
                              f"défaut : {NOM_HEAD}")
    parseur.add_argument("--nombre", "-n", type=int, default=100,
                         help="nombre maximal de commits examinés (défaut : 100)")
    parseur.add_argument("--lignes", action="store_true",
                         help="compte aussi les lignes ajoutées/supprimées (lit les blobs)")
    parseur.add_argument("--fichiers", type=int, default=30,
                         help="nombre de fichiers du classement de churn (défaut : 30)")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def rapport_vide(message: str) -> dict[str, Any]:
    """Sortie JSON minimale quand rien n'a pu être examiné."""
    return {"outil": "lire_historique_git", "moteur": "stdlib", "denominateur": 0,
            "examines": [], "erreur": message}


def main() -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args()
    if args.nombre < 1 or args.fichiers < 0:
        print("erreur : --nombre doit valoir au moins 1 et --fichiers être positif", file=sys.stderr)
        return 2
    chemin = Path(args.depot)
    if not chemin.is_absolute() and args.racine:
        chemin = Path(args.racine) / chemin
    if not chemin.exists():
        print(f"erreur : chemin introuvable : {chemin}", file=sys.stderr)
        return 2
    if not chemin.is_dir():
        print(f"erreur : {chemin} n'est pas un dossier ; attendu : la racine d'un dépôt git",
              file=sys.stderr)
        return 2
    if dulwich_repo is None:
        print("dulwich absent : lecture stdlib seule, sans contre-lecture des commits",
              file=sys.stderr)
    try:
        rapport = analyser(chemin, args.ref, args.nombre, args.lignes, args.fichiers)
    except ErreurDepot as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps(rapport_vide(str(exc)), ensure_ascii=False))
        return exc.code
    if dulwich_repo is not None:
        rapport["moteur"] = "stdlib+dulwich"
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return code_de_sortie(rapport)


__all__ = ["ouvrir_depot", "lire_objet", "appliquer_delta", "analyser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
