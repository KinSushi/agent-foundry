"""Mesure la lisibilité d'un texte (Flesch, Flesch adapté au français de Kandel et Moles,
Flesch-Kincaid, LIX), la longueur de ses phrases, et liste les phrases trop longues.

Mesuré dans cette session (python eval_syllabes.py ; python comparer_lisibilite.py) : le
compteur de syllabes donne le bon compte pour 97,7 % des 12 202 occurrences de mots anglais des docstrings
de la stdlib (référence CMUdict) et pour 119 des 120 mots français d'une liste étiquetée à
la main (syllabes orales), contre 97/120 pour textstat 0.7.13 (« poète », « réel » : 1).

QUESTION
    Ce texte est-il lisible pour son public ?
MESURE
    Le texte (Markdown nettoyé : blocs de code, code en ligne, liens, balises, tableaux
    retirés) est coupé en phrases (ponctuation finale hors abréviations, lignes vides,
    titres et puces) et en mots (suites de lettres). Syllabes : heuristique par groupes
    de voyelles. Anglais : -e, -es, -ed muets, hiatus ia/io/ua/iu. Français, syllabes
    orales : e muet final après consonne non compté, -ent compté, hiatus marqués par
    tréma, é/è suivi d'une voyelle, y entre voyelles. Formules : Flesch = 206,835 -
    1,015 x mots/phrase - 84,6 x syllabes/mot ; Kandel et Moles = 207 - 1,015 x
    mots/phrase - 73,6 x syllabes/mot ; Flesch-Kincaid = 0,39 x mots/phrase + 11,8 x
    syllabes/mot - 15,59 ; LIX = mots/phrase + 100 x mots de plus de 6 lettres / mots.
    Score principal : Kandel et Moles en français, Flesch en anglais.
HYPOTHÈSES
    Le texte est de la prose suivie en français ou en anglais. Les formules mesurent
    la longueur des phrases et des mots, pas la clarté des idées ni le vocabulaire.
LIMITES
    Syllabes heuristiques : en français la terminaison verbale -ent est comptée
    (« parlent » : 2 au lieu de 1) et i + voyelle n'est jamais un hiatus ; en anglais
    les sigles avec voyelle (url, api) et les mots savants se trompent. Flesch-Kincaid
    n'est calibré que pour l'anglais. Sous 100 mots, le score est signalé instable.
    textstat n'est employé qu'en français : en anglais il télécharge cmudict par le
    réseau, ce qui exige --autoriser-reseau. Les deux moteurs ne coupent pas les
    phrases pareil : sur les 144 fichiers .md du dépôt, l'écart médian textstat moins
    stdlib vaut +9,05 en LIX et -5,04 en Kandel et Moles.
CONTRE-EXEMPLES
    Constaté : « Le chat dort. Il fait beau. » obtient 130,36 en Kandel et Moles, hors
    de l'échelle (six mots : signalé « échantillon court ») ; les lignes d'en-tête de la
    notice de paquet de textstat (Name:, Version:...) forment une « phrase » de 106 mots,
    faute de ponctuation finale.
INVOCATION
    {outil} {fichier} --json
    {outil} --texte "Le chat dort. Il fait beau aujourd'hui, et les enfants jouent dans le jardin derrière la maison." --json
DOMAINE
    Documentation, réponses de LLM, courriels et pages destinées à des lecteurs, en
    français ou en anglais, d'au moins quelques phrases ; pas le code ni les listes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import textstat as _TEXTSTAT
except ImportError:
    _TEXTSTAT = None

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
MOTEUR_TIERS = "textstat"
FORMULE_LIX = "LIX"
ENCODAGE_ATTENDU = "UTF-8"

CODE_RIEN = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_VIDE = 3

FLESCH_MIN_DEFAUT = 50.0
LIX_MAX_DEFAUT = 50.0
PHRASE_MAX_DEFAUT = 25
MOTS_FIABLES = 100
MOT_LONG = 6
OCTETS_MAX_DEFAUT = 16 * 1024 * 1024
EXAMINES_MAX = 50
PHRASES_LISTEES_MAX = 100

ABREVIATIONS = frozenset("""
m mm mme mlle dr pr st ste etc cf ex env av apr p pp n no vol chap fig éd ed mr mrs ms prof sr jr
vs e.g i.e inc ltd co eq approx dept est jan feb mar apr jun jul aug sep sept oct nov dec al
""".split())
MOTS_FR = frozenset("le la les des une est que qui dans pour pas sur avec sont ce cette il elle nous vous "
                    "du au aux et ou mais donc ne plus par".split())
MOTS_EN = frozenset("the of and to is that it for are with as was this be by on not or from have an "
                    "which you at but can will".split())
ECHELLE_FLESCH = ((90, "très facile"), (80, "facile"), (70, "assez facile"), (60, "standard"),
                  (50, "assez difficile"), (30, "difficile"), (float("-inf"), "très difficile"))
ECHELLE_LIX = ((30, "très facile"), (40, "facile"), (50, "moyen"), (60, "difficile"),
               (float("inf"), "très difficile"))

RE_CODE_BLOC = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.MULTILINE | re.DOTALL)
RE_CODE_LIGNE = re.compile(r"`[^`\n]*`")
RE_LIEN = re.compile(r"!?\[([^\]\n]*)\]\([^)\n]*\)")
RE_URL = re.compile(r"(?:https?|ftp)://\S+|www\.\S+")
RE_BALISE = re.compile(r"<[^>\n]{0,300}>")
RE_TABLEAU = re.compile(r"^[ \t]*\|.*$", re.MULTILINE)
RE_MOT = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
RE_FIN_PHRASE = re.compile(r"[.!?…]+[\"'»”’)\]]*(?=\s|$)")
RE_DEBUT_UNITE = re.compile(r"^\s*(?:#{1,6}\s|[-*+•]\s|\d{1,3}[.)]\s|>\s)")
RE_VOYELLES_EN = re.compile(r"[aeiouy]+")
RE_VOYELLES_FR = re.compile("[aeiouyàâäéèêëîïôö"
                            "ùûüœæ]+")
RE_ELISION = re.compile(r"^(?:l|d|j|m|n|s|t|c|qu)'")
RE_U_MUET = re.compile(r"(?<=[gq])u(?=[aeiouy\u00e9\u00e8\u00ea\u00ee])")
RE_HIATUS_EN = re.compile(r"(?<![ctsgx])i[ao]|[^qg]ua|iu|ii")
TREMAS = "ëïü"
RE_E_MUET = re.compile("[^aeiouyéèê]es?$")
RE_HIATUS_E = re.compile("[éè](?=[aeiouyéèê])")
RE_HIATUS_AOU = re.compile("(?<=[aou])[éè]")
RE_Y_INTERVOCALIQUE = re.compile(r"(?<=[aeiou])y(?=[aeiou])")


class ErreurEntree(Exception):
    """Entrée illisible ou invalide : porte le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


