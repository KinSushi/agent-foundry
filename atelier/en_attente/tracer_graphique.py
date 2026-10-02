"""Trace des données en SVG autonome (courbe, barres, nuage) avec la seule bibliothèque standard.

Un agent sans écran ne voit pas ses données et l'interpréteur de référence n'a pas
matplotlib (mesuré dans cette session : `import matplotlib` y lève ModuleNotFoundError) ;
cet outil écrit lui-même le SVG, puis le relit avec xml.etree avant de le déclarer valide.
Mesuré : l'INVOCATION ci-dessous produit 16 points, un SVG de 4 590 octets et 66 éléments
relus sans erreur ; 100 000 points (sinus en CSV) donnent 1 364 717 octets en 0,6 s.

QUESTION
    À quoi ressemblent ces données, en un graphique que je peux joindre ?
MESURE
    Lit un CSV ou un TSV (délimiteur deviné par csv.Sniffer), un JSON (liste de nombres,
    liste de paires [x, y], objet nom -> liste, liste d'enregistrements) ou des
    valeurs en ligne ; choisit l'axe x (colonne texte, colonne numérique
    strictement croissante, ou rang) ; calcule des graduations en nombres ronds
    (1, 2 ou 5 fois une puissance de 10) ; écrit un SVG où chaque texte est
    échappé (xml.sax.saxutils) et purgé des caractères interdits en XML 1.0 ; le
    relit avec xml.etree.ElementTree. Le fichier n'est écrit que dans --sortie.
    Avec matplotlib (option --moteur matplotlib, ou sortie .png), le rendu est
    délégué à matplotlib.
HYPOTHÈSES
    Les colonnes numériques contiennent des nombres au format point décimal ; une
    colonne x textuelle est une suite de catégories également espacées ; au plus
    8 séries (palette catégorielle fixe, jamais recyclée).
LIMITES
    Pas d'échelle logarithmique, pas de dates comprises comme un temps continu, pas
    de double axe y (refusé par principe), rendu clair seulement ; PNG impossible
    sans matplotlib ; au-delà de --max-points (100 000 par défaut) l'outil refuse
    au lieu de sous-échantillonner ; la largeur des textes est estimée, pas mesurée ;
    le moteur matplotlib (optionnel) isole sa configuration dans un dossier
    temporaire mais lit les polices du système (mesuré pour un PNG : 62 ouvertures
    hors de l'environnement Python et du dossier de travail, polices de
    share/fonts surtout) ; il n'est donc jamais choisi pour un SVG sauf demande.
CONTRE-EXEMPLES
    Une colonne de dates (2026-01-01, 01-02, 01-03, puis 03-01, 03-02) est lue comme
    du texte : les cinq points sont également espacés, le trou de 57 jours disparaît
    et la hausse de latence paraît brutale. Constaté dans cette session ; convertir
    les dates en nombres (jours, horodatage) avant de tracer.
INVOCATION
    {outil} --valeurs "3,1,4,1,5,9,2,6" --valeurs "2,7,1,8,2,8,1,8" --noms "pi,e" --sortie {dossier}/rendu --json
DOMAINE
    Séries numériques de taille modeste (jusqu'à quelques dizaines de milliers de
    points) à joindre à un rapport, une revue de code ou un ticket.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape, quoteattr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

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

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

OCTETS_MAX_DEFAUT = 20 * 1024 * 1024
POINTS_MAX_DEFAUT = 100_000
POINTS_AVEC_INFOBULLE = 2000
POINTS_AVEC_MARQUEUR = 40
SERIES_MAX = 8
NOM_FICHIER_DEFAUT = "graphique.svg"
ESPACE_NOMS_SVG = "http://www.w3.org/2000/svg"

PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
SURFACE = "#fcfcfb"
ENCRE = "#0b0b0b"
ENCRE_SECONDAIRE = "#52514e"
GRILLE = "#e1e0d9"
AXE = "#c3c2b7"
POLICE = "system-ui, -apple-system, 'Segoe UI', sans-serif"
LARGEUR_CARACTERE = 6.6

CARACTERES_INTERDITS_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Serie:
    """Une série : positions x, valeurs y, nom."""

    nom: str
    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)


@dataclass
class Donnees:
    """Séries prêtes à tracer et description de l'axe x."""

    series: list[Serie]
    mode_x: str
    etiquettes_x: list[str]
    source_x: str
    ignores: int = 0
    avertissements: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Cadre:
    """Géométrie de la zone de tracé et conversion données → pixels."""

    largeur: int
    hauteur: int
    gauche: float
    droite: float
    haut: float
    bas: float
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def abscisse_px(self, x: float) -> float:
        etendue = self.x_max - self.x_min
        return self.gauche + (x - self.x_min) / etendue * (self.droite - self.gauche)

    def ordonnee_px(self, y: float) -> float:
        etendue = self.y_max - self.y_min
        return self.bas - (y - self.y_min) / etendue * (self.bas - self.haut)


def lire_nombre(cellule: Any) -> float | None:
    """Nombre fini depuis une cellule (texte ou nombre JSON), sinon None ; les booléens sont refusés."""
    if isinstance(cellule, bool) or cellule is None:
        return None
    if isinstance(cellule, (int, float)):
        valeur = float(cellule)
    else:
        try:
            valeur = float(str(cellule).strip().replace(" ", "").replace("\xa0", ""))
        except ValueError:
            return None
    return valeur if math.isfinite(valeur) else None


