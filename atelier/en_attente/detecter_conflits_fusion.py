r"""Cherche les marqueurs de conflit de fusion oubliés et les résolutions bâclées dans un arbre.

Mesuré le 2026-10-02 dans un dépôt de test : après une fusion en conflit (style diff3),
`git add code.py` puis `git commit` acceptent le fichier avec ses 4 lignes de marqueurs (code
de sortie 0) ; seul `git diff --check` entre ce commit et son parent les signale ensuite, et
seulement dans le diff d'un commit, jamais dans un arbre ou une archive livrés.

QUESTION
    Reste-t-il des marqueurs de conflit de fusion ou des résolutions bâclées dans cet arbre ?
MESURE
    Lecture en flux, ligne à ligne, de chaque fichier texte (un fichier dont les 8000 premiers
    octets contiennent un octet nul est binaire et ignoré, comme le fait git). Un marqueur est
    une ligne qui COMMENCE par exactement 7 chevrons ouvrants, 7 barres (base diff3), 7 signes
    égal ou 7 chevrons fermants (taille réglable par --taille, comme l'attribut
    conflict-marker-size), suivis d'une fin de ligne, ou d'un blanc et d'une étiquette (le
    séparateur ne porte pas d'étiquette). Un automate suit chaque bloc : ouverture, base
    facultative, séparateur, fermeture. Rendu : blocs complets avec étiquettes et extrait des
    deux côtés (et de la base), blocs incomplets ou déséquilibrés (ouverture jamais fermée,
    fermeture sans ouverture, double séparateur), séparateurs isolés classés « titre probable »
    (ligne de texte au-dessus, dans un .md/.rst/.txt/.adoc, ou d'au plus 7 caractères comme un
    titre souligné RST) ou « isolé », et résidus d'outils de fusion (noms en fichier_BASE_123.ext
    et variantes local, remote, backup de git mergetool, .rej ; .orig en avertissement). Un
    fichier UTF-16 à BOM est décodé ; sans BOM, il est binaire.
HYPOTHÈSES
    Les fichiers sont du texte dont les marqueurs sont en début de ligne (UTF-8, Latin-1, fins
    de ligne Windows : indifférent ; UTF-16 : lu seulement s'il porte un BOM). Les dossiers .git, .hg, .svn ne
    sont pas parcourus ; les liens symboliques ne sont pas suivis.
LIMITES
    Une résolution bâclée SANS marqueur (les deux côtés gardés l'un après l'autre, code
    dupliqué) est invisible. Un séparateur isolé n'est pas un défaut (sauf --strict) : un
    conflit dont on a effacé l'ouverture et la fermeture mais gardé le séparateur n'est donc
    signalé qu'en avertissement. Les marqueurs indentés ou cités (« > » de courriel) ne sont
    pas des marqueurs pour git et ne sont pas signalés.
CONTRE-EXEMPLES
    Constaté : un README.md résolu à la hâte, où l'on a effacé l'ouverture et la fermeture
    mais gardé les deux paragraphes et le séparateur entre eux, est classé « titre probable »
    (Markdown en ferait un titre de niveau 1) et le code de sortie vaut 0. Un fichier UTF-16
    sans BOM qui contient un conflit complet est classé binaire : 0 conflit signalé.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Arbres de sources, documents et configurations avant commit, revue, construction ou
    livraison ; tout fichier texte où git a pu écrire un conflit.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
MOT_COMMENCE = "COMMENCE"
MOT_SANS = "SANS"
ENCODAGE_UTF8 = "UTF-8"
ENCODAGE_UTF16 = "UTF-16"

SIGNES = {"<": "ouverture", "|": "base", "=": "separateur", ">": "fermeture"}
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", ".bzr", "_darcs"})
EXTENSIONS_TITRES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc"})
MOTIF_RESIDU_OUTIL = re.compile(r"_(BASE|LOCAL|REMOTE|BACKUP)_\d+(\.[^/]*)?$")
TAILLE_SONDE_BINAIRE = 8000
MAX_EXAMINES = 200
MAX_LIGNES_EXTRAIT = 5
MAX_CAR_LIGNE = 200


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ou rien à examiner (code 3)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Bloc:
    """Bloc de conflit en cours de lecture ou terminé."""

    debut: int
    etiquette_nous: str
    nous: list[str] = field(default_factory=list)
    base: list[str] | None = None
    eux: list[str] | None = None
    etiquette_base: str | None = None
    etiquette_eux: str | None = None
    fin: int | None = None
    zone: str = "nous"


@dataclass
class Bilan:
    """Constats d'un fichier."""

    chemin: str
    conflits: list[dict[str, Any]] = field(default_factory=list)
    defauts: list[dict[str, Any]] = field(default_factory=list)
    separateurs: list[dict[str, Any]] = field(default_factory=list)
    lignes: int = 0


