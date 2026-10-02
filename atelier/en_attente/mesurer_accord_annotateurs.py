"""Accord entre annotateurs (ou juges LLM) au-delà du hasard : kappas de Cohen et de Fleiss, alpha de Krippendorff.

__POURQUOI__

QUESTION
    Les annotateurs (ou les juges LLM) sont-ils d'accord entre eux au-delà du
    hasard ?
MESURE
    Pour chaque paire d'annotateurs, sur les unités qu'ils ont notées tous les
    deux : pourcentage d'accord brut, matrice de confusion, kappa de Cohen non
    pondéré, pondéré linéaire et quadratique (poids sur le rang des catégories,
    comme scikit-learn). Pour l'ensemble : kappa de Fleiss (forme généralisée
    quand le nombre de notes varie d'une unité à l'autre, signalée), kappa de
    Light (moyenne des Cohen), accord brut moyen par unité, taux d'unanimité,
    et alpha de Krippendorff par matrice de coïncidences, aux niveaux nominal,
    ordinal, intervalle et ratio, avec données manquantes. Chaque coefficient
    reçoit une lecture conventionnelle avec sa source (Landis et Koch 1977 pour
    les kappas, Krippendorff 2004 pour alpha). Si krippendorff, scikit-learn
    ou statsmodels sont installés, alpha, les Cohen, les matrices et Fleiss
    sont recalculés par eux et l'écart maximal est publié.
HYPOTHÈSES
    Les annotateurs ont travaillé indépendamment, sur les mêmes consignes ; une
    valeur vide ou « na » est une absence de note, pas une catégorie ; pour les
    niveaux ordinal, intervalle et ratio, l'ordre des catégories est le bon
    (numérique si toutes les valeurs sont des nombres, sinon --ordre).
LIMITES
    Les paliers d'interprétation sont des conventions, pas des seuils de
    validité. Le kappa pondéré utilise le rang des catégories, pas leur valeur
    (1, 2, 10 sont à un pas l'une de l'autre) : pour des écarts réels, lire
    alpha au niveau intervalle. Sans --ordre, des étiquettes textuelles sont
    ordonnées lexicographiquement (avertissement publié). Les paires ne sont
    calculées que jusqu'à --max-annotateurs-paires annotateurs. Un annotateur
    qui note deux fois la même unité (format long) : la dernière note compte.
CONTRE-EXEMPLES
    __CONTRE_EXEMPLE__
INVOCATION
    {outil} --lignes "oui,oui,non;non,non,non;oui,oui,oui;non,oui,non;oui,oui,oui" --json
DOMAINE
    Jeux d'annotation (étiquettes, notes, échelles de Likert), évaluations par
    plusieurs juges humains ou LLM, de 2 annotateurs à quelques centaines, et
    jusqu'à quelques centaines de milliers d'unités.
"""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import io
import itertools
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
NOM_OUTIL = "mesurer_accord_annotateurs"
MOTEUR_STDLIB = "stdlib"

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

OCTETS_MAX_DEFAUT = 200 * 1024 * 1024
MAX_EXAMINES = 50
MAX_CATEGORIES_MATRICE = 30
MAX_CATEGORIES_QUADRATIQUE = 3000
MANQUANTS_DEFAUT = ",na,n/a,nan,null,none,-,?"
NOMS_ID = ("id", "item", "item_id", "unite", "unité", "unit", "unit_id", "exemple", "example", "cas")
NOMS_ANNOTATEUR = ("annotateur", "annotator", "juge", "judge", "rater", "codeur", "coder", "worker")
NOMS_ETIQUETTE = ("etiquette", "étiquette", "label", "note", "valeur", "value", "annotation", "classe")
NIVEAUX = ("nominal", "ordinal", "intervalle", "ratio")
PALIERS_LANDIS_KOCH = ((0.20, "léger"), (0.40, "passable"), (0.60, "modéré"), (0.80, "substantiel"))
SOURCE_LANDIS_KOCH = ("Landis J.R. et Koch G.G. (1977), The measurement of observer agreement for "
                      "categorical data, Biometrics 33(1)")
SOURCE_KRIPPENDORFF = ("Krippendorff K. (2004), Content Analysis: An Introduction to Its Methodology, "
                       "2e édition, Sage : alpha >= 0,800 fiable ; 0,667 à 0,800 conclusions provisoires")