def lire_texte_borne(chemin: Path, octets_max: int) -> str:
    """Lit un fichier texte UTF-8 borné ; refuse absent, dossier, binaire, encodage invalide."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} est un dossier : un fichier CSV ou JSON est attendu")
    taille = chemin.stat().st_size
    if taille > octets_max:
        raise ErreurEntree(f"{chemin} pèse {taille} octets, au-delà de --max-octets {octets_max}")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, ni CSV ni JSON")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en UTF-8 (octet {exc.start}) : {exc.reason}") from exc


def lire_table_csv(texte: str) -> tuple[list[str], list[list[str]], str]:
    """Découpe un CSV/TSV : en-tête (deviné), lignes, nom du délimiteur."""
    echantillon = texte[:65536]
    try:
        dialecte = csv.Sniffer().sniff(echantillon, delimiters=",;\t|")
        delimiteur = dialecte.delimiter
    except csv.Error:
        delimiteur = ","
    lignes = [ligne for ligne in csv.reader(io.StringIO(texte), delimiter=delimiteur) if any(c.strip() for c in ligne)]
    if not lignes:
        return [], [], delimiteur
    premiere = lignes[0]
    largeur = max(len(ligne) for ligne in lignes)
    a_entete = a_une_entete(premiere, lignes[1:], largeur)
    entete = [c.strip() or f"colonne {i + 1}" for i, c in enumerate(premiere)] if a_entete else []
    entete += [f"colonne {i + 1}" for i in range(len(entete), largeur)]
    corps = lignes[1:] if a_entete else lignes
    return entete, [ligne + [""] * (largeur - len(ligne)) for ligne in corps], delimiteur


def a_une_entete(premiere: Sequence[str], corps: Sequence[Sequence[str]], largeur: int) -> bool:
    """En-tête si une colonne numérique dans le corps porte du texte en première ligne."""
    numeriques = [j for j in range(largeur) if est_numerique([ligne[j] if j < len(ligne) else "" for ligne in corps])]
    if not numeriques:
        return any(lire_nombre(c) is None and c.strip() for c in premiere)
    return any(j < len(premiere) and premiere[j].strip() and lire_nombre(premiere[j]) is None for j in numeriques)


def table_depuis_json(objet: Any) -> tuple[list[str], list[list[Any]]] | None:
    """Ramène une liste d'enregistrements JSON à une table (colonnes = union des clés)."""
    if not (isinstance(objet, list) and objet and all(isinstance(e, dict) for e in objet)):
        return None
    colonnes = list(dict.fromkeys(cle for enregistrement in objet for cle in enregistrement))
    return [str(c) for c in colonnes], [[e.get(c) for c in colonnes] for e in objet]


def est_numerique(colonne: Sequence[Any]) -> bool:
    """Colonne numérique : au moins un nombre, et au moins la moitié des cellules non vides."""
    non_vides = [c for c in colonne if c is not None and str(c).strip()]
    nombres = sum(1 for c in non_vides if lire_nombre(c) is not None)
    return nombres > 0 and nombres * 2 >= len(non_vides)


def trouver_colonne(entete: Sequence[str], choix: str) -> int:
    """Indice d'une colonne désignée par son nom ou son rang (1 = première)."""
    if choix in entete:
        return entete.index(choix)
    if choix.isdigit() and 1 <= int(choix) <= len(entete):
        return int(choix) - 1
    raise ErreurEntree(f"colonne « {choix} » introuvable ; colonnes : {', '.join(entete)}")


def choisir_x(entete: Sequence[str], colonnes: Sequence[Sequence[Any]], choix: str | None) -> tuple[int | None, str]:
    """Choisit la colonne x : imposée, texte en tête, numérique croissante en tête, sinon le rang."""
    if choix == "rang":
        return None, "rang (imposé)"
    if choix:
        indice = trouver_colonne(entete, choix)
        return indice, f"colonne « {entete[indice]} » (imposée)"
    if not colonnes:
        return None, "rang"
    if not est_numerique(colonnes[0]):
        return 0, f"colonne « {entete[0]} » (texte : catégories)"
    valeurs = [lire_nombre(c) for c in colonnes[0]]
    croissante = all(v is not None for v in valeurs) and all(b > a for a, b in zip(valeurs, valeurs[1:]))
    numeriques = sum(1 for c in colonnes if est_numerique(c))
    if croissante and numeriques >= 2:
        return 0, f"colonne « {entete[0]} » (numérique strictement croissante)"
    return None, "rang (aucune colonne x évidente)"


def series_depuis_table(entete: list[str], lignes: list[list[Any]], args: argparse.Namespace) -> Donnees:
    """Construit les séries d'une table selon --x / --y ou les choix automatiques."""
    colonnes = [[ligne[i] for ligne in lignes] for i in range(len(entete))]
    indice_x, source_x = choisir_x(entete, colonnes, args.x)
    if args.y:
        indices_y = [trouver_colonne(entete, nom.strip()) for nom in args.y.split(",") if nom.strip()]
    else:
        indices_y = [i for i, c in enumerate(colonnes) if i != indice_x and est_numerique(c)]
    if not indices_y:
        raise ErreurEntree("dénominateur nul : aucune colonne numérique, rien à examiner", CODE_RIEN)
    mode, etiquettes, positions = axe_x_depuis(colonnes[indice_x] if indice_x is not None else None, len(lignes))
    donnees = Donnees([], mode, etiquettes, source_x)
    for i in indices_y:
        serie = Serie(entete[i])
        for position, cellule in zip(positions, colonnes[i]):
            if cellule is None or not str(cellule).strip():
                continue
            valeur = lire_nombre(cellule)
            if position is None or valeur is None:
                donnees.ignores += 1
                continue
            serie.xs.append(position)
            serie.ys.append(valeur)
        donnees.series.append(serie)
    return donnees


