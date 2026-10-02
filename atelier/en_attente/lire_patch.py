r"""Lit un diff unifié (git ou non) et dit, sans rien écrire, s'il s'applique sur un arbre donné.

Mesuré le 2026-10-02 sur un hunk dont l'en-tête annonce « -4,6 +4,7 » pour 7 et 9 lignes
réelles (l'erreur typique d'un patch écrit à la main ou par un agent) : `git apply --check`
répond « corrupt patch at line 14 » (code 128), `patch --dry-run` « malformed patch at line 12 »
(code 2), unidiff 1.0.1 lève « Hunk is longer than expected », whatthepatch 1.0.7 l'accepte sans
rien dire ; aucun ne donne l'en-tête juste. Cet outil répond « @@ -4,7 +4,9 @@ » et dit que le
hunk ainsi corrigé s'applique.

QUESTION
    Que change ce diff unifié, et s'applique-t-il proprement sur cet arbre ?
MESURE
    Analyse ligne à ligne du patch : en-têtes git (diff --git, modes, index, similarité,
    renommage, copie), chemins --- / +++ (guillemets C de git décodés, horodatages retirés),
    hunks @@ dont les comptes annoncés sont confrontés aux lignes réellement présentes (en-tête
    corrigé proposé en cas d'écart), marqueurs « \ No newline at end of file », binaires (« Binary
    files … differ » et « GIT binary patch » décodé : base85 + zlib + delta). Avec --verifier
    DOSSIER : application à blanc, en mémoire, fichier par fichier et dans l'ordre (une série
    de patchs d'un dossier s'enchaîne) : position annoncée d'abord, puis décalage croissant
    autour d'elle, contexte exact, sans flou ; hunk rejeté → meilleure correspondance trouvée
    et première ligne qui diffère. Si l'en-tête « index » est présent, l'empreinte de blob git
    du fichier avant et après application est comparée à celle du patch. Si unidiff ou
    whatthepatch est importable, le patch est relu par la bibliothèque et les comptes comparés.
HYPOTHÈSES
    Le patch est un diff unifié (diff -u, git diff, git format-patch, éventuellement plusieurs
    à la suite, précédés d'un courriel ou d'un message). Le texte est décodé en UTF-8 sans
    perte (octets invalides conservés). L'arbre donné à --verifier est la racine à laquelle
    les chemins du patch se rapportent après retrait du niveau -p.
LIMITES
    Diffs contextuels (*** ---) et combinés (diff --cc) refusés, non devinés. Pas de flou
    (fuzz) : un hunk dont une ligne de contexte diffère est rejeté, là où `patch` l'accepterait
    avec « fuzz 1 ». Un hunk sans aucune ligne de contexte est posé à la ligne annoncée, alors
    que `git apply` le refuse sans --unidiff-zero. Pas de contrôle du bit exécutable sur
    disque. Fichiers de plus de 64 Mo non lus. Rien n'est jamais écrit.
CONTRE-EXEMPLES
    Constaté : un fichier contient deux fois le bloc « def f(): / x = 1 / return x » ; un
    patch à une ligne de contexte vise le second (ligne 50), puis 30 lignes sont insérées
    avant le premier. L'outil annonce « s'applique, décalé de 11 » et modifie le premier bloc
    (ligne 62) : la position la plus proche gagne. `patch` et `git apply` font la même erreur.
    Le décalage est rapporté ; seul l'en-tête « index » (préimage non conforme) l'aurait
    trahi. whatthepatch 1.0.7 compte aussi 1 ajout et 1 suppression dans un bloc « GIT binary
    patch » : l'écart rapporté est alors le sien.
INVOCATION
    {outil} --texte="--- /dev/null\n+++ b/note.txt\n@@ -0,0 +1,2 @@\n+un\n+deux" --verifier {dossier} --json
DOMAINE
    Patchs produits par git, diff -u ou un agent, avant de les appliquer : revue de ce qu'ils
    touchent et preuve qu'ils s'appliquent (ou pourquoi ils ne s'appliquent pas) sur un arbre.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import unidiff
except ImportError:
    unidiff = None

try:
    import whatthepatch
except ImportError:
    whatthepatch = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
NOM_GIT_BINAIRE = "GIT binary patch"
METAVAR_DOSSIER = "DOSSIER"
ENCODAGE = "UTF-8"

MAX_OCTETS = 64 * 1024 * 1024
MAX_EXAMINES = 200
MAX_COUT_RECHERCHE = 5_000_000
NUL_DEVICE = "/dev/null"
EXTENSIONS_PATCH = (".patch", ".diff")
MOTIF_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
MOTIF_INDEX = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: (\d+))?$")
ENTETES_ETENDUS = ("old mode ", "new mode ", "deleted file mode ", "new file mode ",
                   "copy from ", "copy to ", "rename from ", "rename to ", "rename old ",
                   "rename new ", "similarity index ", "dissimilarity index ", "index ")
ECHAPPEMENTS_C = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ou rien à examiner (code 3)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class Hunk:
    """Un hunk @@ et ses lignes : (opération, contenu sans fin de ligne, fin de ligne présente)."""

    ancien_debut: int
    ancien_longueur: int
    nouveau_debut: int
    nouveau_longueur: int
    section: str
    ligne: int
    lignes: list[tuple[str, str, bool]] = field(default_factory=list)
    defaut: str | None = None
    entete_corrige: str | None = None


@dataclass
class Binaire:
    """Bloc « GIT binary patch » vers l'avant : literal (contenu) ou delta."""

    nature: str
    taille: int
    donnees: bytes


@dataclass
class Entree:
    """Un fichier touché par le patch."""

    source: str
    ligne: int
    git: bool = False
    ancien: str | None = None
    nouveau: str | None = None
    ancien_mode: str | None = None
    nouveau_mode: str | None = None
    index_ancien: str | None = None
    index_nouveau: str | None = None
    similarite: int | None = None
    renomme: bool = False
    copie: bool = False
    cree: bool = False
    supprime: bool = False
    binaire: bool = False
    bloc_binaire: Binaire | None = None
    chemins_lus: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    defauts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Lecture de l'entrée
# ---------------------------------------------------------------------------

def decoder_echappements(texte: str) -> str:
    """\\n, \\t et \\\\ d'un texte en ligne qui ne contient aucun vrai saut de ligne."""
    if "\n" in texte:
        return texte
    return re.sub(r"\\([nt\\])", lambda m: {"n": "\n", "t": "\t", "\\": "\\"}[m.group(1)], texte)


