"""Repère les documents ou enregistrements quasi identiques d'un corpus, sans comparer toutes les paires.

Mesuré le 2 octobre 2026 sur 360 enregistrements JSONL synthétiques (120 textes de
150 mots et 2 variantes de chacun, 0,5 à 8 % des mots remplacés) : sha256 par
enregistrement n'y voit aucun doublon ; cet outil retrouve 140 des 142 paires de
Jaccard ≥ 0,8 (comptées par force brute) en vérifiant 239 candidats au lieu des
64 620 paires possibles (--champ texte --json, graine 1).

QUESTION
    Quels documents ou enregistrements sont des quasi-doublons ?
MESURE
    Chaque document (fichier d'un dossier, ou champ --champ de chaque ligne
    d'un JSONL) est normalisé (casse repliée, accents retirés, blancs
    réduits) puis réduit à l'ensemble de ses bardeaux de k mots ou de k
    caractères. Les documents à ensembles identiques sont regroupés
    d'emblée. Les autres reçoivent une signature MinHash à graine fixe :
    par défaut hachage à une permutation ((a x + b) mod 2^61 - 1, minimum
    par case, cases vides densifiées), ou --schema classique (une
    permutation par position). La signature est découpée en bandes LSH ;
    les paires qui partagent une bande sont candidates et leur Jaccard
    EXACT est recalculé : seules celles ≥ seuil sont rendues, avec des
    groupes (composantes connexes). Le découpage en bandes vise une
    probabilité ≥ 0,95 qu'une paire au seuil devienne candidate ; cette
    probabilité théorique est rendue.
HYPOTHÈSES
    Le recouvrement de bardeaux mesure la ressemblance utile (copie,
    gabarit, reformulation légère). Fichiers en utf-8 ; lignes JSONL
    valides (les autres sont comptées et ignorées).
LIMITES
    Le filtre LSH est probabiliste. Mesuré sur le corpus synthétique
    ci-dessus, graines 1 à 5 : au seuil 0,8, 701 paires retrouvées sur 710
    (classique 704, datasketch 2.0.0 703) ; au seuil 0,6, 1292 sur 1305
    (classique 1298, datasketch 1302) ; aucune fausse paire (Jaccard exact).
    Aucune sémantique : une paraphrase sans mots communs n'est pas vue. Un
    document plus court que k mots devient un seul bardeau : deux textes
    courts qui diffèrent d'un mot ont un Jaccard nul. Coût en pur Python :
    sur outils/ (166 fichiers), 0,7 s par défaut contre 7,9 s en classique.
CONTRE-EXEMPLES
    Constaté : les enregistrements « Livraison prévue à Paris » et
    « Livraison prévue à Lyon » (bardeaux de 5 mots, seuil 0,8) ne sont pas
    signalés : chacun n'a qu'un bardeau, et leur Jaccard vaut 0.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Dédoublonnage de corpus d'entraînement ou d'évaluation, de pages de
    documentation, de tickets ou d'enregistrements textuels.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import json
import random
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
FORMAT_JSONL = "jsonl"
MOT_EXACT = "EXACT"
ALGORITHME = "MinHash"
MOTEUR_STDLIB = "stdlib"
INTITULES = ("QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES",
             "CONTRE-EXEMPLES", "INVOCATION", "DOMAINE")
PREMIER_MERSENNE = (1 << 61) - 1
PAS_INTEGRATION = 200
RAPPEL_AU_SEUIL = 0.95
MAX_EXAMINES = 50
EXTENSIONS_DEFAUT = (".md,.markdown,.mdx,.txt,.rst,.adoc,.org,.py,.js,.ts,.java,.go,.rs,.c,.h,"
                     ".cpp,.cs,.rb,.php,.sh,.sql,.json,.yaml,.yml,.toml,.html,.css,.tex,.csv")
EXTENSIONS_JSONL = (".jsonl", ".ndjson")
DOSSIERS_IGNORES = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv",
                              ".tox", ".mypy_cache", ".pytest_cache"})
RE_DIACRITIQUES = re.compile(r"[\u0300-\u036f]")
RE_MOT = re.compile(r"[^\W_]+")
RE_BLANCS = re.compile(r"\s+")


class ErreurEntree(Exception):
    """Entrée invalide : message pour stderr et code de sortie."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Document:
    """Un document : identifiant lisible et texte."""

    nom: str
    texte: str


