r"""Dire si des sous-titres SRT ou WebVTT sont bien formés et lisibles à l'écran, réplique par réplique.

Mesuré dans cette session : pysrt 1.1.2 (pysrt.from_string, réglage par défaut) lit un SRT de
quatre blocs dont un horodatage de fin antérieur au début (1,500 s -> 1,200 s), un bloc de texte
sans numéro ni horodatage et un point à la place de la virgule des millisecondes, et rend
3 répliques sans aucune erreur : le bloc orphelin disparaît, la durée négative passe.

QUESTION
    Ces sous-titres sont-ils bien formés (numérotation, horodatages, ordre, chevauchements,
    balises) et lisibles à l'écran (durée, vitesse de lecture, longueur et nombre de lignes) ?
MESURE
    Lecture ligne à ligne, sans bibliothèque : blocs séparés par une ligne vide, ligne
    d'horodatage reconnue à sa flèche « --> ». Horodatages confrontés à la forme stricte de
    leur format (SRT hh:mm:ss,mmm ; WebVTT [hh:]mm:ss.mmm selon la spécification du W3C),
    puis relus en mode tolérant pour mesurer quand même. Par réplique : numéro (SRT) ou
    identifiant (WebVTT), durée, ordre et chevauchement avec les précédentes, texte visible
    (balises retirées), caractères par seconde (espaces compris, sauts de ligne exclus),
    longueur et nombre de lignes, balises inconnues ou non fermées. Seuils paramétrables.
    Décalage temporel facultatif, écrit seulement dans --sortie. Comparaison facultative du
    découpage avec pysrt (SRT) et webvtt-py (WebVTT) quand ils sont installés.
HYPOTHÈSES
    Un caractère vaut une colonne à l'écran. Les seuils par défaut (20 car./s, 42 car. par
    ligne, 2 lignes, 1 s à 7 s) sont des choix de l'outil, pas une norme vérifiée ici : les
    régler sur la charte visée. Un SRT non utf-8 est relu en cp1252 (avertissement).
LIMITES
    Pas de lecture de la vidéo : ni plans, ni synchronisation réelle avec la parole. Les
    réglages de position WebVTT (line, position, region...) sont nommés, pas interprétés ;
    STYLE et REGION ne sont pas analysés. Une ligne de texte faite seulement de chiffres
    juste avant une ligne d'horodatage, sans ligne vide, est prise pour un numéro. Les
    écritures à pleine chasse (chinois, japonais) comptent un caractère par signe.
CONTRE-EXEMPLES
    Constaté : un chevauchement voulu en WebVTT (deux locuteurs placés à deux endroits de
    l'écran) est signalé, comme avertissement seulement ; en SRT il compte comme défaut alors
    que certains lecteurs l'affichent correctement. Une réplique d'une seule interjection
    (« Ah ! », 0,4 s) est signalée trop courte alors qu'elle est lisible.
INVOCATION
    {outil} --texte '1\n00:00:01,000 --> 00:00:03,500\nBonjour à tous.\n\n2\n00:00:04,000 --> 00:00:06,000\nOn commence ?' --json
DOMAINE
    Fichiers .srt et .vtt (ou texte en ligne) avant livraison, traduction ou recalage :
    contrôle de forme et de lisibilité, pas de qualité de traduction.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pysrt
except ImportError:
    pysrt = None

try:
    import webvtt
except ImportError:
    webvtt = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")
NOM_OUTIL = "lire_srt_vtt"
ENTETE_VTT = "WEBVTT"
BLOC_NOTE = "NOTE"
BLOC_STYLE = "STYLE"
BLOC_REGION = "REGION"
EXTENSIONS = {".srt": "srt", ".vtt": "vtt"}
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"})
PLAFOND_LISTE = 200
FLECHE = "-->"

SRT_STRICT = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
VTT_STRICT = re.compile(r"^(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})$")
TOLERANT = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?$")
LIGNE_TEMPS = re.compile(r"^\s*(\S+)[ \t]+-->[ \t]+(\S+)(.*)$")
BALISE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_-]*)?([^<>]*)>")
TEMPS_INTERNE = re.compile(r"<(\d{2,}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})>")
BALISE_ASS = re.compile(r"\{\\[^{}]*\}")
ESPERLUETTE_NUE = re.compile(r"&(?!(?:[A-Za-z][A-Za-z0-9]*|#\d+|#[xX][0-9A-Fa-f]+);)")
DECALAGE = re.compile(r"^([+-]?)(?:(\d+(?:\.\d+)?)s|(\d+)|(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3}))$")

BALISES_SRT = frozenset({"i", "b", "u", "s", "font"})
BALISES_VTT = frozenset({"c", "i", "b", "u", "ruby", "rt", "v", "lang"})
REGLAGES_VTT = frozenset({"vertical", "line", "position", "size", "align", "region"})
GENRES_DEFAUT_LISIBILITE = frozenset({"duree_courte", "duree_longue", "vitesse_lecture",
                                      "ligne_longue", "trop_de_lignes"})


class EntreeInvalide(Exception):
    """Entrée inutilisable (chemin absent, binaire, pas des sous-titres) : code 2."""


@dataclass(frozen=True)
class Seuils:
    """Seuils de lisibilité, tous réglables en ligne de commande."""

    cps_max: float
    car_par_ligne: int
    lignes_max: int
    duree_min: int
    duree_max: int


@dataclass
class Replique:
    """Une réplique lue : position dans le fichier, horodatages en millisecondes, texte brut."""

    rang: int
    ligne: int
    numero: str | None
    debut: int
    fin: int
    texte: list[str]


@dataclass
class Constat:
    """Défauts et avertissements d'un fichier."""

    defauts: list[dict[str, Any]] = field(default_factory=list)
    avertissements: list[dict[str, Any]] = field(default_factory=list)

    def noter(self, defaut: bool, genre: str, ligne: int, detail: str) -> None:
        cible = self.defauts if defaut else self.avertissements
        cible.append({"type": genre, "ligne": ligne, "detail": detail})


