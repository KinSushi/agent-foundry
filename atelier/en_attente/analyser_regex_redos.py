"""Dire si une expression régulière peut exploser en temps (ReDoS), et le prouver par une
entrée d'attaque mesurée, au lieu de la juger « valide » parce qu'elle compile.

__FAIT_MESURE__

QUESTION
    Cette expression régulière peut-elle exploser en temps (ReDoS), et sur quelle entrée ?
MESURE
    1) Analyse statique de l'arbre rendu par re._parser (le module stdlib qu'utilise
    re.compile) : repérage des constructions à risque (répétition illimitée contenant une
    répétition illimitée, alternatives dont les premiers caractères se recouvrent sous une
    répétition, répétitions adjacentes qui acceptent les mêmes caractères) ; puis
    traduction en automate fini non déterministe (Thompson, transitions vides retirées),
    les classes de caractères étant évaluées sur un échantillon de caractères par le moteur
    re lui-même. Ambiguïté exponentielle : un état et deux chemins distincts qui y
    reviennent sur le même mot (automate produit des paires) ; ambiguïté polynomiale : deux
    boucles reliées sur le même mot (automate produit des triplets) ; en mode search, la
    boucle implicite de la recherche compte comme une boucle. Chaque ambiguïté donne une
    entrée d'attaque préfixe + pompe × k + suffixe (le suffixe choisi est le premier qui
    fait échouer la correspondance). 2) Mesure : dans un sous-processus python -I à délai
    borné, le temps de search/match/fullmatch est chronométré pour k croissant (pas de 1
    pour l'exponentiel, longueur doublée pour le polynomial) jusqu'à --seuil secondes ou
    --longueur-max caractères. Verdict EXPONENTIEL ou POLYNOMIAL seulement si le seuil est
    atteint ; le facteur de croissance par pompe et la pente log-log sont rendus.
HYPOTHÈSES
    Le moteur est celui de CPython (re, retour arrière, sans mémoïsation) ; l'expression
    est appliquée à une entrée que l'attaquant contrôle, de la longueur indiquée ; le mode
    (search, match, fullmatch) est celui de l'appel trouvé dans le code, search sinon.
LIMITES
    re._parser est un module privé : sa forme peut changer d'une version à l'autre. Les
    assertions (?=...) et les ancres sont ignorées par l'automate ; une référence arrière
    est approchée par une copie du groupe ; une répétition bornée au-delà de 8 copies est
    traitée comme illimitée et un minimum au-delà de 8 est plafonné (signalé dans
    « notes ») ; les classes de caractères ne sont évaluées que sur l'échantillon. Le
    temps mesuré dépend de la machine. Dans le code Python, seuls les motifs littéraux (ou
    constantes de module, concaténations de littéraux) passés à re.* sont extraits ; le
    module regex tiers n'est pas analysé.
CONTRE-EXEMPLES
    __CONTRE_EXEMPLES__
INVOCATION
    {outil} --motif "(a+)+$" --seuil 0.2 --json
DOMAINE
    Expressions régulières de validation ou d'extraction appliquées à des entrées non
    fiables (requêtes, formulaires, journaux, fichiers téléversés) avec le module re de
    CPython ; pour un autre moteur (RE2, Rust, .NET avec délai), le verdict ne vaut pas.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from re import _compiler as compilateur_re
from re import _constants as constantes_re
from re import _parser as analyseur_re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_motif", "extraire_regex_python", "construire_automate", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
VERDICT_EXPONENTIEL = "EXPONENTIEL"
VERDICT_POLYNOMIAL = "POLYNOMIAL"
VERDICT_NON_CONFIRME = "NON CONFIRMÉ"
VERDICT_SANS_CANDIDAT = "SANS AMBIGUÏTÉ"
VERDICT_INVALIDE = "INVALIDE"
VERDICT_NON_ANALYSE = "NON ANALYSÉ"
VERDICT_NON_MESURE = "NON MESURÉ"
VULNERABLES = (VERDICT_EXPONENTIEL, VERDICT_POLYNOMIAL)

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3

PLAFOND_COPIES = 8
ETATS_MAX = 3000
SCC_MAX = 150
PAIRES_MAX = 60_000
TRIPLETS_MAX = 40_000
BUDGET_TRIPLETS = 300_000
UNIVERS_MAX = 400
EXAMINES_MAX = 200
K_EXPONENTIEL_MAX = 200
LONGUEUR_POLY_DEPART = 500
TEMPS_SIGNIFICATIF = 1e-3
DOSSIERS_SAUTES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
                             "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
PALETTE = ("aAzZmM09_ -.@/\\:;,!?\"'#$%&*+=<>()[]{}|^~`\t\n\r\x00\x0b"
           + "".join(map(chr, (0xE9, 0xDF, 0x4E2D, 0x3A9, 0x436, 0x663, 0xA0, 0x2028,
                               0x1F600))))
SUFFIXES_PREFERES = ("!", "\x00", "\n", "", " ", "@", "x", "0", "\t", ";")
FONCTIONS_RE = {"compile": "search", "search": "search", "match": "match",
                "fullmatch": "fullmatch", "findall": "search", "finditer": "search",
                "sub": "search", "subn": "search", "split": "search"}
POSITION_DRAPEAUX = {"compile": 1, "search": 2, "match": 2, "fullmatch": 2, "findall": 2,
                     "finditer": 2, "split": 3, "sub": 4, "subn": 4}
NOMS_DRAPEAUX = frozenset({"I", "IGNORECASE", "M", "MULTILINE", "S", "DOTALL", "X", "VERBOSE",
                           "A", "ASCII", "U", "UNICODE", "L", "LOCALE", "NOFLAG"})
METHODES_MOTIF = frozenset({"search", "match", "fullmatch", "findall", "finditer", "sub",
                            "subn", "split"})
OPS_CARACTERE = frozenset({constantes_re.LITERAL, constantes_re.NOT_LITERAL,
                           constantes_re.ANY, constantes_re.IN})
OPS_REPETITION = frozenset({constantes_re.MAX_REPEAT, constantes_re.MIN_REPEAT,
                            constantes_re.POSSESSIVE_REPEAT})
OPS_IGNORES = frozenset({constantes_re.AT, constantes_re.ASSERT, constantes_re.ASSERT_NOT})

CODE_ENFANT = r'''
import json, re, sys, time
d = json.loads(sys.stdin.read())
motif = d["motif"].encode("latin-1") if d["octets"] else d["motif"]
fn = getattr(re.compile(motif, d["drapeaux"]), d["mode"])
def fabriquer(k, suffixe):
    s = d["prefixe"] + d["pompe"] * k + suffixe
    return s.encode("latin-1") if d["octets"] else s
retenu = None
for candidat in d["suffixes"]:
    if fn(fabriquer(d["k_essai"], candidat)) is None:
        retenu = candidat
        break
print(json.dumps({"suffixe": retenu}), flush=True)
if retenu is not None:
    for k in d["tailles"]:
        s = fabriquer(k, retenu)
        t0 = time.perf_counter()
        fn(s)
        dt = time.perf_counter() - t0
        print(json.dumps({"k": k, "n": len(s), "t": dt}), flush=True)
        if dt >= d["seuil"]:
            break
'''


class NonPrisEnCharge(Exception):
    """Construction de l'arbre que l'automate ne sait pas traduire."""


@dataclass
class Atomes:
    """Table des classes de caractères d'une expression (op, valeur, drapeaux)."""

    cles: dict[Hashable, int] = field(default_factory=dict)
    items: list[tuple[object, object, int]] = field(default_factory=list)
    masques: list[int] = field(default_factory=list)


