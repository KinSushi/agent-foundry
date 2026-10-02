"""Vérifie le contraste WCAG 2.x de paires couleur de texte / couleur de fond, en ligne ou dans des feuilles CSS.

Un agent qui choisit « un gris discret » juge à l'œil et se trompe d'un cheveu : mesuré
dans cette session, #777777 sur #ffffff donne 4,478:1 (wcag-contrast-ratio 0.9 et
coloraide 8.13 rendent la même valeur), sous le seuil AA de 4,5:1, alors que #767676
passe à 4,542:1. Cet outil calcule le rapport sans arrondi et propose la couleur conforme
la plus proche.

QUESTION
    Ces couleurs de texte et de fond sont-elles lisibles selon le WCAG ?
MESURE
    Analyse des couleurs CSS (#rgb, #rgba, #rrggbb, #rrggbbaa, rgb(), rgba(), hsl(),
    hsla(), 148 noms CSS, transparent) ; composition alpha du texte sur le fond et
    du fond sur la page ; luminance relative sRGB (seuil de linéarisation 0,04045) ;
    rapport (L1 + 0,05) / (L2 + 0,05) comparé sans arrondi aux seuils 4,5 et 3 (AA,
    texte normal et grand), 7 et 4,5 (AAA), 3 (composants non textuels) ; recherche
    par dichotomie, dans l'espace TSL du module colorsys, de la luminosité la plus
    proche qui atteint le seuil, pour le texte puis pour le fond. Mode fichier : les
    déclarations color et background-color (ou background) d'une même règle CSS,
    @media compris, var(--x) résolus depuis les propriétés personnalisées du fichier.
    Si wcag-contrast-ratio ou coloraide sont installés, le rapport est recalculé par
    eux et l'écart publié.
HYPOTHÈSES
    Le texte est posé sur un fond uni de la couleur indiquée ; « grand texte » veut
    dire au moins 24 px, ou 18,66 px en gras, et la taille en em/rem se calcule sur
    16 px ; l'écran affiche du sRGB.
LIMITES
    Le mode fichier ne connaît ni la cascade ni l'héritage : une règle qui ne déclare
    que color n'a pas de fond (sauf --fond-defaut), et un fond hérité d'un ancêtre est
    invisible ; dégradés, images de fond, filtres, opacité d'un parent, ombres de texte
    et anticrénelage sont ignorés ; le WCAG 2.x lui-même surestime le contraste des
    couleurs sombres saturées (APCA n'est pas calculé).
CONTRE-EXEMPLES
    Constaté dans cette session : avec « .carte { background: #333 } » et
    « .carte p { color: #555 } », l'outil n'apparie pas le paragraphe à son fond
    hérité ; lancé avec --fond-defaut #fff il déclare #555 sur #fff conforme (7,455:1)
    alors que le texte réel, #555 sur #333, plafonne à 1,694:1.
INVOCATION
    {outil} "#777777" "#ffffff" --json
DOMAINE
    Couleurs de texte et de fond unis d'interfaces web, au sens du critère 1.4.3
    (contraste minimum), 1.4.6 (contraste renforcé) et 1.4.11 (non textuel).
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import wcag_contrast_ratio
except ImportError:
    wcag_contrast_ratio = None

try:
    import coloraide
except ImportError:
    coloraide = None

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
SIGLE_WCAG = "WCAG"
SIGLE_APCA = "APCA"

CODE_OK = 0
CODE_ECHEC = 1
CODE_USAGE = 2
CODE_RIEN = 3

SEUILS = MappingProxyType({("AA", False): 4.5, ("AA", True): 3.0, ("AAA", False): 7.0, ("AAA", True): 4.5})
SEUIL_NON_TEXTE = 3.0
GRAND_TEXTE_PX = 24.0
GRAND_TEXTE_GRAS_PX = 18.66
PX_PAR_EM = 16.0
PX_PAR_PT = 4.0 / 3.0
OCTETS_MAX_DEFAUT = 10 * 1024 * 1024
FICHIERS_MAX = 5000
TOLERANCE_CONTROLE = 1e-9
EXAMINES_MAX = 50

NOMS_CSS_BRUTS = (
    "aliceblue f0f8ff antiquewhite faebd7 aqua 00ffff aquamarine 7fffd4 azure f0ffff beige f5f5dc "
    "bisque ffe4c4 black 000000 blanchedalmond ffebcd blue 0000ff blueviolet 8a2be2 brown a52a2a "
    "burlywood deb887 cadetblue 5f9ea0 chartreuse 7fff00 chocolate d2691e coral ff7f50 "
    "cornflowerblue 6495ed cornsilk fff8dc crimson dc143c cyan 00ffff darkblue 00008b darkcyan 008b8b "
    "darkgoldenrod b8860b darkgray a9a9a9 darkgreen 006400 darkgrey a9a9a9 darkkhaki bdb76b "
    "darkmagenta 8b008b darkolivegreen 556b2f darkorange ff8c00 darkorchid 9932cc darkred 8b0000 "
    "darksalmon e9967a darkseagreen 8fbc8f darkslateblue 483d8b darkslategray 2f4f4f "
    "darkslategrey 2f4f4f darkturquoise 00ced1 darkviolet 9400d3 deeppink ff1493 deepskyblue 00bfff "
    "dimgray 696969 dimgrey 696969 dodgerblue 1e90ff firebrick b22222 floralwhite fffaf0 "
    "forestgreen 228b22 fuchsia ff00ff gainsboro dcdcdc ghostwhite f8f8ff gold ffd700 goldenrod daa520 "
    "gray 808080 green 008000 greenyellow adff2f grey 808080 honeydew f0fff0 hotpink ff69b4 "
    "indianred cd5c5c indigo 4b0082 ivory fffff0 khaki f0e68c lavender e6e6fa lavenderblush fff0f5 "
    "lawngreen 7cfc00 lemonchiffon fffacd lightblue add8e6 lightcoral f08080 lightcyan e0ffff "
    "lightgoldenrodyellow fafad2 lightgray d3d3d3 lightgreen 90ee90 lightgrey d3d3d3 lightpink ffb6c1 "
    "lightsalmon ffa07a lightseagreen 20b2aa lightskyblue 87cefa lightslategray 778899 "
    "lightslategrey 778899 lightsteelblue b0c4de lightyellow ffffe0 lime 00ff00 limegreen 32cd32 "
    "linen faf0e6 magenta ff00ff maroon 800000 mediumaquamarine 66cdaa mediumblue 0000cd "
    "mediumorchid ba55d3 mediumpurple 9370db mediumseagreen 3cb371 mediumslateblue 7b68ee "
    "mediumspringgreen 00fa9a mediumturquoise 48d1cc mediumvioletred c71585 midnightblue 191970 "
    "mintcream f5fffa mistyrose ffe4e1 moccasin ffe4b5 navajowhite ffdead navy 000080 oldlace fdf5e6 "
    "olive 808000 olivedrab 6b8e23 orange ffa500 orangered ff4500 orchid da70d6 palegoldenrod eee8aa "
    "palegreen 98fb98 paleturquoise afeeee palevioletred db7093 papayawhip ffefd5 peachpuff ffdab9 "
    "peru cd853f pink ffc0cb plum dda0dd powderblue b0e0e6 purple 800080 rebeccapurple 663399 "
    "red ff0000 rosybrown bc8f8f royalblue 4169e1 saddlebrown 8b4513 salmon fa8072 sandybrown f4a460 "
    "seagreen 2e8b57 seashell fff5ee sienna a0522d silver c0c0c0 skyblue 87ceeb slateblue 6a5acd "
    "slategray 708090 slategrey 708090 snow fffafa springgreen 00ff7f steelblue 4682b4 tan d2b48c "
    "teal 008080 thistle d8bfd8 tomato ff6347 turquoise 40e0d0 violet ee82ee wheat f5deb3 "
    "white ffffff whitesmoke f5f5f5 yellow ffff00 yellowgreen 9acd32"
)

MOTIF_FONCTION = re.compile(r"^(rgba?|hsla?)\(\s*(.*?)\s*\)$", re.IGNORECASE | re.DOTALL)
MOTIF_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
MOTIF_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)")


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Couleur:
    """Couleur sRGB : canaux 0..255 (flottants) et alpha 0..1."""

    r: float
    g: float
    b: float
    a: float = 1.0

    def hexa(self) -> str:
        canaux = "".join(f"{round(c):02x}" for c in (self.r, self.g, self.b))
        return f"#{canaux}" if self.a >= 1.0 else f"#{canaux}{round(self.a * 255):02x}"


@dataclass
class Paire:
    """Une paire texte / fond à juger, avec son origine."""

    libelle: str
    texte_brut: str
    fond_brut: str
    grand: bool = False
    origine: str = "en ligne"


@dataclass
class Collecte:
    """Paires trouvées et règles écartées lors de la lecture des entrées."""

    paires: list[Paire] = field(default_factory=list)
    fichiers: list[str] = field(default_factory=list)
    ecartees: list[str] = field(default_factory=list)


def charger_noms() -> MappingProxyType:
    """Table immuable des 148 noms de couleurs CSS (CSS Color Module niveau 4)."""
    jetons = NOMS_CSS_BRUTS.split()
    return MappingProxyType({jetons[i]: jetons[i + 1] for i in range(0, len(jetons), 2)})


NOMS_CSS = charger_noms()


def canal_nombre(jeton: str, maximum: float) -> float:
    """Valeur d'un canal : nombre (0..maximum) ou pourcentage, bornée."""
    jeton = jeton.strip().lower()
    if jeton == "none":
        return 0.0
    if jeton.endswith("%"):
        return min(max(float(jeton[:-1]) / 100.0 * maximum, 0.0), maximum)
    return min(max(float(jeton), 0.0), maximum)