class ErreurEntree(Exception):
    """Entrée refusée, avec le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Donnees:
    """Notes par unité : une table annotateur -> valeur par unité."""

    source: str
    unites: list[str] = field(default_factory=list)
    notes: list[dict[str, str]] = field(default_factory=list)
    annotateurs: list[str] = field(default_factory=list)
    anonymes: bool = False
    lignes_vides: int = 0
    doublons: int = 0


# --------------------------------------------------------------------------- lecture


def resoudre(chemin: str, racine: Path | None) -> Path:
    """Résout un chemin relatif contre --racine, ou le répertoire courant."""
    brut = Path(chemin)
    return brut if brut.is_absolute() or racine is None else racine / brut


def lire_texte_borne(chemin: Path, octets_max: int) -> str:
    """Lit un fichier texte utf-8 borné ; refuse absent, dossier, binaire, trop gros."""
    if not chemin.exists():
        raise ErreurEntree(f"fichier introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurEntree(f"{chemin} est un dossier : un fichier csv, tsv ou jsonl est attendu")
    with chemin.open("rb") as flux:
        donnees = flux.read(octets_max + 1)
    if len(donnees) > octets_max:
        raise ErreurEntree(f"{chemin} dépasse --max-octets {octets_max}")
    if b"\x00" in donnees:
        raise ErreurEntree(f"{chemin} contient des octets nuls : fichier binaire, pas un tableau d'annotations")
    try:
        return donnees.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas en utf-8 (octet {exc.start}) : {exc.reason}") from exc


def deviner_separateur(texte: str, impose: str | None) -> str:
    """Séparateur csv : imposé, sinon le plus fréquent de la première ligne."""
    if impose:
        return "\t" if impose in ("\\t", "tab") else impose
    entete = texte.split("\n", 1)[0]
    return max((",", ";", "\t", "|"), key=entete.count)


def enregistrements(texte: str, chemin: Path, separateur: str | None) -> list[dict[str, Any]]:
    """Enregistrements d'un fichier jsonl (objets) ou csv/tsv (avec en-tête)."""
    if chemin.suffix.casefold() in (".jsonl", ".ndjson"):
        resultat = []
        for numero, ligne in enumerate(texte.splitlines(), 1):
            if not ligne.strip():
                continue
            try:
                objet = json.loads(ligne)
            except json.JSONDecodeError as exc:
                raise ErreurEntree(f"{chemin.name} ligne {numero} : JSON invalide ({exc.msg})") from exc
            if not isinstance(objet, dict):
                raise ErreurEntree(f"{chemin.name} ligne {numero} : objet JSON attendu, {type(objet).__name__} trouvé")
            resultat.append(objet)
        return resultat
    sep = "\t" if chemin.suffix.casefold() == ".tsv" and separateur is None else deviner_separateur(texte, separateur)
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=sep)
    try:
        return [dict(ligne) for ligne in lecteur]
    except csv.Error as exc:
        raise ErreurEntree(f"{chemin.name} : csv illisible ligne {lecteur.line_num} : {exc}") from exc


def normaliser_valeur(valeur: Any, manquants: frozenset[str]) -> str | None:
    """Valeur textuelle d'une note, ou None si c'est une absence de note."""
    if valeur is None or isinstance(valeur, (dict, list)):
        return None
    if isinstance(valeur, bool):
        texte = "true" if valeur else "false"
    elif isinstance(valeur, float) and valeur.is_integer():
        texte = str(int(valeur))
    else:
        texte = str(valeur).strip()
    return None if texte.casefold() in manquants else texte


def trouver_colonne(colonnes: Sequence[str], impose: str | None, usuels: Sequence[str], role: str) -> str:
    """Colonne imposée, sinon la première dont le nom est usuel pour ce rôle."""
    if impose:
        if impose not in colonnes:
            raise ErreurEntree(f"colonne {impose!r} ({role}) absente ; colonnes : {', '.join(colonnes)}")
        return impose
    pliees = {c.casefold(): c for c in colonnes}
    for nom in usuels:
        if nom in pliees:
            return pliees[nom]
    raise ErreurEntree(f"colonne {role} introuvable parmi {', '.join(colonnes)} : la préciser")


def construire_large(lignes: list[dict[str, Any]], args: argparse.Namespace, source: str,
                     manquants: frozenset[str]) -> Donnees:
    """Format large : une ligne par unité, une colonne par annotateur."""
    colonnes = list(lignes[0].keys())
    colonne_id = args.id if args.id else next((c for c in colonnes if c.casefold() in NOMS_ID), None)
    if args.id and args.id not in colonnes:
        raise ErreurEntree(f"colonne --id {args.id!r} absente ; colonnes : {', '.join(colonnes)}")
    annotateurs = [a.strip() for a in args.annotateurs.split(",")] if args.annotateurs else [
        c for c in colonnes if c != colonne_id]
    for nom in annotateurs:
        if nom not in colonnes:
            raise ErreurEntree(f"annotateur {nom!r} absent des colonnes : {', '.join(colonnes)}")
    donnees = Donnees(source=source, annotateurs=annotateurs)
    for numero, ligne in enumerate(lignes, 1):
        notes = {a: v for a in annotateurs if (v := normaliser_valeur(ligne.get(a), manquants)) is not None}
        ajouter_unite(donnees, str(ligne.get(colonne_id, numero)) if colonne_id else f"ligne {numero}", notes)
    return donnees


def construire_long(lignes: list[dict[str, Any]], args: argparse.Namespace, source: str,
                    manquants: frozenset[str]) -> Donnees:
    """Format long : une ligne par (unité, annotateur, étiquette)."""
    colonnes = list(lignes[0].keys())
    col_item = trouver_colonne(colonnes, args.col_item, NOMS_ID, "unité")
    col_annot = trouver_colonne(colonnes, args.col_annotateur, NOMS_ANNOTATEUR, "annotateur")
    col_etiq = trouver_colonne(colonnes, args.col_etiquette, NOMS_ETIQUETTE, "étiquette")
    par_unite: dict[str, dict[str, str]] = {}
    donnees = Donnees(source=source)
    vus: dict[str, None] = {}
    for ligne in lignes:
        valeur = normaliser_valeur(ligne.get(col_etiq), manquants)
        unite, annotateur = str(ligne.get(col_item, "")), str(ligne.get(col_annot, ""))
        if valeur is None or not unite or not annotateur:
            donnees.lignes_vides += 1
            continue
        notes = par_unite.setdefault(unite, {})
        donnees.doublons += annotateur in notes
        notes[annotateur] = valeur
        vus[annotateur] = None
    donnees.annotateurs = list(vus)
    for unite, notes in par_unite.items():
        ajouter_unite(donnees, unite, notes)
    return donnees