@dataclass
class Chantier:
    """Automate à transitions vides en cours de construction."""

    atomes: Atomes
    eps: list[list[int]] = field(default_factory=list)
    trans: list[list[tuple[int, int]]] = field(default_factory=list)
    atomique: set[int] = field(default_factory=set)
    groupes: dict[int, list] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Contexte:
    """Drapeaux en vigueur, appartenance à un groupe atomique, profondeur de copie."""

    drapeaux: int
    atomique: bool = False
    profondeur: int = 0


@dataclass
class Automate:
    """Automate sans transition vide : état 0 initial."""

    trans: list[list[tuple[int, int]]]
    acceptants: set[int]
    atomique: set[int]
    implicite: int | None


@dataclass
class Regex:
    """Une expression à examiner et son origine."""

    origine: str
    ligne: int
    colonne: int
    motif: str
    octets: bool
    drapeaux: int
    mode: str


@dataclass
class Candidat:
    """Une ambiguïté trouvée et l'entrée d'attaque qui en découle."""

    genre: str
    construction: str
    prefixe: str
    pompe: str
    suffixes: list[str]
    mesures: list[dict[str, float]] = field(default_factory=list)
    suffixe: str | None = None
    conclusion: str = ""
    detail: str = ""
    entree_python: str = ""


def nouvel_etat(ch: Chantier) -> int:
    """Ajoute un état et rend son numéro."""
    if len(ch.eps) >= ETATS_MAX:
        raise NonPrisEnCharge(f"plus de {ETATS_MAX} états")
    ch.eps.append([])
    ch.trans.append([])
    return len(ch.eps) - 1


def cle_atome(op: object, valeur: object, drapeaux: int) -> Hashable:
    """Clé hachable d'une classe de caractères."""
    if isinstance(valeur, list):
        valeur = tuple((o, tuple(v) if isinstance(v, list) else v) for o, v in valeur)
    return (str(op), valeur, drapeaux)


def id_atome(ch: Chantier, op: object, valeur: object, drapeaux: int) -> int:
    """Numéro de la classe dans la table (créée au besoin)."""
    cle = cle_atome(op, valeur, drapeaux)
    if cle not in ch.atomes.cles:
        ch.atomes.cles[cle] = len(ch.atomes.items)
        ch.atomes.items.append((op, valeur, drapeaux))
    return ch.atomes.cles[cle]


def construire_sequence(ch: Chantier, items: Iterable, debut: int, ctx: Contexte) -> int:
    """Enchaîne les éléments d'une séquence ; rend l'état de fin."""
    courant = debut
    for op, valeur in items:
        courant = construire_item(ch, op, valeur, courant, ctx)
    return courant


def construire_item(ch: Chantier, op: object, valeur: object, debut: int,
                    ctx: Contexte) -> int:
    """Traduit un élément de l'arbre de re._parser."""
    if op in OPS_CARACTERE:
        fin = nouvel_etat(ch)
        ch.trans[debut].append((id_atome(ch, op, valeur, ctx.drapeaux), fin))
        if ctx.atomique:
            ch.atomique.update((debut, fin))
        return fin
    if op in OPS_IGNORES:
        return debut
    if op is constantes_re.SUBPATTERN:
        return _construire_groupe(ch, valeur, debut, ctx)
    if op is constantes_re.BRANCH:
        return construire_alternatives(ch, valeur[1], debut, ctx)
    if op in OPS_REPETITION:
        atome = ctx if op is not constantes_re.POSSESSIVE_REPEAT else Contexte(
            ctx.drapeaux, True, ctx.profondeur)
        return construire_repetition(ch, valeur, debut, atome)
    if op is constantes_re.ATOMIC_GROUP:
        return construire_sequence(ch, valeur, debut, Contexte(ctx.drapeaux, True,
                                                               ctx.profondeur))
    return _construire_reference(ch, op, valeur, debut, ctx)


def _construire_groupe(ch: Chantier, valeur: tuple, debut: int, ctx: Contexte) -> int:
    """Groupe (capturant ou non) avec ses drapeaux locaux."""
    groupe, ajout, retrait, contenu = valeur
    if groupe is not None:
        ch.groupes[groupe] = contenu
    drapeaux = (ctx.drapeaux | ajout) & ~retrait
    return construire_sequence(ch, contenu, debut, Contexte(drapeaux, ctx.atomique,
                                                            ctx.profondeur))


def _construire_reference(ch: Chantier, op: object, valeur: object, debut: int,
                          ctx: Contexte) -> int:
    """Référence arrière (approchée par une copie du groupe) et test d'existence."""
    if op is constantes_re.GROUPREF:
        contenu = ch.groupes.get(valeur)
        if contenu is None or ctx.profondeur > 3:
            ch.notes.append(f"référence arrière \\{valeur} ignorée")
            return debut
        ch.notes.append(f"référence arrière \\{valeur} approchée par une copie du groupe")
        return construire_sequence(ch, contenu, debut, Contexte(ctx.drapeaux, ctx.atomique,
                                                                ctx.profondeur + 1))
    if op is constantes_re.GROUPREF_EXISTS:
        _, oui, non = valeur
        return construire_alternatives(ch, [oui, non or []], debut, ctx)
    raise NonPrisEnCharge(f"élément {op} non traduit")


def construire_alternatives(ch: Chantier, alternatives: list, debut: int,
                            ctx: Contexte) -> int:
    """Alternative : une branche vide par possibilité, réunies en un état de fin."""
    fin = nouvel_etat(ch)
    for alternative in alternatives:
        entree = nouvel_etat(ch)
        ch.eps[debut].append(entree)
        sortie = construire_sequence(ch, alternative, entree, ctx)
        ch.eps[sortie].append(fin)
    return fin


def construire_repetition(ch: Chantier, valeur: tuple, debut: int, ctx: Contexte) -> int:
    """{min,max} : copies obligatoires (plafonnées), puis boucle ou copies facultatives."""
    minimum, maximum, contenu = valeur
    illimite = maximum is constantes_re.MAXREPEAT or maximum - minimum > PLAFOND_COPIES
    if minimum > PLAFOND_COPIES:
        ch.notes.append(f"minimum {minimum} ramené à {PLAFOND_COPIES} copies")
    if illimite and maximum is not constantes_re.MAXREPEAT:
        ch.notes.append(f"répétition {{{minimum},{maximum}}} traitée comme illimitée")
    courant = debut
    for _ in range(min(minimum, PLAFOND_COPIES)):
        courant = construire_sequence(ch, contenu, courant, ctx)
    fin = nouvel_etat(ch)
    if illimite:
        boucle = nouvel_etat(ch)
        ch.eps[courant].append(boucle)
        entree = nouvel_etat(ch)
        ch.eps[boucle].extend((entree, fin))
        ch.eps[construire_sequence(ch, contenu, entree, ctx)].append(boucle)
        return fin
    for _ in range(maximum - minimum):
        ch.eps[courant].append(fin)
        courant = construire_sequence(ch, contenu, courant, ctx)
    ch.eps[courant].append(fin)
    return fin