# ---------------------------------------------------------------------------
# Reconnaissance des marqueurs
# ---------------------------------------------------------------------------

def construire_motif(taille: int) -> re.Pattern[str]:
    """Marqueur de taille exacte : signe ×taille, puis fin de ligne ou blanc + étiquette."""
    return re.compile(rf"^(?:(?P<signe>[<|>])(?P=signe){{{taille - 1}}}(?:[ \t](?P<etiquette>.*))?"
                      rf"|(?P<egal>={{{taille}}})[ \t]*)$")


def lire_marqueur(ligne: str, motif: re.Pattern[str]) -> tuple[str, str] | None:
    """(nature, étiquette) si la ligne est un marqueur, sinon None."""
    trouve = motif.match(ligne)
    if not trouve:
        return None
    if trouve.group("egal"):
        return "separateur", ""
    return SIGNES[trouve.group("signe")], (trouve.group("etiquette") or "").strip()


def extrait(lignes: list[str] | None) -> list[str] | None:
    """Premières lignes d'un côté, tronquées."""
    if lignes is None:
        return None
    return [l[:MAX_CAR_LIGNE] for l in lignes[:MAX_LIGNES_EXTRAIT]]


def decrire_bloc(bloc: Bloc) -> dict[str, Any]:
    """Bloc complet en JSON."""
    return {"debut": bloc.debut, "fin": bloc.fin, "nous": bloc.etiquette_nous,
            "eux": bloc.etiquette_eux, "base": bloc.etiquette_base, "diff3": bloc.base is not None,
            "lignes_nous": len(bloc.nous), "lignes_eux": len(bloc.eux or []),
            "extrait_nous": extrait(bloc.nous), "extrait_eux": extrait(bloc.eux),
            "extrait_base": extrait(bloc.base),
            "cotes_identiques": bloc.nous == (bloc.eux or [])}


def defaut(ligne: int, nature: str, detail: str) -> dict[str, Any]:
    """Un défaut de structure."""
    return {"ligne": ligne, "nature": nature, "detail": detail}


# ---------------------------------------------------------------------------
# Automate
# ---------------------------------------------------------------------------

def hors_bloc(bilan: Bilan, numero: int, nature: str, etiquette: str,
              precedente: str, est_doc: bool, taille: int) -> Bloc | None:
    """Marqueur rencontré hors de tout bloc."""
    if nature == "ouverture":
        return Bloc(debut=numero, etiquette_nous=etiquette)
    if nature == "separateur":
        texte = precedente.strip()
        titre = bool(texte) and not lire_marqueur_simple(precedente) and (
            est_doc or len(texte) <= taille)
        bilan.separateurs.append({"ligne": numero, "classement": "titre probable" if titre
                                  else "isolé", "ligne_precedente": precedente[:MAX_CAR_LIGNE]})
        return None
    bilan.defauts.append(defaut(numero, f"{nature} orpheline" if nature == "base"
                                else "fermeture sans ouverture", etiquette))
    return None


def lire_marqueur_simple(ligne: str) -> bool:
    """La ligne est-elle elle-même faite d'un seul caractère répété (soulignement) ?"""
    propre = ligne.strip()
    return bool(propre) and len(set(propre)) == 1