def decouper_liste(valeur: Any) -> list[Any]:
    """Liste de notes anonymes : liste JSON, ou texte séparé par | ou ;."""
    if isinstance(valeur, list):
        return valeur
    if valeur is None:
        return []
    texte = str(valeur).strip()
    if texte.startswith("["):
        try:
            charge = json.loads(texte)
        except json.JSONDecodeError as exc:
            raise ErreurEntree(f"liste JSON invalide : {texte[:60]!r} ({exc.msg})") from exc
        return charge if isinstance(charge, list) else [charge]
    return texte.replace(";", "|").split("|")


def construire_liste(lignes: list[dict[str, Any]], args: argparse.Namespace, source: str,
                     manquants: frozenset[str]) -> Donnees:
    """Format liste : chaque unité porte une liste de notes d'annotateurs anonymes."""
    colonnes = list(lignes[0].keys())
    if args.liste not in colonnes:
        raise ErreurEntree(f"colonne --liste {args.liste!r} absente ; colonnes : {', '.join(colonnes)}")
    colonne_id = args.id if args.id in colonnes else next((c for c in colonnes if c.casefold() in NOMS_ID), None)
    donnees = Donnees(source=source, anonymes=True)
    largeur = 0
    for numero, ligne in enumerate(lignes, 1):
        valeurs = [v for v in (normaliser_valeur(x, manquants) for x in decouper_liste(ligne.get(args.liste)))
                   if v is not None]
        largeur = max(largeur, len(valeurs))
        notes = {f"r{i + 1}": v for i, v in enumerate(valeurs)}
        ajouter_unite(donnees, str(ligne.get(colonne_id, numero)) if colonne_id else f"ligne {numero}", notes)
    donnees.annotateurs = [f"r{i + 1}" for i in range(largeur)]
    return donnees


def ajouter_unite(donnees: Donnees, nom: str, notes: dict[str, str]) -> None:
    """Ajoute une unité ; une unité sans aucune note est comptée à part."""
    if not notes:
        donnees.lignes_vides += 1
        return
    donnees.unites.append(nom)
    donnees.notes.append(notes)


def lire_en_ligne(texte: str, manquants: frozenset[str]) -> Donnees:
    """--lignes : unités séparées par ';', annotateurs par ','."""
    blocs = [b for b in texte.split(";") if b.strip()]
    largeur = max((len(b.split(",")) for b in blocs), default=0)
    donnees = Donnees(source="--lignes", annotateurs=[f"annotateur_{i + 1}" for i in range(largeur)])
    for numero, bloc in enumerate(blocs, 1):
        valeurs = bloc.split(",")
        notes = {f"annotateur_{i + 1}": v for i, brut in enumerate(valeurs)
                 if (v := normaliser_valeur(brut, manquants)) is not None}
        ajouter_unite(donnees, f"unité {numero}", notes)
    return donnees


def charger(args: argparse.Namespace) -> Donnees:
    """Lit la source désignée par la ligne de commande."""
    manquants = frozenset(m.strip().casefold() for m in args.manquant.split(","))
    if args.lignes is not None:
        if args.fichier:
            raise ErreurEntree("--lignes et un fichier sont exclusifs")
        return lire_en_ligne(args.lignes, manquants)
    if not args.fichier:
        raise ErreurEntree("rien à lire : donner un fichier csv/tsv/jsonl ou --lignes")
    chemin = resoudre(args.fichier, args.racine)
    lignes = enregistrements(lire_texte_borne(chemin, args.max_octets), chemin, args.separateur)
    if not lignes:
        return Donnees(source=str(chemin))
    format_ = "liste" if args.liste else args.format
    constructeur = {"large": construire_large, "long": construire_long, "liste": construire_liste}[format_]
    if format_ == "liste" and not args.liste:
        raise ErreurEntree("--format liste exige --liste COLONNE")
    return constructeur(lignes, args, str(chemin), manquants)


# --------------------------------------------------------------------------- catégories


def en_nombre(texte: str) -> float | None:
    """Valeur numérique d'une étiquette, ou None."""
    try:
        valeur = float(texte.replace(",", ".")) if texte.count(",") <= 1 else math.nan
    except ValueError:
        return None
    return valeur if math.isfinite(valeur) else None


def ordonner_categories(donnees: Donnees, ordre: str | None) -> tuple[list[str], dict[str, float] | None, str]:
    """Catégories ordonnées, valeurs numériques éventuelles, et provenance de l'ordre."""
    observees = sorted({v for notes in donnees.notes for v in notes.values()})
    numeriques = {c: en_nombre(c) for c in observees}
    if ordre:
        imposees = [c.strip() for c in ordre.split(",") if c.strip()]
        absentes = [c for c in observees if c not in imposees]
        if absentes:
            raise ErreurEntree(f"--ordre ne contient pas {', '.join(absentes[:10])}")
        valeurs = {c: numeriques.get(c) for c in imposees}
        complet = all(v is not None for v in valeurs.values())
        return imposees, valeurs if complet else None, "--ordre"
    if all(v is not None for v in numeriques.values()):
        triees = sorted(observees, key=lambda c: (numeriques[c], c))
        return triees, numeriques, "numérique"
    return observees, None, "lexicographique (supposé : préciser --ordre)"