def lire_octets(chemin: Path) -> bytes:
    """Lit un fichier borné, refuse le binaire."""
    try:
        with chemin.open("rb") as flux:
            donnees = flux.read(MAX_OCTETS + 1)
    except OSError as exc:
        raise ErreurEntree(f"lecture impossible de {chemin} : {exc.strerror}") from exc
    if len(donnees) > MAX_OCTETS:
        raise ErreurEntree(f"{chemin} dépasse {MAX_OCTETS // (1024 * 1024)} Mo")
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas un patch")
    return donnees


def sources_du_chemin(chemin: Path) -> list[Path]:
    """Un fichier, ou les *.patch / *.diff d'un dossier (récursif, ordre alphabétique)."""
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    if chemin.is_file():
        return [chemin]
    trouves = sorted(p for p in chemin.rglob("*") if p.is_file()
                     and p.suffix.lower() in EXTENSIONS_PATCH and ".git" not in p.parts)
    if not trouves:
        raise ErreurEntree(f"aucun fichier .patch ou .diff dans {chemin} : dénominateur nul, "
                           "rien à examiner", code=3)
    return trouves


def decouper_lignes(texte: str) -> tuple[list[str], bool]:
    """Lignes sans fin de ligne ; retire les CR si tout le patch est en CRLF."""
    brutes = texte.split("\n")
    if brutes and brutes[-1] == "":
        brutes.pop()
    crlf = bool(brutes) and all(l.endswith("\r") for l in brutes)
    return ([l[:-1] for l in brutes] if crlf else brutes), crlf


# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------

def lire_chaine_c(texte: str, debut: int) -> tuple[str, int]:
    """Chaîne entre guillemets à la manière de git (octal \\303\\251) → (texte, fin)."""
    octets = bytearray()
    i = debut + 1
    while i < len(texte) and texte[i] != '"':
        if texte[i] == "\\" and i + 1 < len(texte):
            suivant = texte[i + 1]
            if suivant in "01234567" and re.match(r"[0-7]{3}", texte[i + 1:i + 4]):
                octets.append(int(texte[i + 1:i + 4], 8))
                i += 4
                continue
            octets.append(ECHAPPEMENTS_C.get(suivant, ord(suivant) & 0xFF))
            i += 2
            continue
        octets += texte[i].encode(ENCODAGE, errors="surrogateescape")
        i += 1
    return octets.decode(ENCODAGE, errors="surrogateescape"), i + 1


def chemin_de_ligne(reste: str) -> str:
    """Chemin d'une ligne --- / +++ : guillemets décodés, horodatage après tabulation retiré."""
    if reste.startswith('"'):
        return lire_chaine_c(reste, 0)[0]
    return reste.split("\t", 1)[0].rstrip()


def chemins_entete_git(reste: str) -> tuple[str | None, str | None]:
    """« a/x b/x » de diff --git, y compris noms à espaces ou entre guillemets."""
    if reste.startswith('"'):
        ancien, fin = lire_chaine_c(reste, 0)
        suite = reste[fin:].lstrip()
        nouveau = lire_chaine_c(suite, 0)[0] if suite.startswith('"') else suite
        return ancien, nouveau
    positions = [m.start() for m in re.finditer(" ", reste)]
    for pos in positions:
        gauche, droite = reste[:pos], reste[pos + 1:]
        if gauche.partition("/")[2] == droite.partition("/")[2]:
            return gauche, droite
    if positions:
        return reste[:positions[0]], reste[positions[0] + 1:]
    return None, None


def retirer_niveau(chemin: str | None, niveau: int) -> str | None:
    """Retire les `niveau` premiers composants (option -p de patch)."""
    if chemin is None or chemin == NUL_DEVICE:
        return None
    morceaux = [m for m in chemin.split("/") if m not in ("", ".")]
    return "/".join(morceaux[niveau:]) if len(morceaux) > niveau else None


# ---------------------------------------------------------------------------
# Analyse du patch
# ---------------------------------------------------------------------------

def compter_hunk(lignes: list[tuple[str, str, bool]]) -> tuple[int, int]:
    """(lignes côté ancien, lignes côté nouveau) réellement présentes."""
    ancien = sum(1 for op, _, _ in lignes if op in " -")
    nouveau = sum(1 for op, _, _ in lignes if op in " +")
    return ancien, nouveau


def marquer_sans_fin(lignes: list[tuple[str, str, bool]]) -> None:
    """« \\ No newline at end of file » : la ligne précédente n'a pas de fin de ligne."""
    if lignes:
        op, contenu, _ = lignes[-1]
        lignes[-1] = (op, contenu, False)


def est_debut_entete(lignes: list[str], i: int) -> bool:
    """Ligne qui ouvre un nouvel en-tête (et ferme donc un hunk)."""
    ligne = lignes[i]
    if ligne.startswith(("@@ ", "diff ", "Binary files ", "index ")) or ligne == NOM_GIT_BINAIRE:
        return True
    return ligne.startswith("--- ") and i + 1 < len(lignes) and lignes[i + 1].startswith("+++ ")


def operation_de(ligne: str) -> str | None:
    """Opération d'une ligne de corps de hunk ; ligne vide = contexte vide abîmé."""
    if ligne == "":
        return " "
    return ligne[0] if ligne[0] in " +-\\" else None


def lire_corps(lignes: list[str], i: int, hunk: Hunk) -> int:
    """Consomme le corps selon les comptes annoncés ; rend l'indice suivant."""
    reste_a, reste_n = hunk.ancien_longueur, hunk.nouveau_longueur
    while (reste_a > 0 or reste_n > 0) and i < len(lignes):
        op = operation_de(lignes[i])
        if op is None or (op != " " and est_debut_entete(lignes, i)):
            break
        if op == "\\":
            marquer_sans_fin(hunk.lignes)
            i += 1
            continue
        if (op == "-" and reste_a == 0) or (op == "+" and reste_n == 0) or \
                (op == " " and (reste_a == 0 or reste_n == 0)):
            break
        hunk.lignes.append((op, lignes[i][1:], True))
        reste_a -= op in " -"
        reste_n -= op in " +"
        i += 1
    while i < len(lignes) and lignes[i].startswith("\\"):
        marquer_sans_fin(hunk.lignes)
        i += 1
    return i