def fermeture(ch: Chantier, etat: int) -> set[int]:
    """États atteignables par transitions vides."""
    vus = {etat}
    pile = [etat]
    while pile:
        for suivant in ch.eps[pile.pop()]:
            if suivant not in vus:
                vus.add(suivant)
                pile.append(suivant)
    return vus


def eliminer_vides(ch: Chantier, debut: int, fin: int, implicite: int | None) -> Automate:
    """Automate équivalent sans transition vide (états utiles seulement)."""
    cibles = sorted({d for liste in ch.trans for _, d in liste} - {debut})
    utiles = [debut] + cibles
    numero = {e: i for i, e in enumerate(utiles)}
    trans: list[list[tuple[int, int]]] = []
    acceptants: set[int] = set()
    for etat in utiles:
        ferme = fermeture(ch, etat)
        if fin in ferme:
            acceptants.add(numero[etat])
        trans.append(sorted({(a, numero[d]) for x in ferme for a, d in ch.trans[x]}))
    atomique = {numero[e] for e in utiles if e in ch.atomique}
    return Automate(trans, acceptants, atomique, None if implicite is None else 0)


def construire_automate(arbre: list, drapeaux: int, recherche: bool,
                        atomes: Atomes | None = None) -> tuple[Automate, Chantier]:
    """Automate d'un arbre ; en mode search, une boucle « tout caractère » le précède."""
    ch = Chantier(atomes if atomes is not None else Atomes())
    debut = nouvel_etat(ch)
    implicite = None
    entree = debut
    if recherche:
        implicite = debut
        entree = nouvel_etat(ch)
        tout = id_atome(ch, constantes_re.ANY, None, constantes_re.SRE_FLAG_DOTALL
                        | (drapeaux & constantes_re.SRE_FLAG_UNICODE))
        ch.trans[debut].append((tout, debut))
        ch.eps[debut].append(entree)
    fin = construire_sequence(ch, arbre, entree, Contexte(drapeaux))
    return eliminer_vides(ch, debut, fin, implicite), ch


def _codes_atome(op: object, valeur: object) -> Iterator[int]:
    """Points de code cités par une classe (littéraux, bornes d'intervalles)."""
    if op in (constantes_re.LITERAL, constantes_re.NOT_LITERAL):
        yield valeur
    elif op is constantes_re.IN:
        for o, v in valeur:
            if o is constantes_re.LITERAL:
                yield v
            elif o is constantes_re.RANGE:
                yield from v


def univers(atomes: Atomes, octets: bool) -> list[str]:
    """Échantillon de caractères : palette fixe, caractères cités, leurs voisins et casses."""
    codes = {ord(c) for c in PALETTE}
    for op, valeur, _ in atomes.items:
        for code in _codes_atome(op, valeur):
            for voisin in (code - 1, code, code + 1):
                if 0 <= voisin <= 0x10FFFF:
                    codes.add(voisin)
                    codes.update(ord(c) for c in chr(voisin).swapcase() if len(
                        chr(voisin).swapcase()) == 1)
    if octets:
        codes = {c for c in codes if c < 256}
    return [chr(c) for c in sorted(codes)][:UNIVERS_MAX]


def masque_atome(op: object, valeur: object, drapeaux: int, echantillon: list[str],
                 octets: bool) -> int:
    """Bits des caractères de l'échantillon acceptés par la classe (moteur re lui-même)."""
    etat = analyseur_re.State()
    etat.flags = drapeaux
    rx = compilateur_re.compile(analyseur_re.SubPattern(etat, [(op, valeur)]), 0)
    masque = 0
    for i, car in enumerate(echantillon):
        if rx.fullmatch(car.encode("latin-1") if octets else car):
            masque |= 1 << i
    return masque


def calculer_masques(atomes: Atomes, echantillon: list[str], octets: bool) -> None:
    """Complète la table des masques pour les classes encore sans masque."""
    for op, valeur, drapeaux in atomes.items[len(atomes.masques):]:
        atomes.masques.append(masque_atome(op, valeur, drapeaux, echantillon, octets))


def composantes_fortes(noeuds: Iterable[Hashable],
                       voisins: Callable[[Hashable], Iterable[Hashable]]) -> list[list]:
    """Composantes fortement connexes (Tarjan itératif), en ordre topologique inverse."""
    index: dict[Hashable, int] = {}
    bas: dict[Hashable, int] = {}
    pile: list[Hashable] = []
    sur_pile: set[Hashable] = set()
    resultat: list[list] = []
    for racine in noeuds:
        if racine not in index:
            _tarjan_depuis(racine, voisins, index, bas, pile, sur_pile, resultat)
    return resultat


def _tarjan_depuis(racine: Hashable, voisins: Callable, index: dict, bas: dict, pile: list,
                   sur_pile: set, resultat: list) -> None:
    """Parcours de Tarjan depuis une racine (sans récursion)."""
    def visiter(n: Hashable) -> None:
        index[n] = bas[n] = len(index)
        pile.append(n)
        sur_pile.add(n)
        travail.append((n, iter(voisins(n))))

    travail: list[tuple[Hashable, Iterator]] = []
    visiter(racine)
    while travail:
        noeud, suite = travail[-1]
        suivant = next((w for w in suite if w not in index or w in sur_pile), None)
        if suivant is not None:
            if suivant in index:
                bas[noeud] = min(bas[noeud], index[suivant])
            else:
                visiter(suivant)
            continue
        travail.pop()
        if travail:
            parent = travail[-1][0]
            bas[parent] = min(bas[parent], bas[noeud])
        if bas[noeud] == index[noeud]:
            composante = []
            while True:
                w = pile.pop()
                sur_pile.discard(w)
                composante.append(w)
                if w == noeud:
                    break
            resultat.append(composante)


def _etats_cycliques(aut: Automate) -> list[list[int]]:
    """Composantes de l'automate qui portent au moins un cycle."""
    voisins = lambda e: [d for _, d in aut.trans[e]]  # noqa: E731
    sortie = []
    for comp in composantes_fortes(range(len(aut.trans)), voisins):
        if len(comp) > 1 or any(d == comp[0] for _, d in aut.trans[comp[0]]):
            sortie.append(comp)
    return sortie


def _chemin(depart: Hashable, arrivee: Hashable, voisins: Callable,
            autorises: set | None) -> list[int] | None:
    """Plus court chemin (bits de caractères) d'un nœud à un autre, en largeur."""
    precedent: dict[Hashable, tuple[Hashable, int] | None] = {depart: None}
    file = deque([depart])
    while file:
        noeud = file.popleft()
        if noeud == arrivee and noeud != depart or (noeud == arrivee and depart != arrivee):
            break
        for suivant, bit in voisins(noeud):
            if suivant == arrivee and suivant == depart:
                return _remonter(precedent, noeud) + [bit]
            if suivant not in precedent and (autorises is None or suivant in autorises):
                precedent[suivant] = (noeud, bit)
                file.append(suivant)
    if arrivee not in precedent:
        return None
    return _remonter(precedent, arrivee)