# --------------------------------------------------------------------------- coefficients


def lecture_kappa(valeur: float | None) -> str | None:
    """Palier conventionnel de Landis et Koch (1977)."""
    if valeur is None:
        return None
    if valeur < 0.0:
        return "moins qu'au hasard"
    for borne, libelle in PALIERS_LANDIS_KOCH:
        if valeur <= borne:
            return libelle
    return "presque parfait"


def lecture_alpha(valeur: float | None) -> str | None:
    """Lecture de Krippendorff (2004)."""
    if valeur is None:
        return None
    if valeur >= 0.800:
        return "fiable"
    return "conclusions provisoires" if valeur >= 0.667 else "insuffisant"


def matrice_confusion(paires: Sequence[tuple[str, str]], index: dict[str, int]) -> list[list[int]]:
    """Matrice de confusion (lignes : premier annotateur, colonnes : second)."""
    matrice = [[0] * len(index) for _ in index]
    for gauche, droite in paires:
        matrice[index[gauche]][index[droite]] += 1
    return matrice


def kappa_cohen(matrice: list[list[int]], poids: str) -> float | None:
    """Kappa de Cohen : non pondéré, linéaire ou quadratique (poids sur le rang)."""
    taille = len(matrice)
    total = sum(map(sum, matrice))
    lignes = [sum(rang) for rang in matrice]
    colonnes = [sum(matrice[i][j] for i in range(taille)) for j in range(taille)]
    distance: Callable[[int, int], float] = {
        "aucun": lambda i, j: float(i != j), "lineaire": lambda i, j: float(abs(i - j)),
        "quadratique": lambda i, j: float((i - j) ** 2)}[poids]
    observe = sum(distance(i, j) * matrice[i][j] for i in range(taille) for j in range(taille) if matrice[i][j])
    attendu = sum(distance(i, j) * lignes[i] * colonnes[j] for i in range(taille) if lignes[i]
                  for j in range(taille) if colonnes[j]) / total
    if attendu == 0.0:
        return None
    return 1.0 - observe / attendu


def comparer_paire(gauche: str, droite: str, donnees: Donnees, categories: list[str]) -> dict[str, Any] | None:
    """Cohen (trois pondérations), accord brut et matrice pour une paire d'annotateurs."""
    paires = [(n[gauche], n[droite]) for n in donnees.notes if gauche in n and droite in n]
    if not paires:
        return None
    index = {c: i for i, c in enumerate(categories)}
    matrice = matrice_confusion(paires, index)
    kappas = {poids: kappa_cohen(matrice, poids) for poids in ("aucun", "lineaire", "quadratique")}
    sortie = {"annotateurs": [gauche, droite], "unites_communes": len(paires),
              "accord_brut": sum(1 for a, b in paires if a == b) / len(paires),
              "kappa_cohen": kappas["aucun"], "kappa_lineaire": kappas["lineaire"],
              "kappa_quadratique": kappas["quadratique"], "lecture": lecture_kappa(kappas["aucun"])}
    if len(categories) <= MAX_CATEGORIES_MATRICE:
        sortie["matrice_confusion"] = matrice
    if kappas["aucun"] is None:
        sortie["note"] = "les deux annotateurs n'emploient qu'une même catégorie : kappa indéfini"
    return sortie


def kappa_fleiss(comptes: list[Counter[str]], categories: list[str]) -> dict[str, Any]:
    """Kappa de Fleiss sur les unités à au moins deux notes (forme généralisée si effectifs variables)."""
    retenues = [c for c in comptes if sum(c.values()) >= 2]
    effectifs = {sum(c.values()) for c in retenues}
    total = sum(sum(c.values()) for c in retenues)
    p_bar = sum(sum(n * (n - 1) for n in c.values()) / (sum(c.values()) * (sum(c.values()) - 1))
                for c in retenues) / len(retenues)
    proportions = {cat: sum(c[cat] for c in retenues) / total for cat in categories}
    p_e = sum(p * p for p in proportions.values())
    kappa = None if p_e >= 1.0 else (p_bar - p_e) / (1.0 - p_e)
    return {"kappa": kappa, "lecture": lecture_kappa(kappa), "accord_observe": p_bar, "accord_attendu": p_e,
            "unites": len(retenues), "effectifs_variables": len(effectifs) > 1,
            "notes_par_unite": sorted(effectifs) if len(effectifs) <= 10 else [min(effectifs), max(effectifs)]}


def coincidences(comptes: list[Counter[str]], index: dict[str, int]) -> tuple[dict[tuple[int, int], float], list[float]]:
    """Matrice de coïncidences de Krippendorff (creuse) et marges n_c."""
    o: dict[tuple[int, int], float] = {}
    marges = [0.0] * len(index)
    for compte in comptes:
        m = sum(compte.values())
        if m < 2:
            continue
        for c, nc in compte.items():
            for k, nk in compte.items():
                paires = nc * (nk - (c == k))
                if paires:
                    cle = (index[c], index[k])
                    o[cle] = o.get(cle, 0.0) + paires / (m - 1)
            marges[index[c]] += nc
    return o, marges