def angle_en_degres(jeton: str) -> float:
    """Teinte CSS en degrés : nombre nu, deg, grad, rad ou turn."""
    jeton = jeton.strip().lower()
    for unite, facteur in (("deg", 1.0), ("grad", 0.9), ("rad", 180.0 / math.pi), ("turn", 360.0)):
        if jeton.endswith(unite):
            return float(jeton[: -len(unite)]) * facteur
    return 0.0 if jeton == "none" else float(jeton)


def arguments_fonction(contenu: str) -> tuple[list[str], str | None]:
    """Sépare les arguments d'une fonction couleur, syntaxe à virgules ou moderne (« / alpha »)."""
    alpha = None
    if "/" in contenu:
        contenu, alpha = (morceau.strip() for morceau in contenu.split("/", 1))
    parties = [p for p in re.split(r"\s*,\s*|\s+", contenu.strip()) if p]
    if alpha is None and len(parties) == 4:
        alpha = parties.pop()
    if len(parties) != 3:
        raise ValueError(f"trois composantes attendues, {len(parties)} trouvées")
    return parties, alpha


def analyser_fonction(nom: str, contenu: str) -> Couleur:
    """rgb()/rgba()/hsl()/hsla() vers Couleur ; colorsys convertit le TSL."""
    parties, alpha_texte = arguments_fonction(contenu)
    alpha = canal_nombre(alpha_texte, 1.0) if alpha_texte is not None else 1.0
    if nom.startswith("rgb"):
        r, g, b = (canal_nombre(p, 255.0) for p in parties)
        return Couleur(r, g, b, alpha)
    teinte = (angle_en_degres(parties[0]) % 360.0) / 360.0
    saturation = canal_nombre(parties[1] if parties[1].endswith("%") else parties[1] + "%", 1.0)
    luminosite = canal_nombre(parties[2] if parties[2].endswith("%") else parties[2] + "%", 1.0)
    r, g, b = colorsys.hls_to_rgb(teinte, luminosite, saturation)
    return Couleur(r * 255.0, g * 255.0, b * 255.0, alpha)