@dataclass
class Lecture:
    """Résultat brut du découpage d'un texte de sous-titres."""

    format: str
    repliques: list[Replique]
    constat: Constat
    lignes_temps: dict[int, tuple[str, str]]


# --------------------------------------------------------------------------- horodatages

def vers_ms(h: str | None, m: str, s: str, frac: str | None) -> int:
    """Convertit des composantes en millisecondes (fraction complétée à droite : ',5' = 500)."""
    return ((int(h or 0) * 60 + int(m)) * 60 + int(s)) * 1000 + int((frac or "0").ljust(3, "0")[:3])


def lire_horodatage(jeton: str, fmt: str) -> tuple[int | None, str | None]:
    """Rend (millisecondes, motif de non-conformité) ; (None, motif) si illisible."""
    strict = (SRT_STRICT if fmt == "srt" else VTT_STRICT).match(jeton)
    if strict:
        h, m, s, frac = strict.groups()
        if int(m) > 59 or int(s) > 59:
            return vers_ms(h, m, s, frac), "minutes ou secondes au-delà de 59"
        return vers_ms(h, m, s, frac), None
    tolerant = TOLERANT.match(jeton)
    if not tolerant:
        return None, f"horodatage illisible « {jeton} »"
    attendu = "hh:mm:ss,mmm" if fmt == "srt" else "[hh:]mm:ss.mmm"
    return vers_ms(*tolerant.groups()), f"« {jeton} » n'a pas la forme {attendu}"


def ecrire_horodatage(ms: int, fmt: str, avec_heures: bool) -> str:
    """Écrit un horodatage dans la forme stricte du format."""
    h, reste = divmod(ms, 3_600_000)
    m, reste = divmod(reste, 60_000)
    s, mil = divmod(reste, 1000)
    if fmt == "srt":
        return f"{h:02d}:{m:02d}:{s:02d},{mil:03d}"
    if avec_heures or h:
        return f"{h:02d}:{m:02d}:{s:02d}.{mil:03d}"
    return f"{m:02d}:{s:02d}.{mil:03d}"


def lire_decalage(texte: str) -> int:
    """'-1500', '+2.5s', '-00:00:01,500' -> millisecondes signées ; lève EntreeInvalide."""
    trouve = DECALAGE.match(texte.strip())
    if not trouve:
        raise EntreeInvalide(f"décalage illisible « {texte} » (ex. -1500, +2.5s, -00:00:01,500)")
    signe, secondes, millis, h, m, s, frac = trouve.groups()
    if secondes is not None:
        valeur = round(float(secondes) * 1000)
    elif millis is not None:
        valeur = int(millis)
    else:
        valeur = vers_ms(h, m, s, frac)
    return -valeur if signe == "-" else valeur


# --------------------------------------------------------------------------- découpage

def blocs_de(lignes: list[str], depart: int) -> list[tuple[int, list[str]]]:
    """Regroupe les lignes non vides consécutives ; rend (numéro de 1re ligne, lignes)."""
    blocs: list[tuple[int, list[str]]] = []
    courant: list[str] = []
    debut = depart
    for i in range(depart, len(lignes)):
        if lignes[i].strip():
            if not courant:
                debut = i
            courant.append(lignes[i])
        elif courant:
            blocs.append((debut + 1, courant))
            courant = []
    if courant:
        blocs.append((debut + 1, courant))
    return blocs


def fleches_du_bloc(bloc: list[str]) -> list[int]:
    """Indices des lignes d'horodatage d'un bloc."""
    return [i for i, ligne in enumerate(bloc) if FLECHE in ligne]


