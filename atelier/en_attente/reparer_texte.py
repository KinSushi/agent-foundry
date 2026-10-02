"""Détecte et répare le texte abîmé (mojibake, double encodage, entités HTML, caractères de
contrôle, forme non NFC) en rapportant chaque segment corrigé, sans toucher au texte sain.

Mesuré dans cette session (python corpus_reparer_texte.py --large, bloc-notes de session) :
sur 144 fichiers .md français du dépôt, un seul est modifié, docs/croiser_motifs.md, qui
porte réellement un caractère de contrôle (retour arrière, 0x08) ; relus en cp1252 après
encodage UTF-8, 143/144 sont restaurés à l'identique, 143/144 aussi après double encodage ;
0 modification sur 13 catalogues français de Django et 61 docstrings anglaises de la stdlib.

QUESTION
    Ce texte est-il abîmé (mojibake, double encodage, entités HTML, caractères de
    contrôle), et que donne sa réparation ?
MESURE
    Par passes successives, chaque passe produisant des segments (position d'origine,
    avant, après) : 1) mojibake : une suite de caractères hors 7 bits, tous codables sur
    un octet en cp1252 / latin-1, est réencodée ; si les octets forment de l'UTF-8 valide qui
    décode vers un caractère plausible, elle est remplacée, et l'opération est répétée
    (double encodage) ; 2) entités HTML terminées par ';' (y compris &amp;eacute;) ;
    3) séquences d'échappement de terminal et caractères de contrôle (C0 sauf tabulation,
    sauts de ligne et saut de page ; DEL ; C1 ; BOM au milieu) ; 4) forme NFC, grappe par
    grappe (désactivable). Le caractère U+FFFD est signalé : la perte est irréversible.
HYPOTHÈSES
    Le texte a été écrit en UTF-8 puis éventuellement relu à tort en cp1252 ou latin-1.
    Aucun octet des séquences n'a été perdu, sauf l'espace insécable de « à », recollée
    seulement si d'autres mojibakes sont attestés dans le même texte.
LIMITES
    Tant qu'aucun mojibake fort (lettre latine accentuée, ponctuation, symbole, emoji)
    n'est attesté, une séquence isolée vers un idéogramme, une lettre cyrillique,
    hébraïque ou arabe n'est pas corrigée : trop proche d'un hasard. Un texte balisé
    (au moins deux balises) garde ses entités HTML. Les autres confusions d'encodage
    (cp437, Shift-JIS, codages cyrilliques à un octet) ne sont pas traitées. ftfy (facultatif) est réglé pour
    ne pas redresser les apostrophes typographiques, les ligatures ni la pleine chasse.
CONTRE-EXEMPLES
    Constaté : la notice de ftfy, qui cite exprès du mojibake en exemple, est « réparée »
    par les deux moteurs ; celle de wcwidth écrit « café » en NFD exprès et la passe NFC
    la change (d'où --sans-nfc) ; « Ã  la maison » seul reste non corrigé, faute d'un
    autre mojibake dans le texte pour attester l'erreur.
INVOCATION
    {outil} {fichier} --json
    {outil} --texte "CafÃ© &amp; thÃ©" --json
DOMAINE
    Textes et sorties de LLM en UTF-8, de quelques octets à quelques dizaines de
    mégaoctets ; pas les fichiers binaires ni les encodages multioctets non UTF-8.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import html
import html.entities
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import ftfy as _FTFY
except ImportError:
    _FTFY = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
INTITULES = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
             TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

MOTEUR_STDLIB = "stdlib"
FAMILLE_HTML = "HTML"
ENCODAGE_CIBLE = "UTF-8"
CARACTERE_PERTE = "U+FFFD"
MOTEUR_TIERS = "ftfy"
VERDICT_SAIN = "sain"
VERDICT_REPARE = "repare"
VERDICT_ALERTE = "alerte"

CODE_RIEN = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_VIDE = 3

OCTETS_MAX_DEFAUT = 64 * 1024 * 1024
SEGMENTS_MAX = 500
TEXTE_JSON_MAX = 1_000_000
TOURS_MAX = 3
BOM = chr(0xFEFF)
EXAMINES_MAX = 50

FTFY_REGLAGES = {"uncurl_quotes": False, "fix_latin_ligatures": False,
                 "fix_character_width": False, "fix_line_breaks": False}

RE_ENTITE = re.compile(r"&(?:amp;)*(?:#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[A-Za-z][A-Za-z0-9]{1,31};)")
RE_BALISE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]{0,200})?/?>")
RE_TERMINAL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]{0,200}(?:\x07|\x1b\\)")
RE_CONTROLE = re.compile("[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f\ufeff\ufffe\uffff\ud800-\udfff]")
RE_NBSP_PERDUE = re.compile("\u00c3 ")
OCTETS_CP1252_INDEFINIS = {0xDC00 + o: o for o in (0x81, 0x8D, 0x8F, 0x90, 0x9D)}
SUIVANTS_DE_A = " \t\r\n.,;:!?)]\u00bb\u00a0"


class ErreurEntree(Exception):
    """Entrée illisible ou invalide : porte le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