def axe_x_depuis(colonne: Sequence[Any] | None, n: int) -> tuple[str, list[str], list[float | None]]:
    """Mode de l'axe x, étiquettes de catégories et position de chaque ligne."""
    if colonne is None:
        return "rang", [], [float(i + 1) for i in range(n)]
    if est_numerique(colonne):
        return "numerique", [], [lire_nombre(c) for c in colonne]
    etiquettes = list(dict.fromkeys(str(c).strip() for c in colonne if c is not None and str(c).strip()))
    index = {e: float(i) for i, e in enumerate(etiquettes)}
    return "categories", etiquettes, [index.get(str(c).strip()) if c is not None else None for c in colonne]


def series_depuis_json(objet: Any, args: argparse.Namespace) -> Donnees:
    """Interprète un JSON : nombres, paires, objet nom → liste, ou enregistrements."""
    table = table_depuis_json(objet)
    if table is not None:
        return series_depuis_table(table[0], table[1], args)
    if isinstance(objet, list):
        objet = {"série 1": objet}
    if not isinstance(objet, dict) or not objet:
        raise ErreurEntree("JSON non reconnu : attendu une liste de nombres, de paires [x, y], "
                           "un objet nom → liste ou une liste d'enregistrements")
    donnees = Donnees([], "rang", [], "rang")
    for nom, valeurs in objet.items():
        if not isinstance(valeurs, list):
            raise ErreurEntree(f"la série « {nom} » n'est pas une liste")
        donnees.series.append(serie_depuis_liste(str(nom), valeurs, donnees))
    return donnees


def serie_depuis_liste(nom: str, valeurs: list[Any], donnees: Donnees) -> Serie:
    """Une série depuis une liste de nombres (x = rang) ou de paires [x, y] (x numérique)."""
    serie = Serie(nom)
    for rang, element in enumerate(valeurs, start=1):
        if isinstance(element, list) and len(element) == 2:
            x, y = lire_nombre(element[0]), lire_nombre(element[1])
            donnees.mode_x, donnees.source_x = "numerique", "paires [x, y]"
        else:
            x, y = float(rang), lire_nombre(element)
        if x is None or y is None:
            donnees.ignores += 1
            continue
        serie.xs.append(x)
        serie.ys.append(y)
    return serie


def series_en_ligne(args: argparse.Namespace) -> Donnees:
    """Séries données par --valeurs (répétable), nommées par --noms, catégories par --etiquettes."""
    noms = [n.strip() for n in args.noms.split(",")] if args.noms else []
    etiquettes = [e.strip() for e in args.etiquettes.split(",")] if args.etiquettes else []
    donnees = Donnees([], "categories" if etiquettes else "rang", etiquettes,
                      "--etiquettes" if etiquettes else "rang")
    for i, texte in enumerate(args.valeurs):
        serie = Serie(noms[i] if i < len(noms) and noms[i] else f"série {i + 1}")
        for rang, jeton in enumerate((j for j in re.split(r"[\s,;]+", texte) if j), start=1):
            valeur = lire_nombre(jeton)
            if valeur is None:
                donnees.ignores += 1
                continue
            serie.xs.append(float(rang - 1) if etiquettes else float(rang))
            serie.ys.append(valeur)
        donnees.series.append(serie)
    if etiquettes and any(len(s.xs) > len(etiquettes) for s in donnees.series):
        donnees.avertissements.append("plus de valeurs que d'étiquettes : les étiquettes manquantes sont vides")
        donnees.etiquettes_x += [""] * (max(len(s.xs) for s in donnees.series) - len(etiquettes))
    return donnees


def nombre_rond(valeur: float, arrondir: bool) -> float:
    """Nombre « rond » (1, 2, 5 × 10^k) proche de valeur (Heckbert, Graphics Gems, 1990)."""
    exposant = math.floor(math.log10(valeur))
    fraction = valeur / 10 ** exposant
    if arrondir:
        rond = 1 if fraction < 1.5 else 2 if fraction < 3 else 5 if fraction < 7 else 10
    else:
        rond = 1 if fraction <= 1 else 2 if fraction <= 2 else 5 if fraction <= 5 else 10
    return rond * 10 ** exposant


def graduations(bas: float, haut: float, cible: int = 5) -> tuple[list[float], int]:
    """Graduations rondes couvrant [bas, haut] et nombre de décimales à afficher."""
    if haut == bas:
        marge = abs(bas) * 0.1 or 1.0
        bas, haut = bas - marge, haut + marge
    pas = nombre_rond(nombre_rond(haut - bas, False) / (cible - 1), True)
    debut, fin = math.floor(bas / pas), math.ceil(haut / pas)
    decimales = max(-math.floor(math.log10(pas)), 0)
    return [round(k * pas, decimales) for k in range(debut, fin + 1)], decimales


def formater_graduation(valeur: float, decimales: int) -> str:
    """Texte d'une graduation : décimales fixes, ou notation scientifique hors plage."""
    if valeur != 0 and (abs(valeur) >= 1e7 or abs(valeur) < 1e-4):
        return f"{valeur:.3g}"
    texte = f"{valeur:.{decimales}f}"
    return "0" if texte.strip("-0.") == "" else texte