@dataclass(frozen=True)
class Parametres:
    """Paramètres du calcul, figés après validation."""

    seuil: float
    bardeaux: str
    k: int
    permutations: int
    bandes: int
    lignes: int
    graine: int
    replier_accents: bool
    schema: str


# --------------------------------------------------------------------------
# Contrat et bibliothèque optionnelle


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring."""
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


def _signaler_absence() -> None:
    """UNE ligne sur stderr si datasketch manque."""
    try:
        present = importlib.util.find_spec("datasketch") is not None
    except (ImportError, ValueError):
        present = False
    if not present:
        print("detecter_quasi_doublons : datasketch absent — pas de comparaison (--comparer "
              "sans effet) ; MinHash, LSH et Jaccard exact par le moteur stdlib.", file=sys.stderr)


def _importer_datasketch() -> ModuleType | None:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return importlib.import_module("datasketch")
    except ImportError:
        return None


# --------------------------------------------------------------------------
# Lecture des documents


def _nom_affiche(chemin: Path, racine: Path) -> str:
    try:
        return chemin.resolve().relative_to(racine.resolve()).as_posix()
    except ValueError:
        return chemin.as_posix()


def _fichiers_du_dossier(dossier: Path, extensions: tuple[str, ...]) -> Iterator[Path]:
    for chemin in sorted(dossier.rglob("*")):
        if any(p in DOSSIERS_IGNORES for p in chemin.relative_to(dossier).parts[:-1]):
            continue
        if chemin.is_file() and chemin.suffix.lower() in extensions:
            yield chemin


def lister_fichiers(chemins: list[Path], extensions: tuple[str, ...]) -> list[tuple[Path, bool]]:
    """(fichier, explicite) pour chaque entrée ; chemin inexistant = erreur."""
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
    return trouves


def lire_texte(chemin: Path, taille_max: int) -> str:
    """Lecture bornée en utf-8 ; ErreurEntree si binaire, trop gros ou mal encodé."""
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
        raise ErreurEntree(f"{chemin} : pas de l'utf-8 valide (octet {exc.start})") from exc


def _texte_du_champ(valeur: Any) -> str | None:
    """Texte d'une valeur de champ JSON (chaîne, ou JSON compact sinon)."""
    if valeur is None:
        return None
    return valeur if isinstance(valeur, str) else json.dumps(valeur, ensure_ascii=False, sort_keys=True)


def lire_jsonl(nom: str, texte: str, champ: str, champ_id: str | None) -> tuple[list[Document], list[int]]:
    """Documents tirés d'un JSONL et numéros des lignes invalides ou sans le champ."""
    documents: list[Document] = []
    rejetees: list[int] = []
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if not ligne.strip():
            continue
        try:
            objet = json.loads(ligne)
        except json.JSONDecodeError:
            rejetees.append(numero)
            continue
        valeur = _texte_du_champ(objet.get(champ)) if isinstance(objet, dict) else None
        if valeur is None:
            rejetees.append(numero)
            continue
        ident = objet.get(champ_id) if champ_id else None
        documents.append(Document(f"{nom}:{numero}" if ident is None else str(ident), valeur))
    return documents, rejetees


def charger_documents(fichiers: list[tuple[Path, bool]], args: argparse.Namespace,
                      racine: Path) -> tuple[list[Document], list[dict[str, Any]]]:
    """Documents (fichiers entiers ou lignes JSONL) et entrées ignorées."""
    documents: list[Document] = []
    ignores: list[dict[str, Any]] = []
    for chemin, explicite in fichiers:
        nom = _nom_affiche(chemin, racine)
        try:
            texte = lire_texte(chemin, args.taille_max_fichier)
        except (ErreurEntree, OSError) as exc:
            if explicite:
                raise ErreurEntree(str(exc)) from exc
            ignores.append({"chemin": nom, "raison": str(exc)})
            continue
        if args.champ is None:
            documents.append(Document(nom, texte))
            continue
        lus, rejetees = lire_jsonl(nom, texte, args.champ, args.champ_id)
        documents.extend(lus)
        if rejetees:
            ignores.append({"chemin": nom, "raison": f"{len(rejetees)} ligne(s) invalide(s) ou sans "
                            f"le champ « {args.champ} »", "lignes": rejetees[:MAX_EXAMINES]})
    return documents, ignores