def lire_debordement(lignes: list[str], i: int, hunk: Hunk) -> int:
    """Lignes +/-/espace au-delà des comptes : en-tête faux, comme souvent chez un agent."""
    suite: list[tuple[str, str, bool]] = []
    j = i
    while j < len(lignes) and lignes[j] != "-- " and not est_debut_entete(lignes, j):
        op = operation_de(lignes[j])
        if op is None or lignes[j] == "":
            break
        if op == "\\":
            marquer_sans_fin(suite)
        else:
            suite.append((op, lignes[j][1:], True))
        j += 1
    hunk.lignes.extend(suite)
    return j


def lire_hunk(lignes: list[str], i: int, entree: Entree) -> int:
    """Lit un hunk à partir de son en-tête ; signale tout écart de comptes."""
    trouve = MOTIF_HUNK.match(lignes[i])
    if not trouve:
        entree.defauts.append(f"ligne {i + 1} : en-tête de hunk illisible : {lignes[i][:80]}")
        return i + 1
    a, la, n, ln, section = trouve.groups()
    hunk = Hunk(int(a), 1 if la is None else int(la), int(n), 1 if ln is None else int(ln),
                section, i + 1)
    suite = lire_corps(lignes, i + 1, hunk)
    suite = lire_debordement(lignes, suite, hunk)
    controler_comptes(hunk)
    entree.hunks.append(hunk)
    return suite


def debut_corrige(debut: int, annonce: int, reel: int) -> int:
    """Une longueur nulle désigne la ligne APRÈS laquelle on insère : décaler d'un cran."""
    if (annonce == 0) == (reel == 0):
        return debut
    return debut + 1 if annonce == 0 else max(debut - 1, 0)


def controler_comptes(hunk: Hunk) -> None:
    """Compare les comptes annoncés aux lignes présentes ; propose l'en-tête juste."""
    ancien, nouveau = compter_hunk(hunk.lignes)
    if (ancien, nouveau) == (hunk.ancien_longueur, hunk.nouveau_longueur):
        return
    debut_a = debut_corrige(hunk.ancien_debut, hunk.ancien_longueur, ancien)
    debut_n = debut_corrige(hunk.nouveau_debut, hunk.nouveau_longueur, nouveau)
    hunk.entete_corrige = f"@@ -{debut_a},{ancien} +{debut_n},{nouveau} @@"
    hunk.defaut = (f"comptes annoncés -{hunk.ancien_longueur} +{hunk.nouveau_longueur}, "
                   f"lignes présentes -{ancien} +{nouveau}")


def appliquer_entete_etendu(entree: Entree, ligne: str) -> None:
    """Interprète un en-tête étendu git (modes, index, renommage, copie, similarité)."""
    cle, _, valeur = ligne.partition(" ")
    if ligne.startswith(("old mode ", "deleted file mode ")):
        entree.ancien_mode = ligne.rsplit(" ", 1)[1]
        entree.supprime = entree.supprime or ligne.startswith("deleted")
    elif ligne.startswith(("new mode ", "new file mode ")):
        entree.nouveau_mode = ligne.rsplit(" ", 1)[1]
        entree.cree = entree.cree or ligne.startswith("new file")
    elif ligne.startswith(("rename from ", "copy from ", "rename old ")):
        entree.ancien = chemin_de_ligne(ligne.split(" ", 2)[2])
        entree.renomme = cle == "rename"
        entree.copie = cle == "copy"
    elif ligne.startswith(("rename to ", "copy to ", "rename new ")):
        entree.nouveau = chemin_de_ligne(ligne.split(" ", 2)[2])
    elif ligne.startswith(("similarity index ", "dissimilarity index ")):
        entree.similarite = int(re.sub(r"\D", "", valeur.split(" ", 1)[-1]) or 0)
    elif ligne.startswith("index "):
        trouve = MOTIF_INDEX.match(ligne)
        if trouve:
            entree.index_ancien, entree.index_nouveau = trouve.group(1), trouve.group(2)
            entree.nouveau_mode = entree.nouveau_mode or trouve.group(3)
            entree.ancien_mode = entree.ancien_mode or trouve.group(3)


def lire_entete_git(lignes: list[str], i: int, source: str) -> tuple[Entree, int]:
    """diff --git et ses en-têtes étendus."""
    entree = Entree(source=source, ligne=i + 1, git=True)
    ancien, nouveau = chemins_entete_git(lignes[i][len("diff --git "):])
    entree.ancien, entree.nouveau = ancien, nouveau
    i += 1
    while i < len(lignes) and lignes[i].startswith(ENTETES_ETENDUS):
        appliquer_entete_etendu(entree, lignes[i])
        i += 1
    if entree.renomme or entree.copie:
        entree.ancien = prefixer(ancien, entree.ancien)
        entree.nouveau = prefixer(nouveau, entree.nouveau)
    return entree, i


def prefixer(entete: str | None, nom: str | None) -> str | None:
    """Redonne au nom de « rename from/to » le préfixe (a/, b/ ou aucun) de diff --git."""
    if nom is None or entete is None or not entete.endswith(nom):
        return nom
    return entete[:len(entete) - len(nom)] + nom


def lire_chemins(lignes: list[str], i: int, entree: Entree) -> int:
    """Lignes --- / +++ : chemins, création et suppression."""
    ancien = chemin_de_ligne(lignes[i][4:])
    nouveau = chemin_de_ligne(lignes[i + 1][4:])
    entree.cree = entree.cree or ancien == NUL_DEVICE
    entree.supprime = entree.supprime or nouveau == NUL_DEVICE
    entree.chemins_lus = True
    if not (entree.git and (entree.renomme or entree.copie)):
        entree.ancien = None if ancien == NUL_DEVICE else ancien
        entree.nouveau = None if nouveau == NUL_DEVICE else nouveau
    if entree.git and entree.cree:
        entree.ancien = None
    if entree.git and entree.supprime:
        entree.nouveau = None
    return i + 2


def decoder_bloc_base85(lignes: list[str], i: int) -> tuple[bytes, int]:
    """Lignes base85 de git (premier caractère = longueur décodée) jusqu'à la ligne vide."""
    morceaux = bytearray()
    while i < len(lignes) and lignes[i]:
        ligne = lignes[i]
        taille = ord(ligne[0]) - (ord("A") - 1 if ligne[0] <= "Z" else ord("a") - 27)
        try:
            morceaux += base64.b85decode(ligne[1:])[:taille]
        except (ValueError, binascii.Error) as exc:
            raise ErreurEntree(f"ligne {i + 1} : base85 invalide dans un patch binaire") from exc
        i += 1
    return bytes(morceaux), i


