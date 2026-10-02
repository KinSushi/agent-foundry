"""Découpe des documents en fragments pour un RAG sans jamais couper un bloc de code.

Mesuré le 2 octobre 2026 sur docs/ (142 fichiers, 284 blocs de code clôturés dont
14 de plus de 1000 caractères) : un découpage naïf tous les 1000 caractères coupe
58 blocs, langchain-text-splitters 1.1.2 et semantic-text-splitter 0.33.0 en
coupent 14 chacun, cet outil 0 (decouper_documents.py docs --comparer --json ;
découpage naïf compté à part avec le même repérage des blocs).

QUESTION
    Comment découper ces documents en fragments pour un RAG sans couper au
    mauvais endroit (milieu d'un bloc de code, d'une phrase, d'un mot) ?
MESURE
    Analyse ligne à ligne : titres ATX et Setext, en-tête « front matter », blocs
    de code clôturés (``` ou ~~~, fermeture de même caractère et au moins
    aussi longue), paragraphes. Les blocs sont regroupés jusqu'à la taille
    cible (caractères, mots ou jetons) ; un paragraphe trop grand est divisé
    en phrases, puis en mots, puis en caractères ; un bloc de code n'est
    jamais divisé. Chaque fragment porte son fichier source, son chemin de
    titres, ses décalages (debut, fin, debut_contexte) et ses lignes. L'outil
    VÉRIFIE que la concaténation des fragments privés de leur recouvrement
    redonne le texte exact, et compte les blocs de code coupés.
HYPOTHÈSES
    Fichiers texte en utf-8 (BOM accepté). Le Markdown suit CommonMark pour
    les titres et les blocs clôturés. Les décalages sont en points de code
    du texte décodé, fins de ligne conservées telles quelles.
LIMITES
    Seuls les blocs de code CLÔTURÉS sont protégés : un bloc indenté de
    quatre espaces contenant une ligne vide peut être séparé. Tableaux,
    listes et html brut sont traités comme des paragraphes. Un bloc de code plus
    grand que la cible reste entier et le fragment est marqué
    surdimensionne. La taille bornée est celle du coeur du fragment : avec
    recouvrement, le texte émis peut atteindre taille + recouvrement. Sans
    --tiktoken, les jetons sont approchés par une expression régulière :
    mesuré sur les paragraphes de docs/*.md d'au moins 20 jetons, écart
    relatif médian de 8,3 % (90e centile 17,5 %) face à cl100k_base et de
    4,8 % (90e centile 12,5 %) face à o200k_base. Avec --tiktoken, la
    bibliothèque peut lire son cache et TÉLÉCHARGER le fichier d'encodage :
    c'est un accès réseau explicite, jamais fait par défaut.
CONTRE-EXEMPLES
    Constaté : un fichier .md fait de « Exemple : », d'une ligne vide, d'un
    bloc indenté de quatre espaces « def f(): / return 1 », d'une ligne vide
    puis de « print(f()) » au même retrait, découpé avec --taille 20, met
    « def f(): » et « print(f()) » dans deux fragments distincts : un bloc de
    code indenté (non clôturé) qui contient une ligne vide est lu comme deux
    paragraphes et coupé.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Préparation de corpus texte, Markdown ou code source (.py) pour
    l'indexation vectorielle ou lexicale ; ne juge pas la pertinence
    sémantique des frontières.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import importlib
import importlib.util
import json
import math
import re
import statistics
import sys
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
MOT_VERIFIE = "VÉRIFIE"
MOT_TELECHARGER = "TÉLÉCHARGER"
MOT_CLOTURES = "CLÔTURÉS"
ENCODAGE = "utf-8"
MOTEUR_STDLIB = "stdlib"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
EXTENSIONS_MARKDOWN = (".md", ".markdown", ".mdx")
EXTENSIONS_DEFAUT = ".md,.markdown,.mdx,.txt,.rst,.adoc,.org,.py"
DOSSIERS_IGNORES = frozenset({".git", "__pycache__", "node_modules", ".venv",
                              "venv", ".tox", ".mypy_cache", ".pytest_cache"})
UNITES = ("caracteres", "mots", "jetons")
MAX_EXAMINES = 50
MAX_FICHIERS = 20000
CARACTERES_PAR_JETON = 6
BIBLIOTHEQUES_OPTIONNELLES = (
    ("tiktoken", "tiktoken", "jetons approchés par expression régulière stdlib"),
    ("langchain_text_splitters", "langchain-text-splitters", "pas de comparaison"),
    ("semantic_text_splitter", "semantic-text-splitter", "pas de comparaison"),
)

RE_TITRE_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
RE_OUVERTURE = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
RE_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
RE_FIN_PHRASE = re.compile(r"[.!?…]+[\"'»”)\]]*[ \t]+|\n")
RE_ESPACES = re.compile(r"\s+")
RE_MOT = re.compile(r"\S+")
RE_DEBUT_MOT = re.compile(r"(?<!\S)\S")
RE_PRE_JETON = re.compile(
    r"'(?i:[sdmt]|ll|ve|re)|(?:[^\r\n\w]|_)?[^\W\d_]+|\d{1,3}"
    r"| ?[^\s\w]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+")


class ErreurEntree(Exception):
    """Entrée invalide : message pour stderr et code de sortie."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Bloc:
    """Unité atomique ou divisible du texte, repérée par ses décalages."""

    genre: str
    debut: int
    fin: int
    niveau: int = 0
    chemin: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """Texte décodé d'un fichier, prêt à découper."""

    nom: str
    texte: str
    markdown: bool