def dans_bloc(bilan: Bilan, bloc: Bloc, numero: int, nature: str, etiquette: str) -> Bloc | None:
    """Marqueur rencontré à l'intérieur d'un bloc ; rend le bloc encore ouvert (ou None)."""
    if nature == "ouverture":
        bilan.defauts.append(defaut(bloc.debut, "bloc non fermé",
                                    f"nouvelle ouverture ligne {numero} avant la fermeture"))
        return Bloc(debut=numero, etiquette_nous=etiquette)
    if nature == "base" and bloc.zone == "nous":
        bloc.base, bloc.etiquette_base, bloc.zone = [], etiquette, "base"
        return bloc
    if nature == "separateur" and bloc.zone in ("nous", "base"):
        bloc.eux, bloc.zone = [], "eux"
        return bloc
    if nature == "fermeture" and bloc.zone == "eux":
        bloc.fin, bloc.etiquette_eux = numero, etiquette
        bilan.conflits.append(decrire_bloc(bloc))
        return None
    probleme = {"base": "base hors de place", "separateur": "second séparateur",
                "fermeture": "fermeture sans séparateur"}[nature]
    bilan.defauts.append(defaut(numero, "bloc déséquilibré", f"{probleme} (bloc ouvert ligne "
                                                             f"{bloc.debut})"))
    return bloc if nature != "fermeture" else None


def ajouter_ligne(bloc: Bloc, ligne: str) -> None:
    """Range une ligne ordinaire dans le côté courant du bloc (borné)."""
    cote = {"nous": bloc.nous, "base": bloc.base, "eux": bloc.eux}[bloc.zone]
    if cote is not None and len(cote) < 10_000:
        cote.append(ligne)


def analyser_lignes(chemin: str, lignes: Iterator[str], motif: re.Pattern[str],
                    est_doc: bool, taille: int = 7) -> Bilan:
    """Fait passer toutes les lignes d'un fichier dans l'automate."""
    bilan = Bilan(chemin=chemin)
    bloc: Bloc | None = None
    precedente = ""
    for numero, ligne in enumerate(lignes, start=1):
        bilan.lignes = numero
        marqueur = lire_marqueur(ligne, motif)
        if marqueur is None:
            if bloc is not None:
                ajouter_ligne(bloc, ligne)
        elif bloc is None:
            bloc = hors_bloc(bilan, numero, marqueur[0], marqueur[1], precedente, est_doc, taille)
        else:
            bloc = dans_bloc(bilan, bloc, numero, marqueur[0], marqueur[1])
        precedente = ligne
    if bloc is not None:
        bilan.defauts.append(defaut(bloc.debut, "bloc non fermé", "fin de fichier atteinte "
                                                                  f"en zone « {bloc.zone} »"))
    return bilan


# ---------------------------------------------------------------------------
# Fichiers
# ---------------------------------------------------------------------------

def sonder(chemin: Path) -> str | None:
    """Encodage de lecture : utf-16 si BOM, utf-8 sinon ; None si binaire (octet nul, règle git)."""
    with chemin.open("rb") as flux:
        debut = flux.read(TAILLE_SONDE_BINAIRE)
    if debut.startswith((b"\xff\xfe", b"\xfe\xff")) and not debut.startswith(b"\xff\xfe\x00\x00"):
        return ENCODAGE_UTF16
    return None if b"\x00" in debut else ENCODAGE_UTF8


def lignes_de(chemin: Path, encodage: str) -> Iterator[str]:
    """Lignes du fichier, en flux, sans fin de ligne (CRLF compris)."""
    with chemin.open("r", encoding=encodage, errors="replace", newline="") as flux:
        for ligne in flux:
            yield ligne.rstrip("\r\n")