def decouper_bloc(premiere: int, bloc: list[str], fmt: str,
                  constat: Constat) -> list[tuple[int, str | None, str, list[str]]]:
    """Découpe un bloc en répliques (ligne, numéro, ligne d'horodatage, texte)."""
    fleches = fleches_du_bloc(bloc)
    if not fleches:
        constat.noter(True, "bloc_sans_horodatage", premiere,
                      f"texte sans horodatage : « {bloc[0].strip()[:60]} »")
        return []
    sorties: list[tuple[int, str | None, str, list[str]]] = []
    for rang, k in enumerate(fleches):
        fin_texte = fleches[rang + 1] if rang + 1 < len(fleches) else len(bloc)
        if rang + 1 < len(fleches):
            constat.noter(True, "ligne_vide_manquante", premiere + fleches[rang + 1],
                          "deux répliques sans ligne vide entre elles")
            if fin_texte - 1 > k and bloc[fin_texte - 1].strip().isdigit():
                fin_texte -= 1
        debut_preambule = 0 if rang == 0 else fleches[rang - 1] + 1
        preambule = [l for l in bloc[debut_preambule:k] if l.strip()]
        if rang > 0:
            preambule = preambule[-1:] if preambule and preambule[-1].strip().isdigit() else []
        numero = preambule[-1].strip() if preambule else None
        if rang == 0 and len(preambule) > 1:
            constat.noter(True, "lignes_avant_horodatage", premiere,
                          f"{len(preambule)} lignes avant l'horodatage")
        sorties.append((premiere + k, numero, bloc[k], bloc[k + 1:fin_texte]))
    return sorties


def lire_ligne_temps(ligne: str, num: int, fmt: str,
                     constat: Constat) -> tuple[int, int, tuple[str, str]] | None:
    """Analyse une ligne d'horodatage ; None si inutilisable (défaut noté)."""
    trouve = LIGNE_TEMPS.match(ligne)
    if not trouve:
        constat.noter(True, "horodatage_illisible", num, f"ligne « {ligne.strip()[:80]} »")
        return None
    brut_debut, brut_fin, reste = trouve.groups()
    debut, motif_d = lire_horodatage(brut_debut, fmt)
    fin, motif_f = lire_horodatage(brut_fin, fmt)
    for motif in (motif_d, motif_f):
        if motif:
            constat.noter(True, "horodatage_non_conforme", num, motif)
    if debut is None or fin is None:
        return None
    controler_reglages(reste, num, fmt, constat)
    return debut, fin, (brut_debut, brut_fin)


def controler_reglages(reste: str, num: int, fmt: str, constat: Constat) -> None:
    """Après l'horodatage : réglages WebVTT reconnus, rien en SRT."""
    if not reste.strip():
        return
    if fmt == "srt":
        constat.noter(False, "texte_apres_horodatage", num, f"« {reste.strip()[:60]} »")
        return
    for reglage in reste.split():
        nom = reglage.split(":", 1)[0]
        if ":" not in reglage or nom not in REGLAGES_VTT:
            constat.noter(False, "reglage_inconnu", num, f"« {reglage} »")


def lire_entete_vtt(lignes: list[str], constat: Constat) -> int:
    """Contrôle la ligne WEBVTT ; rend l'indice de la première ligne après l'en-tête."""
    premiere = lignes[0] if lignes else ""
    if not (premiere == ENTETE_VTT or premiere.startswith((ENTETE_VTT + " ", ENTETE_VTT + "\t"))):
        constat.noter(True, "entete_absent", 1, "la première ligne doit être WEBVTT")
        return 0
    i = 1
    while i < len(lignes) and lignes[i].strip():
        if FLECHE in lignes[i]:
            constat.noter(True, "ligne_vide_manquante", i + 1, "en-tête collé à une réplique")
            return i
        i += 1
    return i


def bloc_special_vtt(bloc: list[str]) -> str | None:
    """NOTE, STYLE ou REGION si le bloc en est un."""
    mot = bloc[0].strip().split(maxsplit=1)[0] if bloc[0].strip() else ""
    if mot in (BLOC_NOTE, BLOC_STYLE, BLOC_REGION) and FLECHE not in bloc[0]:
        return mot
    return None


def segmenter(texte: str, fmt: str) -> Lecture:
    """Découpe le texte en répliques et note les défauts de structure."""
    lignes = texte.split("\n")
    constat = Constat()
    depart = lire_entete_vtt(lignes, constat) if fmt == "vtt" else 0
    repliques: list[Replique] = []
    temps: dict[int, tuple[str, str]] = {}
    for premiere, bloc in blocs_de(lignes, depart):
        special = bloc_special_vtt(bloc) if fmt == "vtt" else None
        if special:
            if special != BLOC_NOTE and repliques:
                constat.noter(True, "bloc_apres_replique", premiere,
                              f"bloc {special} après la première réplique")
            continue
        for num, numero, ligne, corps in decouper_bloc(premiere, bloc, fmt, constat):
            lu = lire_ligne_temps(ligne, num, fmt, constat)
            if lu is None:
                continue
            temps[num] = lu[2]
            repliques.append(Replique(len(repliques) + 1, num, numero, lu[0], lu[1], corps))
    return Lecture(fmt, repliques, constat, temps)


# --------------------------------------------------------------------------- contrôles

