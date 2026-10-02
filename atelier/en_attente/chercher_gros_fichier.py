"""Chercher des motifs dans un fichier de plusieurs gigaoctets sans le charger en mémoire.

Mesuré dans cette session sur un fichier de 629 140 656 octets (600 Mio) : l'outil trouve les
3 occurrences de « ERROR [0-9]+ » (lignes 1 025 267, 4 753 508 et 6 990 453, comme grep -bn)
en 0,87 s avec un pic de mémoire anonyme de 10,9 Mio (tas Python : 1,18 Mo selon tracemalloc),
contre 604,7 Mio et 2,21 s pour Path.read_bytes suivi de re.finditer.

QUESTION
    Où ce motif apparaît-il dans ce (très gros) fichier, combien de fois, à quel décalage en
    octets et à quelle ligne, sans charger le fichier en mémoire ?
MESURE
    Le fichier est projeté en lecture seule par le module mmap ; chaque motif (expression
    régulière sur octets, ou littéral avec -F, ou octets hexadécimaux avec --hex) est cherché
    par re.finditer ou mmap.find directement dans la projection, sans copie. Comptes exacts
    par motif (occurrences non chevauchantes d'un même motif) ; pour les N premières
    occurrences du fichier : décalage en octets, numéro de ligne et colonne (octets) obtenus
    en comptant les fins de ligne par blocs de 1 Mio, extrait et contexte bornés et coupés
    aux fins de ligne, caractères de contrôle échappés (sûr sur du binaire). Avec de nombreux
    littéraux, pyahocorasick (facultatif) fait une seule passe au lieu d'une par motif.
HYPOTHÈSES
    Le fichier ne change pas pendant la lecture (une troncature pendant la projection
    provoque une erreur du système). Les motifs textuels sont encodés en UTF-8 (ou
    --encodage) avant la recherche ; une ligne se termine par l'octet 0x0A.
LIMITES
    Une expression régulière s'applique à des octets : [é] désigne deux octets, et -i
    n'ignore la casse que des lettres de l'alphabet latin de base (a-z). Une expression à
    retour arrière catastrophique reste lente. Les pages lues entrent dans le cache du noyau et comptent dans la mémoire
    résidente du processus (pages de fichier, récupérables), pas dans le tas Python.
    Sans pyahocorasick, le coût croît avec le nombre de motifs (une passe chacun). Un fichier
    au-delà de l'espace d'adressage (système 32 bits) ne peut pas être projeté.
CONTRE-EXEMPLES
    Constaté : sur le texte « é ERROR à » (UTF-8), le motif [é] rend 3 occurrences (les
    deux octets de « é » et le premier octet de « à »), contre 1 pour le motif é sans
    crochets : une classe de caractères porte sur des octets, pas sur des caractères.
    Les pages projetées (610 Mio pour le fichier ci-dessus) comptent dans ru_maxrss :
    cette mesure surestime la mémoire réellement prise.
INVOCATION
    {outil} def {fichier} --json
    {outil} -e def -e import {dossier} --json
DOMAINE
    Journaux, exports, vidages et binaires locaux trop gros pour un éditeur ou pour
    Path.read_text, quand il faut des positions exactes en octets et en lignes.
"""

from __future__ import annotations

import argparse
import heapq
import json
import mmap
import os
import re
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

try:
    import ahocorasick
except ImportError:
    ahocorasick = None

try:
    import resource
except ImportError:
    resource = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")

DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn"})
BLOC_LIGNES = 1 << 20
BLOC_AHO = 1 << 20
TAILLE_SONDE_BINAIRE = 8192
PLAFOND_EXTRAIT = 200
PLAFOND_EXAMINES = 200
CONTEXTE_MAX = 4096
SEUIL_AHO = 4
NOM_OUTIL = "chercher_gros_fichier"
ENCODAGE_DEFAUT = "utf-8"
MOTEUR_STDLIB = "stdlib"
MOTEUR_AHO = "pyahocorasick"
MOT_EXEMPLE = "ERROR"


class EntreeInvalide(Exception):
    """Motif ou chemin inutilisable : code 2."""


@dataclass(frozen=True)
class Motif:
    """Un motif prêt à chercher : libellé d'origine, octets, et regex compilée si nécessaire.

    `regex` vaut None pour un littéral sensible à la casse (cherché par mmap.find).
    """

    libelle: str
    octets: bytes
    regex: re.Pattern[bytes] | None