def parcourir(racine: Path, exclusions: list[str]) -> Iterator[Path]:
    """Fichiers réguliers de l'arbre, sans suivre les liens, sans dossiers de VCS."""
    for dossier, sous, fichiers in os.walk(racine, followlinks=False):
        sous[:] = sorted(d for d in sous if d not in DOSSIERS_IGNORES
                         and not exclu(Path(dossier, d), racine, exclusions))
        for nom in sorted(fichiers):
            chemin = Path(dossier, nom)
            if not chemin.is_symlink() and not exclu(chemin, racine, exclusions):
                yield chemin


def exclu(chemin: Path, racine: Path, exclusions: list[str]) -> bool:
    """Le chemin (relatif à la racine) correspond-il à un motif --exclure ?"""
    relatif = chemin.relative_to(racine).as_posix()
    return any(fnmatch.fnmatch(relatif, m) or fnmatch.fnmatch(chemin.name, m) for m in exclusions)


def classer_residu(chemin: Path) -> str | None:
    """Résidu d'outil de fusion (défaut) ou de sauvegarde (.orig : avertissement)."""
    if MOTIF_RESIDU_OUTIL.search(chemin.name) or chemin.suffix == ".rej":
        return "defaut"
    if chemin.suffix == ".orig":
        return "avertissement"
    return None


@dataclass
class Examen:
    """Ce qui a été examiné, ignoré, trouvé."""

    bilans: list[Bilan] = field(default_factory=list)
    binaires: list[str] = field(default_factory=list)
    illisibles: list[str] = field(default_factory=list)
    residus: list[dict[str, str]] = field(default_factory=list)


def examiner_fichier(chemin: Path, nom: str, motif: re.Pattern[str], examen: Examen,
                     taille: int) -> None:
    """Analyse un fichier, ou le range parmi les binaires / illisibles."""
    nature_residu = classer_residu(chemin)
    if nature_residu:
        examen.residus.append({"chemin": nom, "gravite": nature_residu})
    try:
        encodage = sonder(chemin)
        if encodage is None:
            examen.binaires.append(nom)
            return
        est_doc = chemin.suffix.lower() in EXTENSIONS_TITRES
        examen.bilans.append(analyser_lignes(nom, lignes_de(chemin, encodage), motif, est_doc,
                                             taille))
    except OSError as exc:
        examen.illisibles.append(f"{nom} : {exc.strerror}")


def examiner(cible: Path, taille: int, exclusions: list[str]) -> Examen:
    """Examine un fichier ou tout un arbre."""
    motif = construire_motif(taille)
    examen = Examen()
    if cible.is_file():
        examiner_fichier(cible, cible.name, motif, examen, taille)
        return examen
    for chemin in parcourir(cible, exclusions):
        examiner_fichier(chemin, chemin.relative_to(cible).as_posix(), motif, examen, taille)
    return examen


# ---------------------------------------------------------------------------
# Rapport
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


def assembler(cible: Path, examen: Examen, strict: bool) -> dict[str, Any]:
    """Rapport JSON."""
    touches = [b for b in examen.bilans if b.conflits or b.defauts or b.separateurs]
    isoles = sum(1 for b in examen.bilans for s in b.separateurs if s["classement"] == "isolé")
    return {
        "outil": "detecter_conflits_fusion", "moteur": "stdlib", "contrat": contrat(),
        "cible": str(cible), "denominateur": len(examen.bilans),
        "examines": [b.chemin for b in examen.bilans[:MAX_EXAMINES]],
        "examines_tronques": len(examen.bilans) > MAX_EXAMINES,
        "lignes_lues": sum(b.lignes for b in examen.bilans),
        "binaires_ignores": examen.binaires[:MAX_EXAMINES],
        "nombre_binaires_ignores": len(examen.binaires), "illisibles": examen.illisibles,
        "conflits": sum(len(b.conflits) for b in examen.bilans),
        "blocs_defectueux": sum(len(b.defauts) for b in examen.bilans),
        "separateurs_isoles": isoles, "strict": strict,
        "residus": examen.residus,
        "fichiers": [{"chemin": b.chemin, "conflits": b.conflits, "defauts": b.defauts,
                      "separateurs": b.separateurs} for b in touches],
    }