def texte_visible(ligne: str, fmt: str) -> str:
    """Retire balises (et entités en WebVTT) pour obtenir ce que voit le spectateur."""
    sans = BALISE_ASS.sub("", BALISE.sub("", ligne))
    return html.unescape(sans) if fmt == "vtt" else sans


def controler_numeros(repliques: list[Replique], fmt: str, constat: Constat) -> None:
    """SRT : numéros présents, entiers, consécutifs. WebVTT : identifiants uniques."""
    if fmt == "vtt":
        vus: dict[str, int] = {}
        for r in repliques:
            if r.numero is not None and r.numero in vus:
                constat.noter(True, "identifiant_duplique", r.ligne,
                              f"« {r.numero} » déjà utilisé ligne {vus[r.numero]}")
            elif r.numero is not None:
                vus[r.numero] = r.ligne
        return
    precedent: int | None = None
    for r in repliques:
        valeur = int(r.numero) if r.numero is not None and r.numero.isdigit() else None
        if r.numero is None:
            constat.noter(True, "numero_absent", r.ligne, f"réplique {r.rang} sans numéro")
        elif valeur is None:
            constat.noter(True, "numero_invalide", r.ligne, f"« {r.numero[:40]} »")
        elif precedent is not None and valeur != precedent + 1:
            constat.noter(True, "numerotation", r.ligne, f"attendu {precedent + 1}, trouvé {valeur}")
        elif r.rang == 1 and valeur != 1:
            constat.noter(False, "numerotation_initiale", r.ligne, f"commence à {valeur}, pas à 1")
        precedent = valeur


def controler_temps(repliques: list[Replique], fmt: str, constat: Constat) -> None:
    """Durées positives, ordre des débuts, chevauchements."""
    fin_max = -1
    debut_prec = -1
    for r in repliques:
        if r.fin <= r.debut:
            constat.noter(True, "duree_nulle_ou_negative", r.ligne,
                          f"fin {r.fin} ms <= début {r.debut} ms")
        if r.debut < debut_prec:
            constat.noter(True, "ordre", r.ligne, f"commence avant la réplique précédente ({debut_prec} ms)")
        elif r.debut < fin_max:
            constat.noter(fmt == "srt", "chevauchement", r.ligne,
                          f"commence à {r.debut} ms, la précédente finit à {fin_max} ms")
        debut_prec = max(debut_prec, r.debut)
        fin_max = max(fin_max, r.fin)


def controler_balises(r: Replique, fmt: str, constat: Constat) -> None:
    """Balises connues du format, équilibrées ; esperluettes échappées en WebVTT."""
    connues = BALISES_SRT if fmt == "srt" else BALISES_VTT
    ouvertes: list[str] = []
    for ligne in r.texte:
        if fmt == "srt" and BALISE_ASS.search(ligne):
            constat.noter(False, "balise_ass", r.ligne, "balise {\\...} affichée telle quelle par certains lecteurs")
        if fmt == "vtt" and ESPERLUETTE_NUE.search(BALISE.sub("", ligne)):
            constat.noter(False, "esperluette_non_echappee", r.ligne, "écrire &amp;")
        for ferme, nom, _reste in BALISE.findall(TEMPS_INTERNE.sub("", ligne)):
            nom = nom.lower()
            if nom not in connues:
                constat.noter(False, "balise_inconnue", r.ligne, f"<{ferme}{nom or '?'}>")
            elif not ferme:
                ouvertes.append(nom)
            elif nom in ouvertes:
                ouvertes.remove(nom)
            else:
                constat.noter(False, "fermeture_orpheline", r.ligne, f"</{nom}>")
    for nom in ouvertes:
        if not (fmt == "vtt" and nom in ("v", "lang")):
            constat.noter(False, "balise_non_fermee", r.ligne, f"<{nom}>")


def controler_temps_internes(r: Replique, constat: Constat) -> None:
    """WebVTT : un horodatage interne doit tomber strictement dans la réplique."""
    for ligne in r.texte:
        for jeton in TEMPS_INTERNE.findall(ligne):
            ms, _ = lire_horodatage(jeton, "vtt")
            if ms is not None and not r.debut < ms < r.fin:
                constat.noter(False, "horodatage_interne_hors_replique", r.ligne, jeton)


def mesurer_replique(r: Replique, fmt: str, seuils: Seuils, constat: Constat) -> dict[str, Any]:
    """Texte visible, durée, vitesse ; note les défauts de lisibilité."""
    visibles = [unicodedata.normalize("NFC", texte_visible(l, fmt)).strip() for l in r.texte]
    visibles = [v for v in visibles if v]
    caracteres = sum(len(v) for v in visibles)
    duree = r.fin - r.debut
    cps = round(caracteres / (duree / 1000), 1) if duree > 0 else None
    if not visibles:
        constat.noter(True, "texte_vide", r.ligne, f"réplique {r.rang} sans texte visible")
    if 0 < duree < seuils.duree_min:
        constat.noter(True, "duree_courte", r.ligne, f"{duree} ms < {seuils.duree_min} ms")
    if duree > seuils.duree_max:
        constat.noter(True, "duree_longue", r.ligne, f"{duree} ms > {seuils.duree_max} ms")
    if cps is not None and cps > seuils.cps_max:
        constat.noter(True, "vitesse_lecture", r.ligne, f"{cps} car./s > {seuils.cps_max}")
    for v in visibles:
        if len(v) > seuils.car_par_ligne:
            constat.noter(True, "ligne_longue", r.ligne, f"{len(v)} car. > {seuils.car_par_ligne} : « {v[:50]} »")
    if len(visibles) > seuils.lignes_max:
        constat.noter(True, "trop_de_lignes", r.ligne, f"{len(visibles)} lignes > {seuils.lignes_max}")
    return {"duree": duree, "cps": cps, "lignes": len(visibles),
            "car_max": max((len(v) for v in visibles), default=0)}