def construire_table_octets() -> dict[str, int]:
    """Caractère -> octet qu'il représente quand de l'UTF-8 a été lu en cp1252/latin-1."""
    table: dict[str, int] = {}
    for octet in range(0x80, 0x100):
        table[chr(octet)] = octet
        try:
            table[bytes([octet]).decode("cp1252")] = octet
        except UnicodeDecodeError:
            continue
    return table


TABLE_OCTETS = construire_table_octets()
SPECIAUX_CP1252 = frozenset(c for c in TABLE_OCTETS if ord(c) > 0xFF)
RE_SUITE_SUSPECTE = re.compile("[" + re.escape("".join(sorted(TABLE_OCTETS))) + "]{2,}")

PLAGES_FORTES = ((0x00A0, 0x017F), (0x0300, 0x036F), (0x0370, 0x03FF), (0x2000, 0x206F),
                 (0x20A0, 0x20FF), (0x2100, 0x214F), (0x2190, 0x2BFF), (0xFE00, 0xFE0F),
                 (0xFEFF, 0xFEFF), (0x1F000, 0x1FAFF))
PLAGES_RELAIS = ((0x0080, 0x009F),)
PLAGES_FAIBLES = ((0x0400, 0x04FF), (0x05D0, 0x05EA), (0x0600, 0x06FF),
                  (0x0900, 0x0E7F), (0x1E00, 0x1EFF), (0x2E80, 0x30FF), (0x3400, 0x9FFF),
                  (0xAC00, 0xD7AF), (0xFF00, 0xFFEF), (0x20000, 0x3134F))


# -- passe 1 : mojibake --------------------------------------------------------

def longueur_sequence(octet: int) -> int:
    if 0xC2 <= octet <= 0xDF:
        return 2
    if 0xE0 <= octet <= 0xEF:
        return 3
    if 0xF0 <= octet <= 0xF4:
        return 4
    return 0


def force_caractere(caractere: str) -> str | None:
    """'fort', 'faible', 'relais' ou None selon la plausibilité du caractère décodé.

    Un « relais » (contrôle C1) n'est jamais du texte : il n'est accepté que comme étape
    intermédiaire d'un double encodage, à côté d'un décodage fort."""
    point = ord(caractere)
    if caractere in SPECIAUX_CP1252 or any(a <= point <= b for a, b in PLAGES_FORTES):
        return "fort"
    if any(a <= point <= b for a, b in PLAGES_RELAIS):
        return "relais"
    if any(a <= point <= b for a, b in PLAGES_FAIBLES):
        return "faible"
    return None