def _remonter(precedent: dict, noeud: Hashable) -> list[int]:
    """Reconstitue la suite des bits d'un chemin trouvé en largeur."""
    bits = []
    while precedent[noeud] is not None:
        noeud, bit = precedent[noeud]
        bits.append(bit)
    return bits[::-1]


def _voisins_produit(aut: Automate, masques: list[int], dans: set[int]) -> Callable:
    """Voisins (paire suivante, bit témoin) dans l'automate produit restreint."""
    cache: dict[tuple[int, int], list] = {}

    def voisins(paire: tuple[int, int]) -> list:
        if paire not in cache:
            sortie = []
            for a1, d1 in aut.trans[paire[0]]:
                if d1 not in dans:
                    continue
                for a2, d2 in aut.trans[paire[1]]:
                    m = masques[a1] & masques[a2]
                    if m and d2 in dans:
                        sortie.append(((d1, d2), (m & -m).bit_length() - 1))
            cache[paire] = sortie
        return cache[paire]
    return voisins


def ambiguite_exponentielle(aut: Automate, masques: list[int], comp: list[int],
                            notes: list[str]) -> tuple[int, list[int]] | None:
    """(état, bits de la pompe) si deux chemins distincts reviennent à un même état."""
    if len(comp) > SCC_MAX:
        notes.append(f"composante de {len(comp)} états non explorée (plafond {SCC_MAX})")
        return None
    dans = set(comp)
    voisins = _voisins_produit(aut, masques, dans)
    atteints = _atteindre([(q, q) for q in comp], voisins, PAIRES_MAX)
    if atteints is None:
        notes.append("automate produit des paires trop grand : analyse exponentielle tronquée")
        return None
    simples = lambda p: [s for s, _ in voisins(p)]  # noqa: E731
    for groupe in composantes_fortes(atteints, simples):
        diagonale = [p for p in groupe if p[0] == p[1] and p[0] not in aut.atomique]
        hors = [p for p in groupe if p[0] != p[1]]
        if diagonale and hors and aut.implicite not in diagonale[0]:
            autorises = set(groupe)
            aller = _chemin(diagonale[0], hors[0], voisins, autorises)
            retour = _chemin(hors[0], diagonale[0], voisins, autorises)
            if aller is not None and retour is not None:
                return diagonale[0][0], aller + retour
    return None


def _atteindre(departs: list, voisins: Callable, plafond: int) -> list | None:
    """Nœuds atteignables (en largeur) ; None au-delà du plafond."""
    vus = dict.fromkeys(departs)
    file = deque(departs)
    while file:
        for suivant, _ in voisins(file.popleft()):
            if suivant not in vus:
                vus[suivant] = None
                if len(vus) > plafond:
                    return None
                file.append(suivant)
    return list(vus)


def _accessibles(aut: Automate, depart: int, inverse: bool) -> set[int]:
    """États accessibles depuis (ou co-accessibles vers) un état."""
    if inverse:
        arcs: dict[int, list[int]] = {}
        for s, liste in enumerate(aut.trans):
            for _, d in liste:
                arcs.setdefault(d, []).append(s)
        suivants = lambda e: arcs.get(e, [])  # noqa: E731
    else:
        suivants = lambda e: [d for _, d in aut.trans[e]]  # noqa: E731
    vus = {depart}
    pile = [depart]
    while pile:
        for s in suivants(pile.pop()):
            if s not in vus:
                vus.add(s)
                pile.append(s)
    return vus


def _voisins_triplets(aut: Automate, masques: list[int], zones: tuple[set, set, set]) -> Callable:
    """Voisins dans l'automate produit des triplets (x dans P, y entre P et Q, z dans Q)."""
    zp, zm, zq = zones

    def voisins(t: tuple[int, int, int]) -> Iterator:
        for a1, d1 in aut.trans[t[0]]:
            if d1 not in zp:
                continue
            for a2, d2 in aut.trans[t[1]]:
                m12 = masques[a1] & masques[a2]
                if not m12 or d2 not in zm:
                    continue
                for a3, d3 in aut.trans[t[2]]:
                    m = m12 & masques[a3]
                    if m and d3 in zq:
                        yield (d1, d2, d3), (m & -m).bit_length() - 1
    return voisins


def ambiguites_polynomiales(aut: Automate, masques: list[int], cycliques: list[list[int]],
                            notes: list[str]) -> list[tuple[int, int, list[int]]]:
    """(p, q, pompe) : boucles p et q reliées, toutes trois parcourues sur le même mot."""
    trouves = []
    budget = [BUDGET_TRIPLETS]
    for P in cycliques:
        p = P[0]
        accessibles = _accessibles(aut, p, False)
        for Q in cycliques:
            if Q is P or Q[0] not in accessibles or budget[0] <= 0:
                continue
            temoin = _ambiguite_paire(aut, masques, P, Q, accessibles, budget)
            if temoin is not None:
                trouves.append(temoin)
    if budget[0] <= 0:
        notes.append("analyse polynomiale tronquée (budget de triplets épuisé)")
    return trouves


def _ambiguite_paire(aut: Automate, masques: list[int], P: list[int], Q: list[int],
                     accessibles: set[int], budget: list[int]) -> tuple | None:
    """Cherche un mot w : p→p, p→q et q→q sur w, pour quelques p de P et q de Q."""
    for q in Q[:4]:
        milieu = accessibles & _accessibles(aut, q, True)
        voisins = _voisins_triplets(aut, masques, (set(P), milieu, set(Q)))
        for p in P[:4]:
            if p in aut.atomique or q in aut.atomique:
                continue
            bits = _chemin_triplet((p, p, q), (p, q, q), voisins, budget)
            if bits:
                return p, q, bits
    return None


def _chemin_triplet(depart: tuple, arrivee: tuple, voisins: Callable,
                    budget: list[int]) -> list[int] | None:
    """Chemin non vide en largeur dans l'automate des triplets, borné par le budget."""
    precedent: dict[tuple, tuple | None] = {depart: None}
    file = deque([depart])
    while file and budget[0] > 0:
        noeud = file.popleft()
        for suivant, bit in voisins(noeud):
            budget[0] -= 1
            if suivant == arrivee:
                return _remonter(precedent, noeud) + [bit]
            if suivant not in precedent and len(precedent) < TRIPLETS_MAX:
                precedent[suivant] = (noeud, bit)
                file.append(suivant)
    return None


def _texte_bits(bits: list[int], echantillon: list[str]) -> str:
    """Caractères correspondant à une suite de bits de l'échantillon."""
    return "".join(echantillon[b] for b in bits)