def statistiques(mesures: list[dict[str, Any]], repliques: list[Replique]) -> dict[str, Any]:
    """Résumé chiffré d'un fichier."""
    if not repliques:
        return {}
    cps = [m["cps"] for m in mesures if m["cps"] is not None]
    durees = [m["duree"] for m in mesures]
    return {
        "debut_ms": min(r.debut for r in repliques),
        "fin_ms": max(r.fin for r in repliques),
        "duree_min_ms": min(durees),
        "duree_max_ms": max(durees),
        "cps_max": max(cps, default=None),
        "cps_mediane": round(statistics.median(cps), 1) if cps else None,
        "lignes_max": max(m["lignes"] for m in mesures),
        "car_par_ligne_max": max(m["car_max"] for m in mesures),
    }


# --------------------------------------------------------------------------- lecture des entrées

def decoder(octets: bytes, fmt_suppose: str | None) -> tuple[str, str]:
    """Décode selon le BOM, sinon utf-8, sinon cp1252 ; lève EntreeInvalide si binaire."""
    for bom, nom in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32"),
                     (b"\x00\x00\xfe\xff", "utf-32"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if octets.startswith(bom):
            try:
                return octets.decode(nom), nom
            except UnicodeDecodeError as exc:
                raise EntreeInvalide(f"BOM {nom} mais contenu indécodable : {exc.reason}") from exc
    if b"\x00" in octets[:65536]:
        raise EntreeInvalide("contenu binaire (octets nuls)")
    try:
        return octets.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        if fmt_suppose == "vtt":
            raise EntreeInvalide("WebVTT non décodable en utf-8 (la spécification impose utf-8)") from None
        return octets.decode("cp1252", errors="replace"), "cp1252"


def deviner_format(texte: str, impose: str | None) -> str:
    """srt ou vtt selon l'en-tête ; lève EntreeInvalide si ce ne sont pas des sous-titres."""
    if impose:
        return impose
    debut = texte.lstrip("﻿ \t\n")
    if debut.startswith(ENTETE_VTT):
        return "vtt"
    if FLECHE in texte:
        return "srt"
    raise EntreeInvalide("ni en-tête WEBVTT ni ligne d'horodatage « --> » : pas des sous-titres")


def normaliser_fins(texte: str) -> str:
    """Fins de ligne unifiées en \\n ; BOM retiré."""
    return texte.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def collecter(chemins: list[str], base: Path, max_fichiers: int) -> tuple[list[Path], list[Path]]:
    """Rend (fichiers explicites, fichiers trouvés en dossier) ; lève EntreeInvalide."""
    explicites: list[Path] = []
    trouves: list[Path] = []
    for brut in chemins:
        chemin = Path(brut) if Path(brut).is_absolute() else base / brut
        if chemin.is_dir():
            trouves.extend(parcourir(chemin, max_fichiers - len(trouves)))
        elif chemin.is_file():
            explicites.append(chemin)
        else:
            raise EntreeInvalide(f"chemin introuvable : {brut}")
    return explicites, trouves


def parcourir(dossier: Path, reste: int) -> list[Path]:
    """Fichiers .srt / .vtt d'un dossier, triés, hors dossiers cachés et outillage."""
    trouves: list[Path] = []
    for chemin in sorted(dossier.rglob("*")):
        if len(trouves) >= reste:
            break
        relatif = chemin.relative_to(dossier).parts
        if any(p in DOSSIERS_IGNORES or p.startswith(".") for p in relatif[:-1]):
            continue
        if chemin.suffix.lower() in EXTENSIONS and chemin.is_file():
            trouves.append(chemin)
    return trouves


def lire_fichier(chemin: Path, impose: str | None, max_octets: int) -> tuple[str, str, str, bool]:
    """Lit un fichier borné ; rend (texte, format, encodage, fins de ligne CRLF)."""
    try:
        with chemin.open("rb") as flux:
            octets = flux.read(max_octets + 1)
    except OSError as exc:
        raise EntreeInvalide(f"illisible : {exc.strerror or exc}") from exc
    if len(octets) > max_octets:
        raise EntreeInvalide(f"plus de {max_octets} octets (--max-octets)")
    suppose = impose or EXTENSIONS.get(chemin.suffix.lower())
    texte, encodage = decoder(octets, suppose)
    return normaliser_fins(texte), deviner_format(texte, suppose), encodage, "\r\n" in texte


# --------------------------------------------------------------------------- comparaison

def version_de(nom: str) -> str:
    """Version installée d'une distribution, ou '?'."""
    from importlib import metadata
    try:
        return metadata.version(nom)
    except metadata.PackageNotFoundError:
        return "?"


def erreurs_bibliotheques() -> tuple[type[BaseException], ...]:
    """Exceptions attendues des bibliothèques de comparaison (et des erreurs de valeur)."""
    erreurs: list[type[BaseException]] = [ValueError, TypeError, IndexError, LookupError]
    if pysrt is not None:
        erreurs.append(pysrt.Error)
    if webvtt is not None:
        erreurs.extend([webvtt.errors.MalformedFileError, webvtt.errors.MalformedCaptionError])
    return tuple(erreurs)


def comparer_bibliotheque(texte: str, fmt: str, repliques: list[Replique]) -> dict[str, Any] | None:
    """Découpe le même texte avec pysrt ou webvtt-py et compare les horodatages."""
    nom = ""
    try:
        if fmt == "srt" and pysrt is not None:
            nom = f"pysrt {version_de('pysrt')}"
            autres = [(s.start.ordinal, s.end.ordinal) for s in pysrt.from_string(texte)]
        elif fmt == "vtt" and webvtt is not None:
            nom = f"webvtt-py {version_de('webvtt-py')}"
            autres = [(lire_horodatage(c.start, "vtt")[0], lire_horodatage(c.end, "vtt")[0])
                      for c in webvtt.from_string(texte)]
        else:
            return None
    except erreurs_bibliotheques() as exc:
        return {"bibliotheque": nom, "erreur": f"{type(exc).__name__} : {str(exc)[:200]}"}
    miens = [(r.debut, r.fin) for r in repliques]
    ecarts = [{"rang": i + 1, "stdlib": a, "bibliotheque": b}
              for i, (a, b) in enumerate(zip(miens, autres)) if a != b][:10]
    return {"bibliotheque": nom, "repliques": len(autres), "repliques_stdlib": len(miens),
            "accord": miens == autres, "ecarts": ecarts}


# --------------------------------------------------------------------------- décalage

def decaler_texte(texte: str, lecture: Lecture, decalage: int) -> tuple[str, int, int]:
    """Réécrit les horodatages des lignes de temps (et internes WebVTT) ; rend (texte, n, négatifs)."""
    lignes = texte.split("\n")
    negatifs = 0
    compte = 0

    def remplacer(brut: str) -> str:
        nonlocal negatifs, compte
        ms, _ = lire_horodatage(brut, lecture.format)
        if ms is None:
            return brut
        nouveau = ms + decalage
        negatifs += nouveau < 0
        compte += 1
        return ecrire_horodatage(max(nouveau, 0), lecture.format, brut.count(":") >= 2)

    for num, (brut_d, brut_f) in lecture.lignes_temps.items():
        ligne = lignes[num - 1]
        trouve = LIGNE_TEMPS.match(ligne)
        if trouve:
            reste = trouve.group(3)
            lignes[num - 1] = f"{remplacer(brut_d)} --> {remplacer(brut_f)}{reste}"
    if lecture.format == "vtt":
        lignes = [TEMPS_INTERNE.sub(lambda t: f"<{remplacer(t.group(1))}>", l)
                  if FLECHE not in l else l for l in lignes]
    return "\n".join(lignes), compte, negatifs


def ecrire_decale(texte: str, lecture: Lecture, decalage: int, sortie: Path,
                  ecraser: bool, crlf: bool) -> dict[str, Any]:
    """Écrit la copie décalée dans --sortie ; lève EntreeInvalide si impossible."""
    if sortie.exists() and not ecraser:
        raise EntreeInvalide(f"{sortie} existe déjà (--ecraser pour le remplacer)")
    nouveau, compte, negatifs = decaler_texte(texte, lecture, decalage)
    if negatifs:
        raise EntreeInvalide(f"le décalage rend {negatifs} horodatage(s) négatif(s) : rien écrit")
    if crlf:
        nouveau = nouveau.replace("\n", "\r\n")
    try:
        with sortie.open("w", encoding="utf-8", newline="") as flux:
            flux.write(nouveau)
    except OSError as exc:
        raise EntreeInvalide(f"écriture impossible dans {sortie} : {exc.strerror or exc}") from exc
    return {"decalage_ms": decalage, "sortie": str(sortie), "horodatages_decales": compte,
            "encodage_sortie": "utf-8"}


# --------------------------------------------------------------------------- analyse

def analyser(nom: str, texte: str, fmt: str, encodage: str, seuils: Seuils,
             comparer: bool) -> tuple[dict[str, Any], Lecture]:
    """Analyse complète d'un texte de sous-titres."""
    lecture = segmenter(texte, fmt)
    constat = lecture.constat
    if encodage == "cp1252":
        constat.noter(False, "encodage", 1, "pas en utf-8 : relu en cp1252")
    controler_numeros(lecture.repliques, fmt, constat)
    controler_temps(lecture.repliques, fmt, constat)
    mesures = []
    for r in lecture.repliques:
        controler_balises(r, fmt, constat)
        if fmt == "vtt":
            controler_temps_internes(r, constat)
        mesures.append(mesurer_replique(r, fmt, seuils, constat))
    if not lecture.repliques:
        constat.noter(True, "aucune_replique", 1, "aucune réplique lisible")
    constat.defauts.sort(key=lambda d: d["ligne"])
    constat.avertissements.sort(key=lambda d: d["ligne"])
    return {
        "chemin": nom,
        "format": fmt,
        "encodage": encodage,
        "verdict": verdict_de(constat),
        "nombre_repliques": len(lecture.repliques),
        "statistiques": statistiques(mesures, lecture.repliques),
        "comptes": compter(constat),
        "defauts": constat.defauts[:PLAFOND_LISTE],
        "avertissements": constat.avertissements[:PLAFOND_LISTE],
        "listes_tronquees": max(len(constat.defauts), len(constat.avertissements)) > PLAFOND_LISTE,
        "comparaison": comparer_bibliotheque(texte, fmt, lecture.repliques) if comparer else None,
    }, lecture


def verdict_de(constat: Constat) -> str:
    """forme, lisibilite ou conforme."""
    genres = {d["type"] for d in constat.defauts}
    if genres - GENRES_DEFAUT_LISIBILITE:
        return "defauts_de_forme"
    return "defauts_de_lisibilite" if genres else "conforme"


def compter(constat: Constat) -> dict[str, dict[str, int]]:
    """Nombre de constats par type."""
    sortie: dict[str, dict[str, int]] = {"defauts": {}, "avertissements": {}}
    for cle, liste in (("defauts", constat.defauts), ("avertissements", constat.avertissements)):
        for d in liste:
            sortie[cle][d["type"]] = sortie[cle].get(d["type"], 0) + 1
    return sortie


# --------------------------------------------------------------------------- sortie

def lire_contrat() -> dict[str, str]:
    """Le contrat de mesure, relu dans la docstring."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def construire_rapport(resultats: list[dict[str, Any]], illisibles: list[dict[str, str]],
                       seuils: Seuils, decalage: dict[str, Any] | None) -> dict[str, Any]:
    """Objet JSON final."""
    noms = [f"{r['chemin']} ({r['nombre_repliques']} répliques)" for r in resultats]
    comparees = [r["comparaison"]["bibliotheque"] for r in resultats if r.get("comparaison")]
    return {
        "outil": NOM_OUTIL,
        "moteur": "stdlib",
        "comparaison_avec": sorted(set(comparees)),
        "denominateur": sum(r["nombre_repliques"] for r in resultats),
        "unite": "répliques",
        "examines": noms[:PLAFOND_LISTE],
        "examines_tronques": len(noms) > PLAFOND_LISTE,
        "fichiers": len(resultats),
        "conformes": sum(r["verdict"] == "conforme" for r in resultats),
        "seuils": vars(seuils),
        "resultats": resultats,
        "illisibles": illisibles,
        "decalage": decalage,
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    for r in rapport["resultats"]:
        stats = r["statistiques"]
        print(f"{r['chemin']} : {r['verdict'].upper()} — {r['format']}, {r['encodage']}, "
              f"{r['nombre_repliques']} répliques, cps max {stats.get('cps_max')}")
        for d in r["defauts"][:30]:
            print(f"  défaut ligne {d['ligne']} {d['type']} : {d['detail']}")
        for a in r["avertissements"][:15]:
            print(f"  avertissement ligne {a['ligne']} {a['type']} : {a['detail']}")
        if r.get("comparaison"):
            print(f"  comparaison : {json.dumps(r['comparaison'], ensure_ascii=False)}")
    for i in rapport["illisibles"]:
        print(f"{i['chemin']} : ILLISIBLE — {i['raison']}")
    if rapport["decalage"]:
        print(f"décalage écrit : {json.dumps(rapport['decalage'], ensure_ascii=False)}")
    print(f"{rapport['fichiers']} fichier(s), {rapport['denominateur']} réplique(s), "
          f"{rapport['conformes']} conforme(s)")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Contrôle des sous-titres SRT et WebVTT : forme (numéros, horodatages, ordre, "
                    "chevauchements, balises) et lisibilité (durée, caractères par seconde, lignes).",
        epilog="Exemples : python lire_srt_vtt.py film.fr.srt --json\n"
               "          python lire_srt_vtt.py sous-titres/ --cps-max 17\n"
               "          python lire_srt_vtt.py film.vtt --decaler -1500 --sortie film_recale.vtt\n"
               "Codes : 0 conforme, 1 défaut, 2 entrée invalide, 3 aucune réplique à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="*", help="fichiers .srt/.vtt ou dossiers")
    parseur.add_argument("--texte", help="sous-titres en ligne ; la suite \\n vaut un saut de ligne")
    parseur.add_argument("--format", choices=("srt", "vtt"), help="impose le format (défaut : deviné)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--cps-max", type=float, default=20.0, help="caractères par seconde maximum")
    parseur.add_argument("--car-par-ligne", type=int, default=42, help="caractères par ligne maximum")
    parseur.add_argument("--lignes-max", type=int, default=2, help="lignes par réplique maximum")
    parseur.add_argument("--duree-min", type=int, default=1000, help="durée minimale en ms")
    parseur.add_argument("--duree-max", type=int, default=7000, help="durée maximale en ms")
    parseur.add_argument("--decaler", help="décalage à appliquer (ex. -1500, +2.5s, -00:00:01,500)")
    parseur.add_argument("--sortie", type=Path, help="fichier où écrire la copie décalée")
    parseur.add_argument("--ecraser", action="store_true", help="autorise à remplacer --sortie")
    parseur.add_argument("--sans-comparaison", action="store_true", help="n'appelle pas pysrt / webvtt-py")
    parseur.add_argument("--max-octets", type=int, default=16 << 20, help="taille maximale d'un fichier")
    parseur.add_argument("--max-fichiers", type=int, default=2000, help="fichiers lus par dossier au plus")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def verifier_options(args: argparse.Namespace) -> Seuils:
    """Valide les options ; lève EntreeInvalide."""
    if not args.chemins and args.texte is None:
        raise EntreeInvalide("donner un fichier, un dossier ou --texte")
    if min(args.car_par_ligne, args.lignes_max, args.duree_min, args.duree_max,
           args.max_octets, args.max_fichiers) < 1 or args.cps_max <= 0:
        raise EntreeInvalide("les seuils et plafonds doivent être positifs")
    if args.decaler is not None and args.sortie is None:
        raise EntreeInvalide("--decaler exige --sortie (le fichier d'origine n'est jamais modifié)")
    if args.sortie is not None and args.decaler is None:
        raise EntreeInvalide("--sortie n'a de sens qu'avec --decaler")
    return Seuils(args.cps_max, args.car_par_ligne, args.lignes_max, args.duree_min, args.duree_max)


def signaler_absences() -> None:
    """Une ligne sur stderr si une bibliothèque de comparaison manque."""
    absentes = [nom for nom, mod in (("pysrt", pysrt), ("webvtt-py", webvtt)) if mod is None]
    if absentes:
        print(f"mode dégradé — {' et '.join(absentes)} absent(s) : pas de comparaison, "
              "lecture stdlib seule", file=sys.stderr)


def sources(args: argparse.Namespace) -> list[tuple[str, Path | None, str | None]]:
    """(nom, chemin, texte) à analyser ; lève EntreeInvalide."""
    explicites, trouves = collecter(args.chemins, base_relative(args.racine), args.max_fichiers)
    liste: list[tuple[str, Path | None, str | None]] = [(str(p), p, None) for p in explicites + trouves]
    if args.texte is not None:
        texte = args.texte.replace("\\r\\n", "\n").replace("\\n", "\n")
        liste.append(("<texte>", None, texte))
    return liste


def traiter(args: argparse.Namespace, seuils: Seuils) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any] | None]:
    """Analyse toutes les sources ; écrit le décalage si demandé."""
    liste = sources(args)
    if args.decaler is not None and len(liste) != 1:
        raise EntreeInvalide(f"--decaler s'applique à une seule entrée ({len(liste)} données)")
    decalage = lire_decalage(args.decaler) if args.decaler is not None else None
    resultats: list[dict[str, Any]] = []
    illisibles: list[dict[str, str]] = []
    ecrit = None
    for nom, chemin, texte in liste:
        try:
            if chemin is not None:
                texte, fmt, encodage, crlf = lire_fichier(chemin, args.format, args.max_octets)
            else:
                texte, crlf = normaliser_fins(texte or ""), False
                fmt, encodage = deviner_format(texte, args.format), "utf-8"
        except EntreeInvalide as exc:
            illisibles.append({"chemin": nom, "raison": str(exc)})
            continue
        resultat, lecture = analyser(nom, texte, fmt, encodage, seuils, not args.sans_comparaison)
        resultats.append(resultat)
        if decalage is not None:
            ecrit = ecrire_decale(texte, lecture, decalage, args.sortie, args.ecraser, crlf)
    return resultats, illisibles, ecrit


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        seuils = verifier_options(args)
        if not args.sans_comparaison:
            signaler_absences()
        resultats, illisibles, ecrit = traiter(args, seuils)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    rapport = construire_rapport(resultats, illisibles, seuils, ecrit)
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    for i in illisibles:
        print(f"entrée invalide : {i['chemin']} : {i['raison']}", file=sys.stderr)
    if illisibles:
        return 2
    if rapport["denominateur"] == 0:
        print("dénominateur nul : aucune réplique lue, rien à examiner", file=sys.stderr)
        return 3
    return 0 if rapport["conformes"] == len(resultats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