# --------------------------------------------------------------------------
# Contrat et bibliothèques optionnelles


def extraire_contrat(doc: str) -> dict[str, str]:
    """Renvoie les sections du contrat de mesure lues dans la docstring."""
    contrat: dict[str, str] = {}
    courant = ""
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES:
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def _bibliotheque_presente(module: str) -> bool:
    """Indique, sans l'importer, si un module tiers est installé."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _signaler_absences() -> None:
    """Écrit UNE ligne sur stderr pour les bibliothèques optionnelles absentes."""
    absentes = [f"{nom} ({repli})" for module, nom, repli in BIBLIOTHEQUES_OPTIONNELLES
                if not _bibliotheque_presente(module)]
    if absentes:
        print("decouper_documents : absent(s) : " + " ; ".join(absentes)
              + " — découpage par le moteur stdlib.", file=sys.stderr)


def _importer(module: str) -> ModuleType | None:
    """Importe un module optionnel ; None s'il manque."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return importlib.import_module(module)
    except ImportError:
        return None


def _version(module: ModuleType) -> str:
    """Version déclarée d'un module tiers, ou « ? »."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return "?"
    nom = module.__name__.replace("_", "-")
    try:
        return version(nom)
    except PackageNotFoundError:
        return str(getattr(module, "__version__", "?"))


# --------------------------------------------------------------------------
# Mesure de taille


def _jetons_approches(texte: str) -> int:
    """Compte approché de jetons BPE : pré-découpage façon cl100k, mots longs divisés."""
    total = 0
    for morceau in RE_PRE_JETON.findall(texte):
        coeur = morceau.strip()
        if not coeur:
            total += 1
        elif coeur[:1].isalpha() or coeur[-1:].isalpha():
            poids = len(coeur) + sum(1 for c in coeur if ord(c) > 127)
            total += max(1, math.ceil(poids / CARACTERES_PAR_JETON))
        else:
            total += max(1, math.ceil(len(coeur) / 2))
    return total


def construire_mesure(unite: str, encodeur: Any) -> Callable[[str], int]:
    """Fonction de taille selon l'unité choisie (et l'encodeur tiktoken éventuel)."""
    if unite == "caracteres":
        return len
    if unite == "mots":
        return lambda texte: len(RE_MOT.findall(texte))
    if encodeur is not None:
        return lambda texte: len(encodeur.encode(texte, disallowed_special=()))
    return _jetons_approches


def charger_tiktoken(nom_encodage: str | None) -> tuple[Any, str]:
    """Charge l'encodeur tiktoken demandé explicitement ; repli stdlib sinon."""
    if not nom_encodage:
        return None, MOTEUR_STDLIB
    module = _importer("tiktoken")
    if module is None:
        return None, MOTEUR_STDLIB
    try:
        return module.get_encoding(nom_encodage), f"tiktoken:{nom_encodage}"
    except (ValueError, KeyError, OSError) as exc:
        raise ErreurEntree(f"encodage tiktoken « {nom_encodage} » indisponible : {exc}") from exc


# --------------------------------------------------------------------------
# Lecture des entrées


def _nom_affiche(chemin: Path, racine: Path) -> str:
    """Chemin relatif à la racine si possible, sinon tel quel."""
    try:
        return chemin.resolve().relative_to(racine.resolve()).as_posix()
    except ValueError:
        return chemin.as_posix()


def _fichiers_du_dossier(dossier: Path, extensions: tuple[str, ...]) -> Iterator[Path]:
    """Fichiers du dossier (récursif, trié) portant une extension retenue."""
    for chemin in sorted(dossier.rglob("*")):
        relatif = chemin.relative_to(dossier).parts
        if any(partie in DOSSIERS_IGNORES for partie in relatif[:-1]):
            continue
        if chemin.is_file() and chemin.suffix.lower() in extensions:
            yield chemin


def lister_fichiers(chemins: list[Path], extensions: tuple[str, ...]) -> list[tuple[Path, bool]]:
    """Liste (fichier, explicite) ; un chemin inexistant est une erreur d'entrée."""
    trouves: list[tuple[Path, bool]] = []
    for chemin in chemins:
        if not chemin.exists():
            raise ErreurEntree(f"chemin introuvable : {chemin}")
        if chemin.is_dir():
            trouves.extend((f, False) for f in _fichiers_du_dossier(chemin, extensions))
        elif chemin.is_file():
            trouves.append((chemin, True))
        else:
            raise ErreurEntree(f"ni fichier ni dossier : {chemin}")
        if len(trouves) > MAX_FICHIERS:
            raise ErreurEntree(f"plus de {MAX_FICHIERS} fichiers : restreindre l'entrée")
    return trouves