def analyser_couleur(brute: str) -> Couleur:
    """Analyse une couleur CSS ; lève ValueError si elle n'est pas reconnue."""
    texte = brute.strip().removesuffix("!important").strip()
    minuscule = texte.lower()
    if minuscule == "transparent":
        return Couleur(0.0, 0.0, 0.0, 0.0)
    if minuscule in NOMS_CSS:
        texte = "#" + NOMS_CSS[minuscule]
    trouve = MOTIF_HEX.match(texte)
    if trouve:
        chiffres = trouve.group(1)
        if len(chiffres) in (3, 4):
            chiffres = "".join(c * 2 for c in chiffres)
        valeurs = [int(chiffres[i:i + 2], 16) for i in range(0, len(chiffres), 2)]
        return Couleur(*map(float, valeurs[:3]), valeurs[3] / 255.0 if len(valeurs) == 4 else 1.0)
    fonction = MOTIF_FONCTION.match(texte)
    if fonction:
        return analyser_fonction(fonction.group(1).lower(), fonction.group(2))
    raise ValueError(f"couleur non reconnue : « {brute} »")


def est_couleur(texte: str) -> bool:
    """Vrai si le texte s'analyse comme une couleur CSS."""
    try:
        analyser_couleur(texte)
    except ValueError:
        return False
    return True