def texte_xml(brut: str, longueur_max: int = 60) -> str:
    """Texte sûr pour XML 1.0 : caractères interdits retirés, longueur bornée, échappé."""
    propre = CARACTERES_INTERDITS_XML.sub("", brut)
    if len(propre) > longueur_max:
        propre = propre[:longueur_max - 1] + "…"
    return escape(propre)


def attribut_xml(brut: str, longueur_max: int = 120) -> str:
    """Valeur d'attribut XML sûre, guillemets compris."""
    propre = CARACTERES_INTERDITS_XML.sub("", brut)[:longueur_max]
    return quoteattr(propre)


def etendues(donnees: Donnees, type_graphique: str) -> tuple[float, float, float, float]:
    """Étendues x et y des données ; les barres incluent toujours zéro."""
    xs = [x for s in donnees.series for x in s.xs]
    ys = [y for s in donnees.series for y in s.ys]
    y_bas, y_haut = min(ys), max(ys)
    if type_graphique == "barres":
        y_bas, y_haut = min(y_bas, 0.0), max(y_haut, 0.0)
    return min(xs), max(xs), y_bas, y_haut


def construire_cadre(donnees: Donnees, args: argparse.Namespace) -> tuple[Cadre, dict[str, Any]]:
    """Calcule graduations et marges, puis le cadre de tracé."""
    x_bas, x_haut, y_bas, y_haut = etendues(donnees, args.type)
    ticks_y, dec_y = graduations(y_bas, y_haut)
    etiquettes_y = [formater_graduation(t, dec_y) for t in ticks_y]
    axe_x = axe_x_graduations(donnees, args.type, x_bas, x_haut)
    gauche = 16 + max(len(e) for e in etiquettes_y) * LARGEUR_CARACTERE + (18 if args.titre_y else 0)
    haut = 40 + (22 if len(donnees.series) > 1 else 0)
    bas = args.hauteur - 36 - (18 if args.titre_x else 0)
    cadre = Cadre(args.largeur, args.hauteur, gauche, args.largeur - 20, haut, bas,
                  axe_x["min"], axe_x["max"], ticks_y[0], ticks_y[-1])
    return cadre, {"x": axe_x, "y": {"min": ticks_y[0], "max": ticks_y[-1], "graduations": ticks_y,
                                     "etiquettes": etiquettes_y}}


def axe_x_graduations(donnees: Donnees, type_graphique: str, x_bas: float, x_haut: float) -> dict[str, Any]:
    """Graduations de l'axe x : catégories, rangs ou nombres ronds."""
    if type_graphique == "barres" or donnees.mode_x == "categories":
        positions = sorted({x for s in donnees.series for x in s.xs})
        etiquettes = [libelle_position(donnees, p) for p in positions]
        return {"mode": "categories", "positions": positions, "etiquettes": etiquettes,
                "min": positions[0] - 0.5, "max": positions[-1] + 0.5}
    ticks, decimales = graduations(x_bas, x_haut)
    entiers = [t for t in ticks if t == int(t)]
    if donnees.mode_x == "rang" and len(entiers) >= 2:
        ticks = entiers
    return {"mode": donnees.mode_x, "positions": ticks, "min": ticks[0], "max": ticks[-1],
            "etiquettes": [formater_graduation(t, decimales) for t in ticks]}


def libelle_nombre(valeur: float) -> str:
    """Écriture courte d'un nombre : entier sans décimale, sinon format général."""
    return str(int(valeur)) if valeur == int(valeur) and abs(valeur) < 1e15 else f"{valeur:g}"


def libelle_position(donnees: Donnees, position: float) -> str:
    """Étiquette d'une position x : catégorie, rang ou nombre."""
    if donnees.mode_x == "categories" and 0 <= int(position) < len(donnees.etiquettes_x):
        return donnees.etiquettes_x[int(position)]
    return libelle_nombre(position)


def vers_categories(donnees: Donnees) -> None:
    """Pour des barres : chaque valeur x distincte devient une catégorie également espacée."""
    if donnees.mode_x == "categories":
        return
    distinctes = sorted({x for s in donnees.series for x in s.xs})
    index = {x: float(i) for i, x in enumerate(distinctes)}
    donnees.etiquettes_x = [libelle_nombre(x) for x in distinctes]
    for serie in donnees.series:
        serie.xs = [index[x] for x in serie.xs]
    donnees.mode_x = "categories"


def svg_grille_et_axes(cadre: Cadre, axes: dict[str, Any]) -> list[str]:
    """Lignes de grille horizontales, axe de base et graduations des deux axes."""
    morceaux = ['<g class="grille">']
    for valeur, etiquette in zip(axes["y"]["graduations"], axes["y"]["etiquettes"]):
        y = round(cadre.ordonnee_px(valeur), 2)
        morceaux.append(f'<line x1="{cadre.gauche:.2f}" x2="{cadre.droite:.2f}" y1="{y}" y2="{y}" '
                        f'stroke="{GRILLE}" stroke-width="1"/>')
        morceaux.append(f'<text x="{cadre.gauche - 8:.2f}" y="{y + 4}" text-anchor="end" '
                        f'fill="{ENCRE_SECONDAIRE}">{texte_xml(etiquette)}</text>')
    zero = 0.0 if cadre.y_min <= 0.0 <= cadre.y_max else cadre.y_min
    y0 = round(cadre.ordonnee_px(zero), 2)
    morceaux.append(f'<line x1="{cadre.gauche:.2f}" x2="{cadre.droite:.2f}" y1="{y0}" y2="{y0}" '
                    f'stroke="{AXE}" stroke-width="1"/>')
    morceaux += svg_graduations_x(cadre, axes["x"])
    morceaux.append("</g>")
    return morceaux