# -- nettoyage et découpage -------------------------------------------------

def nettoyer(texte: str) -> str:
    """Remplace par des espaces ce qui n'est pas de la prose, en gardant les positions."""
    blanc = lambda m: re.sub(r"[^\n]", " ", m.group())  # noqa: E731
    for motif in (RE_CODE_BLOC, RE_TABLEAU, RE_URL, RE_BALISE, RE_CODE_LIGNE):
        texte = motif.sub(blanc, texte)
    return RE_LIEN.sub(lambda m: m.group(1).ljust(len(m.group())), texte)


def unites(texte: str) -> list[tuple[int, str]]:
    """Blocs de lecture : paragraphes, titres et puces (une ligne coupée au fil du
    texte est recollée à la suivante)."""
    blocs: list[tuple[int, str]] = []
    debut, morceaux = 0, []
    position = 0
    for ligne in texte.splitlines(keepends=True):
        nouvelle = not ligne.strip() or RE_DEBUT_UNITE.match(ligne)
        titre_avant = morceaux and morceaux[-1].lstrip().startswith("#")
        if (nouvelle or titre_avant) and morceaux:
            blocs.append((debut, "".join(morceaux)))
            morceaux = []
        if ligne.strip():
            if not morceaux:
                debut = position
            morceaux.append(ligne)
        position += len(ligne)
    if morceaux:
        blocs.append((debut, "".join(morceaux)))
    return blocs