def composer(dessus: Couleur, dessous: Couleur) -> Couleur:
    """Composition alpha « source over » dans l'espace sRGB (comme les navigateurs)."""
    a = dessus.a + dessous.a * (1.0 - dessus.a)
    if a == 0.0:
        return Couleur(0.0, 0.0, 0.0, 0.0)
    mix = [(c1 * dessus.a + c2 * dessous.a * (1.0 - dessus.a)) / a
           for c1, c2 in ((dessus.r, dessous.r), (dessus.g, dessous.g), (dessus.b, dessous.b))]
    return Couleur(*mix, a)


def luminance(couleur: Couleur) -> float:
    """Luminance relative WCAG 2.x d'une couleur opaque."""
    def lineaire(canal: float) -> float:
        c = canal / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lineaire(couleur.r) + 0.7152 * lineaire(couleur.g) + 0.0722 * lineaire(couleur.b)


def rapport_contraste(c1: Couleur, c2: Couleur) -> float:
    """Rapport de contraste (L clair + 0,05) / (L sombre + 0,05), de 1 à 21."""
    l1, l2 = luminance(c1), luminance(c2)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def arrondir_8_bits(couleur: Couleur) -> Couleur:
    """Ramène une couleur opaque aux 256 niveaux par canal qu'affiche l'écran."""
    return Couleur(*(float(round(c)) for c in (couleur.r, couleur.g, couleur.b)))


def avec_luminosite(couleur: Couleur, luminosite: float) -> Couleur:
    """Même teinte et saturation TSL (colorsys), autre luminosité."""
    teinte, _, saturation = colorsys.rgb_to_hls(couleur.r / 255.0, couleur.g / 255.0, couleur.b / 255.0)
    r, g, b = colorsys.hls_to_rgb(teinte, min(max(luminosite, 0.0), 1.0), saturation)
    return arrondir_8_bits(Couleur(r * 255.0, g * 255.0, b * 255.0))


def chercher_luminosite(variable: Couleur, fixe: Couleur, seuil: float, vers_le_haut: bool) -> Couleur | None:
    """Luminosité la plus proche, dans un sens, qui atteint le seuil ; None si impossible."""
    _, depart, _ = colorsys.rgb_to_hls(variable.r / 255.0, variable.g / 255.0, variable.b / 255.0)
    borne = 1.0 if vers_le_haut else 0.0
    if rapport_contraste(avec_luminosite(variable, borne), fixe) < seuil:
        return None
    proche, loin = depart, borne
    for _ in range(40):
        milieu = (proche + loin) / 2.0
        candidate = avec_luminosite(variable, milieu)
        clair = luminance(candidate) > luminance(fixe)
        if rapport_contraste(candidate, fixe) >= seuil and clair == vers_le_haut:
            loin = milieu
        else:
            proche = milieu
    resultat = avec_luminosite(variable, loin)
    return resultat if rapport_contraste(resultat, fixe) >= seuil else None


def proposer(variable: Couleur, fixe: Couleur, seuil: float) -> dict[str, Any] | None:
    """Couleur conforme la plus proche en luminosité (vers le clair ou le sombre)."""
    _, depart, _ = colorsys.rgb_to_hls(variable.r / 255.0, variable.g / 255.0, variable.b / 255.0)
    candidates = []
    for vers_le_haut in (True, False):
        trouvee = chercher_luminosite(variable, fixe, seuil, vers_le_haut)
        if trouvee is not None:
            _, arrivee, _ = colorsys.rgb_to_hls(trouvee.r / 255.0, trouvee.g / 255.0, trouvee.b / 255.0)
            candidates.append((abs(arrivee - depart), trouvee, "plus clair" if vers_le_haut else "plus sombre"))
    if not candidates:
        return None
    ecart, meilleure, sens = min(candidates, key=lambda c: c[0])
    return {"couleur": meilleure.hexa(), "ratio": rapport_contraste(meilleure, fixe),
            "sens": sens, "ecart_luminosite_tsl": round(ecart, 4)}