def _prefixe(aut: Automate, masques: list[int], cible: int, echantillon: list[str]) -> str:
    """Plus court mot menant de l'état initial à l'état cible."""
    def voisins(e: int) -> Iterator:
        for a, d in aut.trans[e]:
            if masques[a]:
                yield d, (masques[a] & -masques[a]).bit_length() - 1
    bits = _chemin(0, cible, voisins, None) if cible != 0 else []
    return _texte_bits(bits or [], echantillon)


def _suffixes(pompe: str, echantillon: list[str], octets: bool) -> list[str]:
    """Suffixes candidats pour faire échouer la correspondance, préférés d'abord."""
    candidats = list(SUFFIXES_PREFERES) + [c for c in echantillon if c not in pompe]
    vus = dict.fromkeys(c for c in candidats if c not in pompe or c == "")
    if octets:
        vus = {c: None for c in vus if all(ord(x) < 256 for x in c)}
    return list(vus)[:24]


def constructions(arbre: list, drapeaux: int, atomes: Atomes, echantillon: list[str],
                  octets: bool) -> list[str]:
    """Constructions à risque repérées dans l'arbre (description lisible)."""
    trouvees: list[str] = []
    _parcourir(arbre, drapeaux, atomes, echantillon, octets, trouvees, False)
    return list(dict.fromkeys(trouvees))


def _premiers(items: list, drapeaux: int, atomes: Atomes, echantillon: list[str],
              octets: bool) -> tuple[int, bool]:
    """(masque des premiers caractères, peut être vide) d'une sous-expression."""
    aut, _ = construire_automate(items, drapeaux, False, atomes)
    calculer_masques(atomes, echantillon, octets)
    masque = 0
    for a, _ in aut.trans[0]:
        masque |= atomes.masques[a]
    return masque, 0 in aut.acceptants


def _illimitee(op: object, valeur: object) -> bool:
    """Répétition sans borne (ou à borne traitée comme telle), hors possessive."""
    return op in (constantes_re.MAX_REPEAT, constantes_re.MIN_REPEAT) and (
        valeur[1] is constantes_re.MAXREPEAT or valeur[1] - valeur[0] > PLAFOND_COPIES)


def _parcourir(items: list, drapeaux: int, atomes: Atomes, echantillon: list[str],
               octets: bool, trouvees: list[str], sous_repetition: bool) -> None:
    """Parcours récursif de l'arbre à la recherche des trois constructions classiques."""
    outils = (atomes, echantillon, octets)
    _adjacentes(items, drapeaux, outils, trouvees)
    for op, valeur in items:
        if op is constantes_re.ATOMIC_GROUP or op is constantes_re.POSSESSIVE_REPEAT:
            continue
        if _illimitee(op, valeur):
            if sous_repetition:
                trouvees.append("quantificateurs imbriqués (répétition illimitée dans une "
                                "répétition illimitée)")
            _alternatives_sous(valeur[2], drapeaux, outils, trouvees)
            _parcourir(valeur[2], drapeaux, *outils, trouvees, True)
        elif op in OPS_REPETITION:
            _parcourir(valeur[2], drapeaux, *outils, trouvees, sous_repetition)
        elif op is constantes_re.SUBPATTERN:
            _parcourir(valeur[3], (drapeaux | valeur[1]) & ~valeur[2], *outils, trouvees,
                       sous_repetition)
        elif op is constantes_re.BRANCH:
            for alternative in valeur[1]:
                _parcourir(alternative, drapeaux, *outils, trouvees, sous_repetition)


def _alternatives_sous(items: list, drapeaux: int, outils: tuple, trouvees: list[str]) -> None:
    """Alternatives (même enfouies dans des groupes) dont les débuts se recouvrent."""
    for op, valeur in items:
        if op is constantes_re.SUBPATTERN:
            _alternatives_sous(valeur[3], drapeaux, outils, trouvees)
        elif op is constantes_re.BRANCH:
            debuts = [_premiers(alt, drapeaux, *outils) for alt in valeur[1]]
            for i, (m1, vide1) in enumerate(debuts):
                for m2, vide2 in debuts[i + 1:]:
                    if m1 & m2 or vide1 or vide2:
                        trouvees.append("alternatives qui se recouvrent sous une répétition")
                        return


def _adjacentes(items: list, drapeaux: int, outils: tuple, trouvees: list[str]) -> None:
    """Deux répétitions illimitées voisines (séparées de rien d'obligatoire) qui se
    recouvrent."""
    precedente = None
    for op, valeur in items:
        if _illimitee(op, valeur):
            masque, _ = _premiers(valeur[2], drapeaux, *outils)
            if precedente is not None and precedente & masque:
                trouvees.append("répétitions adjacentes qui acceptent les mêmes caractères")
                return
            precedente = masque
        elif op not in OPS_IGNORES:
            _, vide = _premiers([(op, valeur)], drapeaux, *outils)
            if not vide:
                precedente = None


def analyse_statique(rx: Regex) -> tuple[list[Candidat], list[str], dict[str, int], list[str]]:
    """(candidats, constructions, taille de l'automate, notes) d'une expression."""
    arbre = analyseur_re.parse(rx.motif.encode("latin-1") if rx.octets else rx.motif,
                               rx.drapeaux)
    drapeaux = arbre.state.flags
    atomes = Atomes()
    aut, ch = construire_automate(list(arbre), drapeaux, rx.mode == "search", atomes)
    echantillon = univers(atomes, rx.octets)
    calculer_masques(atomes, echantillon, rx.octets)
    trouvees = constructions(list(arbre), drapeaux, atomes, echantillon, rx.octets)
    candidats = _candidats(aut, atomes.masques, echantillon, rx.octets, trouvees, ch.notes)
    taille = {"etats": len(aut.trans), "transitions": sum(map(len, aut.trans)),
              "classes": len(atomes.items), "echantillon": len(echantillon)}
    return candidats, trouvees, taille, list(dict.fromkeys(ch.notes))


def _candidats(aut: Automate, masques: list[int], echantillon: list[str], octets: bool,
               trouvees: list[str], notes: list[str]) -> list[Candidat]:
    """Une ambiguïté exponentielle, puis polynomiales (intrinsèque, puis due à search)."""
    cycliques = _etats_cycliques(aut)
    sortie: list[Candidat] = []
    for comp in cycliques:
        temoin = ambiguite_exponentielle(aut, masques, comp, notes)
        if temoin is not None:
            etat, bits = temoin
            pompe = _texte_bits(bits, echantillon)
            sortie.append(Candidat("exponentiel", "; ".join(trouvees) or "ambiguïté de l'automate",
                                   _prefixe(aut, masques, etat, echantillon), pompe,
                                   _suffixes(pompe, echantillon, octets)))
            break
    vus_genres: set[str] = set()
    for p, q, bits in ambiguites_polynomiales(aut, masques, cycliques, notes):
        genre = "quadratique_recherche" if p == aut.implicite else "polynomial"
        if genre in vus_genres:
            continue
        vus_genres.add(genre)
        pompe = _texte_bits(bits, echantillon)
        construction = ("boucle implicite de search suivie d'une répétition" if genre ==
                        "quadratique_recherche" else "; ".join(trouvees) or "deux boucles reliées")
        sortie.append(Candidat(genre, construction, _prefixe(aut, masques, p, echantillon),
                               pompe, _suffixes(pompe, echantillon, octets)))
    return sortie