def code_de_sortie(rapport: dict[str, Any]) -> int:
    """1 si conflit, bloc défectueux, résidu (ou séparateur isolé avec --strict)."""
    if rapport["conflits"] or rapport["blocs_defectueux"] or rapport["illisibles"]:
        return 1
    if any(r["gravite"] == "defaut" for r in rapport["residus"]):
        return 1
    return 1 if rapport["strict"] and rapport["separateurs_isoles"] else 0


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    print(f"{rapport['denominateur']} fichier(s) texte examiné(s), {rapport['lignes_lues']} "
          f"ligne(s) ; {rapport['nombre_binaires_ignores']} binaire(s) ignoré(s)")
    print(f"Conflits : {rapport['conflits']} ; blocs défectueux : {rapport['blocs_defectueux']} ; "
          f"séparateurs isolés : {rapport['separateurs_isoles']}")
    for f in rapport["fichiers"]:
        for c in f["conflits"]:
            print(f"  CONFLIT {f['chemin']}:{c['debut']}-{c['fin']} ({c['nous']} / {c['eux']}"
                  f"{', diff3' if c['diff3'] else ''})")
            print(f"      nous : {' ⏎ '.join(c['extrait_nous'] or [])[:160]}")
            print(f"      eux  : {' ⏎ '.join(c['extrait_eux'] or [])[:160]}")
        for d in f["defauts"]:
            print(f"  DÉFAUT  {f['chemin']}:{d['ligne']} {d['nature']} — {d['detail']}")
        for s in f["separateurs"]:
            print(f"  séparateur {s['classement']} {f['chemin']}:{s['ligne']}")
    for r in rapport["residus"]:
        print(f"  RÉSIDU ({r['gravite']}) {r['chemin']}")
    for i in rapport["illisibles"]:
        print(f"  ILLISIBLE {i}")


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Détecte les marqueurs de conflit de fusion (y compris diff3) oubliés, les "
                    "blocs incomplets et les résidus d'outils de fusion dans un fichier ou un arbre.",
        epilog="Exemple : python outils/detecter_conflits_fusion.py . --exclure 'tests/*' --json")
    parseur.add_argument("chemin", help="fichier ou dossier à examiner")
    parseur.add_argument("--taille", type=int, default=7,
                         help="longueur des marqueurs (attribut conflict-marker-size ; défaut 7)")
    parseur.add_argument("--exclure", action="append", default=[],
                         help="motif glob de chemins à ignorer (répétable)")
    parseur.add_argument("--strict", action="store_true",
                         help="un séparateur isolé hors bloc compte aussi comme défaut")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def executer(args: argparse.Namespace) -> dict[str, Any]:
    """Valide l'entrée, examine, assemble."""
    if not 2 <= args.taille <= 100:
        raise ErreurEntree("--taille doit être comprise entre 2 et 100")
    cible = Path(args.chemin)
    if not cible.is_absolute() and args.racine:
        cible = Path(args.racine) / cible
    if not cible.exists():
        raise ErreurEntree(f"chemin introuvable : {cible}")
    if not (cible.is_file() or cible.is_dir()):
        raise ErreurEntree(f"{cible} n'est ni un fichier ni un dossier")
    examen = examiner(cible, args.taille, args.exclure)
    if not examen.bilans and not examen.residus:
        raise ErreurEntree(f"aucun fichier texte dans {cible} ({len(examen.binaires)} binaire(s) "
                           "ignoré(s)) : dénominateur nul, rien à examiner", code=3)
    return assembler(cible, examen, args.strict)


def main() -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args()
    try:
        rapport = executer(args)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "detecter_conflits_fusion", "moteur": "stdlib",
                              "denominateur": 0, "examines": [], "erreur": str(exc)},
                             ensure_ascii=False))
        return exc.code
    except OSError as exc:
        print(f"erreur : lecture impossible : {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return code_de_sortie(rapport)


__all__ = ["analyser_lignes", "construire_motif", "examiner", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