def resoudre_var(valeur: str, proprietes: dict[str, str], profondeur: int = 0) -> str:
    """Remplace var(--x, repli) par la valeur de la propriété personnalisée (ou le repli)."""
    if profondeur > 10 or "var(" not in valeur:
        return valeur
    def remplacer(trouve: re.Match[str]) -> str:
        nom, repli = trouve.group(1), trouve.group(2)
        if nom in proprietes:
            return proprietes[nom]
        if repli is not None:
            return repli.strip()
        raise ValueError(f"{nom} non définie dans le fichier")
    return resoudre_var(MOTIF_VAR.sub(remplacer, valeur), proprietes, profondeur + 1)


def couper_hors_parentheses(texte: str, separateur: str) -> list[str]:
    """Découpe sur un séparateur hors parenthèses et hors chaînes."""
    morceaux, courant, profondeur, guillemet = [], [], 0, ""
    for caractere in texte:
        if guillemet:
            guillemet = "" if caractere == guillemet else guillemet
        elif caractere in "\"'":
            guillemet = caractere
        elif caractere == "(":
            profondeur += 1
        elif caractere == ")":
            profondeur = max(profondeur - 1, 0)
        elif caractere == separateur and profondeur == 0:
            morceaux.append("".join(courant))
            courant = []
            continue
        courant.append(caractere)
    morceaux.append("".join(courant))
    return [m for m in morceaux if m.strip()]


def couleur_de_fond(valeur: str) -> str | None:
    """La couleur d'une déclaration background (raccourci) ou background-color."""
    if re.search(r"gradient\(|url\(", valeur, re.IGNORECASE):
        return None
    for jeton in reversed(couper_hors_parentheses(valeur.strip(), " ")):
        if est_couleur(jeton):
            return jeton.strip()
    return None


def taille_en_px(valeur: str) -> float | None:
    """Taille de police en px depuis px, pt, em ou rem ; None si inconnue (mots-clés, %)."""
    trouve = re.fullmatch(r"\s*([\d.]+)\s*(px|pt|em|rem)\s*(?:!important)?\s*", valeur.lower())
    if not trouve:
        return None
    nombre, unite = float(trouve.group(1)), trouve.group(2)
    return nombre * {"px": 1.0, "pt": PX_PAR_PT, "em": PX_PAR_EM, "rem": PX_PAR_EM}[unite]


def est_grand_texte(declarations: dict[str, str]) -> bool:
    """Grand texte au sens WCAG : ≥ 24 px, ou ≥ 18,66 px en gras."""
    taille = taille_en_px(declarations.get("font-size", ""))
    if taille is None:
        return False
    poids = declarations.get("font-weight", "").strip().lower()
    gras = poids in ("bold", "bolder") or (poids.isdigit() and int(poids) >= 700)
    return taille >= GRAND_TEXTE_PX or (gras and taille >= GRAND_TEXTE_GRAS_PX)


def iterer_regles(css: str, contexte: str = "") -> Iterator[tuple[str, str]]:
    """Parcourt les règles (sélecteur, déclarations), en descendant dans @media et @supports."""
    position = 0
    while True:
        ouverture = css.find("{", position)
        if ouverture < 0:
            return
        entete = css[position:ouverture].strip().split(";")[-1].strip()
        fermeture, profondeur = ouverture, 0
        for fermeture in range(ouverture, len(css)):
            profondeur += {"{": 1, "}": -1}.get(css[fermeture], 0)
            if profondeur == 0:
                break
        corps = css[ouverture + 1:fermeture]
        if entete.startswith(("@media", "@supports", "@layer", "@container")):
            yield from iterer_regles(corps, (contexte + " " + entete).strip())
        elif not entete.startswith("@"):
            yield ((contexte + " " + entete).strip(), corps)
        position = fermeture + 1