def decoder_suite(suite: str, relache: bool) -> tuple[str, int]:
    """Un tour de réparation d'une suite suspecte ; rend (texte, séquences décodées).

    Strict : il faut un décodage fort ou deux faibles. Relâché (quand le document a
    déjà un mojibake attesté) : un seul décodage faible suffit."""
    octets = [TABLE_OCTETS[c] for c in suite]
    morceaux: list[str] = []
    forces: list[str] = []
    i = 0
    while i < len(octets):
        caractere = sequence_utf8(octets, i)
        force = force_caractere(caractere) if caractere else None
        if force is None:
            morceaux.append(suite[i])
            i += 1
            continue
        morceaux.append("" if caractere == BOM else caractere)
        forces.append(force)
        i += longueur_sequence(octets[i])
    if "fort" in forces or forces.count("faible") >= (1 if relache else 2):
        return "".join(morceaux), len(forces)
    return suite, 0


def sequence_utf8(octets: list[int], i: int) -> str | None:
    longueur = longueur_sequence(octets[i])
    bloc = octets[i:i + longueur]
    if longueur == 0 or len(bloc) < longueur or not all(0x80 <= o <= 0xBF for o in bloc[1:]):
        return None
    try:
        return bytes(bloc).decode("utf-8")
    except UnicodeDecodeError:
        return None


def reparer_suite(suite: str, relache: bool = False) -> tuple[str, int]:
    """Répète le décodage tant qu'il progresse ; rend (texte, nombre de tours utiles)."""
    tours = 0
    courant = suite
    while tours < TOURS_MAX:
        candidats = [m for m in RE_SUITE_SUSPECTE.finditer(courant)]
        nouveau = courant
        for trouve in reversed(candidats):
            repare, decodes = decoder_suite(trouve.group(), relache)
            if decodes:
                nouveau = nouveau[:trouve.start()] + repare + nouveau[trouve.end():]
        if nouveau == courant:
            break
        courant = nouveau
        tours += 1
    return courant, tours


def passe_mojibake(texte: str) -> list[tuple[int, int, str, str]]:
    """Éditions (début, fin, remplacement, type) pour le mojibake du texte.

    Premier tour strict ; s'il atteste du mojibake, second tour relâché sur tout le texte."""
    editions = editions_mojibake(texte, False)
    if editions:
        editions = editions_mojibake(texte, True)
        editions.extend(editions_nbsp(texte, editions))
    return sorted(editions)


def editions_mojibake(texte: str, relache: bool) -> list[tuple[int, int, str, str]]:
    editions: list[tuple[int, int, str, str]] = []
    for trouve in RE_SUITE_SUSPECTE.finditer(texte):
        repare, tours = reparer_suite(trouve.group(), relache)
        if tours:
            type_ = "double_encodage" if tours > 1 else "mojibake"
            editions.extend(decouper_edition(trouve.start(), trouve.group(), repare, type_))
    return editions


def decouper_edition(debut: int, avant: str, apres: str, type_: str) -> list[tuple[int, int, str, str]]:
    """Réduit une édition aux seules zones changées (une suite peut mêler sain et abîmé)."""
    resultat = []
    comparateur = difflib.SequenceMatcher(None, avant, apres, autojunk=False)
    for code, a1, a2, b1, b2 in comparateur.get_opcodes():
        if code != "equal":
            resultat.append((debut + a1, debut + a2, apres[b1:b2], type_))
    return resultat