def lire_texte(chemin: Path, taille_max: int) -> str:
    """Lit un fichier UTF-8 borné ; lève ErreurEntree s'il est binaire ou trop gros."""
    taille = chemin.stat().st_size
    if taille > taille_max:
        raise ErreurEntree(f"{chemin} : {taille} octets > --taille-max-fichier {taille_max}")
    with chemin.open("rb") as flux:
        brut = flux.read(taille_max + 1)
    if b"\x00" in brut:
        raise ErreurEntree(f"{chemin} : fichier binaire (octet nul)")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} : pas de l'UTF-8 valide ({exc.reason} à l'octet {exc.start})") from exc


def charger_documents(fichiers: list[tuple[Path, bool]], args: argparse.Namespace,
                      racine: Path) -> tuple[list[Document], list[dict[str, str]]]:
    """Décode les fichiers ; un fichier explicite illisible est fatal, un fichier de dossier est ignoré."""
    documents: list[Document] = []
    ignores: list[dict[str, str]] = []
    for chemin, explicite in fichiers:
        nom = _nom_affiche(chemin, racine)
        try:
            texte = lire_texte(chemin, args.taille_max_fichier)
        except (ErreurEntree, OSError) as exc:
            if explicite:
                raise ErreurEntree(str(exc)) from exc
            ignores.append({"chemin": nom, "raison": str(exc)})
            continue
        if not texte.strip():
            ignores.append({"chemin": nom, "raison": "vide ou blanc"})
            continue
        documents.append(Document(nom, texte, _est_markdown(chemin, args.format)))
    return documents, ignores


def _est_markdown(chemin: Path, choix: str) -> bool:
    """Le fichier doit-il être analysé comme Markdown ?"""
    if choix == "auto":
        return chemin.suffix.lower() in EXTENSIONS_MARKDOWN
    return choix == "markdown"


# --------------------------------------------------------------------------
# Analyse en blocs


def _lignes_positionnees(texte: str) -> list[tuple[int, str]]:
    """Lignes (fins de ligne incluses) avec leur décalage de début."""
    resultat: list[tuple[int, str]] = []
    position = 0
    for ligne in texte.splitlines(keepends=True):
        resultat.append((position, ligne))
        position += len(ligne)
    return resultat