def lire_binaire_git(lignes: list[str], i: int, entree: Entree) -> int:
    """« GIT binary patch » : bloc avant (literal/delta), bloc inverse ignoré."""
    entree.binaire = True
    i += 1
    trouve = re.match(r"^(literal|delta) (\d+)$", lignes[i]) if i < len(lignes) else None
    if not trouve:
        entree.defauts.append(f"ligne {i + 1} : bloc binaire sans « literal » ni « delta »")
        return i
    try:
        compresse, i = decoder_bloc_base85(lignes, i + 1)
        donnees = zlib.decompress(compresse)
    except (ErreurEntree, zlib.error) as exc:
        entree.defauts.append(f"patch binaire illisible : {exc}")
        return i
    if len(donnees) != int(trouve.group(2)):
        entree.defauts.append("patch binaire : taille décompressée différente de l'annonce")
    entree.bloc_binaire = Binaire(trouve.group(1), int(trouve.group(2)), donnees)
    while i < len(lignes) and (lignes[i] == "" or re.match(r"^(literal|delta) \d+$", lignes[i])):
        i += 1
        while lignes[i - 1] and i < len(lignes) and lignes[i]:
            i += 1
    return i


def marquer_binaire(courante: Entree | None, ligne: str, source: str, i: int,
                    entrees: list[Entree]) -> Entree:
    """« Binary files A and B differ » : marque l'entrée git en cours, ou en crée une."""
    if courante is not None and courante.git and not courante.hunks and not courante.binaire:
        courante.binaire = True
        return courante
    trouve = re.match(r"^Binary files (.+) and (.+) differ$", ligne)
    entree = Entree(source=source, ligne=i + 1, binaire=True)
    if trouve:
        entree.ancien = None if trouve.group(1) == NUL_DEVICE else trouve.group(1)
        entree.nouveau = None if trouve.group(2) == NUL_DEVICE else trouve.group(2)
        entree.cree, entree.supprime = entree.ancien is None, entree.nouveau is None
    entrees.append(entree)
    return entree


def analyser_patch(texte: str, source: str) -> tuple[list[Entree], list[str], int, bool]:
    """Découpe le patch en entrées ; rend aussi les refus globaux et les lignes ignorées."""
    lignes, crlf = decouper_lignes(texte)
    entrees: list[Entree] = []
    refus: list[str] = []
    ignorees = 0
    courante: Entree | None = None
    i = 0
    while i < len(lignes):
        ligne = lignes[i]
        if ligne.startswith("diff --git "):
            courante, i = lire_entete_git(lignes, i, source)
            entrees.append(courante)
        elif ligne.startswith(("diff --cc ", "diff --combined ")) or ligne.startswith("@@@ "):
            refus.append(f"{source} ligne {i + 1} : diff combiné (fusion) non pris en charge")
            i += 1
        elif ligne.startswith("*** ") and i + 1 < len(lignes) and lignes[i + 1].startswith("--- "):
            refus.append(f"{source} ligne {i + 1} : diff contextuel non pris en charge")
            i += 2
        elif ligne.startswith("--- ") and i + 1 < len(lignes) and lignes[i + 1].startswith("+++ "):
            if courante is None or not courante.git or courante.chemins_lus or courante.hunks:
                courante = Entree(source=source, ligne=i + 1)
                entrees.append(courante)
            i = lire_chemins(lignes, i, courante)
        elif ligne.startswith("@@ ") and courante is not None:
            i = lire_hunk(lignes, i, courante)
        elif ligne == NOM_GIT_BINAIRE and courante is not None:
            i = lire_binaire_git(lignes, i, courante)
        elif ligne.startswith("Binary files ") and ligne.endswith(" differ"):
            courante = marquer_binaire(courante, ligne, source, i, entrees)
            i += 1
        else:
            ignorees += 1
            i += 1
    return entrees, refus, ignorees, crlf


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

def statut(entree: Entree) -> str:
    """Nature du changement."""
    if entree.cree:
        return "ajouté"
    if entree.supprime:
        return "supprimé"
    if entree.renomme:
        return "renommé"
    if entree.copie:
        return "copié"
    if entree.ancien_mode and entree.nouveau_mode and entree.ancien_mode != entree.nouveau_mode \
            and not entree.hunks and not entree.binaire:
        return "mode"
    return "modifié"


def chemin_affiche(entree: Entree) -> str:
    """Chemin lisible : sans préfixe a/ b/ (git, ou diff dont les deux côtés les portent)."""
    brut = entree.nouveau or entree.ancien
    if brut is None:
        return "?"
    prefixes = (entree.ancien or "a/").startswith("a/") and (entree.nouveau or "b/").startswith("b/")
    return retirer_niveau(brut, 1 if entree.git or prefixes else 0) or brut


def decrire_hunk(hunk: Hunk) -> dict[str, Any]:
    """Hunk en JSON."""
    ajouts = sum(1 for op, _, _ in hunk.lignes if op == "+")
    suppressions = sum(1 for op, _, _ in hunk.lignes if op == "-")
    return {"ligne_patch": hunk.ligne, "ancien": [hunk.ancien_debut, hunk.ancien_longueur],
            "nouveau": [hunk.nouveau_debut, hunk.nouveau_longueur], "section": hunk.section,
            "ajouts": ajouts, "suppressions": suppressions,
            "contexte": sum(1 for op, _, _ in hunk.lignes if op == " "),
            "defaut": hunk.defaut, "entete_corrige": hunk.entete_corrige}


def decrire_entree(entree: Entree) -> dict[str, Any]:
    """Entrée en JSON."""
    hunks = [decrire_hunk(h) for h in entree.hunks]
    return {"chemin": chemin_affiche(entree), "statut": statut(entree), "source": entree.source,
            "ligne_patch": entree.ligne, "ancien": entree.ancien, "nouveau": entree.nouveau,
            "git": entree.git, "binaire": entree.binaire,
            "binaire_git": entree.bloc_binaire.nature if entree.bloc_binaire else None,
            "ancien_mode": entree.ancien_mode, "nouveau_mode": entree.nouveau_mode,
            "index": [entree.index_ancien, entree.index_nouveau] if entree.index_ancien else None,
            "similarite": entree.similarite, "hunks": hunks,
            "ajouts": sum(h["ajouts"] for h in hunks),
            "suppressions": sum(h["suppressions"] for h in hunks),
            "defauts": entree.defauts + [f"hunk ligne {h['ligne_patch']} : {h['defaut']}"
                                         for h in hunks if h["defaut"]]}