# --------------------------------------------------------------------------
# Bardeaux et MinHash


def normaliser(texte: str, replier_accents: bool) -> str:
    """Casse repliée, accents retirés (option), blancs réduits."""
    texte = texte.casefold()
    if replier_accents:
        texte = RE_DIACRITIQUES.sub("", unicodedata.normalize("NFKD", texte))
    return RE_BLANCS.sub(" ", texte).strip()


def _empreinte(morceau: str) -> int:
    """Empreinte 64 bits stable (blake2b), indépendante de PYTHONHASHSEED."""
    return int.from_bytes(hashlib.blake2b(morceau.encode("utf-8"), digest_size=8).digest(), "little")


def bardeaux(texte: str, parametres: Parametres) -> frozenset[int]:
    """Ensemble des empreintes de bardeaux (k mots ou k caractères)."""
    normal = normaliser(texte, parametres.replier_accents)
    if parametres.bardeaux == "mots":
        unites: list[str] = RE_MOT.findall(normal)
        separateur = " "
    else:
        unites = list(normal)
        separateur = ""
    if not unites:
        return frozenset()
    k = min(parametres.k, len(unites))
    return frozenset(_empreinte(separateur.join(unites[i:i + k]))
                     for i in range(len(unites) - k + 1))


@dataclass(frozen=True)
class Hachage:
    """Fonctions de hachage figées par la graine : permutations et ordres de sondage."""

    schema: str
    permutations: tuple[tuple[int, int], ...]
    sondes: tuple[tuple[int, ...], ...]


def preparer_hachage(schema: str, nombre: int, graine: int) -> Hachage:
    """Classique : `nombre` permutations (a x + b) mod p. Une permutation (oph) : une seule
    permutation, `nombre` cases, et pour chaque case un ordre de sondage fixe (densification)."""
    alea = random.Random(graine)
    if schema == "classique":
        permutations = tuple((alea.randrange(1, PREMIER_MERSENNE), alea.randrange(0, PREMIER_MERSENNE))
                             for _ in range(nombre))
        return Hachage(schema, permutations, ())
    unique = ((alea.randrange(1, PREMIER_MERSENNE), alea.randrange(0, PREMIER_MERSENNE)),)
    sondes = []
    for case in range(nombre):
        autres = [j for j in range(nombre) if j != case]
        alea.shuffle(autres)
        sondes.append(tuple(autres))
    return Hachage(schema, unique, tuple(sondes))


def signature(ensemble: frozenset[int], hachage: Hachage) -> tuple[int, ...]:
    """Signature MinHash de l'ensemble selon le schéma choisi."""
    if hachage.schema == "classique":
        elements = list(ensemble)
        return tuple(min([(a * x + b) % PREMIER_MERSENNE for x in elements])
                     for a, b in hachage.permutations)
    return _signature_une_permutation(ensemble, hachage)


def _signature_une_permutation(ensemble: frozenset[int], hachage: Hachage) -> tuple[int, ...]:
    """Hachage à une permutation : minimum par case, cases vides densifiées."""
    nombre = len(hachage.sondes)
    a, b = hachage.permutations[0]
    cases: list[int | None] = [None] * nombre
    for x in ensemble:
        valeur = (a * x + b) % PREMIER_MERSENNE
        case, rang = valeur % nombre, valeur // nombre
        courant = cases[case]
        if courant is None or rang < courant:
            cases[case] = rang
    return tuple(_densifier(cases, hachage.sondes))