def delta_ordinal(marges: Sequence[float]) -> Callable[[int, int], float]:
    """Distance ordinale de Krippendorff, par sommes cumulées des marges."""
    cumul = list(itertools.accumulate(marges, initial=0.0))

    def delta(i: int, j: int) -> float:
        bas, haut = min(i, j), max(i, j)
        return (cumul[haut + 1] - cumul[bas] - (marges[i] + marges[j]) / 2.0) ** 2
    return delta


def attendu_ferme(niveau: str, marges: Sequence[float], valeurs: Sequence[float] | None) -> float | None:
    """Σ n_c n_k δ²_ck en forme fermée (nominal, intervalle), sinon None."""
    total = sum(marges)
    if niveau == "nominal":
        return total * total - sum(n * n for n in marges)
    if niveau == "intervalle" and valeurs is not None:
        somme = sum(n * v for n, v in zip(marges, valeurs))
        carres = sum(n * v * v for n, v in zip(marges, valeurs))
        return 2.0 * (total * carres - somme * somme)
    return None


def alpha_krippendorff(comptes: list[Counter[str]], categories: list[str], niveau: str,
                       numeriques: dict[str, float] | None) -> dict[str, Any]:
    """Alpha de Krippendorff au niveau demandé, avec données manquantes."""
    if niveau in ("intervalle", "ratio") and numeriques is None:
        return {"alpha": None, "note": "étiquettes non numériques : niveau non applicable"}
    index = {c: i for i, c in enumerate(categories)}
    o, marges = coincidences(comptes, index)
    valeurs = [numeriques[c] for c in categories] if numeriques is not None else None
    delta = distance_krippendorff(niveau, marges, valeurs)
    total = sum(marges)
    observe = sum(n * delta(i, j) for (i, j), n in o.items())
    attendu = attendu_ferme(niveau, marges, valeurs)
    if attendu is None:
        if len(categories) > MAX_CATEGORIES_QUADRATIQUE:
            return {"alpha": None, "note": f"plus de {MAX_CATEGORIES_QUADRATIQUE} valeurs distinctes : non calculé"}
        presentes = [i for i, n in enumerate(marges) if n]
        attendu = sum(marges[i] * marges[j] * delta(i, j) for i in presentes for j in presentes)
    if attendu <= 0.0 or total < 2:
        return {"alpha": None, "note": "une seule valeur pairable dans tout le jeu : alpha indéfini"}
    alpha = 1.0 - (total - 1.0) * observe / attendu
    return {"alpha": alpha, "lecture": lecture_alpha(alpha), "valeurs_pairables": int(total)}


def distance_krippendorff(niveau: str, marges: Sequence[float], valeurs: Sequence[float] | None) -> Callable[[int, int], float]:
    """δ² selon le niveau de mesure."""
    if niveau == "nominal":
        return lambda i, j: float(i != j)
    if niveau == "ordinal":
        return delta_ordinal(marges)
    assert valeurs is not None
    if niveau == "intervalle":
        return lambda i, j: (valeurs[i] - valeurs[j]) ** 2
    return lambda i, j: 0.0 if valeurs[i] + valeurs[j] == 0 else ((valeurs[i] - valeurs[j]) / (valeurs[i] + valeurs[j])) ** 2


def accords_globaux(comptes: list[Counter[str]]) -> dict[str, Any]:
    """Accord brut moyen par unité (paires concordantes) et taux d'unanimité."""
    retenues = [c for c in comptes if sum(c.values()) >= 2]
    par_unite = [sum(n * (n - 1) for n in c.values()) / (sum(c.values()) * (sum(c.values()) - 1)) for c in retenues]
    return {"accord_brut_moyen": sum(par_unite) / len(par_unite),
            "unanimite": sum(1 for c in retenues if len(c) == 1) / len(retenues)}


def analyser(donnees: Donnees, args: argparse.Namespace) -> dict[str, Any]:
    """Tous les coefficients, leurs lectures et leurs sources."""
    categories, numeriques, provenance = ordonner_categories(donnees, args.ordre)
    comptes = [Counter(notes.values()) for notes in donnees.notes]
    paires = calculer_paires(donnees, categories, args.max_annotateurs_paires)
    kappas = [p["kappa_cohen"] for p in paires if p["kappa_cohen"] is not None]
    rapport: dict[str, Any] = {
        "annotateurs": donnees.annotateurs, "annotateurs_anonymes": donnees.anonymes,
        "categories": categories, "ordre_categories": provenance,
        "repartition": dict(sum(comptes, Counter()).most_common()),
        "global": accords_globaux(comptes) | {"kappa_fleiss": kappa_fleiss(comptes, categories),
                                              "kappa_light": sum(kappas) / len(kappas) if kappas else None},
        "alpha_krippendorff": {niveau: alpha_krippendorff(comptes, categories, niveau, numeriques) for niveau in NIVEAUX},
        "paires": paires,
        "interpretation": {"kappas": SOURCE_LANDIS_KOCH, "alpha": SOURCE_KRIPPENDORFF,
                           "avertissement": "paliers conventionnels : des repères, pas des seuils de validité"},
    }
    rapport["decision"] = decider(rapport, args)
    rapport["avertissements"] = avertissements(donnees, rapport, args)
    return rapport