def svg_graduations_x(cadre: Cadre, axe_x: dict[str, Any]) -> list[str]:
    """Étiquettes de l'axe x, une sur k pour éviter les chevauchements."""
    positions, etiquettes = axe_x["positions"], axe_x["etiquettes"]
    place = (cadre.droite - cadre.gauche) / max(len(positions), 1)
    plus_longue = max((min(len(e), 16) for e in etiquettes), default=1)
    pas = max(1, math.ceil((plus_longue * LARGEUR_CARACTERE + 8) / max(place, 1)))
    morceaux = []
    for rang, (position, etiquette) in enumerate(zip(positions, etiquettes)):
        if rang % pas:
            continue
        x = round(cadre.abscisse_px(position), 2)
        morceaux.append(f'<text x="{x}" y="{cadre.bas + 18:.2f}" text-anchor="middle" '
                        f'fill="{ENCRE_SECONDAIRE}">{texte_xml(etiquette, 16)}</text>')
    return morceaux


def infobulle(serie: Serie, x_texte: str, y: float, nb_points: int) -> str:
    """Élément <title> d'une marque (infobulle native), omis au-delà de POINTS_AVEC_INFOBULLE."""
    if nb_points > POINTS_AVEC_INFOBULLE:
        return ""
    return f"<title>{texte_xml(f'{serie.nom} — {x_texte} : {y:g}', 120)}</title>"


def svg_courbes(cadre: Cadre, donnees: Donnees, nb_points: int) -> list[str]:
    """Une polyligne de 2 px par série ; marqueurs de 8 px si la série est courte."""
    morceaux = ['<g class="series">']
    for rang, serie in enumerate(donnees.series):
        couleur = PALETTE[rang]
        points = " ".join(f"{cadre.abscisse_px(x):.2f},{cadre.ordonnee_px(y):.2f}" for x, y in zip(serie.xs, serie.ys))
        morceaux.append(f'<polyline fill="none" stroke="{couleur}" stroke-width="2" stroke-linejoin="round" '
                        f'stroke-linecap="round" points="{points}"><title>{texte_xml(serie.nom, 120)}</title></polyline>')
        if len(serie.xs) <= POINTS_AVEC_MARQUEUR:
            morceaux += svg_points(cadre, donnees, serie, couleur, nb_points)
    morceaux.append("</g>")
    return morceaux


def svg_points(cadre: Cadre, donnees: Donnees, serie: Serie, couleur: str, nb_points: int) -> list[str]:
    """Cercles de 8 px cerclés de la couleur de fond (lisibles en superposition)."""
    return [f'<circle cx="{cadre.abscisse_px(x):.2f}" cy="{cadre.ordonnee_px(y):.2f}" r="4" fill="{couleur}" '
            f'stroke="{SURFACE}" stroke-width="2">{infobulle(serie, libelle_position(donnees, x), y, nb_points)}</circle>'
            for x, y in zip(serie.xs, serie.ys)]


def svg_nuage(cadre: Cadre, donnees: Donnees, nb_points: int) -> list[str]:
    """Nuage de points : un cercle par observation."""
    morceaux = ['<g class="series">']
    for rang, serie in enumerate(donnees.series):
        morceaux += svg_points(cadre, donnees, serie, PALETTE[rang], nb_points)
    morceaux.append("</g>")
    return morceaux


def svg_barres(cadre: Cadre, donnees: Donnees, nb_points: int) -> list[str]:
    """Barres groupées par position x, séparées de 2 px, ancrées sur zéro."""
    morceaux = ['<g class="series">']
    nb_series = len(donnees.series)
    largeur_groupe = (cadre.abscisse_px(1.0) - cadre.abscisse_px(0.0)) * 0.8 if cadre.x_max > cadre.x_min else 10.0
    largeur = max((largeur_groupe - 2 * (nb_series - 1)) / nb_series, 1.0)
    zero = cadre.ordonnee_px(min(max(0.0, cadre.y_min), cadre.y_max))
    for rang, serie in enumerate(donnees.series):
        for x, y in zip(serie.xs, serie.ys):
            gauche = cadre.abscisse_px(x) - largeur_groupe / 2 + rang * (largeur + 2)
            sommet = cadre.ordonnee_px(y)
            morceaux.append(f'<rect x="{gauche:.2f}" y="{min(sommet, zero):.2f}" width="{largeur:.2f}" '
                            f'height="{abs(zero - sommet):.2f}" fill="{PALETTE[rang]}">'
                            f'{infobulle(serie, libelle_position(donnees, x), y, nb_points)}</rect>')
    morceaux.append("</g>")
    return morceaux


def svg_legende(cadre: Cadre, donnees: Donnees) -> list[str]:
    """Légende horizontale sous le titre, présente dès deux séries."""
    if len(donnees.series) < 2:
        return []
    morceaux, x = ['<g class="legende">'], cadre.gauche
    for rang, serie in enumerate(donnees.series):
        nom = texte_xml(serie.nom, 24)
        morceaux.append(f'<rect x="{x:.2f}" y="44" width="12" height="12" rx="2" fill="{PALETTE[rang]}"/>')
        morceaux.append(f'<text x="{x + 16:.2f}" y="54" fill="{ENCRE}">{nom}</text>')
        x += 16 + min(len(serie.nom), 24) * LARGEUR_CARACTERE + 18
    morceaux.append("</g>")
    return morceaux