def _densifier(cases: list[int | None], sondes: tuple[tuple[int, ...], ...]) -> list[int]:
    """Densification optimale : une case vide copie la première case pleine de son ordre de sondage."""
    pleines: list[int] = []
    for case, valeur in enumerate(cases):
        if valeur is None:
            valeur = next(cases[j] for j in sondes[case] if cases[j] is not None)
        pleines.append(valeur)
    return pleines


def estimer_jaccard(sig_a: tuple[int, ...], sig_b: tuple[int, ...]) -> float:
    """Estimation MinHash : part des positions égales."""
    return sum(x == y for x, y in zip(sig_a, sig_b)) / len(sig_a)


def jaccard(a: frozenset[int], b: frozenset[int]) -> float:
    """Indice de Jaccard exact."""
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# --------------------------------------------------------------------------
# Paramètres LSH


def _probabilite_candidate(s: float, bandes: int, lignes: int) -> float:
    """Probabilité qu'une paire de Jaccard s partage au moins une bande."""
    return 1.0 - (1.0 - s ** lignes) ** bandes


def _integrer(bas: float, haut: float, fonction: Any) -> float:
    """Intégrale par trapèzes (pas fixe)."""
    if haut <= bas:
        return 0.0
    pas = (haut - bas) / PAS_INTEGRATION
    valeurs = [fonction(bas + i * pas) for i in range(PAS_INTEGRATION + 1)]
    return pas * (sum(valeurs) - (valeurs[0] + valeurs[-1]) / 2)


def choisir_bandes(seuil: float, permutations: int) -> tuple[int, int]:
    """(bandes, lignes) : moindre aire de faux positifs parmi les découpages qui rendent
    candidate une paire au seuil avec une probabilité ≥ RAPPEL_AU_SEUIL ; à défaut, le
    découpage de meilleur rappel au seuil. Le Jaccard exact élimine ensuite les faux positifs.
    """
    meilleur, cle_min = (permutations, 1), (1, float("inf"))
    for bandes in range(1, permutations + 1):
        lignes_max = permutations // bandes
        for lignes in range(1, lignes_max + 1):
            rappel = _probabilite_candidate(seuil, bandes, lignes)
            aire = _integrer(0.0, seuil, lambda s: _probabilite_candidate(s, bandes, lignes))
            cle = (0, aire) if rappel >= RAPPEL_AU_SEUIL else (1, -rappel)
            if cle < cle_min:
                meilleur, cle_min = (bandes, lignes), cle
    return meilleur


# --------------------------------------------------------------------------
# Détection


def regrouper_identiques(ensembles: list[frozenset[int]]) -> dict[frozenset[int], list[int]]:
    """Documents à ensembles de bardeaux identiques (le premier représente le groupe)."""
    groupes: dict[frozenset[int], list[int]] = {}
    for indice, ensemble in enumerate(ensembles):
        groupes.setdefault(ensemble, []).append(indice)
    return groupes


def candidats_lsh(signatures: dict[int, tuple[int, ...]], parametres: Parametres,
                  maximum: int) -> set[tuple[int, int]]:
    """Paires partageant au moins une bande de signature."""
    paires: set[tuple[int, int]] = set()
    for bande in range(parametres.bandes):
        seaux: dict[tuple[int, ...], list[int]] = defaultdict(list)
        debut = bande * parametres.lignes
        for indice, sig in signatures.items():
            seaux[sig[debut:debut + parametres.lignes]].append(indice)
        for membres in seaux.values():
            for i, a in enumerate(membres):
                paires.update((a, b) for b in membres[i + 1:])
            if len(paires) > maximum:
                raise ErreurEntree(f"plus de {maximum} paires candidates : relever --seuil, "
                                   "allonger les bardeaux ou relever --max-candidats")
    return paires