def declarations_de(corps: str) -> dict[str, str]:
    """Déclarations d'une règle : propriété en minuscules → valeur (la dernière l'emporte)."""
    resultat = {}
    for declaration in couper_hors_parentheses(corps, ";"):
        if ":" in declaration:
            propriete, valeur = declaration.split(":", 1)
            resultat[propriete.strip().lower() if not propriete.strip().startswith("--") else propriete.strip()] = valeur.strip()
    return resultat


def paires_du_css(css: str, origine: str, fond_defaut: str | None, collecte: Collecte) -> None:
    """Extrait les paires color / fond de chaque règle d'une feuille CSS."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    regles = [(selecteur, declarations_de(corps)) for selecteur, corps in iterer_regles(css)]
    proprietes = {cle: valeur for _, decl in regles for cle, valeur in decl.items() if cle.startswith("--")}
    for selecteur, decl in regles:
        if "color" not in decl and "background-color" not in decl and "background" not in decl:
            continue
        libelle = f"{origine} : {' '.join(selecteur.split())[:80]}"
        try:
            texte = resoudre_var(decl["color"], proprietes) if "color" in decl else None
            fond_brut = decl.get("background-color") or decl.get("background")
            fond = couleur_de_fond(resoudre_var(fond_brut, proprietes)) if fond_brut else fond_defaut
        except ValueError as exc:
            collecte.ecartees.append(f"{libelle} : {exc}")
            continue
        if texte is None or fond is None or not est_couleur(texte):
            raison = "pas de color" if texte is None else ("fond inconnu (dégradé, image, absent)" if fond is None
                                                           else f"color non évaluable « {texte} »")
            collecte.ecartees.append(f"{libelle} : {raison}")
            continue
        collecte.paires.append(Paire(libelle, texte, fond, est_grand_texte(decl), origine))


def lire_css(chemin: Path, octets_max: int) -> str:
    """Lit une feuille CSS UTF-8 bornée ; refuse binaire et encodage invalide."""
    taille = chemin.stat().st_size
    if taille > octets_max:
        raise ErreurEntree(f"{chemin} pèse {taille} octets, au-delà de --max-octets {octets_max}")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas une feuille CSS")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en UTF-8 (octet {exc.start}) : {exc.reason}") from exc


def fichiers_css(chemin: Path) -> list[Path]:
    """Le fichier lui-même, ou les .css d'un dossier (récursif, borné)."""
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    if chemin.is_file():
        return [chemin]
    trouves = sorted(p for p in chemin.rglob("*.css") if p.is_file())
    if len(trouves) > FICHIERS_MAX:
        raise ErreurEntree(f"{len(trouves)} fichiers .css sous {chemin}, au-delà de {FICHIERS_MAX}")
    return trouves


def collecter(args: argparse.Namespace) -> Collecte:
    """Range les entrées : couleurs consécutives par paires, chemins vers des feuilles CSS."""
    collecte, couleurs = Collecte(), []
    for entree in args.entrees:
        if est_couleur(entree):
            couleurs.append(entree)
            continue
        chemin = Path(entree) if Path(entree).is_absolute() or args.racine is None else args.racine / entree
        if not chemin.exists() and not re.search(r"[/\\]|\.css$", entree):
            raise ErreurEntree(f"« {entree} » n'est ni une couleur CSS ni un fichier existant")
        for fichier in fichiers_css(chemin):
            collecte.fichiers.append(str(fichier))
            paires_du_css(lire_css(fichier, args.max_octets), str(fichier), args.fond_defaut, collecte)
    if len(couleurs) % 2:
        raise ErreurEntree(f"{len(couleurs)} couleurs en ligne : il faut des paires TEXTE FOND")
    for i in range(0, len(couleurs), 2):
        collecte.paires.append(Paire(f"{couleurs[i]} sur {couleurs[i + 1]}", couleurs[i], couleurs[i + 1], args.grand_texte))
    return collecte


def analyse_coloraide(brute: str) -> str | None:
    """Hexadécimal 8 bits de la couleur selon coloraide, None si coloraide la refuse."""
    try:
        couleur = coloraide.Color(brute.strip().removesuffix("!important").strip()).convert("srgb")
    except (ValueError, TypeError, KeyError) as exc:
        return f"refusée par coloraide : {exc}"
    canaux = [min(max(c, 0.0), 1.0) * 255.0 for c in couleur.coords()]
    return Couleur(*canaux).hexa()