def editions_nbsp(texte: str, deja: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """« Ã » suivi d'une espace : le « à » dont l'insécable a été changée en espace."""
    occupees = {i for a, b, _, _ in deja for i in range(a, b)}
    resultat = []
    for trouve in RE_NBSP_PERDUE.finditer(texte):
        if trouve.start() in occupees:
            continue
        suivant = texte[trouve.end():trouve.end() + 1]
        fin = trouve.end() if (not suivant or suivant in SUIVANTS_DE_A) else trouve.start() + 1
        resultat.append((trouve.start(), fin, "\u00e0", "mojibake_nbsp"))
    return resultat


# -- passes 2 à 4 --------------------------------------------------------------

def passe_entites(texte: str) -> list[tuple[int, int, str, str]]:
    if len(RE_BALISE.findall(texte, 0, 2_000_000)) >= 2:
        return []
    editions = []
    for trouve in RE_ENTITE.finditer(texte):
        avant = trouve.group()
        if not entite_connue(avant):
            continue
        apres = avant
        for _ in range(TOURS_MAX + 1):
            suivant = html.unescape(apres)
            if suivant == apres:
                break
            apres = suivant
        if apres != avant and not RE_ENTITE.fullmatch(apres):
            type_ = "entite_html_double" if avant.startswith("&amp;") and len(avant) > 5 else "entite_html"
            editions.append((trouve.start(), trouve.end(), apres, type_))
    return editions


def entite_connue(brut: str) -> bool:
    """Le nom final existe en HTML5 (évite que « &notit; » devienne « ¬it; »)."""
    reste = brut[1:]
    while reste.startswith("amp;") and len(reste) > 4:
        reste = reste[4:]
    return reste.startswith("#") or reste in html.entities.html5


def passe_controles(texte: str) -> list[tuple[int, int, str, str]]:
    editions = [(m.start(), m.end(), "", "sequence_terminal") for m in RE_TERMINAL.finditer(texte)]
    prises = {i for a, b, _, _ in editions for i in range(a, b)}
    for trouve in RE_CONTROLE.finditer(texte):
        if trouve.start() in prises:
            continue
        caractere = trouve.group()
        remplacement = "\ufffd" if 0xD800 <= ord(caractere) <= 0xDFFF else ""
        editions.append((trouve.start(), trouve.end(), remplacement, "controle"))
    return sorted(editions)


def passe_nfc(texte: str) -> list[tuple[int, int, str, str]]:
    if unicodedata.is_normalized("NFC", texte):
        return []
    editions = []
    debut = 0
    for i in range(1, len(texte) + 1):
        if i == len(texte) or not prolonge_grappe(texte[i]):
            grappe = texte[debut:i]
            normale = unicodedata.normalize("NFC", grappe)
            if normale != grappe:
                editions.append((debut, i, normale, "normalisation_nfc"))
            debut = i
    return editions


def prolonge_grappe(caractere: str) -> bool:
    return unicodedata.combining(caractere) != 0 or 0x1160 <= ord(caractere) <= 0x11FF


# -- application des passes, positions ramenées au texte d'origine -------------

def appliquer(texte: str, editions: list[tuple[int, int, str, str]]) -> str:
    morceaux = []
    curseur = 0
    for debut, fin, remplacement, _ in editions:
        morceaux.append(texte[curseur:debut])
        morceaux.append(remplacement)
        curseur = fin
    morceaux.append(texte[curseur:])
    return "".join(morceaux)


Carte = tuple[list[int], list[tuple[int, int, int, int]]]


def ramener(position: int, cartes: list[Carte]) -> int:
    """Ramène une position d'une passe tardive vers le texte d'origine."""
    for carte in reversed(cartes):
        position = ramener_une(position, carte)
    return position


def ramener_une(position: int, carte: Carte) -> int:
    cles, editions = carte
    rang = bisect.bisect_right(cles, position) - 1
    if rang < 0:
        return position
    _, nouveau_fin, ancien_debut, ancien_fin = editions[rang]
    if position < nouveau_fin:
        return ancien_debut
    return position + (ancien_fin - nouveau_fin)


def carte_de(editions: list[tuple[int, int, str, str]]) -> Carte:
    carte = []
    delta = 0
    for debut, fin, remplacement, _ in editions:
        nouveau_debut = debut + delta
        carte.append((nouveau_debut, nouveau_debut + len(remplacement), debut, fin))
        delta += len(remplacement) - (fin - debut)
    return [e[0] for e in carte], carte


def reparer_stdlib(original: str, nfc: bool = True) -> tuple[str, list[dict[str, Any]]]:
    """Applique les passes ; rend le texte réparé et les segments d'origine."""
    texte = original
    cartes: list[Carte] = []
    segments: list[dict[str, Any]] = []
    passes = (passe_mojibake, passe_entites, passe_controles) + ((passe_nfc,) if nfc else ())
    for passe in passes:
        editions = passe(texte)
        if not editions:
            continue
        for debut, fin, remplacement, type_ in editions:
            a, b = ramener(debut, cartes), ramener(fin, cartes)
            segments.append({"type": type_, "position": a, "avant": original[a:b], "apres": remplacement})
        cartes.append(carte_de(editions))
        texte = appliquer(texte, editions)
    return texte, segments


# -- moteur tiers ------------------------------------------------------------

def reparer_tiers(original: str, nfc: bool = True) -> tuple[str, list[dict[str, Any]], list[str]]:
    """ftfy (réglé pour rester dans le périmètre) ; segments retrouvés par différence."""
    explique = _FTFY.fix_and_explain(original, normalization="NFC" if nfc else None, **FTFY_REGLAGES)
    etapes = [f"{action}:{detail}" for action, detail in (explique.explanation or [])]
    segments = segments_par_difference(original, explique.text)
    return explique.text, segments, etapes


def segments_par_difference(avant: str, apres: str) -> list[dict[str, Any]]:
    lignes_a, lignes_b = avant.splitlines(keepends=True), apres.splitlines(keepends=True)
    if len(lignes_a) != len(lignes_b):
        return differences(avant, apres, 0)
    segments: list[dict[str, Any]] = []
    decalage = 0
    for la, lb in zip(lignes_a, lignes_b):
        if la != lb:
            segments.extend(differences(la, lb, decalage))
        decalage += len(la)
    return segments


def differences(avant: str, apres: str, decalage: int) -> list[dict[str, Any]]:
    """Zones changées, élargies au mot entier (difflib coupe au milieu de « &amp; »)."""
    operations = difflib.SequenceMatcher(None, avant, apres, autojunk=False).get_opcodes()
    zones: list[list[int]] = []
    for code, a1, a2, _, _ in operations:
        if code == "equal":
            continue
        gauche, droite = etendre_au_mot(avant, a1, a2)
        if zones and a1 - gauche <= zones[-1][1]:
            zones[-1][1] = max(zones[-1][1], a2 + droite)
        else:
            zones.append([a1 - gauche, a2 + droite])
    resultat = []
    for a1, a2 in zones:
        b1, b2 = vers_apres(operations, a1, True), vers_apres(operations, a2, False)
        resultat.append({"type": classer_segment(avant[a1:a2], apres[b1:b2]), "position": decalage + a1,
                         "avant": avant[a1:a2], "apres": apres[b1:b2]})
    return resultat


def vers_apres(operations: list[tuple[str, int, int, int, int]], position: int, debut: bool) -> int:
    """Position correspondante dans le texte réparé (début : première opération qui la
    contient ; fin : dernière), une insertion placée sur la borne étant incluse."""
    ordre = operations if debut else list(reversed(operations))
    for code, a1, a2, b1, b2 in ordre:
        dedans = (a1 <= position < a2) if debut else (a1 < position <= a2)
        if dedans or a1 == a2 == position:
            if code == "equal":
                return b1 + (position - a1)
            return b1 if debut else b2
    return 0 if debut else operations[-1][4]


def etendre_au_mot(texte: str, debut: int, fin: int) -> tuple[int, int]:
    gauche = droite = 0
    while debut - gauche > 0 and not texte[debut - gauche - 1].isspace() and gauche < 40:
        gauche += 1
    while fin + droite < len(texte) and not texte[fin + droite].isspace() and droite < 40:
        droite += 1
    return gauche, droite


def classer_segment(avant: str, apres: str) -> str:
    """Donne un type stdlib à un segment trouvé par un autre moteur."""
    if html.unescape(avant) == apres:
        return "entite_html"
    if appliquer(avant, sorted(editions_mojibake(avant, True))) == apres:
        return "mojibake"
    if RE_CONTROLE.sub("", RE_TERMINAL.sub("", avant)) == apres:
        return "controle"
    if unicodedata.normalize("NFC", avant) == apres:
        return "normalisation_nfc"
    return "ftfy"


# -- orchestration -------------------------------------------------------------

def situer(texte: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    debuts = [0] + [m.end() for m in re.finditer("\n", texte)]
    for segment in segments:
        ligne = bisect.bisect_right(debuts, segment["position"]) - 1
        segment["ligne"] = ligne + 1
        segment["colonne"] = segment["position"] - debuts[ligne] + 1
    return sorted(segments, key=lambda s: s["position"])


def alertes_de(texte: str, original: str) -> list[dict[str, Any]]:
    alertes = []
    pertes = [m.start() for m in re.finditer("\ufffd", original)]
    if pertes:
        alertes.append({"type": "perte_irreversible", "nombre": len(pertes), "positions": pertes[:20],
                        "detail": f"{CARACTERE_PERTE} présent : des octets ont déjà été remplacés, rien à récupérer"})
    if len(RE_BALISE.findall(texte, 0, 2_000_000)) >= 2 and RE_ENTITE.search(texte):
        alertes.append({"type": "balisage", "nombre": 1, "positions": [],
                        "detail": f"texte balisé : entités {FAMILLE_HTML} conservées (elles y sont légitimes)"})
    return alertes


def compter_types(segments: list[dict[str, Any]]) -> dict[str, int]:
    compte: dict[str, int] = {}
    for segment in segments:
        compte[segment["type"]] = compte.get(segment["type"], 0) + 1
    return compte


def traiter_document(nom: str, original: str, encodage: str, options: argparse.Namespace) -> dict[str, Any]:
    moteur, nfc = options.moteur_effectif, not options.sans_nfc
    if moteur == MOTEUR_TIERS:
        repare, segments, etapes = reparer_tiers(original, nfc)
    else:
        (repare, segments), etapes = reparer_stdlib(original, nfc), []
    segments = situer(original, segments)
    alertes = alertes_de(repare, original)
    if encodage != "utf-8":
        alertes.append({"type": "fichier_non_utf8", "nombre": 1, "positions": [],
                        "detail": f"le fichier n'est pas de l'{ENCODAGE_CIBLE} : relu en {encodage}"})
    verdict = VERDICT_REPARE if segments else (VERDICT_ALERTE if alertes else VERDICT_SAIN)
    document = {"nom": nom, "moteur": moteur, "encodage_lu": encodage, "verdict": verdict,
                "nb_caracteres": len(original), "nb_segments": len(segments),
                "par_type": compter_types(segments), "segments": segments[:SEGMENTS_MAX],
                "segments_tronques": len(segments) > SEGMENTS_MAX, "alertes": alertes,
                "etapes_ftfy": etapes, "texte_repare": None, "texte_repare_omis": False}
    if repare != original:
        omis = len(repare) > TEXTE_JSON_MAX
        document.update({"texte_repare": None if omis else repare, "texte_repare_omis": omis})
    if options.comparer and _FTFY is not None:
        document["comparaison"] = comparer_moteurs(original, repare, moteur, nfc)
    return document | {"_repare": repare}


def comparer_moteurs(original: str, repare: str, moteur: str, nfc: bool) -> dict[str, Any]:
    autre = MOTEUR_STDLIB if moteur == MOTEUR_TIERS else MOTEUR_TIERS
    texte_autre = (reparer_stdlib(original, nfc)[0] if autre == MOTEUR_STDLIB
                   else reparer_tiers(original, nfc)[0])
    ecarts = segments_par_difference(repare, texte_autre)
    return {"moteur": autre, "meme_texte": texte_autre == repare, "nb_ecarts": len(ecarts),
            "ecarts": [{"position_dans_repare": e["position"], moteur: e["avant"], autre: e["apres"]}
                       for e in ecarts[:20]]}


# -- entrées -------------------------------------------------------------------

def decoder(octets: bytes, nom: str) -> tuple[str, str]:
    """UTF-8 (BOM toléré) sinon cp1252 ; refuse le binaire."""
    if b"\x00" in octets[:65536]:
        raise ErreurEntree(f"{nom} : fichier binaire (octet NUL), pas du texte")
    try:
        return octets.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        texte = octets.decode("cp1252", "surrogateescape").translate(OCTETS_CP1252_INDEFINIS)
        if sum(1 for c in texte[:65536] if ord(c) < 0x20 and c not in "\t\r\n\f") > len(texte[:65536]) // 10:
            raise ErreurEntree(f"{nom} : ni UTF-8 ni texte cp1252 plausible (contrôles en masse) : binaire ?")
        return texte, "cp1252"


def lire_borne(flux: Any, octets_max: int, nom: str) -> bytes:
    octets = flux.read(octets_max + 1)
    if len(octets) > octets_max:
        raise ErreurEntree(f"{nom} dépasse {octets_max} octets (--max-octets)")
    return octets


def collecter_entree(args: argparse.Namespace, base: Path) -> tuple[str, bytes]:
    if args.texte is not None:
        return "<texte>", os.fsencode(args.texte)
    if args.chemin is None:
        raise ErreurEntree("rien à lire : donner un CHEMIN, '-' (entrée standard) ou --texte")
    if args.chemin == "-":
        return "<stdin>", lire_borne(sys.stdin.buffer, args.max_octets, "<stdin>")
    chemin = Path(args.chemin)
    chemin = chemin if chemin.is_absolute() else base / chemin
    if not chemin.exists():
        raise ErreurEntree(f"{chemin} : chemin inexistant")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} : c'est un dossier ; donner un fichier texte")
    with chemin.open("rb") as flux:
        return str(chemin), lire_borne(flux, args.max_octets, str(chemin))


def choisir_moteur(demande: str) -> str:
    if demande == MOTEUR_TIERS and _FTFY is None:
        raise ErreurEntree("--moteur ftfy demandé mais le paquet ftfy est absent")
    if demande == "auto":
        if _FTFY is None:
            print("ftfy absent : mode dégradé, réparateur stdlib (cp1252/latin-1, entités, "
                  "contrôles, NFC)", file=sys.stderr)
            return MOTEUR_STDLIB
        return MOTEUR_TIERS
    return demande


# -- sorties -----------------------------------------------------------------

def extraire_contrat(doc: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith((" ", "\t")):
            courant = tete
            sections[courant] = []
        elif courant is not None and tete:
            sections[courant].append(tete)
    return {cle.lower().replace("è", "e").replace("-", "_"): " ".join(v) for cle, v in sections.items()}


def construire_rapport(documents: list[dict[str, Any]], base: Path, moteur: str) -> dict[str, Any]:
    noms = [d["nom"] for d in documents]
    return {
        "denominateur": len(documents),
        "examines": noms[:EXAMINES_MAX],
        "moteur": moteur,
        "ftfy_disponible": _FTFY is not None,
        "racine": str(base),
        "bilan": {v: sum(1 for d in documents if d["verdict"] == v)
                  for v in (VERDICT_SAIN, VERDICT_REPARE, VERDICT_ALERTE)},
        "documents": [{k: v for k, v in d.items() if not k.startswith("_")} for d in documents],
        "contrat": extraire_contrat(__doc__ or ""),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    for d in rapport["documents"]:
        sys.stdout.write(f"{d['nom']} : {d['verdict'].upper()} — {d['nb_segments']} segment(s) corrigé(s) "
                         f"sur {d['nb_caracteres']} caractères (moteur {d['moteur']}, lu en {d['encodage_lu']})\n")
        for s in d["segments"][:100]:
            lieu = f"l.{s['ligne']}:{s['colonne']}"
            montrer = ascii if s["type"] == "normalisation_nfc" else repr
            sys.stdout.write(f"  {lieu:<12} {s['type']:<20} {montrer(s['avant'])} -> {montrer(s['apres'])}\n")
        if d["nb_segments"] > 100:
            sys.stdout.write(f"  ... {d['nb_segments'] - 100} autre(s) ; par type : {d['par_type']}\n")
        for a in d["alertes"]:
            sys.stdout.write(f"  alerte {a['type']} : {a['detail']}\n")
        if "comparaison" in d:
            c = d["comparaison"]
            sys.stdout.write(f"  second avis {c['moteur']} : {'même texte' if c['meme_texte'] else str(c['nb_ecarts']) + ' écart(s)'}\n")


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Détecte et répare le texte abîmé : mojibake (UTF-8 lu en cp1252/latin-1), "
                    "double encodage, entités HTML, caractères de contrôle, forme non NFC. "
                    "Chaque segment corrigé est rapporté ; un texte sain n'est pas modifié.",
        epilog="Exemple : python reparer_texte.py export.csv --json\n"
               "          python reparer_texte.py --texte \"l'Ã©tÃ© &amp; lâ€™hiver\" --sortie propre.txt\n"
               "Codes : 0 texte sain ; 1 segments corrigés ou alerte ; 2 usage ; 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemin", nargs="?", help="fichier texte à examiner, ou '-' pour l'entrée standard")
    parseur.add_argument("--texte", help="texte donné en ligne (au lieu d'un chemin)")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_TIERS), default="auto",
                         help="auto : ftfy s'il est installé, sinon stdlib")
    parseur.add_argument("--comparer", action="store_true", help="second avis de l'autre moteur (ftfy requis)")
    parseur.add_argument("--sans-nfc", action="store_true",
                         help="ne pas normaliser en NFC (formes décomposées voulues)")
    parseur.add_argument("--sortie", type=Path, help="écrit le texte réparé (UTF-8) dans ce fichier")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue")
    parseur.add_argument("--racine", type=Path,
                         help=f"base des chemins relatifs (défaut : dossier courant ; outil : {RACINE.name})")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    base = (args.racine if args.racine is not None else Path.cwd()).resolve()
    try:
        moteur = args.moteur_effectif = choisir_moteur(args.moteur)
        nom, octets = collecter_entree(args, base)
        texte, encodage = decoder(octets, nom)
        documents = [traiter_document(nom, texte, encodage, args)] if texte else []
        if documents and args.sortie is not None:
            sortie = args.sortie if args.sortie.is_absolute() else base / args.sortie
            sortie.write_text(documents[0]["_repare"], encoding=ENCODAGE_CIBLE, errors="surrogatepass")
    except ErreurEntree as erreur:
        print(f"erreur : {erreur}", file=sys.stderr)
        return erreur.code
    except OSError as erreur:
        print(f"erreur de lecture/écriture : {erreur}", file=sys.stderr)
        return CODE_USAGE
    rapport = construire_rapport(documents, base, moteur)
    if args.json:
        sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
    else:
        afficher_humain(rapport)
    if not documents:
        print("dénominateur nul : rien à examiner (texte vide)", file=sys.stderr)
        return CODE_VIDE
    return CODE_RIEN if documents[0]["verdict"] == VERDICT_SAIN else CODE_DEFAUT


if __name__ == "__main__":
    raise SystemExit(main())