def _tailles(candidat: Candidat, longueur_max: int) -> list[int]:
    """Valeurs de k : pas de 1 pour l'exponentiel, longueur doublée pour le polynomial."""
    pompe = max(1, len(candidat.pompe))
    k_max = max(1, (longueur_max - len(candidat.prefixe) - 1) // pompe)
    if candidat.genre == "exponentiel":
        return list(range(1, min(K_EXPONENTIEL_MAX, k_max) + 1))
    tailles = []
    longueur = LONGUEUR_POLY_DEPART
    while longueur < longueur_max:
        tailles.append(max(1, longueur // pompe))
        longueur *= 2
    tailles.append(k_max)
    return sorted(set(tailles))


def mesurer(rx: Regex, candidat: Candidat, options: argparse.Namespace,
            delai: float) -> None:
    """Chronomètre l'entrée d'attaque dans un sous-processus borné ; complète le candidat."""
    charge = {"motif": rx.motif, "octets": rx.octets, "drapeaux": rx.drapeaux,
              "mode": rx.mode, "prefixe": candidat.prefixe, "pompe": candidat.pompe,
              "suffixes": candidat.suffixes, "seuil": options.seuil,
              "k_essai": 4 if candidat.genre == "exponentiel" else 16,
              "tailles": _tailles(candidat, options.longueur_max)}
    depasse = False
    try:
        fini = subprocess.run([sys.executable, "-I", "-S", "-c", CODE_ENFANT],
                              input=json.dumps(charge), capture_output=True, text=True,
                              timeout=max(1.0, delai))
        sortie = fini.stdout
    except subprocess.TimeoutExpired as exc:
        depasse = True
        brut = exc.stdout or b""
        sortie = brut.decode("utf-8", errors="replace") if isinstance(brut, bytes) else brut
    _lire_mesures(candidat, sortie, depasse, charge["tailles"], delai)
    conclure(candidat, options.seuil, depasse)


def _lire_mesures(candidat: Candidat, sortie: str, depasse: bool, tailles: list[int],
                  delai: float) -> None:
    """Interprète les lignes JSON écrites par le sous-processus."""
    lignes = [json.loads(l) for l in sortie.splitlines() if l.strip().startswith("{")]
    if lignes and "suffixe" in lignes[0]:
        candidat.suffixe = lignes[0]["suffixe"]
    candidat.mesures = [l for l in lignes if "k" in l]
    if depasse and candidat.suffixe is not None:
        faits = {m["k"] for m in candidat.mesures}
        restant = [k for k in tailles if k not in faits]
        if restant:
            n = len(candidat.prefixe) + len(candidat.pompe) * restant[0] + len(candidat.suffixe)
            candidat.mesures.append({"k": restant[0], "n": n, "t": delai, "delai_depasse": True})


def _croissance(mesures: list[dict[str, float]]) -> tuple[float | None, float | None]:
    """(facteur par pompe, pente log-log) entre les deux dernières mesures significatives."""
    utiles = [m for m in mesures if m["t"] >= TEMPS_SIGNIFICATIF]
    if len(utiles) < 2:
        return None, None
    a, b = utiles[-2], utiles[-1]
    if b["k"] == a["k"] or b["n"] == a["n"] or a["t"] <= 0:
        return None, None
    facteur = (b["t"] / a["t"]) ** (1 / (b["k"] - a["k"]))
    pente = math.log(b["t"] / a["t"]) / math.log(b["n"] / a["n"])
    return facteur, pente


def conclure(candidat: Candidat, seuil: float, depasse: bool) -> None:
    """Verdict d'un candidat à partir des mesures, et entrée Python reproductible."""
    if candidat.suffixe is None:
        candidat.conclusion = VERDICT_NON_CONFIRME
        candidat.detail = "aucun suffixe essayé ne fait échouer la correspondance"
        return
    facteur, pente = _croissance(candidat.mesures)
    dernier = candidat.mesures[-1] if candidat.mesures else {"k": 0, "n": 0, "t": 0.0}
    atteint = depasse or dernier["t"] >= seuil
    croissance = (f"facteur {facteur:.2f} par pompe, pente log-log {pente:.2f}"
                  if facteur is not None else "croissance non mesurable")
    candidat.entree_python = (f"{candidat.prefixe!r} + {candidat.pompe!r} * {dernier['k']} + "
                              f"{candidat.suffixe!r}")
    temps = "délai dépassé" if dernier.get("delai_depasse") else f"{dernier['t']:.3f} s"
    candidat.detail = f"{temps} pour {dernier['n']} caractères ; {croissance}"
    if not atteint:
        candidat.conclusion = VERDICT_NON_CONFIRME
    elif candidat.genre == "exponentiel" and (depasse or (facteur or 0) >= 1.2):
        candidat.conclusion = VERDICT_EXPONENTIEL
    else:
        candidat.conclusion = VERDICT_POLYNOMIAL


def analyser_motif(rx: Regex, options: argparse.Namespace, horloge: list[float]) -> dict:
    """Analyse statique puis mesure d'une expression ; rend son rapport."""
    rapport: dict[str, object] = {"origine": rx.origine, "ligne": rx.ligne,
                                  "colonne": rx.colonne, "motif": rx.motif,
                                  "octets": rx.octets, "drapeaux": rx.drapeaux, "mode": rx.mode}
    try:
        re.compile(rx.motif.encode("latin-1") if rx.octets else rx.motif, rx.drapeaux)
        candidats, trouvees, taille, notes = analyse_statique(rx)
    except re.error as exc:
        rapport.update(verdict=VERDICT_INVALIDE, detail=f"ne compile pas : {exc}")
        return rapport
    except (NonPrisEnCharge, RecursionError) as exc:
        rapport.update(verdict=VERDICT_NON_ANALYSE, detail=str(exc) or type(exc).__name__)
        return rapport
    rapport.update(constructions=trouvees, automate=taille, notes=notes)
    for candidat in candidats:
        if options.sans_mesure:
            continue
        reste = options.budget - (time.monotonic() - horloge[0])
        if reste <= 0:
            candidat.conclusion = VERDICT_NON_MESURE
            candidat.detail = "budget de mesure épuisé (--budget)"
            continue
        mesurer(rx, candidat, options, min(reste, options.seuil * 8 + 4))
        if candidat.conclusion in VULNERABLES:
            break
    rapport["candidats"] = [asdict(c) for c in candidats]
    rapport["verdict"] = _verdict_motif(candidats, options.sans_mesure)
    return rapport


def _verdict_motif(candidats: list[Candidat], sans_mesure: bool) -> str:
    """Le pire verdict des candidats."""
    if not candidats:
        return VERDICT_SANS_CANDIDAT
    if sans_mesure:
        return VERDICT_EXPONENTIEL if any(c.genre == "exponentiel" for c in candidats) \
            else VERDICT_NON_MESURE
    conclusions = [c.conclusion for c in candidats]
    for verdict in (VERDICT_EXPONENTIEL, VERDICT_POLYNOMIAL, VERDICT_NON_CONFIRME):
        if verdict in conclusions:
            return verdict
    return VERDICT_NON_MESURE


def _plier(noeud: ast.AST, constantes: dict[str, object]) -> object | None:
    """Valeur d'un littéral, d'une constante de module ou d'une concaténation de littéraux."""
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, (str, bytes)):
        return noeud.value
    if isinstance(noeud, ast.Name):
        return constantes.get(noeud.id)
    if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.Add):
        gauche, droite = _plier(noeud.left, constantes), _plier(noeud.right, constantes)
        if type(gauche) is type(droite) and gauche is not None:
            return gauche + droite
    return None


def _drapeaux_ast(noeud: ast.AST | None, alias_re: set[str]) -> int | None:
    """Valeur des drapeaux (re.I | re.M, entier littéral) ; None si inconnue."""
    if noeud is None:
        return 0
    if isinstance(noeud, ast.Constant) and isinstance(noeud.value, int):
        return noeud.value
    if isinstance(noeud, ast.Attribute) and isinstance(noeud.value, ast.Name) \
            and noeud.value.id in alias_re and noeud.attr in NOMS_DRAPEAUX:
        return int(getattr(re, noeud.attr))
    if isinstance(noeud, ast.BinOp) and isinstance(noeud.op, ast.BitOr):
        g, d = _drapeaux_ast(noeud.left, alias_re), _drapeaux_ast(noeud.right, alias_re)
        return None if g is None or d is None else g | d
    return None


def _noms_re(arbre: ast.AST) -> tuple[set[str], dict[str, str]]:
    """Alias du module re et fonctions importées directement (from re import ...)."""
    alias: set[str] = set()
    directes: dict[str, str] = {}
    for n in ast.walk(arbre):
        if isinstance(n, ast.Import):
            alias.update(a.asname or a.name for a in n.names if a.name == "re")
        elif isinstance(n, ast.ImportFrom) and n.module == "re":
            directes.update({a.asname or a.name: a.name for a in n.names
                             if a.name in FONCTIONS_RE})
    return alias, directes


def _usages_motifs(arbre: ast.AST) -> dict[str, set[str]]:
    """Méthodes appelées sur chaque nom (x.match(...) → {"x": {"match"}})."""
    usages: dict[str, set[str]] = {}
    for n in ast.walk(arbre):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in METHODES_MOTIF:
            cible = n.func.value
            nom = cible.id if isinstance(cible, ast.Name) else (
                cible.attr if isinstance(cible, ast.Attribute) else None)
            if nom:
                usages.setdefault(nom, set()).add(n.func.attr)
    return usages


def _mode_compile(appel: ast.Call, parents: dict[int, ast.AST],
                  usages: dict[str, set[str]]) -> str:
    """Mode d'un motif compilé : d'après les méthodes appelées sur la variable qui le reçoit."""
    parent = parents.get(id(appel))
    methodes: set[str] = set()
    if isinstance(parent, ast.Attribute) and parent.attr in METHODES_MOTIF:
        methodes = {parent.attr}
    elif isinstance(parent, (ast.Assign, ast.AnnAssign)):
        cibles = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        for c in cibles:
            nom = c.id if isinstance(c, ast.Name) else getattr(c, "attr", None)
            methodes |= usages.get(nom, set())
    if methodes and methodes <= {"fullmatch"}:
        return "fullmatch"
    if methodes and methodes <= {"match", "fullmatch"}:
        return "match"
    return "search"


def _constantes_module(arbre: ast.Module) -> dict[str, object]:
    """Constantes de module NOM = "..." (ou concaténation de littéraux)."""
    constantes: dict[str, object] = {}
    for n in arbre.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0],
                                                                             ast.Name):
            valeur = _plier(n.value, constantes)
            if valeur is not None:
                constantes[n.targets[0].id] = valeur
    return constantes