def controles_croises(texte: Couleur, fond: Couleur, ratio: float, paire: Paire,
                      actifs: Sequence[str]) -> dict[str, Any]:
    """Recalcule le rapport (et l'analyse des couleurs opaques) avec les bibliothèques actives."""
    controles: dict[str, Any] = {}
    if "wcag-contrast-ratio" in actifs:
        autre = wcag_contrast_ratio.rgb(tuple(c / 255.0 for c in (texte.r, texte.g, texte.b)),
                                        tuple(c / 255.0 for c in (fond.r, fond.g, fond.b)))
        controles["wcag-contrast-ratio"] = {"ratio": autre, "ecart": abs(autre - ratio)}
    if "coloraide" in actifs:
        autre = coloraide.Color(texte.hexa()).contrast(coloraide.Color(fond.hexa()), method="wcag21")
        analyses = {}
        for role, brute in (("texte", paire.texte_brut), ("fond", paire.fond_brut)):
            propre = analyser_couleur(brute)
            if propre.a >= 1.0:
                analyses[role] = {"stdlib": arrondir_8_bits(propre).hexa(), "coloraide": analyse_coloraide(brute)}
        controles["coloraide"] = {"ratio": autre, "ecart": abs(autre - ratio), "analyses": analyses,
                                  "analyses_concordantes": all(a["stdlib"] == a["coloraide"] for a in analyses.values())}
    return controles