class _Partition:
    """Union-find pour former les groupes de quasi-doublons."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def trouver(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def unir(self, a: int, b: int) -> None:
        self.parent[self.trouver(a)] = self.trouver(b)

    def groupes(self) -> list[list[int]]:
        par_racine: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            par_racine[self.trouver(x)].append(x)
        return sorted((sorted(g) for g in par_racine.values() if len(g) > 1), key=lambda g: (-len(g), g))


def detecter(ensembles: list[frozenset[int]], parametres: Parametres,
             max_candidats: int) -> dict[str, Any]:
    """Pipeline complet : identiques, MinHash, LSH, vérification exacte, groupes."""
    identiques = regrouper_identiques(ensembles)
    representants = [membres[0] for membres in identiques.values()]
    hachage = preparer_hachage(parametres.schema, parametres.permutations, parametres.graine)
    signatures = {i: signature(ensembles[i], hachage) for i in representants}
    candidats = candidats_lsh(signatures, parametres, max_candidats)
    paires = []
    for a, b in sorted(candidats):
        valeur = jaccard(ensembles[a], ensembles[b])
        if valeur >= parametres.seuil:
            paires.append((a, b, valeur, estimer_jaccard(signatures[a], signatures[b])))
    partition = _Partition()
    for membres in identiques.values():
        for autre in membres[1:]:
            partition.unir(membres[0], autre)
    for a, b, _, _ in paires:
        partition.unir(a, b)
    return {"identiques": [m for m in identiques.values() if len(m) > 1], "candidats": candidats,
            "paires": paires, "groupes": partition.groupes(), "signatures": signatures}


# --------------------------------------------------------------------------
# Comparaison avec datasketch


def comparer_datasketch(ensembles: list[frozenset[int]], resultat: dict[str, Any],
                        parametres: Parametres) -> dict[str, Any]:
    """Même pipeline avec datasketch (mêmes bardeaux, même découpage en bandes)."""
    module = _importer_datasketch()
    if module is None:
        return {"absent": "datasketch"}
    representants = list(resultat["signatures"])
    with contextlib.redirect_stdout(sys.stderr):
        lsh = module.MinHashLSH(threshold=parametres.seuil, num_perm=parametres.permutations,
                                params=(parametres.bandes, parametres.lignes))
        signatures = {}
        for indice in representants:
            minhash = module.MinHash(num_perm=parametres.permutations, seed=parametres.graine)
            minhash.update_batch([x.to_bytes(8, "little") for x in ensembles[indice]])
            signatures[indice] = minhash
            lsh.insert(indice, minhash)
        candidats = {tuple(sorted((i, j))) for i in representants
                     for j in lsh.query(signatures[i]) if j != i}
    leurs = {(a, b) for a, b in candidats if jaccard(ensembles[a], ensembles[b]) >= parametres.seuil}
    notres = {(a, b) for a, b, _, _ in resultat["paires"]}
    ecarts_dk = [abs(signatures[a].jaccard(signatures[b]) - jaccard(ensembles[a], ensembles[b]))
                 for a, b in candidats]
    ecarts_nous = [abs(estimer_jaccard(resultat["signatures"][a], resultat["signatures"][b])
                       - jaccard(ensembles[a], ensembles[b])) for a, b in resultat["candidats"]]
    return {"version": getattr(module, "__version__", "?"), "candidats": len(candidats),
            "paires": len(leurs), "paires_communes": len(leurs & notres),
            "seulement_stdlib": len(notres - leurs), "seulement_datasketch": len(leurs - notres),
            "erreur_estimation_moyenne_datasketch": _moyenne(ecarts_dk),
            "erreur_estimation_moyenne_stdlib": _moyenne(ecarts_nous)}


def _moyenne(valeurs: list[float]) -> float | None:
    return sum(valeurs) / len(valeurs) if valeurs else None


# --------------------------------------------------------------------------
# Sorties


def construire_sortie(documents: list[Document], resultat: dict[str, Any] | None,
                      parametres: Parametres, vides: list[str],
                      ignores: list[dict[str, Any]], max_paires: int) -> dict[str, Any]:
    """Objet JSON rendu."""
    noms = [d.nom for d in documents]
    denominateur = len(documents) if len(documents) >= 2 else 0
    sortie: dict[str, Any] = {
        "outil": "detecter_quasi_doublons", "moteur": MOTEUR_STDLIB, "algorithme": ALGORITHME,
        "parametres": asdict(parametres),
        "probabilite_candidate": {
            "au_seuil": _probabilite_candidate(parametres.seuil, parametres.bandes, parametres.lignes),
            "seuil_moins_0_1": _probabilite_candidate(max(0.0, parametres.seuil - 0.1),
                                                      parametres.bandes, parametres.lignes),
            "seuil_plus_0_1": _probabilite_candidate(min(1.0, parametres.seuil + 0.1),
                                                     parametres.bandes, parametres.lignes)},
        "denominateur": denominateur, "documents_lus": len(documents),
        "examines": noms[:MAX_EXAMINES], "examines_tronques": len(noms) > MAX_EXAMINES,
        "vides": vides[:MAX_EXAMINES], "ignores": ignores[:MAX_EXAMINES],
        "contrat": extraire_contrat(__doc__ or ""),
    }
    if resultat is not None:
        sortie.update(_decrire_resultat(resultat, noms, max_paires))
    return sortie


def _decrire_resultat(resultat: dict[str, Any], noms: list[str], max_paires: int) -> dict[str, Any]:
    """Paires et groupes avec noms lisibles."""
    paires = sorted(resultat["paires"], key=lambda p: (-p[2], p[0], p[1]))
    identiques = sum(len(m) * (len(m) - 1) // 2 for m in resultat["identiques"])
    return {
        "groupes_identiques": len(resultat["identiques"]),
        "paires_identiques": identiques,
        "candidats_verifies": len(resultat["candidats"]),
        "faux_positifs_lsh": len(resultat["candidats"]) - len(paires),
        "paires_quasi_doublons": len(paires),
        "paires": [{"a": noms[a], "b": noms[b], "jaccard": round(j, 6), "estimation_minhash": round(e, 6)}
                   for a, b, j, e in paires[:max_paires]],
        "paires_tronquees": len(paires) > max_paires,
        "groupes": [[noms[i] for i in g[:MAX_EXAMINES]] for g in resultat["groupes"][:MAX_EXAMINES]],
    }


def presenter_humain(sortie: dict[str, Any]) -> None:
    """Affichage lisible."""
    p = sortie["parametres"]
    print(f"{sortie['documents_lus']} document(s) ; bardeaux de {p['k']} {p['bardeaux']}, seuil "
          f"{p['seuil']}, {p['permutations']} permutations = {p['bandes']} bandes × {p['lignes']} lignes")
    print(f"probabilité qu'une paire au seuil soit candidate : {sortie['probabilite_candidate']['au_seuil']:.3f}")
    if "paires_quasi_doublons" not in sortie:
        return
    print(f"candidats vérifiés : {sortie['candidats_verifies']} ; paires quasi-doublons : "
          f"{sortie['paires_quasi_doublons']} ; paires identiques : {sortie['paires_identiques']}")
    for paire in sortie["paires"][:30]:
        print(f"  {paire['jaccard']:.3f}  {paire['a']}  ~  {paire['b']}")
    for groupe in sortie["groupes"][:20]:
        print(f"  groupe de {len(groupe)} : {', '.join(groupe[:8])}")
    if "comparaison" in sortie:
        print(f"comparaison : {sortie['comparaison']}")


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    parser = argparse.ArgumentParser(
        description="Détecte les quasi-doublons (fichiers d'un dossier ou lignes JSONL) par "
                    "bardeaux, MinHash et LSH, puis vérification par Jaccard exact.",
        epilog="Exemple : python3 detecter_quasi_doublons.py corpus.jsonl --champ texte --seuil 0.7 --json")
    parser.add_argument("chemins", nargs="+", type=Path, help="fichiers ou dossiers à examiner")
    parser.add_argument("--champ", help="lire les fichiers comme JSONL et comparer ce champ")
    parser.add_argument("--champ-id", help="champ JSONL servant d'identifiant (défaut fichier:ligne)")
    parser.add_argument("--seuil", type=float, default=0.8, help="Jaccard minimal (défaut 0.8)")
    parser.add_argument("--bardeaux", choices=("mots", "caracteres"), default="mots", metavar="TYPE",
                        help="bardeaux de mots (défaut) ou de caractères")
    parser.add_argument("--k", type=int, help="taille des bardeaux (défaut 5 mots ou 7 caractères)")
    parser.add_argument("--permutations", type=int, default=128, help="taille de la signature MinHash")
    parser.add_argument("--graine", type=int, default=1, help="graine des permutations (défaut 1)")
    parser.add_argument("--schema", choices=("une-permutation", "classique"), default="une-permutation",
                        metavar="SCHEMA", help="une-permutation (défaut, rapide, densifiée) ou classique "
                        "(une permutation par position, coût × permutations)")
    parser.add_argument("--garder-accents", action="store_true", help="ne pas retirer les accents")
    parser.add_argument("--extensions", default=EXTENSIONS_DEFAUT, help="extensions lues dans un dossier")
    parser.add_argument("--taille-max-fichier", type=int, default=50_000_000, metavar="OCTETS",
                        help="taille maximale lue par fichier")
    parser.add_argument("--max-candidats", type=int, default=5_000_000, metavar="N",
                        help="arrêt si les paires candidates dépassent N")
    parser.add_argument("--max-paires", type=int, default=200, metavar="N",
                        help="paires détaillées dans la sortie (défaut 200, les plus proches d'abord)")
    parser.add_argument("--comparer", action="store_true", help="refaire le calcul avec datasketch et comparer")
    parser.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=Path,
                        help=f"base des chemins relatifs (défaut : répertoire courant ; outil dans {RACINE})")
    return parser


def preparer_parametres(args: argparse.Namespace) -> Parametres:
    """Valide les options et fixe le découpage LSH."""
    if not 0.0 < args.seuil <= 1.0:
        raise ErreurEntree("--seuil doit être dans ]0, 1]")
    k = args.k if args.k is not None else (5 if args.bardeaux == "mots" else 7)
    if k < 1 or not 1 <= args.permutations <= 1024:
        raise ErreurEntree("--k doit être ≥ 1 et --permutations entre 1 et 1024")
    if args.taille_max_fichier < 1 or args.max_candidats < 1 or args.max_paires < 0:
        raise ErreurEntree("--taille-max-fichier, --max-candidats ≥ 1 et --max-paires ≥ 0")
    bandes, lignes = choisir_bandes(args.seuil, args.permutations)
    return Parametres(args.seuil, args.bardeaux, k, args.permutations, bandes, lignes,
                      args.graine, not args.garder_accents, args.schema)


def executer(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Charge, calcule, compare ; renvoie la sortie et le code."""
    parametres = preparer_parametres(args)
    racine = args.racine if args.racine is not None else Path.cwd()
    entrees = [c if c.is_absolute() else racine / c for c in args.chemins]
    extensions = EXTENSIONS_JSONL if args.champ else tuple(
        e.strip().lower() for e in args.extensions.split(",") if e.strip())
    documents, ignores = charger_documents(lister_fichiers(entrees, extensions), args, racine)
    ensembles_tous = [bardeaux(d.texte, parametres) for d in documents]
    vides = [d.nom for d, e in zip(documents, ensembles_tous) if not e]
    gardes = [(d, e) for d, e in zip(documents, ensembles_tous) if e]
    documents = [d for d, _ in gardes]
    ensembles = [e for _, e in gardes]
    if len(documents) < 2:
        return construire_sortie(documents, None, parametres, vides, ignores, args.max_paires), 3
    resultat = detecter(ensembles, parametres, args.max_candidats)
    sortie = construire_sortie(documents, resultat, parametres, vides, ignores, args.max_paires)
    if args.comparer:
        sortie["comparaison"] = comparer_datasketch(ensembles, resultat, parametres)
    return sortie, 1 if sortie["paires_quasi_doublons"] or sortie["paires_identiques"] else 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    args = construire_analyseur().parse_args(argv)
    _signaler_absence()
    try:
        sortie, code = executer(args)
    except ErreurEntree as exc:
        print(f"detecter_quasi_doublons : {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"detecter_quasi_doublons : erreur d'entrée/sortie : {exc}", file=sys.stderr)
        return 2
    if code == 3:
        print("detecter_quasi_doublons : dénominateur nul — rien à examiner (moins de deux "
              "documents non vides, aucune paire possible)", file=sys.stderr)
    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    elif code != 3:
        presenter_humain(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