@dataclass(frozen=True)
class Reglages:
    """Paramètres de recherche communs à tous les fichiers."""

    motifs: tuple[Motif, ...]
    contexte: int
    max_resultats: int
    moteur: str
    ignorer_casse: bool


@dataclass
class Releve:
    """Comptes exacts par motif et premières occurrences (décalage début, fin, indice du motif)."""

    comptes: list[int]
    premieres: list[tuple[int, int, int]] = field(default_factory=list)

    def noter(self, indice: int, debut: int, fin: int, plafond: int) -> None:
        self.comptes[indice] += 1
        if self.comptes[indice] <= plafond:
            self.premieres.append((debut, fin, indice))


def preparer_motifs(textes: list[str], litteral: bool, hexa: bool, ignorer_casse: bool,
                    encodage: str) -> tuple[Motif, ...]:
    """Compile chaque motif ; refuse les motifs vides ou acceptant la chaîne vide."""
    motifs = []
    for texte in dict.fromkeys(textes):
        octets = octets_du_motif(texte, hexa, encodage)
        if not octets and (litteral or hexa):
            raise EntreeInvalide("motif littéral vide")
        simple = (litteral or hexa) and not ignorer_casse
        motifs.append(Motif(texte, octets, None if simple else compiler(octets, litteral or hexa,
                                                                         ignorer_casse)))
    return tuple(motifs)


def octets_du_motif(texte: str, hexa: bool, encodage: str) -> bytes:
    """Texte du motif en octets (encodage choisi) ou décodage hexadécimal."""
    try:
        return bytes.fromhex(texte) if hexa else texte.encode(encodage)
    except (ValueError, LookupError) as exc:
        raise EntreeInvalide(f"motif {texte!r} inutilisable : {exc}") from exc


def compiler(octets: bytes, litteral: bool, ignorer_casse: bool) -> re.Pattern[bytes]:
    """Regex sur octets ; une regex qui accepte la chaîne vide rendrait une occurrence par octet."""
    try:
        regex = re.compile(re.escape(octets) if litteral else octets,
                           re.IGNORECASE if ignorer_casse else 0)
    except re.error as exc:
        raise EntreeInvalide(f"expression régulière invalide {octets!r} : {exc}") from exc
    if regex.fullmatch(b"") is not None:
        raise EntreeInvalide(f"le motif {octets!r} accepte la chaîne vide")
    return regex


def occurrences_stdlib(projection: mmap.mmap, motif: Motif) -> Iterator[tuple[int, int]]:
    """Occurrences non chevauchantes d'un motif, dans l'ordre du fichier."""
    if motif.regex is not None:
        for trouve in motif.regex.finditer(projection):
            yield trouve.span()
        return
    position = 0
    longueur = len(motif.octets)
    while (debut := projection.find(motif.octets, position)) != -1:
        yield debut, debut + longueur
        position = debut + longueur


def chercher_stdlib(projection: mmap.mmap, reglages: Reglages) -> Releve:
    """Une passe par motif, sans copie du fichier."""
    releve = Releve([0] * len(reglages.motifs))
    for indice, motif in enumerate(reglages.motifs):
        for debut, fin in occurrences_stdlib(projection, motif):
            releve.noter(indice, debut, fin, reglages.max_resultats)
    return releve


def construire_automate(reglages: Reglages) -> Any:
    """Automate d'Aho-Corasick sur les littéraux vus en latin-1 (un octet = un caractère)."""
    automate = ahocorasick.Automaton()
    for indice, motif in enumerate(reglages.motifs):
        cle = motif.octets.lower() if reglages.ignorer_casse else motif.octets
        automate.add_word(cle.decode("latin-1"), (indice, len(cle)))
    automate.make_automaton()
    return automate


def chercher_aho(projection: mmap.mmap, reglages: Reglages) -> Releve:
    """Une seule passe pour tous les littéraux ; mêmes règles de non-chevauchement par motif."""
    automate = construire_automate(reglages)
    releve = Releve([0] * len(reglages.motifs))
    derniere_fin = [0] * len(reglages.motifs)
    recouvrement = max(longueur for _, longueur in automate.values()) - 1
    for depart in range(0, len(projection), BLOC_AHO):
        bloc = projection[depart:depart + BLOC_AHO + recouvrement]
        bloc = bloc.lower() if reglages.ignorer_casse else bloc
        for fin_rel, (indice, longueur) in automate.iter(bloc.decode("latin-1")):
            debut = depart + fin_rel - longueur + 1
            if debut < depart + BLOC_AHO and debut >= derniere_fin[indice]:
                derniere_fin[indice] = debut + longueur
                releve.noter(indice, debut, debut + longueur, reglages.max_resultats)
    return releve