def svg_titres(cadre: Cadre, args: argparse.Namespace, titre: str) -> list[str]:
    """Titre du graphique et titres d'axes."""
    morceaux = [f'<text x="{cadre.gauche:.2f}" y="24" font-size="15" font-weight="600" fill="{ENCRE}">'
                f'{texte_xml(titre, 90)}</text>']
    if args.titre_x:
        morceaux.append(f'<text x="{(cadre.gauche + cadre.droite) / 2:.2f}" y="{cadre.hauteur - 10}" '
                        f'text-anchor="middle" fill="{ENCRE_SECONDAIRE}">{texte_xml(args.titre_x, 80)}</text>')
    if args.titre_y:
        milieu = (cadre.haut + cadre.bas) / 2
        morceaux.append(f'<text x="16" y="{milieu:.2f}" text-anchor="middle" transform="rotate(-90 16 {milieu:.2f})" '
                        f'fill="{ENCRE_SECONDAIRE}">{texte_xml(args.titre_y, 60)}</text>')
    return morceaux


def rendre_svg(donnees: Donnees, args: argparse.Namespace, titre: str) -> tuple[str, dict[str, Any]]:
    """Assemble le document SVG complet et rend aussi la description des axes."""
    cadre, axes = construire_cadre(donnees, args)
    nb_points = sum(len(s.xs) for s in donnees.series)
    traceur = {"courbe": svg_courbes, "barres": svg_barres, "nuage": svg_nuage}[args.type]
    description = f"{args.type} : {len(donnees.series)} série(s), {nb_points} points"
    morceaux = [
        f'<svg xmlns="{ESPACE_NOMS_SVG}" width="{cadre.largeur}" height="{cadre.hauteur}" '
        f'viewBox="0 0 {cadre.largeur} {cadre.hauteur}" role="img" font-family={attribut_xml(POLICE)} font-size="12">',
        f"<title>{texte_xml(titre, 200)}</title>", f"<desc>{texte_xml(description, 200)}</desc>",
        f'<rect width="{cadre.largeur}" height="{cadre.hauteur}" fill="{SURFACE}"/>',
    ]
    morceaux += svg_grille_et_axes(cadre, axes) + traceur(cadre, donnees, nb_points)
    morceaux += svg_legende(cadre, donnees) + svg_titres(cadre, args, titre) + ["</svg>"]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(morceaux) + "\n", axes


def valider_xml(document: str) -> tuple[bool, str]:
    """Relit le SVG avec xml.etree : vrai s'il est bien formé et de racine svg."""
    try:
        racine = ElementTree.fromstring(document.encode("utf-8"))
    except ElementTree.ParseError as exc:
        return False, f"XML mal formé : {exc}"
    if racine.tag != f"{{{ESPACE_NOMS_SVG}}}svg":
        return False, f"racine inattendue : {racine.tag}"
    return True, f"{sum(1 for _ in racine.iter())} éléments"


def verifier_fichier_ecrit(chemin: Path) -> dict[str, Any]:
    """Relit le fichier produit par matplotlib : XML bien formé pour un SVG, signature pour un PNG."""
    contenu = chemin.read_bytes()
    if chemin.suffix.lower() == ".png":
        valide = contenu.startswith(b"\x89PNG\r\n\x1a\n")
        return {"svg_valide": None, "png_valide": valide,
                "validation": "signature PNG présente" if valide else "signature PNG absente"}
    valide, detail = valider_xml(contenu.decode("utf-8", errors="replace"))
    return {"svg_valide": valide, "validation": detail}


def chemin_sortie(brut: str, racine: Path | None) -> Path:
    """Chemin du fichier à écrire : un dossier existant reçoit graphique.svg."""
    chemin = Path(brut)
    if not chemin.is_absolute() and racine is not None:
        chemin = racine / chemin
    if chemin.is_dir():
        return chemin / NOM_FICHIER_DEFAUT
    if chemin.suffix.lower() not in (".svg", ".png"):
        raise ErreurEntree(f"--sortie {chemin} : extension .svg ou .png attendue (ou un dossier existant)")
    if not chemin.parent.is_dir():
        raise ErreurEntree(f"--sortie {chemin} : le dossier parent n'existe pas (l'outil n'en crée pas)")
    return chemin


def rendre_avec_matplotlib(donnees: Donnees, args: argparse.Namespace, titre: str, chemin: Path) -> str | None:
    """Rendu délégué à matplotlib (configuration isolée dans un dossier temporaire) ; None s'il est absent."""
    with tempfile.TemporaryDirectory() as configuration:
        os.environ["MPLCONFIGDIR"] = configuration
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as pyplot
        except ImportError:
            return None
        figure, axe = pyplot.subplots(figsize=(args.largeur / 100, args.hauteur / 100), dpi=100)
        for rang, serie in enumerate(donnees.series):
            tracer_serie_matplotlib(axe, serie, rang, len(donnees.series), args.type)
        axe.set_title(titre)
        axe.set_xlabel(args.titre_x or "")
        axe.set_ylabel(args.titre_y or "")
        if donnees.mode_x == "categories":
            axe.set_xticks(range(len(donnees.etiquettes_x)), donnees.etiquettes_x)
        if len(donnees.series) > 1:
            axe.legend()
        figure.savefig(chemin, format=chemin.suffix.lstrip(".").lower())
        pyplot.close(figure)
        return matplotlib.__version__