def _contenu(ligne: str) -> str:
    """Ligne sans ses caractères de fin de ligne."""
    return ligne.rstrip("\r\n\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _fin_entete_yaml(lignes: list[tuple[int, str]]) -> int:
    """Indice de la ligne qui ferme un en-tête YAML initial, ou -1."""
    if not lignes or _contenu(lignes[0][1]) != "---":
        return -1
    for indice in range(1, len(lignes)):
        if _contenu(lignes[indice][1]).rstrip() in ("---", "..."):
            return indice
    return -1


def _largeur_retrait(retrait: str) -> int:
    """Largeur d'un retrait, tabulation comptée pour quatre espaces."""
    return len(retrait.expandtabs(4))


def _fin_bloc_code(lignes: list[tuple[int, str]], depart: int, retrait: str, cloture: str) -> int:
    """Indice de la ligne fermant le bloc ouvert en `depart`, ou -1 si ce n'est pas un bloc.

    Ouverture à retrait ≤ 3 (CommonMark) : non fermée, elle court jusqu'à la fin. Ouverture
    plus retirée (bloc dans un élément de liste) : la fermeture doit avoir le même retrait
    et venir avant toute ligne moins retirée, sinon ce n'est pas un bloc de code.
    """
    largeur = _largeur_retrait(retrait)
    motif = re.compile(r"^([ \t]*)" + re.escape(cloture[0]) + "{" + str(len(cloture)) + r",}[ \t]*$")
    for indice in range(depart + 1, len(lignes)):
        contenu = _contenu(lignes[indice][1])
        fermeture = motif.match(contenu)
        if fermeture:
            largeur_fermeture = _largeur_retrait(fermeture.group(1))
            if largeur_fermeture == largeur or (largeur <= 3 and largeur_fermeture <= 3):
                return indice
        if largeur > 3 and contenu.strip() and _largeur_retrait(contenu[:len(contenu) - len(contenu.lstrip())]) < largeur:
            return -1
    return len(lignes) - 1 if largeur <= 3 else -1


class _Analyseur:
    """Automate ligne à ligne produisant des blocs qui pavent exactement le texte."""

    def __init__(self, texte: str, markdown: bool) -> None:
        self.texte = texte
        self.markdown = markdown
        self.lignes = _lignes_positionnees(texte)
        self.blocs: list[Bloc] = []
        self.pile: list[tuple[int, str]] = []
        self.blanc_avant = False
        self.debut_attente: int | None = None

    def chemin(self) -> tuple[str, ...]:
        return tuple(titre for _, titre in self.pile)

    def ajouter(self, genre: str, debut: int, fin: int, niveau: int = 0) -> None:
        if self.debut_attente is not None:
            debut, self.debut_attente = self.debut_attente, None
        self.blocs.append(Bloc(genre, debut, fin, niveau, self.chemin()))
        self.blanc_avant = False

    def etendre_dernier(self, fin: int) -> None:
        self.blocs[-1] = replace(self.blocs[-1], fin=fin)

    def empiler_titre(self, niveau: int, titre: str) -> None:
        while self.pile and self.pile[-1][0] >= niveau:
            self.pile.pop()
        self.pile.append((niveau, titre))

    def traiter_blanc(self, debut: int, fin: int) -> None:
        if self.blocs:
            self.etendre_dernier(fin)
        elif self.debut_attente is None:
            self.debut_attente = debut
        self.blanc_avant = True

    def traiter_setext(self, contenu: str, fin: int) -> bool:
        precedent = self.blocs[-1] if self.blocs else None
        if precedent is None or precedent.genre != "paragraphe" or self.blanc_avant:
            return False
        niveau = 1 if contenu.strip().startswith("=") else 2
        titre = " ".join(self.texte[precedent.debut:precedent.fin].split())
        self.empiler_titre(niveau, titre)
        self.blocs[-1] = Bloc("titre", precedent.debut, fin, niveau, self.chemin())
        return True

    def traiter_paragraphe(self, debut: int, fin: int) -> None:
        precedent = self.blocs[-1] if self.blocs else None
        if precedent is not None and precedent.genre == "paragraphe" and not self.blanc_avant:
            self.etendre_dernier(fin)
        else:
            self.ajouter("paragraphe", debut, fin)

    def traiter_ligne(self, indice: int) -> int:
        """Traite la ligne `indice` ; renvoie l'indice de la ligne suivante."""
        debut, ligne = self.lignes[indice]
        fin = debut + len(ligne)
        contenu = _contenu(ligne)
        if not contenu.strip():
            self.traiter_blanc(debut, fin)
            return indice + 1
        if self.markdown:
            suivant = self.traiter_markdown(indice, contenu, debut, fin)
            if suivant is not None:
                return suivant
        self.traiter_paragraphe(debut, fin)
        return indice + 1

    def traiter_markdown(self, indice: int, contenu: str, debut: int, fin: int) -> int | None:
        ouverture = RE_OUVERTURE.match(contenu)
        dernier = -1
        if ouverture and not (ouverture.group(2)[0] == "`" and "`" in ouverture.group(3)):
            dernier = _fin_bloc_code(self.lignes, indice, ouverture.group(1), ouverture.group(2))
        if dernier >= 0:
            fin_code = self.lignes[dernier][0] + len(self.lignes[dernier][1])
            self.ajouter("code", debut, fin_code)
            return dernier + 1
        titre = RE_TITRE_ATX.match(contenu)
        if titre:
            self.empiler_titre(len(titre.group(1)), (titre.group(2) or "").strip())
            self.ajouter("titre", debut, fin, len(titre.group(1)))
            return indice + 1
        if RE_SETEXT.match(contenu) and self.traiter_setext(contenu, fin):
            return indice + 1
        return None

    def analyser(self) -> list[Bloc]:
        indice = 0
        if self.markdown:
            fin_yaml = _fin_entete_yaml(self.lignes)
            if fin_yaml > 0:
                self.ajouter("entete", 0, self.lignes[fin_yaml][0] + len(self.lignes[fin_yaml][1]))
                indice = fin_yaml + 1
        while indice < len(self.lignes):
            indice = self.traiter_ligne(indice)
        return self.blocs


def analyser_blocs(texte: str, markdown: bool) -> list[Bloc]:
    """Blocs contigus couvrant tout le texte (titres, code, paragraphes, en-tête)."""
    return _Analyseur(texte, markdown).analyser()


# --------------------------------------------------------------------------
# Subdivision des paragraphes trop grands


def _coupures(texte: str, debut: int, fin: int, motif: re.Pattern[str]) -> list[int]:
    """Positions de coupure (fins de correspondance) strictement internes au segment."""
    positions = [m.end() for m in motif.finditer(texte, debut, fin)]
    return [p for p in positions if debut < p < fin]


def _segments(debut: int, fin: int, coupures: Iterable[int]) -> list[tuple[int, int]]:
    """Segments contigus délimités par les coupures."""
    bornes = [debut, *sorted(set(coupures)), fin]
    return [(a, b) for a, b in zip(bornes, bornes[1:]) if b > a]


def _couper_dur(texte: str, debut: int, fin: int, mesure: Callable[[str], int],
                cible: int) -> list[tuple[int, int]]:
    """Coupe en caractères : plus long préfixe tenant dans la cible (recherche dichotomique)."""
    morceaux: list[tuple[int, int]] = []
    while debut < fin:
        coupure = _plus_long_prefixe(texte, debut, fin, mesure, cible)
        morceaux.append((debut, coupure))
        debut = coupure
    return morceaux


def _plus_long_prefixe(texte: str, debut: int, fin: int, mesure: Callable[[str], int],
                       cible: int) -> int:
    """Fin du plus long préfixe ≤ cible (au moins un caractère) ; fenêtre doublée puis dichotomie."""
    haut = min(fin, debut + cible)
    while haut < fin and mesure(texte[debut:haut]) <= cible:
        haut = min(fin, debut + 2 * (haut - debut))
    bas = debut + 1
    while bas < haut:
        milieu = (bas + haut + 1) // 2
        if mesure(texte[debut:milieu]) <= cible:
            bas = milieu
        else:
            haut = milieu - 1
    return bas


def subdiviser(texte: str, debut: int, fin: int, mesure: Callable[[str], int],
               cible: int) -> list[tuple[int, int]]:
    """Divise un segment en morceaux ≤ cible : phrases, puis mots, puis caractères."""
    if mesure(texte[debut:fin]) <= cible:
        return [(debut, fin)]
    morceaux: list[tuple[int, int]] = []
    for a, b in _segments(debut, fin, _coupures(texte, debut, fin, RE_FIN_PHRASE)):
        if mesure(texte[a:b]) <= cible:
            morceaux.append((a, b))
            continue
        for c, d in _segments(a, b, _coupures(texte, a, b, RE_ESPACES)):
            if mesure(texte[c:d]) <= cible:
                morceaux.append((c, d))
            else:
                morceaux.extend(_couper_dur(texte, c, d, mesure, cible))
    return morceaux


def preparer_unites(texte: str, blocs: list[Bloc], mesure: Callable[[str], int],
                    cible: int) -> list[Bloc]:
    """Remplace chaque paragraphe trop grand par ses morceaux ; le code reste entier."""
    unites: list[Bloc] = []
    for bloc in blocs:
        if bloc.genre != "paragraphe":
            unites.append(bloc)
            continue
        for a, b in subdiviser(texte, bloc.debut, bloc.fin, mesure, cible):
            unites.append(replace(bloc, genre="morceau" if (a, b) != (bloc.debut, bloc.fin)
                                  else "paragraphe", debut=a, fin=b))
    return unites


# --------------------------------------------------------------------------
# Regroupement en fragments


def _seulement_titres(courant: list[Bloc]) -> bool:
    return bool(courant) and all(b.genre in ("titre", "entete") for b in courant)


def chemin_fragment(groupe: list[Bloc]) -> tuple[str, ...]:
    """Chemin de titres d'un fragment : celui du dernier titre de tête, sinon du premier bloc."""
    chemin = groupe[0].chemin
    for bloc in groupe:
        if bloc.genre not in ("titre", "entete"):
            break
        chemin = bloc.chemin
    return chemin


def _doit_couper_avant(unite: Bloc, niveau_coupure: int) -> bool:
    return unite.genre == "titre" and unite.niveau <= niveau_coupure


def _accrocher_au_titre(texte: str, courant: list[Bloc], unite: Bloc,
                        mesure: Callable[[str], int], cible: int) -> list[Bloc]:
    """Après un titre seul, recoupe l'unité pour tenir dans le reste du budget."""
    if unite.genre in ("code", "titre", "entete"):
        return [unite]
    budget = cible - mesure(texte[courant[0].debut:unite.debut])
    if budget < 1:
        return [unite]
    morceaux = subdiviser(texte, unite.debut, unite.fin, mesure, budget)
    return [replace(unite, genre="morceau", debut=a, fin=b) for a, b in morceaux]


def _deborde(texte: str, courant: list[Bloc], unite: Bloc, estime: int, taille_unite: int,
             mesure: Callable[[str], int], cible: int) -> tuple[bool, int]:
    """L'unité fait-elle déborder le fragment ? Renvoie (déborde, nouvelle estimation).

    L'estimation additive majore la taille réelle (unités coupées sur des blancs) ; la
    mesure exacte n'est faite que lorsque cette majoration dépasse la cible.
    """
    if estime + taille_unite <= cible:
        return False, estime + taille_unite
    exacte = mesure(texte[courant[0].debut:unite.fin])
    return exacte > cible, exacte


def regrouper(texte: str, unites: list[Bloc], mesure: Callable[[str], int], cible: int,
              niveau_coupure: int) -> list[list[Bloc]]:
    """Regroupe les unités en fragments ≤ cible, coupe forcée avant chaque titre."""
    fragments: list[list[Bloc]] = []
    courant: list[Bloc] = []
    estime = 0
    file_attente = deque(unites)
    while file_attente:
        unite = file_attente.popleft()
        if courant and _doit_couper_avant(unite, niveau_coupure) and not _seulement_titres(courant):
            fragments.append(courant)
            courant, estime = [], 0
        taille_unite = mesure(texte[unite.debut:unite.fin])
        if courant:
            deborde, nouvelle = _deborde(texte, courant, unite, estime, taille_unite, mesure, cible)
            if not deborde:
                courant.append(unite)
                estime = nouvelle
                continue
            if _seulement_titres(courant):
                morceaux = _accrocher_au_titre(texte, courant, unite, mesure, cible)
                courant.append(morceaux[0])
                estime = mesure(texte[courant[0].debut:morceaux[0].fin])
                file_attente.extendleft(reversed(morceaux[1:]))
                continue
            fragments.append(courant)
        courant, estime = [unite], taille_unite
    if courant:
        fragments.append(courant)
    return fragments


def _hors_code(position: int, limite: int, blocs_code: list[Bloc]) -> int:
    """Repousse une position qui tomberait à l'intérieur d'un bloc de code (blocs triés)."""
    indice = bisect.bisect_left([b.debut for b in blocs_code], position) - 1
    if indice >= 0 and blocs_code[indice].debut < position < blocs_code[indice].fin:
        return min(blocs_code[indice].fin, limite)
    return position


def debut_contexte(texte: str, precedent: list[Bloc] | None, fragment: list[Bloc],
                   mesure: Callable[[str], int], recouvrement: int,
                   blocs_code: list[Bloc]) -> int:
    """Début du recouvrement : derniers mots du fragment précédent de la même section."""
    debut = fragment[0].debut
    if (recouvrement <= 0 or precedent is None or fragment[0].genre == "titre"
            or chemin_fragment(fragment) != chemin_fragment(precedent)):
        return debut
    meilleur = debut
    departs = [m.start() for m in RE_DEBUT_MOT.finditer(texte, precedent[0].debut, debut)]
    for position in reversed(departs):
        if mesure(texte[position:debut]) > recouvrement:
            break
        meilleur = position
    return _hors_code(meilleur, debut, blocs_code)


# --------------------------------------------------------------------------
# Construction des enregistrements et vérifications


def _numero_ligne(departs_lignes: list[int], position: int) -> int:
    return bisect.bisect_right(departs_lignes, position)


def construire_fragments(document: Document, args: argparse.Namespace,
                         mesure: Callable[[str], int]) -> tuple[list[dict[str, Any]], list[Bloc]]:
    """Fragments d'un document (dictionnaires prêts pour JSONL) et ses blocs de code."""
    texte = document.texte
    blocs = analyser_blocs(texte, document.markdown)
    blocs_code = [b for b in blocs if b.genre == "code"]
    unites = preparer_unites(texte, blocs, mesure, args.taille)
    groupes = regrouper(texte, unites, mesure, args.taille, args.niveau_coupure)
    departs_lignes = [p for p, _ in _lignes_positionnees(texte)]
    fragments: list[dict[str, Any]] = []
    for indice, groupe in enumerate(groupes):
        precedent = groupes[indice - 1] if indice else None
        contexte = debut_contexte(texte, precedent, groupe, mesure, args.recouvrement, blocs_code)
        fragments.append(_enregistrement(document, indice, groupe, contexte, departs_lignes, mesure, args))
    return fragments, blocs_code


def _enregistrement(document: Document, indice: int, groupe: list[Bloc], contexte: int,
                    departs_lignes: list[int], mesure: Callable[[str], int],
                    args: argparse.Namespace) -> dict[str, Any]:
    """Un fragment sous forme de dictionnaire."""
    debut, fin = groupe[0].debut, groupe[-1].fin
    taille = mesure(document.texte[debut:fin])
    return {
        "id": f"{document.nom}#{indice:04d}",
        "source": document.nom,
        "index": indice,
        "chemin_titres": list(chemin_fragment(groupe)),
        "debut": debut,
        "fin": fin,
        "debut_contexte": contexte,
        "ligne_debut": _numero_ligne(departs_lignes, debut),
        "ligne_fin": _numero_ligne(departs_lignes, max(debut, fin - 1)),
        "unite": args.unite,
        "taille": taille,
        "contient_code": any(b.genre == "code" for b in groupe),
        "surdimensionne": taille > args.taille,
        "texte": document.texte[contexte:fin],
    }


def verifier_reconstruction(texte: str, fragments: list[dict[str, Any]]) -> bool:
    """La concaténation des fragments sans leur recouvrement redonne-t-elle le texte ?"""
    morceaux = [f["texte"][f["debut"] - f["debut_contexte"]:] for f in fragments]
    return "".join(morceaux) == texte


def compter_code_coupe(blocs_code: list[Bloc], fragments: list[dict[str, Any]]) -> int:
    """Blocs de code qui ne tiennent entiers dans le coeur d'aucun fragment."""
    debuts = [f["debut"] for f in fragments]
    coupes = 0
    for bloc in blocs_code:
        indice = bisect.bisect_right(debuts, bloc.debut) - 1
        coupes += indice < 0 or fragments[indice]["fin"] < bloc.fin
    return coupes


# --------------------------------------------------------------------------
# Comparaison avec les bibliothèques optionnelles


def _morceaux_langchain(module: ModuleType, texte: str, markdown: bool, cible: int,
                        mesure: Callable[[str], int]) -> list[str]:
    classe = module.RecursiveCharacterTextSplitter
    if markdown:
        decoupeur = classe.from_language(module.Language.MARKDOWN, chunk_size=cible,
                                         chunk_overlap=0, length_function=mesure)
    else:
        decoupeur = classe(chunk_size=cible, chunk_overlap=0, length_function=mesure)
    return list(decoupeur.split_text(texte))


def _morceaux_semantic(module: ModuleType, texte: str, markdown: bool, cible: int,
                       mesure: Callable[[str], int]) -> list[str]:
    classe = module.MarkdownSplitter if markdown else module.TextSplitter
    decoupeur = classe.from_callback(mesure, cible)
    return list(decoupeur.chunks(texte))


def _evaluer_decoupeur(documents: list[Document], fonction: Callable[..., list[str]],
                       module: ModuleType, args: argparse.Namespace,
                       mesure: Callable[[str], int]) -> dict[str, Any]:
    """Applique un découpeur tiers et compte les blocs de code qu'il coupe."""
    total, coupes, blocs, trop_grands = 0, 0, 0, 0
    for document in documents:
        morceaux = fonction(module, document.texte, document.markdown, args.taille, mesure)
        total += len(morceaux)
        trop_grands += sum(1 for m in morceaux if mesure(m) > args.taille)
        for bloc in analyser_blocs(document.texte, document.markdown):
            if bloc.genre != "code":
                continue
            blocs += 1
            contenu = document.texte[bloc.debut:bloc.fin].strip()
            coupes += not any(contenu in m for m in morceaux)
    return {"version": _version(module), "fragments": total, "blocs_code": blocs,
            "blocs_code_coupes": coupes, "fragments_surdimensionnes": trop_grands}


def comparer_bibliotheques(documents: list[Document], args: argparse.Namespace,
                           mesure: Callable[[str], int]) -> dict[str, Any]:
    """Même découpage confié à langchain-text-splitters et semantic-text-splitter, s'ils sont là."""
    resultats: dict[str, Any] = {}
    candidats = (("langchain-text-splitters", "langchain_text_splitters", _morceaux_langchain),
                 ("semantic-text-splitter", "semantic_text_splitter", _morceaux_semantic))
    for nom, module_nom, fonction in candidats:
        module = _importer(module_nom)
        if module is None:
            resultats[nom] = {"absent": True}
            continue
        with contextlib.redirect_stdout(sys.stderr):
            resultats[nom] = _evaluer_decoupeur(documents, fonction, module, args, mesure)
    return resultats


# --------------------------------------------------------------------------
# Orchestration


def decouper(documents: list[Document], args: argparse.Namespace,
             mesure: Callable[[str], int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Découpe tous les documents ; renvoie fragments et bilan par fichier."""
    tous: list[dict[str, Any]] = []
    bilans: list[dict[str, Any]] = []
    for document in documents:
        fragments, blocs_code = construire_fragments(document, args, mesure)
        tous.extend(fragments)
        bilans.append({
            "source": document.nom,
            "markdown": document.markdown,
            "fragments": len(fragments),
            "blocs_code": len(blocs_code),
            "blocs_code_coupes": compter_code_coupe(blocs_code, fragments),
            "surdimensionnes": sum(1 for f in fragments if f["surdimensionne"]),
            "reconstruction_exacte": verifier_reconstruction(document.texte, fragments),
        })
    return tous, bilans


def ecrire_jsonl(chemin: Path, fragments: list[dict[str, Any]]) -> None:
    """Écrit un fragment JSON par ligne dans le fichier de sortie explicite."""
    with chemin.open("w", encoding=ENCODAGE, newline="\n") as flux:
        for fragment in fragments:
            flux.write(json.dumps(fragment, ensure_ascii=False) + "\n")


def _repartition(tailles: list[int]) -> dict[str, float]:
    if not tailles:
        return {}
    return {"min": min(tailles), "mediane": statistics.median(tailles), "max": max(tailles)}


def resumer(documents: list[Document], ignores: list[dict[str, str]],
            fragments: list[dict[str, Any]], bilans: list[dict[str, Any]],
            args: argparse.Namespace, moteur: str) -> dict[str, Any]:
    """Résumé JSON du découpage."""
    noms = [d.nom for d in documents]
    return {
        "outil": "decouper_documents",
        "moteur": moteur,
        "unite": args.unite,
        "taille_cible": args.taille,
        "recouvrement": args.recouvrement,
        "denominateur": len(documents),
        "examines": noms[:MAX_EXAMINES],
        "examines_tronques": len(noms) > MAX_EXAMINES,
        "ignores": ignores[:MAX_EXAMINES],
        "fragments": len(fragments),
        "fragments_surdimensionnes": sum(b["surdimensionnes"] for b in bilans),
        "blocs_code": sum(b["blocs_code"] for b in bilans),
        "blocs_code_coupes": sum(b["blocs_code_coupes"] for b in bilans),
        "reconstruction_exacte": all(b["reconstruction_exacte"] for b in bilans),
        "fichiers_non_reconstruits": [b["source"] for b in bilans if not b["reconstruction_exacte"]],
        "taille_fragments": _repartition([f["taille"] for f in fragments]),
        "par_fichier": bilans[:MAX_EXAMINES],
        "apercu": [{k: (v[:120] if k == "texte" else v) for k, v in f.items()} for f in fragments[:3]],
        "sortie": str(args.sortie) if args.sortie else None,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def presenter_humain(resume: dict[str, Any]) -> None:
    """Affichage lisible du résumé."""
    print(f"{resume['denominateur']} fichier(s) découpé(s) en {resume['fragments']} fragment(s) "
          f"— unité {resume['unite']}, cible {resume['taille_cible']}, "
          f"recouvrement {resume['recouvrement']}, moteur {resume['moteur']}")
    print(f"blocs de code : {resume['blocs_code']}, coupés : {resume['blocs_code_coupes']} ; "
          f"surdimensionnés : {resume['fragments_surdimensionnes']} ; "
          f"reconstruction exacte : {'oui' if resume['reconstruction_exacte'] else 'NON'}")
    if resume["taille_fragments"]:
        t = resume["taille_fragments"]
        print(f"tailles : min {t['min']}, médiane {t['mediane']}, max {t['max']}")
    for bilan in resume["par_fichier"]:
        print(f"  {bilan['source']} : {bilan['fragments']} fragment(s), "
              f"{bilan['blocs_code']} bloc(s) de code")
    for ignore in resume["ignores"]:
        print(f"  ignoré {ignore['chemin']} : {ignore['raison']}")
    for nom, valeurs in resume.get("comparaison", {}).items():
        print(f"comparaison {nom} : {valeurs}")
    if resume["sortie"]:
        print(f"fragments écrits dans {resume['sortie']}")


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    parser = argparse.ArgumentParser(
        description="Découpe des fichiers texte/Markdown en fragments pour un RAG, par titres "
                    "et paragraphes, sans jamais couper un bloc de code clôturé.",
        epilog="Exemple : python3 decouper_documents.py docs --unite mots --taille 300 "
               "--recouvrement 40 --sortie fragments.jsonl --json")
    parser.add_argument("chemins", nargs="+", type=Path, help="fichiers ou dossiers à découper")
    parser.add_argument("--unite", choices=UNITES, default="caracteres", metavar="UNITE",
                        help="unité de taille : caracteres, mots ou jetons (défaut caracteres)")
    parser.add_argument("--taille", type=int, default=1000, help="taille cible du coeur d'un fragment")
    parser.add_argument("--recouvrement", type=int, default=0,
                        help="contexte repris du fragment précédent de la même section (même unité)")
    parser.add_argument("--niveau-coupure", type=int, default=6, metavar="NIVEAU",
                        help="coupe forcée avant chaque titre de niveau ≤ NIVEAU (1 à 6, défaut 6)")
    parser.add_argument("--format", choices=("auto", "markdown", "texte"), default="auto",
                        metavar="FORMAT", help="auto (selon l'extension), markdown ou texte")
    parser.add_argument("--extensions", default=EXTENSIONS_DEFAUT,
                        help=f"extensions retenues dans les dossiers (défaut {EXTENSIONS_DEFAUT})")
    parser.add_argument("--taille-max-fichier", type=int, default=10_000_000, metavar="OCTETS",
                        help="taille maximale lue par fichier")
    parser.add_argument("--sortie", type=Path, help="fichier JSONL où écrire les fragments")
    parser.add_argument("--tiktoken", metavar="ENCODAGE",
                        help="compter les jetons avec tiktoken (ex. cl100k_base) ; peut lire son "
                             "cache et télécharger l'encodage")
    parser.add_argument("--comparer", action="store_true",
                        help="comparer avec langchain-text-splitters et semantic-text-splitter")
    parser.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=Path,
                        help=f"base des chemins relatifs (défaut : répertoire courant ; outil dans {RACINE})")
    return parser


def valider_arguments(args: argparse.Namespace) -> None:
    """Contrôle les valeurs numériques et la sortie."""
    if args.taille < 1:
        raise ErreurEntree("--taille doit être ≥ 1")
    if not 0 <= args.recouvrement < args.taille:
        raise ErreurEntree("--recouvrement doit être ≥ 0 et < --taille")
    if not 1 <= args.niveau_coupure <= 6:
        raise ErreurEntree("--niveau-coupure doit être entre 1 et 6")
    if args.taille_max_fichier < 1:
        raise ErreurEntree("--taille-max-fichier doit être ≥ 1")
    if args.sortie is not None and args.sortie.is_dir():
        raise ErreurEntree(f"--sortie désigne un dossier : {args.sortie}")


def _resoudre(chemin: Path, racine: Path) -> Path:
    return chemin if chemin.is_absolute() else racine / chemin


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Exécute le découpage ; renvoie le résumé et le code de sortie."""
    valider_arguments(args)
    racine = args.racine if args.racine is not None else Path.cwd()
    if not racine.is_dir():
        raise ErreurEntree(f"--racine n'est pas un dossier : {racine}")
    extensions = tuple(e.strip().lower() for e in args.extensions.split(",") if e.strip())
    entrees = [_resoudre(c, racine) for c in args.chemins]
    if args.sortie is not None:
        args.sortie = _resoudre(args.sortie, racine)
        if any(args.sortie.resolve() == e.resolve() for e in entrees):
            raise ErreurEntree("--sortie ne peut pas écraser un fichier d'entrée")
    encodeur, moteur = charger_tiktoken(args.tiktoken if args.unite == "jetons" else None)
    mesure = construire_mesure(args.unite, encodeur)
    documents, ignores = charger_documents(lister_fichiers(entrees, extensions), args, racine)
    fragments, bilans = decouper(documents, args, mesure)
    resume = resumer(documents, ignores, fragments, bilans, args, moteur)
    if args.comparer and documents:
        resume["comparaison"] = comparer_bibliotheques(documents, args, mesure)
    if not documents:
        return resume, 3
    if args.sortie is not None:
        ecrire_jsonl(args.sortie, fragments)
    defaut = not resume["reconstruction_exacte"] or resume["blocs_code_coupes"] > 0
    return resume, 1 if defaut else 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_analyseur().parse_args(argv)
    _signaler_absences()
    try:
        resume, code = executer(args)
    except ErreurEntree as exc:
        print(f"decouper_documents : {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"decouper_documents : erreur d'entrée/sortie : {exc}", file=sys.stderr)
        return 2
    if code == 3:
        print("decouper_documents : dénominateur nul — rien à examiner (aucun fichier texte "
              "non vide trouvé)", file=sys.stderr)
    if args.json:
        print(json.dumps(resume, ensure_ascii=False, indent=2))
    elif code != 3:
        presenter_humain(resume)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