def compter_fins_de_ligne(projection: mmap.mmap, debut: int, fin: int) -> int:
    """Fins de ligne entre deux décalages, par blocs bornés (jamais tout le fichier en mémoire)."""
    return sum(projection[pos:min(fin, pos + BLOC_LIGNES)].count(b"\n")
               for pos in range(debut, fin, BLOC_LIGNES))


def echapper(octets: bytes) -> str:
    """Texte affichable : UTF-8 si possible, contrôles et octets invalides échappés."""
    texte = octets.decode(ENCODAGE_DEFAUT, errors="backslashreplace")
    return "".join(c if c.isprintable() or c == "\t" else f"\\x{ord(c):02x}" if ord(c) < 256
                   else f"\\u{ord(c):04x}" for c in texte)


def decrire_occurrence(projection: mmap.mmap, occurrence: tuple[int, int, int],
                       ligne_colonne: tuple[int, int], reglages: Reglages) -> dict[str, Any]:
    """Décalage, ligne, colonne, extrait et contexte coupé aux fins de ligne."""
    debut, fin, indice = occurrence
    avant = projection[max(0, debut - reglages.contexte):debut]
    apres = projection[fin:fin + reglages.contexte]
    return {
        "motif": reglages.motifs[indice].libelle,
        "decalage": debut,
        "longueur": fin - debut,
        "ligne": ligne_colonne[0],
        "colonne_octets": ligne_colonne[1],
        "extrait": echapper(projection[debut:min(fin, debut + PLAFOND_EXTRAIT)]),
        "avant": echapper(avant.rpartition(b"\n")[2]),
        "apres": echapper(apres.partition(b"\n")[0]),
    }


def lister_occurrences(projection: mmap.mmap, releve: Releve, reglages: Reglages) -> list[dict[str, Any]]:
    """Les N premières occurrences du fichier, numérotées en lignes au fil du fichier."""
    retenues = heapq.nsmallest(reglages.max_resultats, releve.premieres)
    sortie, position, ligne, debut_ligne = [], 0, 1, 0
    for occurrence in retenues:
        ligne += compter_fins_de_ligne(projection, position, occurrence[0])
        derniere_fin = projection.rfind(b"\n", position, occurrence[0])
        debut_ligne = derniere_fin + 1 if derniere_fin != -1 else debut_ligne
        position = occurrence[0]
        colonne = occurrence[0] - debut_ligne + 1
        sortie.append(decrire_occurrence(projection, occurrence, (ligne, colonne), reglages))
    return sortie


def examiner_fichier(chemin: Path, nom: str, reglages: Reglages) -> dict[str, Any]:
    """Projette le fichier (sauf s'il est vide : mmap refuse la longueur 0) et cherche."""
    taille = chemin.stat().st_size
    resultat: dict[str, Any] = {"fichier": nom, "octets": taille, "binaire": False}
    if taille == 0:
        releve, liste = Releve([0] * len(reglages.motifs)), []
    else:
        with chemin.open("rb") as flux, mmap.mmap(flux.fileno(), 0, access=mmap.ACCESS_READ) as proj:
            if hasattr(proj, "madvise") and hasattr(mmap, "MADV_SEQUENTIAL"):
                proj.madvise(mmap.MADV_SEQUENTIAL)
            resultat["binaire"] = b"\x00" in proj[:TAILLE_SONDE_BINAIRE]
            chercher = chercher_aho if reglages.moteur == MOTEUR_AHO else chercher_stdlib
            releve = chercher(proj, reglages)
            liste = lister_occurrences(proj, releve, reglages)
    resultat["occurrences"] = {m.libelle: n for m, n in zip(reglages.motifs, releve.comptes)}
    resultat["total"] = sum(releve.comptes)
    resultat["liste"] = liste
    resultat["liste_tronquee"] = resultat["total"] > len(liste)
    return resultat