def extraire_regex_python(nom: str, source: str) -> tuple[list[Regex], list[str]]:
    """Expressions passées à re.* dans un source Python ; et celles non résolues."""
    arbre = ast.parse(source)
    alias, directes = _noms_re(arbre)
    constantes = _constantes_module(arbre)
    usages = _usages_motifs(arbre)
    parents = {id(enfant): n for n in ast.walk(arbre) for enfant in ast.iter_child_nodes(n)}
    trouvees: list[Regex] = []
    non_resolues: list[str] = []
    for n in ast.walk(arbre):
        fonction = _fonction_re(n, alias, directes)
        if fonction is None or not n.args and not n.keywords:
            continue
        cible = n.args[0] if n.args else next((k.value for k in n.keywords
                                               if k.arg == "pattern"), None)
        motif = _plier(cible, constantes) if cible is not None else None
        drapeaux = _drapeaux_ast(_argument_drapeaux(n, fonction), alias)
        if motif is None or drapeaux is None:
            non_resolues.append(f"{nom}:{n.lineno} re.{fonction}(...) non littéral")
            continue
        mode = (_mode_compile(n, parents, usages) if fonction == "compile"
                else FONCTIONS_RE[fonction])
        octets = isinstance(motif, bytes)
        texte = motif.decode("latin-1") if octets else motif
        trouvees.append(Regex(nom, n.lineno, n.col_offset + 1, texte, octets, drapeaux, mode))
    return trouvees, non_resolues


def _fonction_re(n: ast.AST, alias: set[str], directes: dict[str, str]) -> str | None:
    """Nom de la fonction re appelée par ce nœud, sinon None."""
    if not isinstance(n, ast.Call):
        return None
    f = n.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
            and f.value.id in alias and f.attr in FONCTIONS_RE:
        return f.attr
    if isinstance(f, ast.Name) and f.id in directes:
        return directes[f.id]
    return None


def _argument_drapeaux(appel: ast.Call, fonction: str) -> ast.AST | None:
    """Nœud de l'argument flags (positionnel ou nommé), s'il est donné."""
    for k in appel.keywords:
        if k.arg == "flags":
            return k.value
    position = POSITION_DRAPEAUX[fonction]
    return appel.args[position] if len(appel.args) > position else None


def _fichiers_python(dossier: Path, ignores: list[dict[str, str]]) -> Iterator[Path]:
    """Fichiers .py d'un dossier, sans suivre les liens, dossiers usuels sautés."""
    for courant, sous, fichiers in os.walk(dossier):
        base = Path(courant)
        for nom in sorted(sous):
            if nom in DOSSIERS_SAUTES:
                ignores.append({"chemin": str(base / nom), "raison": "dossier sauté"})
        sous[:] = sorted(n for n in sous if n not in DOSSIERS_SAUTES
                         and not (base / n).is_symlink())
        yield from (base / f for f in sorted(fichiers) if f.endswith(".py"))


def _lire_python(chemin: Path, taille_max: int) -> tuple[str, str]:
    """(source, raison d'écart) d'un fichier Python."""
    try:
        if chemin.stat().st_size > taille_max:
            return "", f"plus de {taille_max} octets"
        octets = chemin.read_bytes()
    except OSError as exc:
        return "", f"illisible ({exc.strerror or exc})"
    if b"\x00" in octets:
        return "", "binaire (octet nul) : pas un source Python"
    try:
        return octets.decode("utf-8-sig"), ""
    except UnicodeDecodeError as exc:
        return "", f"pas en UTF-8 ({exc.reason})"