def calculer_paires(donnees: Donnees, categories: list[str], maximum: int) -> list[dict[str, Any]]:
    """Comparaisons deux à deux, si les annotateurs sont identifiés et pas trop nombreux."""
    if donnees.anonymes or len(donnees.annotateurs) > maximum:
        return []
    resultats = (comparer_paire(a, b, donnees, categories) for a, b in itertools.combinations(donnees.annotateurs, 2))
    return [r for r in resultats if r is not None]


def decider(rapport: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Coefficient principal comparé au seuil ; indéfini = fiabilité non démontrée."""
    if args.critere == "alpha":
        valeur, nom = rapport["alpha_krippendorff"][args.niveau_mesure]["alpha"], f"alpha {args.niveau_mesure}"
    elif len(rapport["annotateurs"]) == 2 and rapport["paires"]:
        valeur, nom = rapport["paires"][0]["kappa_cohen"], "kappa de Cohen"
    else:
        valeur, nom = rapport["global"]["kappa_fleiss"]["kappa"], "kappa de Fleiss"
    atteint = valeur is not None and valeur >= args.seuil
    return {"critere": nom, "valeur": valeur, "seuil": args.seuil, "atteint": atteint,
            "phrase": (f"{nom} = {valeur:.4f} " if valeur is not None else f"{nom} indéfini ")
            + ("≥" if atteint else "<" if valeur is not None else "—") + f" seuil {args.seuil} : "
            + ("accord démontré" if atteint else "accord non démontré")}


def avertissements(donnees: Donnees, rapport: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Situations qui fragilisent la lecture."""
    notes = []
    if rapport["ordre_categories"].startswith("lexico") and args.niveau_mesure == "ordinal":
        notes.append("niveau ordinal sur des étiquettes textuelles sans --ordre : ordre lexicographique supposé")
    if rapport["global"]["kappa_fleiss"]["effectifs_variables"]:
        notes.append("nombre de notes variable selon les unités : kappa de Fleiss en forme généralisée, préférer alpha")
    if donnees.doublons:
        notes.append(f"{donnees.doublons} note(s) en double (même annotateur, même unité) : la dernière a été gardée")
    if not donnees.anonymes and len(donnees.annotateurs) > args.max_annotateurs_paires:
        notes.append(f"{len(donnees.annotateurs)} annotateurs > --max-annotateurs-paires : kappas par paire omis")
    if len(rapport["categories"]) == 1:
        notes.append("une seule catégorie employée par tous : aucun coefficient n'est défini")
    return notes


# --------------------------------------------------------------------------- contrôle croisé


def importer_optionnels(moteur: str) -> dict[str, Any]:
    """Importe krippendorff, scikit-learn, statsmodels s'ils sont là ; une ligne sur stderr sinon."""
    modules: dict[str, Any] = {}
    if moteur == "stdlib":
        return modules
    absents = []
    try:
        import krippendorff
        import numpy
        modules["krippendorff"] = (version_distribution("krippendorff"), krippendorff, numpy)
    except ImportError:
        absents.append("krippendorff")
    try:
        import sklearn
        import sklearn.metrics as metriques
        modules["scikit-learn"] = (sklearn.__version__, metriques)
    except ImportError:
        absents.append("scikit-learn")
    try:
        import statsmodels
        import statsmodels.stats.inter_rater as inter_rater
        modules["statsmodels"] = (statsmodels.__version__, inter_rater)
    except ImportError:
        absents.append("statsmodels")
    if absents:
        print(f"{NOM_OUTIL} : {', '.join(absents)} absent(s) — repli stdlib seul, coefficients non "
              f"contre-vérifiés par {', '.join(absents)}", file=sys.stderr)
    return modules


def version_distribution(nom: str) -> str:
    """Version installée d'une distribution, lue dans ses métadonnées."""
    try:
        return importlib.metadata.version(nom)
    except importlib.metadata.PackageNotFoundError:
        return "inconnue"


def paires_krippendorff(module: tuple[Any, ...], donnees: Donnees, rapport: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Alpha recalculé par la bibliothèque krippendorff, à chaque niveau applicable."""
    _, krippendorff, numpy = module
    categories = rapport["categories"]
    index = {c: i for i, c in enumerate(categories)}
    sorties = []
    for niveau, nom_lib in (("nominal", "nominal"), ("ordinal", "ordinal"), ("intervalle", "interval"), ("ratio", "ratio")):
        notre = rapport["alpha_krippendorff"][niveau]["alpha"]
        if notre is None:
            continue
        numerique = niveau in ("intervalle", "ratio")
        matrice = [[(en_nombre(n[a]) if numerique else index[n[a]]) if a in n else numpy.nan for n in donnees.notes]
                   for a in donnees.annotateurs]
        domaine = None if numerique else list(range(len(categories)))
        theirs = krippendorff.alpha(reliability_data=numpy.array(matrice, dtype=float),
                                    level_of_measurement=nom_lib, value_domain=domaine)
        sorties.append((f"alpha.{niveau}", notre, float(theirs)))
    return sorties


def paires_sklearn(module: tuple[Any, ...], donnees: Donnees, rapport: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Cohen (trois pondérations) et matrices recalculés par scikit-learn."""
    metriques = module[1]
    categories = rapport["categories"]
    sorties = []
    for paire in rapport["paires"]:
        a, b = paire["annotateurs"]
        gauche = [n[a] for n in donnees.notes if a in n and b in n]
        droite = [n[b] for n in donnees.notes if a in n and b in n]
        for cle, poids in (("kappa_cohen", None), ("kappa_lineaire", "linear"), ("kappa_quadratique", "quadratic")):
            if paire[cle] is not None:
                theirs = metriques.cohen_kappa_score(gauche, droite, labels=categories, weights=poids)
                sorties.append((f"{a}~{b}.{cle}", paire[cle], float(theirs)))
        if "matrice_confusion" in paire:
            leur = metriques.confusion_matrix(gauche, droite, labels=categories).tolist()
            ecart = max(abs(x - y) for l1, l2 in zip(paire["matrice_confusion"], leur) for x, y in zip(l1, l2))
            sorties.append((f"{a}~{b}.matrice_confusion", 0.0, float(ecart)))
    return sorties


def paires_statsmodels(module: tuple[Any, ...], donnees: Donnees, rapport: dict[str, Any]) -> list[tuple[str, float, float]]:
    """Fleiss recalculé par statsmodels, seulement à effectif constant (sa définition)."""
    fleiss = rapport["global"]["kappa_fleiss"]
    if fleiss["effectifs_variables"] or fleiss["kappa"] is None:
        return []
    categories = rapport["categories"]
    table = [[Counter(n.values())[c] for c in categories] for n in donnees.notes if len(n) >= 2]
    return [("kappa_fleiss", fleiss["kappa"], float(module[1].fleiss_kappa(table, method="fleiss")))]


def controle_croise(modules: dict[str, Any], donnees: Donnees, rapport: dict[str, Any]) -> dict[str, Any]:
    """Écart maximal de chaque bibliothèque présente."""
    fonctions = {"krippendorff": paires_krippendorff, "scikit-learn": paires_sklearn, "statsmodels": paires_statsmodels}
    sortie: dict[str, Any] = {}
    for nom, module in modules.items():
        try:
            paires = fonctions[nom](module, donnees, rapport)
        except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
            sortie[nom] = {"version": module[0], "statut": "non comparable", "raison": f"{type(exc).__name__}: {exc}"}
            continue
        ecarts = [{"grandeur": g, "stdlib": x, nom: y, "ecart": abs(x - y)} for g, x, y in paires]
        sortie[nom] = {"version": module[0], "statut": "calculé" if ecarts else "aucune grandeur comparable",
                       "comparaisons": len(ecarts), "ecart_max": max((e["ecart"] for e in ecarts), default=None),
                       "details": ecarts}
    return sortie


# --------------------------------------------------------------------------- sortie


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring."""
    contrat: dict[str, str] = {}
    courant = ""
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def nettoyer_non_finis(objet: Any) -> Any:
    """Remplace NaN et infinis par None : le JSON strict les interdit."""
    if isinstance(objet, float) and not math.isfinite(objet):
        return None
    if isinstance(objet, dict):
        return {str(cle): nettoyer_non_finis(valeur) for cle, valeur in objet.items()}
    if isinstance(objet, list):
        return [nettoyer_non_finis(valeur) for valeur in objet]
    return objet


def base_rapport(donnees: Donnees | None, moteur: str) -> dict[str, Any]:
    """Champs communs à tout rapport JSON, y compris en refus."""
    pairables = [u for u, n in zip(donnees.unites, donnees.notes) if len(n) >= 2] if donnees else []
    return {"outil": NOM_OUTIL, "moteur": moteur, "moteur_calcul": MOTEUR_STDLIB,
            "denominateur": len(pairables), "unite_denominateur": "unités notées par au moins deux annotateurs",
            "examines": pairables[:MAX_EXAMINES], "examines_tronques": len(pairables) > MAX_EXAMINES,
            "source": donnees.source if donnees else None,
            "unites_lues": len(donnees.unites) if donnees else 0,
            "unites_a_une_seule_note": (len(donnees.unites) - len(pairables)) if donnees else 0,
            "lignes_sans_note": donnees.lignes_vides if donnees else 0,
            "contrat": extraire_contrat(__doc__ or "")}


def formater(valeur: float | None) -> str:
    """Nombre à quatre décimales, ou « indéfini »."""
    return "indéfini" if valeur is None else f"{valeur:.4f}"


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    print(f"{rapport['denominateur']} unités pairables, {len(rapport['annotateurs'])} annotateurs, "
          f"{len(rapport['categories'])} catégories (ordre {rapport['ordre_categories']})")
    g = rapport["global"]
    print(f"accord brut moyen {formater(g['accord_brut_moyen'])}, unanimité {formater(g['unanimite'])}")
    f = g["kappa_fleiss"]
    print(f"kappa de Fleiss {formater(f['kappa'])} ({f['lecture']}), kappa de Light {formater(g['kappa_light'])}")
    for niveau, a in rapport["alpha_krippendorff"].items():
        print(f"alpha {niveau:<10} {formater(a['alpha'])}" + (f" ({a['lecture']})" if a.get("lecture") else f" — {a.get('note', '')}"))
    for p in rapport["paires"]:
        print(f"  {p['annotateurs'][0]} ~ {p['annotateurs'][1]} : n={p['unites_communes']} brut {formater(p['accord_brut'])} "
              f"Cohen {formater(p['kappa_cohen'])} linéaire {formater(p['kappa_lineaire'])} "
              f"quadratique {formater(p['kappa_quadratique'])} ({p['lecture']})")
    for nom, c in rapport.get("controle_croise", {}).items():
        print(f"contrôle {nom} {c['version']} : {c['statut']}, écart max {c.get('ecart_max')}")
    print(rapport["decision"]["phrase"])
    for note in rapport["avertissements"]:
        print(f"  avertissement : {note}")


def presenter_json(rapport: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(nettoyer_non_finis(rapport), ensure_ascii=False, indent=2, allow_nan=False))


def construire_parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Accord entre annotateurs au-delà du hasard : kappa de Cohen (pondéré), kappa de Fleiss, "
                    "alpha de Krippendorff (nominal, ordinal, intervalle, ratio). Code 1 si le critère est sous le seuil.",
        epilog='exemples : mesurer_accord_annotateurs.py --lignes "a,a,b;b,b,b;a,a,a" --json | '
               "mesurer_accord_annotateurs.py notes.csv --id item --annotateurs alice,bob | "
               "mesurer_accord_annotateurs.py notes_long.jsonl --format long | "
               "mesurer_accord_annotateurs.py juges.jsonl --liste votes --niveau-mesure ordinal --ordre bas,moyen,haut",
    )
    parseur.add_argument("fichier", nargs="?", help="csv/tsv (en-tête) ou jsonl (un objet par ligne)")
    parseur.add_argument("--lignes", help="données en ligne : unités séparées par ';', annotateurs par ','")
    parseur.add_argument("--format", choices=("large", "long", "liste"), default="large",
                         help="large : une colonne par annotateur ; long : unité/annotateur/étiquette ; liste : notes anonymes")
    parseur.add_argument("--annotateurs", help="colonnes des annotateurs (format large), séparées par ','")
    parseur.add_argument("--id", help="colonne identifiant l'unité (exclue des annotateurs)")
    parseur.add_argument("--col-item", help="format long : colonne de l'unité")
    parseur.add_argument("--col-annotateur", help="format long : colonne de l'annotateur")
    parseur.add_argument("--col-etiquette", help="format long : colonne de l'étiquette")
    parseur.add_argument("--liste", help="colonne portant une liste de notes anonymes (JSON ou a|b|c)")
    parseur.add_argument("--separateur", help="séparateur csv (défaut : deviné sur l'en-tête)")
    parseur.add_argument("--manquant", default=MANQUANTS_DEFAUT, help="valeurs lues comme absence de note, séparées par ','")
    parseur.add_argument("--ordre", help="ordre des catégories, séparées par ',' (ordinal, kappas pondérés)")
    parseur.add_argument("--niveau-mesure", choices=NIVEAUX, default="nominal", help="niveau de l'alpha décisif (défaut nominal)")
    parseur.add_argument("--critere", choices=("alpha", "kappa"), default="alpha",
                         help="alpha de Krippendorff, ou kappa (Cohen à 2 annotateurs, Fleiss au-delà)")
    parseur.add_argument("--seuil", type=float, default=0.667, help="valeur minimale du critère (défaut 0.667, Krippendorff)")
    parseur.add_argument("--max-annotateurs-paires", type=int, default=10, help="au-delà, pas de kappas par paire (défaut 10)")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : contre-vérifie avec krippendorff, scikit-learn, statsmodels s'ils sont installés")
    parseur.add_argument("--racine", type=Path, help="base des chemins relatifs (défaut : répertoire courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lit, refuse ou calcule, publie, rend le code."""
    args = construire_parseur().parse_args(argv)
    donnees: Donnees | None = None
    moteur = MOTEUR_STDLIB
    try:
        if not -1.0 <= args.seuil <= 1.0 or args.max_annotateurs_paires < 2:
            raise ErreurEntree("--seuil doit être entre -1 et 1, --max-annotateurs-paires au moins 2")
        donnees = charger(args)
        if not base_rapport(donnees, moteur)["denominateur"]:
            raise ErreurEntree(f"dénominateur nul : aucune unité notée par au moins deux annotateurs dans "
                               f"{donnees.source}, rien à examiner", CODE_RIEN)
        modules = importer_optionnels(args.moteur)
        moteur = "+".join([MOTEUR_STDLIB, *modules]) if modules else MOTEUR_STDLIB
        rapport = base_rapport(donnees, moteur) | analyser(donnees, args)
        if modules:
            rapport["controle_croise"] = controle_croise(modules, donnees, rapport)
    except (ErreurEntree, ArithmeticError, OSError) as exc:
        code = exc.code if isinstance(exc, ErreurEntree) else CODE_USAGE
        print(f"{NOM_OUTIL} : {exc}", file=sys.stderr)
        if args.json:
            presenter_json(base_rapport(donnees, moteur) | {"refus": str(exc)})
        return code
    if args.json:
        presenter_json(rapport)
    else:
        afficher_humain(rapport)
    return CODE_OK if rapport["decision"]["atteint"] else CODE_DEFAUT


if __name__ == "__main__":
    raise SystemExit(main())