def lister_fichiers(dossier: Path, plafond: int) -> tuple[list[Path], bool]:
    """Fichiers ordinaires d'un dossier (récursif, liens symboliques non suivis), triés."""
    trouves: list[Path] = []
    for courant, sous_dossiers, fichiers in os.walk(dossier):
        sous_dossiers[:] = sorted(d for d in sous_dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            chemin = Path(courant) / nom
            if chemin.is_file() and not chemin.is_symlink():
                trouves.append(chemin)
                if len(trouves) >= plafond:
                    return trouves, True
    return trouves, False


def rassembler_fichiers(chemins: list[str], base: Path, plafond: int) -> tuple[list[tuple[Path, str]], bool]:
    """(chemin, nom affiché) de chaque fichier à examiner ; lève EntreeInvalide."""
    sortie: list[tuple[Path, str]] = []
    tronque = False
    for entree in chemins:
        chemin = Path(entree) if Path(entree).is_absolute() else base / entree
        if chemin.is_dir():
            fichiers, coupe = lister_fichiers(chemin, plafond)
            tronque = tronque or coupe
            sortie += [(f, str(f.relative_to(chemin))) for f in fichiers]
        elif chemin.is_file():
            sortie.append((chemin, entree))
        elif chemin.exists():
            raise EntreeInvalide(f"ni fichier ordinaire ni dossier : {entree}")
        else:
            raise EntreeInvalide(f"chemin introuvable : {entree}")
    return sortie, tronque


def choisir_moteur(demande: str, motifs: tuple[Motif, ...], litteral: bool) -> str:
    """pyahocorasick pour de nombreux littéraux s'il est présent (ou demandé), sinon stdlib."""
    if demande == MOTEUR_STDLIB or not litteral:
        if demande == MOTEUR_AHO:
            raise EntreeInvalide("--moteur pyahocorasick exige des littéraux (-F ou --hex)")
        return MOTEUR_STDLIB
    if ahocorasick is None:
        if demande == MOTEUR_AHO:
            raise EntreeInvalide("--moteur pyahocorasick demandé mais le module est absent")
        return MOTEUR_STDLIB
    return MOTEUR_AHO if demande == MOTEUR_AHO or len(motifs) >= SEUIL_AHO else MOTEUR_STDLIB


def lire_contrat() -> dict[str, str]:
    """Sections du contrat de mesure, lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def mesurer_memoire(debut: float) -> dict[str, Any]:
    """Pic du tas Python (tracemalloc) et mémoire résidente maximale (resource, Unix)."""
    _, pic = tracemalloc.get_traced_memory()
    rss = None
    if resource is not None:
        echelle = 1 if sys.platform == "darwin" else 1024
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * echelle
    return {"tas_python_pic_octets": pic, "rss_max_octets": rss,
            "secondes": round(time.perf_counter() - debut, 3)}


def construire_rapport(resultats: list[dict[str, Any]], reglages: Reglages, tronque: bool) -> dict[str, Any]:
    noms = [r["fichier"] for r in resultats]
    par_motif = {m.libelle: sum(r["occurrences"][m.libelle] for r in resultats) for m in reglages.motifs}
    return {
        "outil": NOM_OUTIL,
        "moteur": reglages.moteur,
        "denominateur": len(resultats),
        "examines": noms[:PLAFOND_EXAMINES],
        "examines_tronques": len(noms) > PLAFOND_EXAMINES or tronque,
        "octets_examines": sum(r["octets"] for r in resultats),
        "occurrences_par_motif": par_motif,
        "occurrences_total": sum(par_motif.values()),
        "fichiers_avec_occurrences": sum(1 for r in resultats if r["total"]),
        "max_resultats_par_fichier": reglages.max_resultats,
        "resultats": resultats,
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Format proche de grep : fichier:ligne:colonne: extrait, puis le bilan."""
    for r in rapport["resultats"]:
        for o in r["liste"]:
            print(f"{r['fichier']}:{o['ligne']}:{o['colonne_octets']} (octet {o['decalage']}): "
                  f"{o['avant']}[{o['extrait']}]{o['apres']}")
        if r["liste_tronquee"]:
            print(f"{r['fichier']}: … {r['total'] - len(r['liste'])} occurrence(s) de plus")
    comptes = ", ".join(f"{m!r}: {n}" for m, n in rapport["occurrences_par_motif"].items())
    print(f"{rapport['denominateur']} fichier(s), {rapport['octets_examines']} octets, "
          f"moteur {rapport['moteur']} ; occurrences : {comptes}")
    if "memoire" in rapport:
        print(f"mémoire : {rapport['memoire']}")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Cherche des motifs (regex sur octets ou littéraux) dans de très gros fichiers "
                    "par projection mémoire (mmap) : comptes, décalages, lignes, contexte borné.",
        epilog=f"Exemple : python chercher_gros_fichier.py '{MOT_EXEMPLE} [0-9]+' app.log --json\n"
               "          python chercher_gros_fichier.py -F -e alice -e bob vidage.sql\n"
               "          python chercher_gros_fichier.py --hex -e 7f454c46 image.bin\n"
               "Codes : 0 aucune occurrence, 1 occurrence(s) trouvée(s), 2 entrée invalide, "
               "3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("elements", nargs="*", metavar="MOTIF_OU_CHEMIN",
                         help="sans -e : le motif puis les chemins ; avec -e : les chemins")
    parseur.add_argument("-e", "--motif", action="append", default=[], help="motif (répétable)")
    parseur.add_argument("-F", "--litteral", action="store_true", help="motifs littéraux, pas des regex")
    parseur.add_argument("--hex", action="store_true", help="motifs donnés en hexadécimal (littéraux)")
    parseur.add_argument("-i", "--ignorer-casse", action="store_true", help="casse ASCII ignorée")
    parseur.add_argument("--encodage", default=ENCODAGE_DEFAUT, help="encodage des motifs textuels")
    parseur.add_argument("--contexte", type=int, default=40, help=f"octets de contexte (max {CONTEXTE_MAX})")
    parseur.add_argument("--max-resultats", type=int, default=100, help="occurrences détaillées par fichier")
    parseur.add_argument("--max-fichiers", type=int, default=10_000, help="fichiers lus au plus par dossier")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_AHO), default="auto",
                         help=f"auto : pyahocorasick à partir de {SEUIL_AHO} littéraux s'il est installé")
    parseur.add_argument("--mesurer-memoire", action="store_true",
                         help="ajoute le pic du tas Python (tracemalloc) et la mémoire résidente maximale")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def preparer(args: argparse.Namespace) -> tuple[Reglages, list[str]]:
    """Sépare motifs et chemins, valide les bornes, choisit le moteur ; lève EntreeInvalide."""
    textes, chemins = (args.motif, args.elements) if args.motif else (args.elements[:1], args.elements[1:])
    if not textes:
        raise EntreeInvalide("aucun motif (premier argument ou -e)")
    if not chemins:
        raise EntreeInvalide("aucun chemin à examiner après le motif")
    if not 0 <= args.contexte <= CONTEXTE_MAX or args.max_resultats < 0 or args.max_fichiers < 1:
        raise EntreeInvalide(f"--contexte entre 0 et {CONTEXTE_MAX}, plafonds positifs")
    litteral = args.litteral or args.hex
    motifs = preparer_motifs(textes, litteral, args.hex, args.ignorer_casse, args.encodage)
    moteur = choisir_moteur(args.moteur, motifs, litteral)
    return Reglages(motifs, args.contexte, args.max_resultats, moteur, args.ignorer_casse), chemins