def totaliser(fichiers: list[dict[str, Any]]) -> dict[str, int]:
    """Totaux par statut et par ligne."""
    totaux = {"fichiers": len(fichiers), "hunks": sum(len(f["hunks"]) for f in fichiers),
              "ajouts": sum(f["ajouts"] for f in fichiers),
              "suppressions": sum(f["suppressions"] for f in fichiers),
              "binaires": sum(1 for f in fichiers if f["binaire"])}
    for nom in ("ajouté", "supprimé", "modifié", "renommé", "copié", "mode"):
        totaux[nom] = sum(1 for f in fichiers if f["statut"] == nom)
    return totaux


# ---------------------------------------------------------------------------
# Application à blanc
# ---------------------------------------------------------------------------

def empreinte_blob(contenu: bytes, longueur: int) -> str:
    """Empreinte de blob git (sha1, ou sha256 si l'abréviation fait 64 caractères)."""
    hacheur = hashlib.sha256 if longueur == 64 else hashlib.sha1
    return hacheur(b"blob %d\x00" % len(contenu) + contenu).hexdigest()


def comparer_index(contenu: bytes | None, attendu: str | None) -> bool | None:
    """L'empreinte du contenu commence-t-elle par l'abréviation du patch ?"""
    if attendu is None or contenu is None or set(attendu) == {"0"}:
        return None
    return empreinte_blob(contenu, len(attendu)).startswith(attendu)


def chemin_sur(arbre: Path, relatif: str | None) -> Path | None:
    """Chemin dans l'arbre ; None s'il en sortirait (absolu, .., lien)."""
    if relatif is None or relatif.startswith("/") or ".." in relatif.split("/"):
        return None
    cible = arbre / relatif
    try:
        if not cible.resolve().is_relative_to(arbre.resolve()):
            return None
    except OSError:
        return None
    return cible


def lire_dans_arbre(arbre: Path, relatif: str, etat: dict[str, bytes | None]) -> bytes | None:
    """Contenu courant d'un fichier : état en mémoire d'abord, puis disque (borné)."""
    if relatif in etat:
        return etat[relatif]
    cible = chemin_sur(arbre, relatif)
    if cible is None or not cible.is_file():
        return None
    try:
        with cible.open("rb") as flux:
            donnees = flux.read(MAX_OCTETS + 1)
    except OSError:
        return None
    return donnees if len(donnees) <= MAX_OCTETS else None


def egal_ligne(ligne_fichier: bytes, contenu: bytes) -> bool:
    """Compare une ligne du fichier (avec sa fin) au contenu attendu (sans fin \\n)."""
    return (ligne_fichier[:-1] if ligne_fichier.endswith(b"\n") else ligne_fichier) == contenu


def correspond(lignes: list[bytes], position: int, attendu: list[bytes]) -> bool:
    """Le bloc attendu est-il exactement à cette position ?"""
    if position < 0 or position + len(attendu) > len(lignes):
        return False
    return all(egal_ligne(lignes[position + k], attendu[k]) for k in range(len(attendu)))


def chercher_position(lignes: list[bytes], attendu: list[bytes], prevue: int,
                      plancher: int) -> int | None:
    """Position annoncée, puis décalages croissants (après le hunk précédent)."""
    if not attendu:
        return min(max(prevue, plancher), len(lignes))
    for ecart in range(0, len(lignes) + 1):
        for position in ((prevue + ecart, prevue - ecart) if ecart else (prevue,)):
            if position >= plancher and correspond(lignes, position, attendu):
                return position
        if prevue - ecart < plancher and prevue + ecart > len(lignes):
            return None
    return None


def meilleure_correspondance(lignes: list[bytes], attendu: list[bytes],
                             plancher: int) -> dict[str, Any] | None:
    """Pour un hunk rejeté : position qui partage le plus de lignes, et première divergence."""
    if not attendu or len(lignes) * len(attendu) > MAX_COUT_RECHERCHE:
        return None
    meilleure = (-1, 0)
    for position in range(plancher, max(len(lignes) - len(attendu), plancher) + 1):
        score = sum(1 for k in range(len(attendu)) if position + k < len(lignes)
                    and egal_ligne(lignes[position + k], attendu[k]))
        if score > meilleure[0]:
            meilleure = (score, position)
    score, position = meilleure
    for k, ligne in enumerate(attendu):
        trouvee = lignes[position + k] if position + k < len(lignes) else None
        if trouvee is None or not egal_ligne(trouvee, ligne):
            return {"ligne_fichier": position + 1, "lignes_concordantes": score,
                    "lignes_attendues": len(attendu), "premiere_divergence": {
                        "ligne_fichier": position + k + 1,
                        "attendu": ligne.decode("utf-8", errors="replace")[:200],
                        "trouve": None if trouvee is None else
                        trouvee.rstrip(b"\r\n").decode("utf-8", errors="replace")[:200]}}
    return None


def encoder(contenu: str) -> bytes:
    """Ligne du patch → octets d'origine."""
    return contenu.encode(ENCODAGE, errors="surrogateescape")


def nouvelles_lignes(hunk: Hunk, lignes: list[bytes], position: int) -> list[bytes]:
    """Lignes produites : contexte repris du fichier, ajouts tirés du patch."""
    sortie = []
    k = position
    for op, contenu, fin in hunk.lignes:
        if op == " ":
            ligne = lignes[k].rstrip(b"\n")
            sortie.append(ligne + b"\n" if fin else ligne)
            k += 1
        elif op == "-":
            k += 1
        else:
            sortie.append(encoder(contenu) + (b"\n" if fin else b""))
    return sortie


def decouper_octets(contenu: bytes) -> list[bytes]:
    """Lignes coupées sur \\n seulement (comme git), fin de ligne conservée."""
    morceaux = contenu.split(b"\n")
    lignes = [m + b"\n" for m in morceaux[:-1]]
    return lignes + ([morceaux[-1]] if morceaux[-1] else [])


def appliquer_hunks(contenu: bytes, hunks: list[Hunk]) -> tuple[bytes, list[dict[str, Any]]]:
    """Applique les hunks en mémoire ; rend le résultat et un rapport par hunk."""
    lignes = decouper_octets(contenu)
    sortie: list[bytes] = []
    curseur = 0
    decalage = 0
    rapports = []
    for hunk in hunks:
        attendu = [encoder(c) for op, c, _ in hunk.lignes if op in " -"]
        prevue = (hunk.ancien_debut - 1 if hunk.ancien_longueur else hunk.ancien_debut) + decalage
        position = None if hunk.defaut else chercher_position(lignes, attendu, prevue, curseur)
        rapport: dict[str, Any] = {"ligne_patch": hunk.ligne, "applique": position is not None}
        if position is None:
            rapport["raison"] = ("en-tête @@ incohérent" if hunk.defaut else
                                 "contexte introuvable")
            corps = chercher_position(lignes, attendu, prevue, curseur) if hunk.defaut else None
            rapport["applicable_si_entete_corrige"] = corps is not None if hunk.defaut else None
            rapport["meilleure_correspondance"] = meilleure_correspondance(lignes, attendu, curseur)
            rapports.append(rapport)
            continue
        rapport["decalage"] = position - (prevue - decalage)
        decalage = rapport["decalage"]
        sortie += lignes[curseur:position]
        sortie += nouvelles_lignes(hunk, lignes, position)
        curseur = position + len(attendu)
        rapports.append(rapport)
    sortie += lignes[curseur:]
    return b"".join(sortie), rapports