def tracer_serie_matplotlib(axe: Any, serie: Serie, rang: int, nb_series: int, type_graphique: str) -> None:
    """Trace une série sur un axe matplotlib avec la même palette."""
    if type_graphique == "barres":
        largeur = 0.8 / nb_series
        axe.bar([x - 0.4 + largeur * (rang + 0.5) for x in serie.xs], serie.ys, width=largeur,
                color=PALETTE[rang], label=serie.nom)
    elif type_graphique == "nuage":
        axe.scatter(serie.xs, serie.ys, color=PALETTE[rang], label=serie.nom)
    else:
        axe.plot(serie.xs, serie.ys, color=PALETTE[rang], linewidth=2, label=serie.nom)


def charger(args: argparse.Namespace) -> Donnees:
    """Charge les données depuis le fichier ou --valeurs."""
    if args.fichier and args.valeurs:
        raise ErreurEntree("donnez un fichier OU --valeurs, pas les deux")
    if args.valeurs:
        return series_en_ligne(args)
    if not args.fichier:
        raise ErreurEntree("dénominateur nul : rien à examiner — donnez un fichier CSV/JSON ou --valeurs", CODE_RIEN)
    chemin = Path(args.fichier)
    if not chemin.is_absolute() and args.racine is not None:
        chemin = args.racine / chemin
    texte = lire_texte_borne(chemin, args.max_octets)
    if chemin.suffix.lower() == ".json" or texte.lstrip()[:1] in ("[", "{"):
        try:
            return series_depuis_json(json.loads(texte), args)
        except json.JSONDecodeError as exc:
            raise ErreurEntree(f"{chemin} : JSON invalide ligne {exc.lineno} colonne {exc.colno} : {exc.msg}") from exc
    entete, lignes, _ = lire_table_csv(texte)
    if not lignes:
        raise ErreurEntree(f"dénominateur nul : {chemin} ne contient aucune ligne de données, rien à examiner", CODE_RIEN)
    return series_depuis_table(entete, lignes, args)


def verifier_donnees(donnees: Donnees, args: argparse.Namespace) -> int:
    """Refuse séries vides, trop de séries ou trop de points ; rend le nombre de points."""
    vides = [s.nom for s in donnees.series if not s.xs]
    if vides:
        donnees.avertissements.append(f"série(s) sans aucun point, omise(s) : {', '.join(vides)}")
    donnees.series = [s for s in donnees.series if s.xs]
    total = sum(len(s.xs) for s in donnees.series)
    if total == 0:
        raise ErreurEntree("dénominateur nul : aucun point numérique, rien à examiner", CODE_RIEN)
    if len(donnees.series) > SERIES_MAX:
        raise ErreurEntree(f"{len(donnees.series)} séries : au-delà de {SERIES_MAX}, regroupez-en (« autres ») "
                           f"ou tracez plusieurs graphiques ; choisissez les colonnes avec --y")
    for valeurs in ([x for s in donnees.series for x in s.xs], [y for s in donnees.series for y in s.ys]):
        if not math.isfinite(max(valeurs) - min(valeurs)):
            raise ErreurEntree("étendue des valeurs hors de la plage des flottants : impossible de graduer l'axe")
    if total > args.max_points:
        raise ErreurEntree(f"{total} points > --max-points {args.max_points} : agrégez d'abord, "
                           f"ou relevez la limite explicitement")
    if args.type == "courbe" and any(any(b < a for a, b in zip(s.xs, s.xs[1:])) for s in donnees.series):
        donnees.avertissements.append("x non croissant dans une série : la courbe relie les points dans l'ordre du fichier")
    if args.type == "barres":
        vers_categories(donnees)
    return total