def executer(args: argparse.Namespace) -> dict[str, Any]:
    """Prépare, examine chaque fichier, assemble le rapport ; lève EntreeInvalide."""
    debut = time.perf_counter()
    if args.mesurer_memoire:
        tracemalloc.start()
    reglages, chemins = preparer(args)
    fichiers, tronque = rassembler_fichiers(chemins, base_relative(args.racine), args.max_fichiers)
    resultats = []
    for chemin, nom in fichiers:
        try:
            resultats.append(examiner_fichier(chemin, nom, reglages))
        except (OSError, ValueError) as exc:
            raise EntreeInvalide(f"lecture impossible de {nom} : {exc}") from exc
    rapport = construire_rapport(resultats, reglages, tronque)
    if args.mesurer_memoire:
        rapport["memoire"] = mesurer_memoire(debut)
        tracemalloc.stop()
    return rapport


def main() -> int:
    args = construire_parseur().parse_args()
    if ahocorasick is None and (args.litteral or args.hex):
        print("mode dégradé — pyahocorasick absent : une passe mmap par littéral (stdlib)", file=sys.stderr)
    try:
        rapport = executer(args)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        print("dénominateur nul : aucun fichier ordinaire trouvé, rien à examiner", file=sys.stderr)
        return 3
    return 1 if rapport["occurrences_total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