def lire_varint(donnees: bytes, position: int) -> tuple[int, int]:
    """Entier 7 bits par octet, poids faible d'abord (format delta de git)."""
    valeur = decal = 0
    while True:
        octet = donnees[position]
        position += 1
        valeur |= (octet & 0x7F) << decal
        decal += 7
        if not octet & 0x80:
            return valeur, position


def decoder_copie(delta: bytes, i: int, op: int) -> tuple[int, int, int]:
    """Instruction de copie d'un delta git : (décalage, taille, position suivante)."""
    decalage = taille = 0
    for bit in range(4):
        if op & (1 << bit):
            decalage |= delta[i] << (8 * bit)
            i += 1
    for bit in range(3):
        if op & (0x10 << bit):
            taille |= delta[i] << (8 * bit)
            i += 1
    return decalage, taille or 0x10000, i


def appliquer_delta_git(base: bytes, delta: bytes) -> bytes | None:
    """Delta binaire git (copie/insertion) ; None s'il ne correspond pas à la base."""
    try:
        source, i = lire_varint(delta, 0)
        cible, i = lire_varint(delta, i)
        if source != len(base):
            return None
        sortie = bytearray()
        while i < len(delta):
            op = delta[i]
            i += 1
            if op & 0x80:
                decalage, taille, i = decoder_copie(delta, i, op)
                if decalage + taille > len(base):
                    return None
                sortie += base[decalage:decalage + taille]
            elif op:
                sortie += delta[i:i + op]
                i += op
            else:
                return None
    except IndexError:
        return None
    return bytes(sortie) if len(sortie) == cible else None


@dataclass
class Verification:
    """Contexte de l'application à blanc."""

    arbre: Path
    niveau: int | None
    etat: dict[str, bytes | None] = field(default_factory=dict)


def niveau_pour(entree: Entree, verif: Verification) -> int:
    """Niveau -p : imposé, ou 1 pour git, ou celui qui désigne un fichier existant."""
    if verif.niveau is not None:
        return verif.niveau
    if entree.git:
        return 1
    for niveau in (0, 1, 2):
        relatif = retirer_niveau(entree.ancien or entree.nouveau, niveau)
        if relatif and lire_dans_arbre(verif.arbre, relatif, verif.etat) is not None:
            return niveau
    return 1 if (entree.ancien or entree.nouveau or "").startswith(("a/", "b/")) else 0


def verifier_entree(entree: Entree, verif: Verification) -> dict[str, Any]:
    """Application à blanc d'une entrée ; met à jour l'état en mémoire."""
    niveau = niveau_pour(entree, verif)
    ancien = None if entree.cree else retirer_niveau(entree.ancien or entree.nouveau, niveau)
    nouveau = None if entree.supprime else retirer_niveau(entree.nouveau or entree.ancien, niveau)
    rapport: dict[str, Any] = {"chemin": chemin_affiche(entree), "niveau": niveau,
                               "cible": nouveau or ancien, "problemes": [], "hunks": []}
    apres = confronter(entree, verif, ancien, nouveau, rapport)
    if rapport["problemes"] or rapport.get("preimage_conforme") is False:
        rapport["deja_applique"] = deja_applique(entree, verif, ancien, nouveau)
    enregistrer(entree, verif, ancien, nouveau, apres, rapport)
    return rapport


def confronter(entree: Entree, verif: Verification, ancien: str | None, nouveau: str | None,
               rapport: dict[str, Any]) -> bytes | None:
    """Contrôles d'existence puis application ; rend le contenu produit."""
    for relatif in (ancien, nouveau):
        if relatif is not None and chemin_sur(verif.arbre, relatif) is None:
            rapport["problemes"].append(f"chemin hors de l'arbre refusé : {relatif}")
            return None
    avant = lire_dans_arbre(verif.arbre, ancien, verif.etat) if ancien else b""
    if avant is None:
        rapport["problemes"].append(f"fichier absent de l'arbre : {ancien}")
        return None
    if nouveau and nouveau != ancien and lire_dans_arbre(verif.arbre, nouveau, verif.etat) \
            is not None:
        rapport["problemes"].append(f"le fichier à créer existe déjà : {nouveau}")
        return None
    apres = produire(entree, avant, rapport)
    rapport["preimage_conforme"] = comparer_index(avant if ancien else None, entree.index_ancien)
    rapport["postimage_conforme"] = comparer_index(apres, entree.index_nouveau)
    if rapport["preimage_conforme"] is False:
        rapport["avertissement"] = "le fichier de l'arbre n'est pas celui d'où le patch est tiré"
    return apres


def inverser(hunk: Hunk) -> Hunk:
    """Hunk retourné (ajouts ↔ suppressions), pour reconnaître un patch déjà appliqué."""
    sens = {"+": "-", "-": "+", " ": " "}
    return Hunk(hunk.nouveau_debut, hunk.nouveau_longueur, hunk.ancien_debut,
                hunk.ancien_longueur, hunk.section, hunk.ligne,
                [(sens[op], contenu, fin) for op, contenu, fin in hunk.lignes])


def deja_applique(entree: Entree, verif: Verification, ancien: str | None,
                  nouveau: str | None) -> bool | None:
    """Le patch semble-t-il déjà appliqué ? (None si on ne peut pas le dire.)"""
    if entree.binaire or any(h.defaut for h in entree.hunks):
        return None
    if entree.supprime:
        return lire_dans_arbre(verif.arbre, ancien, verif.etat) is None if ancien else None
    actuel = lire_dans_arbre(verif.arbre, nouveau, verif.etat) if nouveau else None
    if actuel is None:
        return False
    if entree.cree:
        return appliquer_hunks(b"", entree.hunks)[0] == actuel
    if not entree.hunks:
        return ancien != nouveau and lire_dans_arbre(verif.arbre, ancien, verif.etat) is None
    _, rapports = appliquer_hunks(actuel, [inverser(h) for h in entree.hunks])
    return all(r["applique"] for r in rapports)