def produire(donnees: Donnees, args: argparse.Namespace, titre: str) -> dict[str, Any]:
    """Rend le SVG (ou délègue à matplotlib), le valide, l'écrit si --sortie est donné."""
    document, axes = rendre_svg(donnees, args, titre)
    valide, detail = valider_xml(document)
    resultat: dict[str, Any] = {"svg_valide": valide, "validation": detail, "axes": axes,
                                "octets_svg": len(document.encode("utf-8")), "chemin_ecrit": None, "moteur": "stdlib"}
    if not args.sortie:
        return resultat
    chemin = chemin_sortie(args.sortie, args.racine)
    veut_matplotlib = args.moteur == "matplotlib" or (args.moteur == "auto" and chemin.suffix.lower() == ".png")
    if veut_matplotlib:
        version = rendre_avec_matplotlib(donnees, args, titre, chemin)
        if version is not None:
            return resultat | verifier_fichier_ecrit(chemin) | {
                "chemin_ecrit": str(chemin), "moteur": "matplotlib", "version_moteur": version,
                "octets_ecrits": chemin.stat().st_size}
        print("tracer_graphique : matplotlib absent — repli stdlib (SVG seulement)", file=sys.stderr)
        if chemin.suffix.lower() == ".png":
            raise ErreurEntree("PNG impossible sans matplotlib : donnez --sortie fichier.svg")
    if not valide:
        raise ErreurEntree(f"SVG produit invalide, rien n'est écrit : {detail}", CODE_DEFAUT)
    chemin.write_text(document, encoding="utf-8")
    return resultat | {"chemin_ecrit": str(chemin), "octets_ecrits": len(document.encode("utf-8"))}


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait du docstring les sections du contrat de mesure."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def construire_parseur() -> argparse.ArgumentParser:
    """Déclare l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Trace des données (fichier CSV/TSV/JSON ou valeurs en ligne) en SVG "
                    "autonome : courbe, barres ou nuage. N'écrit que dans --sortie ; stdout = résumé ou JSON.",
        epilog='exemple : tracer_graphique.py mesures.csv --type courbe --sortie rapport/latence.svg --json   '
               '| tracer_graphique.py --valeurs "3,1,4" --etiquettes "lun,mar,mer" --type barres --sortie barres.svg',
    )
    parseur.add_argument("fichier", nargs="?", metavar="FICHIER", help="fichier CSV, TSV ou JSON")
    parseur.add_argument("--valeurs", action="append", default=[], metavar="NOMBRES",
                         help='série en ligne "3,1,4" ; répétable pour plusieurs séries')
    parseur.add_argument("--noms", help="noms des séries en ligne, séparés par des virgules")
    parseur.add_argument("--etiquettes", help="catégories de l'axe x pour --valeurs, séparées par des virgules")
    parseur.add_argument("--type", choices=("courbe", "barres", "nuage"), default="courbe", help="forme (défaut courbe)")
    parseur.add_argument("--x", help="colonne x (nom ou rang 1..n), ou « rang » pour numéroter les lignes")
    parseur.add_argument("--y", help="colonnes y, séparées par des virgules (défaut : toutes les numériques)")
    parseur.add_argument("--titre", help="titre du graphique (défaut : nom du fichier)")
    parseur.add_argument("--titre-x", help="titre de l'axe x")
    parseur.add_argument("--titre-y", help="titre de l'axe y")
    parseur.add_argument("--sortie", help="fichier .svg/.png à écrire, ou dossier existant (reçoit graphique.svg) ; "
                                          "sans --sortie, rien n'est écrit")
    parseur.add_argument("--largeur", type=int, default=720, help="largeur en pixels (défaut 720)")
    parseur.add_argument("--hauteur", type=int, default=400, help="hauteur en pixels (défaut 400)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "matplotlib"), default="auto",
                         help="auto : stdlib pour le SVG, matplotlib pour le PNG ; matplotlib : toujours lui")
    parseur.add_argument("--max-points", type=int, default=POINTS_MAX_DEFAUT, help="refus au-delà (défaut 100 000)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue (défaut 20 Mio)")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def verifier_parametres(args: argparse.Namespace) -> None:
    """Refuse des dimensions absurdes ou des bornes non positives."""
    if not (200 <= args.largeur <= 10000 and 150 <= args.hauteur <= 10000):
        raise ErreurEntree("--largeur doit valoir 200..10000 et --hauteur 150..10000")
    if args.max_points < 1 or args.max_octets < 1:
        raise ErreurEntree("--max-points et --max-octets doivent être positifs")


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible du tracé."""
    destination = rapport["chemin_ecrit"] or "aucun fichier écrit (pas de --sortie)"
    print(f"{rapport['type']} : {len(rapport['series'])} série(s), {rapport['denominateur']} points → {destination}")
    etat = {True: "SVG valide", False: "SVG INVALIDE", None: "PNG"}[rapport["svg_valide"]]
    octets = rapport.get("octets_ecrits", rapport["octets_svg"])
    print(f"{etat} ({rapport['validation']}), {octets} octets, moteur {rapport['moteur']}")
    for serie in rapport["series"]:
        print(f"  {serie['nom']} : {serie['points']} points, y de {serie['min']:.12g} à {serie['max']:.12g} ({serie['couleur']})")
    print(f"  axe x : {rapport['source_x']} ; graduations y : {', '.join(rapport['axes']['y']['etiquettes'])}")
    for note in rapport["avertissements"]:
        print(f"  avertissement : {note}")


def assembler_rapport(donnees: Donnees, args: argparse.Namespace, total: int, rendu: dict[str, Any]) -> dict[str, Any]:
    """Objet JSON final : dénominateur, séries, axes, fichier écrit."""
    avertissements = list(donnees.avertissements)
    if donnees.ignores:
        avertissements.append(f"{donnees.ignores} valeur(s) non numérique(s) ou non finie(s) écartée(s) du tracé")
    return {
        "outil": "tracer_graphique", "denominateur": total, "unite_denominateur": "points",
        "examines": [s.nom for s in donnees.series], "type": args.type, "source_x": donnees.source_x,
        "series": [{"nom": s.nom, "points": len(s.xs), "min": min(s.ys), "max": max(s.ys), "couleur": PALETTE[i]}
                   for i, s in enumerate(donnees.series)],
        "valeurs_ecartees": donnees.ignores, "avertissements": avertissements,
    } | rendu | {"contrat": extraire_contrat(__doc__ or "")}


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : charge, vérifie, trace, écrit, publie."""
    args = construire_parseur().parse_args(argv)
    try:
        verifier_parametres(args)
        donnees = charger(args)
        total = verifier_donnees(donnees, args)
        titre = args.titre or (Path(args.fichier).name if args.fichier else "Graphique")
        rapport = assembler_rapport(donnees, args, total, produire(donnees, args, titre))
    except (ErreurEntree, OSError, csv.Error, RecursionError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"tracer_graphique : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "tracer_graphique", "moteur": "stdlib", "denominateur": 0, "examines": [],
                              "refus": str(exc)},
                             ensure_ascii=False))
        return code
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    invalide = rapport["svg_valide"] is False or rapport.get("png_valide") is False
    return CODE_DEFAUT if donnees.ignores or invalide else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