def phrases_de(texte: str) -> list[tuple[int, str]]:
    """Phrases (position, texte) : coupe après . ! ? … sauf abréviation ou initiale."""
    phrases = []
    for debut_bloc, bloc in unites(texte):
        debut = 0
        for fin in RE_FIN_PHRASE.finditer(bloc):
            if est_abreviation(bloc, fin.start()):
                continue
            phrases.append((debut_bloc + debut, bloc[debut:fin.end()]))
            debut = fin.end()
        phrases.append((debut_bloc + debut, bloc[debut:]))
    return [(p + len(t) - len(t.lstrip()), t.strip()) for p, t in phrases if RE_MOT.search(t)]


def est_abreviation(bloc: str, point: int) -> bool:
    avant = re.search(r"([^\W\d_][\w.]*)$", bloc[:point])
    if avant is None or bloc[point] != ".":
        return False
    mot = avant.group(1)
    return mot.lower() in ABREVIATIONS or (len(mot) == 1 and mot.isupper())


# -- syllabes ------------------------------------------------------------------

def syllabes_en(mot: str) -> int:
    """Groupes de voyelles ; -e, -es, -ed muets ; hiatus ia, io, ua, iu ; sigle sans
    voyelle (html, xml) épelé lettre à lettre."""
    m = re.sub(r"[^a-z]", "", mot.lower())
    if len(m) <= 2:
        return 1
    if not re.search(r"[aeiouy]", m):
        return len(m)
    compte = len(RE_VOYELLES_EN.findall(m))
    if re.search(r"[^aeiouy]e$", m) and not re.search(r"[^aeiouy]le$", m):
        compte -= 1
    if re.search(r"[^aeiouytd]ed$|[aeiouy][^aeiouysxzhgc]es$", m) or m.endswith("ely"):
        compte -= 1
    compte += len(RE_HIATUS_EN.findall(m))
    return max(compte, 1)


def syllabes_fr(mot: str) -> int:
    """Syllabes orales : groupes de voyelles, plus les hiatus, moins l'e muet final."""
    m = RE_ELISION.sub("", mot.lower().replace(chr(0x2019), "'")).replace("'", "")
    m = RE_U_MUET.sub("", m)
    groupes = list(RE_VOYELLES_FR.finditer(m))
    if not groupes:
        return 1
    compte = len(groupes) + sum(hiatus_fr(g.group(), re.fullmatch(r"s?", m[g.end():]) is not None)
                                for g in groupes)
    if len(groupes) > 1 and RE_E_MUET.search(m):
        compte -= 1
    return max(compte, 1)


def hiatus_fr(groupe: str, final: bool) -> int:
    """Syllabes en plus dans un groupe de voyelles : voyelle à tréma, é ou è suivi
    d'une voyelle (sauf e muet en fin de mot), a, o ou u suivi de é ou è, y entre
    deux voyelles."""
    extra = sum(1 for c in groupe[1:] if c in TREMAS)
    suite = groupe[:-1] if final and groupe.endswith("e") else groupe
    extra += len(RE_HIATUS_E.findall(suite))
    extra += len(RE_HIATUS_AOU.findall(groupe))
    extra += len(RE_Y_INTERVOCALIQUE.findall(groupe))
    return extra


# -- mesure -------------------------------------------------------------------