def juger(paire: Paire, args: argparse.Namespace, page: Couleur, actifs: Sequence[str]) -> dict[str, Any]:
    """Juge une paire : composition, rapport, seuils, propositions, contrôles croisés."""
    fond = arrondir_8_bits(composer(analyser_couleur(paire.fond_brut), page))
    texte = arrondir_8_bits(composer(analyser_couleur(paire.texte_brut), fond))
    ratio = rapport_contraste(texte, fond)
    seuil = SEUIL_NON_TEXTE if args.non_texte else SEUILS[(args.niveau, paire.grand)]
    resultat = {
        "libelle": paire.libelle, "texte": paire.texte_brut, "fond": paire.fond_brut,
        "texte_opaque": texte.hexa(), "fond_opaque": fond.hexa(), "ratio": ratio,
        "ratio_affiche": f"{math.floor(ratio * 1000) / 1000:.3f}:1", "grand_texte": paire.grand,
        "aa_normal": ratio >= SEUILS[("AA", False)], "aa_grand": ratio >= SEUILS[("AA", True)],
        "aaa_normal": ratio >= SEUILS[("AAA", False)], "aaa_grand": ratio >= SEUILS[("AAA", True)],
        "non_texte": ratio >= SEUIL_NON_TEXTE, "seuil_exige": seuil, "conforme": ratio >= seuil,
    }
    if not resultat["conforme"]:
        resultat["proposition_texte"] = proposer(texte, fond, seuil)
        resultat["proposition_fond"] = proposer(fond, texte, seuil)
    controles = controles_croises(texte, fond, ratio, paire, actifs)
    if controles:
        resultat["controles"] = controles
    return resultat


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
        description="Vérifie le contraste " + SIGLE_WCAG + " 2.x de paires texte/fond, données en ligne "
                    "(TEXTE FOND ...) ou lues dans des feuilles CSS. Code 1 si une paire échoue.",
        epilog='exemple : verifier_contraste.py "#777" "#fff" "rgb(0 0 0 / 60%)" white --json   '
               "| verifier_contraste.py styles/ --niveau AAA --fond-defaut \"#ffffff\"",
    )
    parseur.add_argument("entrees", nargs="+", metavar="COULEUR_OU_FICHIER",
                         help="couleurs par paires TEXTE FOND, ou fichiers/dossiers .css")
    parseur.add_argument("--niveau", choices=("AA", "AAA"), default="AA", help="niveau exigé (défaut AA)")
    parseur.add_argument("--grand-texte", action="store_true",
                         help="les paires en ligne sont du grand texte (≥ 24 px, ou ≥ 18,66 px gras)")
    parseur.add_argument("--non-texte", action="store_true", help="juger au seuil 3:1 des composants non textuels")
    parseur.add_argument("--fond-defaut", metavar="COULEUR",
                         help="fond supposé des règles CSS qui ne déclarent que color (défaut : règle écartée)")
    parseur.add_argument("--fond-page", metavar="COULEUR", default="#ffffff",
                         help="couleur de page sous un fond semi-transparent (défaut #ffffff)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale par fichier CSS")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : recalcule avec wcag-contrast-ratio et coloraide s'ils sont installés")
    parseur.add_argument("--racine", type=Path, default=None, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Une ligne par paire, et la proposition si elle échoue."""
    for r in rapport["paires"]:
        etat = "OK  " if r["conforme"] else "ÉCHEC"
        print(f"{etat} {r['ratio_affiche']:>8}  {r['libelle']}  (seuil {r['seuil_exige']}:1)")
        for cle, quoi in (("proposition_texte", "texte"), ("proposition_fond", "fond")):
            proposition = r.get(cle)
            if cle in r:
                print(f"       {quoi} → {proposition['couleur']} ({proposition['ratio']:.2f}:1, {proposition['sens']})"
                      if proposition else f"       {quoi} → aucune luminosité de cette teinte n'atteint le seuil")
    if rapport["regles_ecartees"]:
        print(f"{len(rapport['regles_ecartees'])} règle(s) CSS écartée(s) (voir --json)")
    print(f"{rapport['echecs']} échec(s) sur {rapport['denominateur']} paire(s), niveau {rapport['niveau']}")


def moteurs_actifs(moteur: str) -> list[str]:
    """Bibliothèques de contrôle utilisées ; annonce sur stderr celles qui manquent."""
    if moteur == "stdlib":
        return []
    actifs = [nom for nom, module in (("wcag-contrast-ratio", wcag_contrast_ratio), ("coloraide", coloraide)) if module]
    manquants = [nom for nom, module in (("wcag-contrast-ratio", wcag_contrast_ratio), ("coloraide", coloraide)) if not module]
    if manquants:
        print(f"verifier_contraste : {' et '.join(manquants)} absent(s) — repli stdlib "
              f"(colorsys + formule WCAG), {'sans' if not actifs else 'avec un seul'} contrôle croisé", file=sys.stderr)
    return actifs


def evaluer(args: argparse.Namespace, actifs: Sequence[str]) -> dict[str, Any]:
    """Collecte, juge chaque paire, assemble le rapport."""
    collecte = collecter(args)
    if args.fond_defaut is not None and not est_couleur(args.fond_defaut):
        raise ErreurEntree(f"--fond-defaut « {args.fond_defaut} » n'est pas une couleur")
    page = composer(analyser_couleur(args.fond_page), Couleur(255.0, 255.0, 255.0))
    jugements = [juger(p, args, page, actifs) for p in collecte.paires]
    return {
        "outil": "verifier_contraste", "moteur": "+".join(actifs) or "stdlib",
        "methode": f"{SIGLE_WCAG} 2.x, rapport de luminances relatives ; {SIGLE_APCA} non calculé",
        "denominateur": len(jugements), "unite_denominateur": "paires",
        "examines": [j["libelle"] for j in jugements[:EXAMINES_MAX]], "examines_tronques": len(jugements) > EXAMINES_MAX,
        "fichiers": collecte.fichiers, "niveau": args.niveau, "paires": jugements,
        "echecs": sum(1 for j in jugements if not j["conforme"]),
        "regles_ecartees": collecte.ecartees, "contrat": extraire_contrat(__doc__ or ""),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : juge les paires, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    actifs = moteurs_actifs(args.moteur)
    try:
        if not est_couleur(args.fond_page) or args.max_octets < 1:
            raise ErreurEntree("--fond-page doit être une couleur et --max-octets positif")
        rapport = evaluer(args, actifs)
    except (ErreurEntree, ValueError, OSError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"verifier_contraste : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "verifier_contraste", "moteur": "+".join(actifs) or "stdlib",
                              "denominateur": 0, "examines": [], "refus": str(exc)},
                             ensure_ascii=False))
        return code
    if rapport["denominateur"] == 0:
        print("verifier_contraste : dénominateur nul : aucune paire texte/fond trouvée, rien à examiner", file=sys.stderr)
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    elif rapport["denominateur"]:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        return CODE_RIEN
    return CODE_ECHEC if rapport["echecs"] else CODE_OK


if __name__ == "__main__":
    raise SystemExit(main())