def collecter(options: argparse.Namespace, ignores: list[dict[str, str]]) -> list[Regex]:
    """Expressions en ligne (--motif) puis celles extraites des fichiers Python."""
    regex = [Regex(f"<motif {i}>", 0, 0, m, False, 0, options.mode or "search")
             for i, m in enumerate(options.motif or [], start=1)]
    for chemin in options.resolus:
        fichiers = _fichiers_python(chemin, ignores) if chemin.is_dir() else [chemin]
        for fichier in fichiers:
            nom = _nom_affiche(fichier, options.base)
            source, raison = _lire_python(fichier, options.taille_max)
            if not raison:
                try:
                    trouvees, non_resolues = extraire_regex_python(nom, source)
                except (SyntaxError, ValueError, RecursionError) as exc:
                    raison = f"source Python illisible ({type(exc).__name__}: {exc})"
            if raison:
                ignores.append({"chemin": nom, "raison": raison})
                continue
            ignores.extend({"chemin": n, "raison": "motif non littéral"} for n in non_resolues)
            for r in trouvees:
                r.mode = options.mode or r.mode
            regex.extend(trouvees)
    return regex


def _nom_affiche(chemin: Path, base: Path) -> str:
    """Chemin relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def analyser(options: argparse.Namespace) -> dict[str, object]:
    """Rapport complet (sans le contrat)."""
    ignores: list[dict[str, str]] = []
    expressions = collecter(options, ignores)
    horloge = [time.monotonic()]
    resultats = [analyser_motif(rx, options, horloge) for rx in expressions]
    noms = [f"{r['origine']}:{r['ligne']}" if r["ligne"] else str(r["origine"])
            for r in resultats]
    vulnerables = [r for r in resultats if r["verdict"] in VULNERABLES]
    return {
        "denominateur": len(resultats),
        "examines": noms[:EXAMINES_MAX],
        "examines_tronques": len(noms) > EXAMINES_MAX,
        "moteur": "stdlib",
        "verdict": "VULNÉRABLE" if vulnerables else "AUCUNE EXPLOSION MESURÉE",
        "seuil_secondes": options.seuil,
        "longueur_max": options.longueur_max,
        "python": sys.version.split()[0],
        "vulnerables": len(vulnerables),
        "par_verdict": {v: sum(r["verdict"] == v for r in resultats)
                        for v in dict.fromkeys(str(r["verdict"]) for r in resultats)},
        "expressions": sorted(resultats, key=lambda r: r["verdict"] not in VULNERABLES),
        "ignores": ignores,
    }


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne[:1].isspace():
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {titre: " ".join(l for l in lignes if l) for titre, lignes in sections.items()}


def afficher_json(rapport: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def afficher_humain(rapport: dict[str, object]) -> None:
    """Résumé lisible : une expression par bloc, entrée d'attaque reproductible."""
    print(f"{rapport['denominateur']} expression(s) examinée(s) — {rapport['verdict']} "
          f"({rapport['vulnerables']} vulnérable(s) ; seuil {rapport['seuil_secondes']} s, "
          f"{rapport['longueur_max']} caractères au plus, Python {rapport['python']})")
    for r in rapport["expressions"]:
        lieu = f"{r['origine']}:{r['ligne']}" if r["ligne"] else r["origine"]
        print(f"  {lieu} [{r['mode']}] {r['motif']!r} — {r['verdict']}")
        if r.get("detail"):
            print(f"      {r['detail']}")
        for c in r.get("candidats", []):
            print(f"      {c['genre']} ({c['construction']}) : {c['conclusion'] or 'non mesuré'}"
                  f" — {c['detail']}")
            if c["entree_python"]:
                print(f"      entrée : {c['entree_python']}")
    for i in rapport["ignores"][:20]:
        print(f"  ignoré : {i['chemin']} — {i['raison']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Cherche les expressions régulières qui explosent en temps (ReDoS) : "
                    "analyse de l'automate puis mesure sur une entrée d'attaque construite.",
        epilog=f"Exemple : python {RACINE.name}/analyser_regex_redos.py src/ --motif "
               "'^(\\w+\\s?)*$' --json (code 0 : aucune explosion mesurée ; 1 : vulnérable ; "
               "2 : entrée invalide ; 3 : rien à examiner)")
    p.add_argument("chemins", nargs="*", type=Path,
                   help="fichiers .py ou dossiers dont les appels re.* sont extraits")
    p.add_argument("--motif", "-e", action="append", metavar="REGEX",
                   help="expression à examiner directement (répétable)")
    p.add_argument("--mode", choices=("search", "match", "fullmatch"), default=None,
                   help="méthode simulée (défaut : celle de l'appel trouvé, sinon search)")
    p.add_argument("--seuil", type=float, default=1.0,
                   help="temps (s) d'une seule correspondance qui confirme l'explosion (défaut 1)")
    p.add_argument("--longueur-max", type=int, default=50_000,
                   help="longueur maximale de l'entrée d'attaque (défaut 50000)")
    p.add_argument("--budget", type=float, default=120.0,
                   help="temps total de mesure, en secondes (défaut 120)")
    p.add_argument("--sans-mesure", action="store_true",
                   help="analyse statique seule (code 1 si ambiguïté exponentielle)")
    p.add_argument("--taille-max", type=float, default=5.0,
                   help="taille maximale d'un fichier Python lu, en Mo (défaut 5)")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs et des noms affichés (défaut : dossier courant)")
    return p


def _valider(options: argparse.Namespace) -> str:
    """Message d'erreur d'usage, ou chaîne vide."""
    if not options.chemins and not options.motif:
        return "rien à examiner : donner des fichiers, des dossiers ou --motif"
    if not options.base.is_dir():
        return f"--racine n'est pas un dossier : {options.base}"
    if options.seuil <= 0 or options.longueur_max < 10 or options.budget <= 0 \
            or options.taille_max <= 0:
        return "--seuil, --budget, --taille-max positifs et --longueur-max ≥ 10"
    for chemin in options.resolus:
        if not chemin.exists():
            return f"chemin introuvable : {chemin}"
    return ""


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : codes 0 (rien), 1 (vulnérable), 2 (usage), 3 (rien à examiner)."""
    options = _parseur().parse_args(argv)
    options.base = options.racine if options.racine is not None else Path.cwd()
    options.resolus = [c if c.is_absolute() else options.base / c for c in options.chemins]
    erreur = _valider(options)
    if erreur:
        print(f"analyser_regex_redos : {erreur}", file=sys.stderr)
        return CODE_USAGE
    options.taille_max = int(options.taille_max * 1_000_000)
    rapport = analyser(options)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if rapport["denominateur"] == 0:
        print("analyser_regex_redos : dénominateur nul — rien à examiner (aucune expression "
              f"littérale trouvée ; {len(rapport['ignores'])} écartée(s)).", file=sys.stderr)
    if options.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        return CODE_VIDE
    return CODE_TROUVE if rapport["vulnerables"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