def detecter_langue(mots: list[str]) -> tuple[str, bool]:
    minuscules = [m.lower() for m in mots[:20000]]
    fr = sum(m in MOTS_FR for m in minuscules)
    en = sum(m in MOTS_EN for m in minuscules)
    return ("fr" if fr >= en else "en"), abs(fr - en) >= max(3, (fr + en) // 5)


def statistiques(phrases: list[tuple[int, str]], langue: str) -> dict[str, Any]:
    compter = syllabes_fr if langue == "fr" else syllabes_en
    mots = [m for _, p in phrases for m in RE_MOT.findall(p)]
    syllabes = sum(compter(m) for m in mots)
    longs = sum(1 for m in mots if len(m.replace("-", "").replace("'", "")) > MOT_LONG)
    nb_phrases, nb_mots = len(phrases), max(len(mots), 1)
    return {"phrases": nb_phrases, "mots": len(mots), "syllabes": syllabes, "mots_longs": longs,
            "mots_par_phrase": round(len(mots) / nb_phrases, 3),
            "syllabes_par_mot": round(syllabes / nb_mots, 4),
            "part_mots_longs": round(longs / nb_mots, 4)}


def formules(s: dict[str, Any]) -> dict[str, float]:
    mpp, spm = s["mots_par_phrase"], s["syllabes_par_mot"]
    return {"flesch": round(206.835 - 1.015 * mpp - 84.6 * spm, 2),
            "kandel_moles": round(207.0 - 1.015 * mpp - 73.6 * spm, 2),
            "flesch_kincaid": round(0.39 * mpp + 11.8 * spm - 15.59, 2),
            "lix": round(mpp + 100.0 * s["part_mots_longs"], 2)}


def echelon(valeur: float, echelle: tuple[tuple[float, str], ...], croissante: bool) -> str:
    for borne, nom in echelle:
        if (valeur < borne) if croissante else (valeur >= borne):
            return nom
    return echelle[-1][1]


def phrases_longues(phrases: list[tuple[int, str]], texte: str, maximum: int) -> list[dict[str, Any]]:
    longues = []
    for position, phrase in phrases:
        nombre = len(RE_MOT.findall(phrase))
        if nombre > maximum:
            longues.append({"position": position, "ligne": texte.count("\n", 0, position) + 1,
                            "mots": nombre, "extrait": " ".join(phrase.split())[:160]})
    return longues


# -- moteur tiers -------------------------------------------------------------

def mesurer_tiers(texte: str, langue: str) -> dict[str, float]:
    """textstat : en français il compte les syllabes avec pyphen (hors ligne)."""
    _TEXTSTAT.set_lang("fr" if langue == "fr" else "en_US")
    return {"flesch" if langue == "en" else "kandel_moles": round(_TEXTSTAT.flesch_reading_ease(texte), 2),
            "flesch_kincaid": round(_TEXTSTAT.flesch_kincaid_grade(texte), 2),
            "lix": round(_TEXTSTAT.lix(texte), 2),
            "mots_par_phrase": round(_TEXTSTAT.words_per_sentence(texte), 3),
            "syllabes": _TEXTSTAT.syllable_count(texte)}


def tiers_utilisable(langue: str, reseau: bool) -> bool:
    """En anglais, textstat télécharge le dictionnaire cmudict (réseau) s'il manque."""
    return _TEXTSTAT is not None and (langue == "fr" or reseau)


# -- orchestration --------------------------------------------------------------

def analyser(texte: str, args: argparse.Namespace) -> dict[str, Any]:
    propre = nettoyer(texte)
    phrases = phrases_de(propre)
    mots = [m for _, p in phrases for m in RE_MOT.findall(p)]
    langue, nette = (args.langue, True) if args.langue != "auto" else detecter_langue(mots)
    resultat: dict[str, Any] = {"langue": langue, "langue_imposee": args.langue != "auto",
                                "langue_nette": nette, "phrases_detail": phrases}
    if not phrases:
        return resultat
    stats = statistiques(phrases, langue)
    scores = formules(stats)
    moteur = choisir_moteur(args, langue)
    sources = dict.fromkeys(scores, MOTEUR_STDLIB)
    if moteur == MOTEUR_TIERS:
        tiers = {k: v for k, v in mesurer_tiers(propre, langue).items() if k in scores}
        scores.update(tiers)
        sources.update(dict.fromkeys(tiers, MOTEUR_TIERS))
    principal = "kandel_moles" if langue == "fr" else "flesch"
    resultat.update({"moteur": moteur, "statistiques": stats, "scores": scores, "source_des_scores": sources,
                     "echantillon_court": stats["mots"] < MOTS_FIABLES, "formule_principale": principal,
                     "score_principal": scores[principal],
                     "niveau": echelon(scores[principal], ECHELLE_FLESCH, False),
                     "niveau_lix": echelon(scores["lix"], ECHELLE_LIX, True),
                     "phrases_trop_longues": phrases_longues(phrases, texte, args.phrase_max)})
    if args.comparer and tiers_utilisable(langue, args.autoriser_reseau) and _TEXTSTAT is not None:
        resultat["comparaison"] = comparer(propre, langue, scores if moteur == MOTEUR_STDLIB else formules(stats), stats)
    return resultat


def comparer(texte: str, langue: str, scores: dict[str, float], stats: dict[str, Any]) -> dict[str, Any]:
    tiers = mesurer_tiers(texte, langue)
    communs = {k: {"stdlib": scores.get(k, stats.get(k)), "textstat": v,
                   "ecart": round(v - scores.get(k, stats.get(k, 0)), 2)}
               for k, v in tiers.items()}
    return {"moteur": MOTEUR_TIERS, "formules": communs}


def choisir_moteur(args: argparse.Namespace, langue: str) -> str:
    if args.moteur == MOTEUR_STDLIB:
        return MOTEUR_STDLIB
    if tiers_utilisable(langue, args.autoriser_reseau):
        return MOTEUR_TIERS
    if _TEXTSTAT is not None:
        print("textstat en anglais télécharge cmudict par le réseau : non utilisé sans "
              "--autoriser-reseau, moteur stdlib", file=sys.stderr)
    else:
        print("textstat absent : mode dégradé, formules et syllabes heuristiques stdlib", file=sys.stderr)
    return MOTEUR_STDLIB


def verdict(resultat: dict[str, Any], args: argparse.Namespace) -> tuple[str, list[str]]:
    raisons = []
    if resultat["score_principal"] < args.flesch_min:
        raisons.append(f"{resultat['formule_principale']} {resultat['score_principal']} < {args.flesch_min}")
    if resultat["scores"]["lix"] > args.lix_max:
        raisons.append(f"{FORMULE_LIX} {resultat['scores']['lix']} > {args.lix_max}")
    return ("difficile" if raisons else "lisible"), raisons


# -- entrées -------------------------------------------------------------------

def decoder(octets: bytes, nom: str) -> str:
    if b"\x00" in octets[:65536]:
        raise ErreurEntree(f"{nom} : fichier binaire (octet NUL), pas du texte")
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError:
        print(f"{nom} : pas de l'{ENCODAGE_ATTENDU} valide, relu en cp1252", file=sys.stderr)
        return octets.decode("cp1252", "replace")


def lire_borne(flux: Any, octets_max: int, nom: str) -> bytes:
    octets = flux.read(octets_max + 1)
    if len(octets) > octets_max:
        raise ErreurEntree(f"{nom} dépasse {octets_max} octets (--max-octets)")
    return octets


def collecter_entree(args: argparse.Namespace, base: Path) -> tuple[str, str]:
    if args.texte is not None:
        return "<texte>", decoder(os.fsencode(args.texte), "<texte>")
    if args.chemin is None:
        raise ErreurEntree("rien à lire : donner un CHEMIN, '-' (entrée standard) ou --texte")
    if args.chemin == "-":
        return "<stdin>", decoder(lire_borne(sys.stdin.buffer, args.max_octets, "<stdin>"), "<stdin>")
    chemin = Path(args.chemin)
    chemin = chemin if chemin.is_absolute() else base / chemin
    if not chemin.exists():
        raise ErreurEntree(f"{chemin} : chemin inexistant")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} : c'est un dossier ; donner un fichier texte")
    with chemin.open("rb") as flux:
        return str(chemin), decoder(lire_borne(flux, args.max_octets, str(chemin)), str(chemin))


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