def produire(entree: Entree, avant: bytes, rapport: dict[str, Any]) -> bytes | None:
    """Contenu après application (None si le binaire n'est pas vérifiable)."""
    if entree.supprime and not entree.hunks:
        return b""
    if entree.binaire:
        bloc = entree.bloc_binaire
        if bloc is None:
            rapport["problemes"].append("binaire sans contenu (« Binary files … differ ») : "
                                        "non vérifiable")
            return None
        apres = bloc.donnees if bloc.nature == "literal" else appliquer_delta_git(avant, bloc.donnees)
        if apres is None:
            rapport["problemes"].append("delta binaire inapplicable à ce fichier")
        return apres
    apres, rapport["hunks"] = appliquer_hunks(avant, entree.hunks)
    for h in rapport["hunks"]:
        if not h["applique"]:
            rapport["problemes"].append(f"hunk ligne {h['ligne_patch']} rejeté : {h['raison']}")
    if entree.supprime and apres and not rapport["problemes"]:
        rapport["problemes"].append("le fichier ne serait pas vide après sa suppression")
    return apres


def enregistrer(entree: Entree, verif: Verification, ancien: str | None, nouveau: str | None,
                apres: bytes | None, rapport: dict[str, Any]) -> None:
    """Reporte le résultat dans l'état en mémoire (pour les entrées suivantes)."""
    if rapport["problemes"]:
        return
    if entree.supprime and ancien:
        verif.etat[ancien] = None
        return
    if ancien and nouveau and ancien != nouveau and not entree.copie:
        verif.etat[ancien] = None
    if nouveau:
        verif.etat[nouveau] = apres


# ---------------------------------------------------------------------------
# Contrôle croisé optionnel
# ---------------------------------------------------------------------------