def construire_rapport(nom: str, texte: str, resultat: dict[str, Any], args: argparse.Namespace,
                       base: Path) -> dict[str, Any]:
    phrases = resultat.pop("phrases_detail")
    noms = [f"phrase {i} (l.{texte.count(chr(10), 0, p) + 1})" for i, (p, _) in enumerate(phrases[:EXAMINES_MAX], 1)]
    rapport = {"denominateur": len(phrases), "examines": noms, "examines_tronques": len(phrases) > EXAMINES_MAX,
               "source": nom, "racine": str(base), **resultat,
               "seuils": {"flesch_min": args.flesch_min, "lix_max": args.lix_max, "phrase_max": args.phrase_max}}
    if phrases:
        rapport["verdict"], rapport["raisons"] = verdict(resultat, args)
        rapport["nb_phrases_trop_longues"] = len(rapport["phrases_trop_longues"])
        rapport["phrases_trop_longues"] = rapport["phrases_trop_longues"][:PHRASES_LISTEES_MAX]
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    return rapport


def afficher_humain(r: dict[str, Any]) -> None:
    s, sc = r["statistiques"], r["scores"]
    sys.stdout.write(f"{r['source']} : langue {r['langue']}{'' if r['langue_nette'] else ' (incertaine)'}, "
                     f"moteur {r['moteur']} — {s['phrases']} phrases, {s['mots']} mots, "
                     f"{s['mots_par_phrase']} mots/phrase, {s['syllabes_par_mot']} syllabes/mot\n")
    sys.stdout.write(f"  Flesch {sc['flesch']}  Kandel-Moles {sc['kandel_moles']}  Flesch-Kincaid "
                     f"{sc['flesch_kincaid']}  {FORMULE_LIX} {sc['lix']}\n")
    sys.stdout.write(f"  {r['formule_principale']} = {r['score_principal']} : {r['niveau']} ; "
                     f"{FORMULE_LIX} : {r['niveau_lix']} -> {r['verdict'].upper()} {r['raisons']}\n")
    if r["echantillon_court"]:
        sys.stdout.write(f"  échantillon court (moins de {MOTS_FIABLES} mots) : score instable\n")
    sys.stdout.write(f"  {r['nb_phrases_trop_longues']} phrase(s) de plus de {r['seuils']['phrase_max']} mots\n")
    for p in r["phrases_trop_longues"][:20]:
        sys.stdout.write(f"    l.{p['ligne']:<5} {p['mots']:>3} mots  {p['extrait'][:100]}\n")
    if "comparaison" in r:
        for nom, c in r["comparaison"]["formules"].items():
            sys.stdout.write(f"  textstat {nom} : {c['textstat']} (stdlib {c['stdlib']}, écart {c['ecart']})\n")


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Mesure la lisibilité d'un texte français ou anglais : Flesch, Flesch adapté au "
                    "français (Kandel et Moles), Flesch-Kincaid, LIX, longueur des phrases, et liste "
                    "des phrases trop longues.",
        epilog="Exemple : python mesurer_lisibilite.py notice.md --phrase-max 20\n"
               "Codes : 0 lisible selon les seuils ; 1 difficile ; 2 usage ; 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemin", nargs="?", help="fichier texte ou Markdown, ou '-' pour l'entrée standard")
    parseur.add_argument("--texte", help="texte donné en ligne (au lieu d'un chemin)")
    parseur.add_argument("--langue", choices=("auto", "fr", "en"), default="auto",
                         help="langue des syllabes et de la formule principale (auto : détectée)")
    parseur.add_argument("--flesch-min", type=float, default=FLESCH_MIN_DEFAUT,
                         help="score principal minimal pour « lisible » (défaut 50 = assez difficile)")
    parseur.add_argument("--lix-max", type=float, default=LIX_MAX_DEFAUT, help="indice LIX maximal (défaut 50)")
    parseur.add_argument("--phrase-max", type=int, default=PHRASE_MAX_DEFAUT, help="mots au-delà desquels "
                         "une phrase est listée comme trop longue (défaut 25)")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_TIERS), default="auto",
                         help="auto : textstat s'il est installé (en anglais seulement avec --autoriser-reseau)")
    parseur.add_argument("--comparer", action="store_true", help="écarts textstat / stdlib sur les formules communes")
    parseur.add_argument("--autoriser-reseau", action="store_true",
                         help="laisse textstat télécharger cmudict pour l'anglais (désactivé par défaut)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue")
    parseur.add_argument("--racine", type=Path,
                         help=f"base des chemins relatifs (défaut : dossier courant ; outil : {RACINE.name})")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    base = (args.racine if args.racine is not None else Path.cwd()).resolve()
    if args.phrase_max < 1:
        print("erreur : --phrase-max doit valoir au moins 1", file=sys.stderr)
        return CODE_USAGE
    if args.moteur == MOTEUR_TIERS and _TEXTSTAT is None:
        print("erreur : --moteur textstat demandé mais le paquet textstat est absent", file=sys.stderr)
        return CODE_USAGE
    try:
        nom, texte = collecter_entree(args, base)
    except ErreurEntree as erreur:
        print(f"erreur : {erreur}", file=sys.stderr)
        return erreur.code
    except OSError as erreur:
        print(f"erreur de lecture : {erreur}", file=sys.stderr)
        return CODE_USAGE
    rapport = construire_rapport(nom, texte, analyser(texte, args), args, base)
    if not rapport["denominateur"]:
        if args.json:
            sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
        print("dénominateur nul : rien à examiner (aucune phrase avec des mots)", file=sys.stderr)
        return CODE_VIDE
    if args.json:
        sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
    else:
        afficher_humain(rapport)
    return CODE_RIEN if rapport["verdict"] == "lisible" else CODE_DEFAUT


if __name__ == "__main__":
    raise SystemExit(main())