def controler_unidiff(texte: str, fichiers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Relit le patch avec unidiff et compare fichiers, hunks, ajouts, suppressions."""
    if unidiff is None:
        return None
    try:
        lot = unidiff.PatchSet(texte)
    except (unidiff.UnidiffParseError, ValueError) as exc:
        return {"bibliotheque": "unidiff", "erreur": str(exc)[:300]}
    lus = [(len(f), f.added, f.removed) for f in lot]
    nos = [(len(f["hunks"]), f["ajouts"], f["suppressions"]) for f in fichiers]
    return {"bibliotheque": "unidiff", "fichiers": len(lus), "concordance": lus == nos,
            "ecarts": [{"rang": k, "nous": a, "unidiff": b}
                       for k, (a, b) in enumerate(zip(nos, lus)) if a != b][:MAX_EXAMINES]}


def controler_whatthepatch(texte: str, fichiers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Relit le patch avec whatthepatch (ajouts et suppressions par fichier)."""
    if whatthepatch is None:
        return None
    try:
        lus = []
        for diff in whatthepatch.parse_patch(texte):
            changements = diff.changes or []
            lus.append((sum(1 for c in changements if c.old is None and c.new is not None),
                        sum(1 for c in changements if c.new is None and c.old is not None)))
    except (ValueError, IndexError, whatthepatch.exceptions.WhatThePatchException) as exc:
        return {"bibliotheque": "whatthepatch", "erreur": str(exc)[:300]}
    nos = [(f["ajouts"], f["suppressions"]) for f in fichiers]
    return {"bibliotheque": "whatthepatch", "fichiers": len(lus), "concordance": lus == nos,
            "ecarts": [{"rang": k, "nous": a, "whatthepatch": b}
                       for k, (a, b) in enumerate(zip(nos, lus)) if a != b][:MAX_EXAMINES]}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def contrat() -> dict[str, str]:
    """Contrat de mesure extrait de la docstring."""
    texte = __doc__ or ""
    sections: dict[str, str] = {}
    for rang, titre in enumerate(TITRES_CONTRAT):
        suivants = "|".join(re.escape(t) for t in TITRES_CONTRAT[rang + 1:]) or r"\Z"
        trouve = re.search(rf"^{re.escape(titre)}\s*\n(.*?)(?=^(?:{suivants})\s*$|\Z)", texte,
                           re.MULTILINE | re.DOTALL)
        sections[titre] = " ".join(trouve.group(1).split()) if trouve else ""
    return sections


def charger_textes(args: argparse.Namespace) -> list[tuple[str, str]]:
    """(nom, texte) de chaque patch à lire."""
    if args.texte is not None:
        return [("<texte>", decoder_echappements(args.texte))]
    chemin = Path(args.chemin)
    if not chemin.is_absolute() and args.racine:
        chemin = Path(args.racine) / chemin
    return [(str(p), lire_octets(p).decode(ENCODAGE, errors="surrogateescape"))
            for p in sources_du_chemin(chemin)]


def analyser(textes: list[tuple[str, str]], arbre: Path | None,
             niveau: int | None) -> dict[str, Any]:
    """Analyse tous les patchs, puis les applique à blanc si un arbre est donné."""
    entrees: list[Entree] = []
    refus: list[str] = []
    ignorees = 0
    crlf = []
    for nom, texte in textes:
        lues, refus_texte, ign, en_crlf = analyser_patch(texte, nom)
        entrees += lues
        refus += refus_texte
        ignorees += ign
        if en_crlf:
            crlf.append(nom)
    fichiers = [decrire_entree(e) for e in entrees]
    rapport = {"fichiers": fichiers, "totaux": totaliser(fichiers), "refus": refus,
               "lignes_hors_diff": ignorees, "patchs_crlf": crlf, "verification": None}
    if arbre is not None:
        verif = Verification(arbre=arbre, niveau=niveau)
        rapport["verification"] = resumer_verification(
            arbre, [verifier_entree(e, verif) for e in entrees])
    texte_total = "\n".join(t for _, t in textes)
    rapport["controles"] = [c for c in (controler_unidiff(texte_total, fichiers),
                                        controler_whatthepatch(texte_total, fichiers)) if c]
    return rapport


def resumer_verification(arbre: Path, rapports: list[dict[str, Any]]) -> dict[str, Any]:
    """Bilan de l'application à blanc."""
    hunks = [h for r in rapports for h in r["hunks"]]
    return {"arbre": str(arbre), "fichiers": rapports,
            "fichiers_en_echec": sum(1 for r in rapports if r["problemes"]),
            "hunks_appliques": sum(1 for h in hunks if h["applique"]),
            "hunks_decales": sum(1 for h in hunks if h["applique"] and h.get("decalage")),
            "hunks_rejetes": sum(1 for h in hunks if not h["applique"]),
            "s_applique": all(not r["problemes"] for r in rapports)}


def code_de_sortie(rapport: dict[str, Any]) -> int:
    """1 si un défaut de forme, un refus ou un échec d'application ; 0 sinon."""
    if rapport["refus"] or any(f["defauts"] for f in rapport["fichiers"]):
        return 1
    verif = rapport["verification"]
    if verif is not None and not verif["s_applique"]:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    t = rapport["totaux"]
    print(f"{t['fichiers']} fichier(s), {t['hunks']} hunk(s), +{t['ajouts']} -{t['suppressions']}"
          f" ; ajoutés {t['ajouté']}, supprimés {t['supprimé']}, modifiés {t['modifié']}, "
          f"renommés {t['renommé']}, copiés {t['copié']}, binaires {t['binaires']}")
    for f in rapport["fichiers"][:MAX_EXAMINES]:
        extra = f" (mode {f['ancien_mode']} → {f['nouveau_mode']})" if f["statut"] == "mode" else ""
        print(f"  {f['statut']:9} {f['chemin']}  +{f['ajouts']} -{f['suppressions']}"
              f"  {len(f['hunks'])} hunk(s){' binaire' if f['binaire'] else ''}{extra}")
        for defaut in f["defauts"]:
            print(f"      DÉFAUT : {defaut}")
        for h in f["hunks"]:
            if h["entete_corrige"]:
                print(f"      en-tête juste : {h['entete_corrige']}")
    for refus in rapport["refus"]:
        print(f"REFUS : {refus}")
    if rapport["verification"]:
        afficher_verification(rapport["verification"])


def afficher_verification(verif: dict[str, Any]) -> None:
    """Résultat de l'application à blanc."""
    print(f"\nApplication à blanc sur {verif['arbre']} : "
          f"{'S APPLIQUE' if verif['s_applique'] else 'ÉCHEC'} — {verif['hunks_appliques']} "
          f"hunk(s) appliqué(s) dont {verif['hunks_decales']} décalé(s), "
          f"{verif['hunks_rejetes']} rejeté(s)")
    for r in verif["fichiers"]:
        for h in r["hunks"]:
            if h["applique"] and h.get("decalage"):
                print(f"  {r['chemin']} : hunk ligne {h['ligne_patch']} décalé de {h['decalage']}")
        for probleme in r["problemes"]:
            print(f"  {r['chemin']} : {probleme}")
        for h in r["hunks"]:
            meilleure = h.get("meilleure_correspondance")
            if meilleure:
                d = meilleure["premiere_divergence"]
                print(f"      au mieux ligne {meilleure['ligne_fichier']} "
                      f"({meilleure['lignes_concordantes']}/{meilleure['lignes_attendues']}) ; "
                      f"ligne {d['ligne_fichier']} attendue « {d['attendu']} », "
                      f"trouvée « {d['trouve']} »")
        if r.get("preimage_conforme") is False:
            print(f"  {r['chemin']} : AVERTISSEMENT {r['avertissement']}")
        if r.get("deja_applique"):
            print(f"  {r['chemin']} : le patch semble DÉJÀ APPLIQUÉ (l'inverse s'applique)")


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Lit un diff unifié (fichiers, hunks, lignes, en-têtes git, binaires) et, "
                    "avec --verifier, l'applique à blanc en mémoire sur un arbre.",
        epilog="Exemple : python outils/lire_patch.py correctif.patch --verifier . --json")
    parseur.add_argument("chemin", nargs="?",
                         help="fichier .patch/.diff, ou dossier de patchs (série appliquée dans "
                              "l'ordre alphabétique)")
    parseur.add_argument("--texte", help="patch en ligne ; sans vrai saut de ligne, les séquences "
                                         "\\n, \\t et \\\\ sont interprétées")
    parseur.add_argument("--verifier", metavar=METAVAR_DOSSIER,
                         help="arbre sur lequel appliquer à blanc (rien n'est écrit)")
    parseur.add_argument("-p", "--niveau", type=int, default=None,
                         help="composants de chemin à retirer (défaut : 1 pour git, sinon deviné)")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def preparer_arbre(args: argparse.Namespace) -> Path | None:
    """Valide le dossier de --verifier."""
    if args.verifier is None:
        return None
    arbre = Path(args.verifier)
    if not arbre.is_absolute() and args.racine:
        arbre = Path(args.racine) / arbre
    if not arbre.is_dir():
        raise ErreurEntree(f"--verifier attend un dossier existant : {arbre}")
    return arbre


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Lecture, analyse, assemblage du rapport et code de sortie."""
    if (args.chemin is None) == (args.texte is None):
        raise ErreurEntree("donner soit un fichier ou dossier de patchs, soit --texte")
    if args.niveau is not None and args.niveau < 0:
        raise ErreurEntree("--niveau doit être positif")
    arbre = preparer_arbre(args)
    textes = charger_textes(args)
    rapport = analyser(textes, arbre, args.niveau)
    if not rapport["fichiers"] and not rapport["refus"]:
        raise ErreurEntree("aucun diff unifié trouvé dans l'entrée : dénominateur nul, "
                           "rien à examiner", code=3)
    moteurs = ["stdlib"] + [c["bibliotheque"] for c in rapport["controles"]]
    entete = {"outil": "lire_patch", "moteur": "+".join(moteurs), "contrat": contrat(),
              "sources": [nom for nom, _ in textes][:MAX_EXAMINES],
              "denominateur": len(rapport["fichiers"]),
              "examines": [f["chemin"] for f in rapport["fichiers"]][:MAX_EXAMINES],
              "examines_tronques": len(rapport["fichiers"]) > MAX_EXAMINES}
    complet = {**entete, **rapport}
    return complet, code_de_sortie(complet)


def main() -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args()
    manquantes = [n for n, m in (("unidiff", unidiff), ("whatthepatch", whatthepatch)) if m is None]
    if manquantes:
        print(f"{', '.join(manquantes)} absent(s) : analyse stdlib seule, sans contre-lecture "
              f"par {' ni '.join(manquantes)}", file=sys.stderr)
    try:
        rapport, code = executer(args)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "lire_patch", "moteur": "stdlib", "denominateur": 0,
                              "examines": [], "erreur": str(exc)}, ensure_ascii=False))
        return exc.code
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return code


__all__ = ["analyser_patch", "appliquer_hunks", "analyser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
