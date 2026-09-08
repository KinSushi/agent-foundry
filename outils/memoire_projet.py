#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mémoire_projet.py — mémoire de projet scellée à son contexte.

Pourquoi cet outil existe
-------------------------
Un agent qui reprend un projet ne sait ni ce qui a été décidé, ni si c'est
encore vrai. Trois bases de documentation examinées dans ce projet étaient
périmées, aucune ne se signalait elle-même. Une mémoire qui ne sait pas si
elle est périmée est pire qu'une absence de mémoire : elle est crue.

Contrat de mesure
-----------------
QUESTION       ce que je crois savoir de ce projet est-il encore vrai ?
MESURE         FTS5 pour retrouver, sha256 pour dater, difflib pour montrer
HYPOTHÈSES     les fichiers décrits existent et sont lisibles ; l'auteur de
               l'entrée a nommé les bons fichiers
LIMITES        ne détecte pas qu'une entrée était FAUSSE dès l'écriture ;
               ne voit pas un changement dans un fichier non déclaré ;
               la recherche de termes utilise re.search (faux positifs possibles)
CONTRE-EXEMPLES une entrée qui ne déclare aucun fichier est classée comme NON VÉRIFIABLE (et non VALIDE)
DOMAINE        un arbre de fichiers, sur cette machine
"""

from __future__ import annotations

import sys
import json
import hashlib
import difflib
import re
import argparse
import sqlite3
import zlib
from pathlib import Path
from datetime import datetime, timezone

# Encodage stdout en utf-8 (règle 2)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Racine par défaut : répertoire de l'outil (socle règle 6)
RACINE = Path(__file__).resolve().parent

# Constantes pour les états
VALIDE = "VALIDE"
PERIMEE = "PÉRIMÉE"
ORPHELINE = "ORPHELINE"
NON_VERIFIABLE = "NON VÉRIFIABLE"


class Memoire:
    """Mémoire de projet stockée dans une base SQLite avec FTS5 contentless."""

    def __init__(self, chemin_db: str | Path, racine: Path = RACINE) -> None:
        self.racine = Path(racine).resolve()
        self.chemin_db = Path(chemin_db)
        self._connecter()

    def _connecter(self) -> None:
        """Ouvre la base et crée les tables si nécessaire."""
        self.connexion = sqlite3.connect(
            self.chemin_db,
            autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
        )
        self.connexion.execute("PRAGMA journal_mode=WAL")
        self._creer_schema()

    def _creer_schema(self) -> None:
        """Crée les tables memoires et fts (contentless)."""
        with self.connexion:
            self.connexion.execute(
                """
                CREATE TABLE IF NOT EXISTS memoires (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    corps_compresse BLOB NOT NULL,
                    meta_json TEXT NOT NULL,
                    empreintes_json TEXT NOT NULL,
                    date_creation TEXT NOT NULL
                )
                """
            )
            self.connexion.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS fts
                USING fts5(contenu, content='')
                """
            )

    def _empreinte_fichier(self, chemin: Path) -> str:
        """Calcule l'empreinte sha256 d'un fichier."""
        h = hashlib.sha256()
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(65536), b""):
                h.update(bloc)
        return h.hexdigest()

    def _resoudre_chemin(self, chemin_relatif: str) -> Path:
        """Résout un chemin relatif par rapport à la racine."""
        return (self.racine / chemin_relatif).resolve()

    def noter(
        self,
        texte: str,
        fichiers: list[str] | None = None,
        metadonnees: dict | None = None,
    ) -> int:
        """
        Ajoute une entrée à la mémoire.

        - texte : corps de l'entrée (sera compressé et indexé)
        - fichiers : liste de chemins relatifs à la racine
        - metadonnees : dictionnaire JSON sérialisable

        Retourne l'identifiant de la nouvelle entrée.
        """
        if fichiers is None:
            fichiers = []
        if metadonnees is None:
            metadonnees = {}

        # Compression du corps
        corps_compresse = zlib.compress(texte.encode("utf-8"))

        # Calcul des empreintes des fichiers déclarés
        empreintes = {}
        for chemin_rel in fichiers:
            chemin = self._resoudre_chemin(chemin_rel)
            if chemin.exists():
                with open(chemin, "rb") as f:
                    content_bytes = f.read()
                hash_val = self._empreinte_fichier(chemin)
                try:
                    content_text = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    content_text = None
                empreintes[chemin_rel] = {"hash": hash_val, "content": content_text}
            else:
                empreintes[chemin_rel] = None

        date_creation = datetime.now(timezone.utc).isoformat()

        with self.connexion:
            curseur = self.connexion.execute(
                """
                INSERT INTO memoires (corps_compresse, meta_json, empreintes_json, date_creation)
                VALUES (?, ?, ?, ?)
                """,
                (
                    corps_compresse,
                    json.dumps(metadonnees, ensure_ascii=False),
                    json.dumps(empreintes, ensure_ascii=False),
                    date_creation,
                ),
            )
            id_entree = curseur.lastrowid
            self.connexion.execute(
                "INSERT INTO fts(rowid, contenu) VALUES (?, ?)",
                (id_entree, texte),
            )
        return id_entree

    def chercher(self, terme: str, limite: int = 10) -> list[dict]:
        """
        Recherche un terme dans la mémoire.

        Retourne une liste de dictionnaires avec 'id', 'extrait', 'metadonnees'.
        """
        curseur = self.connexion.execute(
            """
            SELECT rowid FROM fts
            WHERE fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (terme, limite),
        )
        ids = [row[0] for row in curseur.fetchall()]

        resultats = []
        for id_entree in ids:
            curseur = self.connexion.execute(
                "SELECT corps_compresse, meta_json FROM memoires WHERE id = ?",
                (id_entree,),
            )
            row = curseur.fetchone()
            if row is None:
                continue
            corps_compresse, meta_json = row
            texte = zlib.decompress(corps_compresse).decode("utf-8")
            extrait = self._extrait(texte, terme)
            resultats.append(
                {
                    "id": id_entree,
                    "extrait": extrait,
                    "metadonnees": json.loads(meta_json),
                }
            )
        return resultats

    def _extrait(self, texte: str, terme: str, largeur: int = 80) -> str:
        """Génère un extrait du texte autour de la première occurrence du terme en tant que mot entier."""
        pattern = re.compile(r"\b" + re.escape(terme) + r"\b", re.IGNORECASE)
        match = pattern.search(texte)
        if match is None:
            return texte[:largeur] + "…"
        pos = match.start()
        debut = max(0, pos - largeur // 2)
        fin = min(len(texte), pos + len(terme) + largeur // 2)
        extrait = texte[debut:fin]
        if debut > 0:
            extrait = "…" + extrait
        if fin < len(texte):
            extrait += "…"
        return extrait

    def perimees(self) -> list[dict]:
        """
        Vérifie l'état de chaque entrée par rapport aux fichiers déclarés.

        Retourne une liste de dictionnaires avec 'id', 'etat', 'fichiers_modifies'.
        Chaque élément de 'fichiers_modifies' est un dictionnaire contenant:
          - 'fichier': le chemin relatif du fichier
          - 'diff': le diff unifié si le fichier a changé et que le contenu texte est disponible, sinon None
        """
        curseur = self.connexion.execute(
            "SELECT id, empreintes_json FROM memoires"
        )
        resultats = []
        for id_entree, empreintes_json in curseur.fetchall():
            empreintes = json.loads(empreintes_json)
            if not empreintes:
                resultats.append(
                    {
                        "id": id_entree,
                        "etat": NON_VERIFIABLE,
                        "fichiers_modifies": [],
                    }
                )
                continue

            sample_val = next(iter(empreintes.values()))
            new_format = isinstance(sample_val, dict) and "hash" in sample_val and "content" in sample_val

            fichiers_modifies_details = []
            orpheline = False
            fichiers_modifies_names = []

            for chemin_rel, empreinte_info in empreintes.items():
                chemin = self._resoudre_chemin(chemin_rel)
                if not chemin.exists():
                    fichiers_modifies_details.append({"fichier": chemin_rel, "diff": None})
                    orpheline = True
                else:
                    with open(chemin, "rb") as f:
                        content_bytes = f.read()
                    hash_actuelle = self._empreinte_fichier(chemin)

                    if new_format:
                        old_hash = empreinte_info["hash"]
                        old_content = empreinte_info["content"]
                    else:
                        old_hash = empreinte_info
                        old_content = None

                    if old_hash != hash_actuelle:
                        fichiers_modifies_names.append(chemin_rel)
                        diff = None
                        try:
                            current_text = content_bytes.decode("utf-8")
                            if old_content is not None:
                                diff_lines = difflib.unified_diff(
                                    old_content.splitlines(keepends=True),
                                    current_text.splitlines(keepends=True),
                                    fromfile=f"{chemin_rel} (ancien)",
                                    tofile=f"{chemin_rel} (nouveau)",
                                    lineterm="",
                                )
                            else:
                                diff_lines = difflib.unified_diff(
                                    [],
                                    current_text.splitlines(keepends=True),
                                    fromfile=f"{chemin_rel} (ancien)",
                                    tofile=f"{chemin_rel} (nouveau)",
                                    lineterm="",
                                )
                            diff = "".join(diff_lines)
                        except UnicodeDecodeError:
                            pass
                        fichiers_modifies_details.append({"fichier": chemin_rel, "diff": diff})

            if orpheline:
                etat = ORPHELINE
            elif fichiers_modifies_names:
                etat = PERIMEE
            else:
                etat = VALIDE

            resultats.append(
                {
                    "id": id_entree,
                    "etat": etat,
                    "fichiers_modifies": fichiers_modifies_details,
                }
            )
        return resultats

    def oublier(self, id_entree: int) -> None:
        """Supprime une entrée et son index FTS5."""
        with self.connexion:
            self.connexion.execute("DELETE FROM memoires WHERE id = ?", (id_entree,))
            self.connexion.execute("DELETE FROM fts WHERE rowid = ?", (id_entree,))

    def fermer(self) -> None:
        """Ferme la connexion."""
        self.connexion.close()


def _construire_contrat() -> dict:
    """Retourne le contrat de mesure sous forme de dictionnaire."""
    return {
        "QUESTION": "ce que je crois savoir de ce projet est-il encore vrai ?",
        "MESURE": "FTS5 pour retrouver, sha256 pour dater, difflib pour montrer",
        "HYPOTHÈSES": "les fichiers décrits existent et sont lisibles ; l'auteur de l'entrée a nommé les bons fichiers",
        "LIMITES": "ne détecte pas qu'une entrée était FAUSSE dès l'écriture ; ne voit pas un changement dans un fichier non déclaré ; la recherche de termes utilise re.search (faux positifs possibles)",
        "CONTRE-EXEMPLES": "une entrée qui ne déclare aucun fichier est classée comme NON VÉRIFIABLE (et non VALIDE)",
        "DOMAINE": "un arbre de fichiers, sur cette machine",
        "CODES_SORTIE": {
            "0": "aucune entrée périmée ou orpheline",
            "1": "au moins une entrée périmée ou orpheline",
            "2": "erreur d'exécution",
            "3": "aucune entrée à vérifier (zéro silencieux)",
        },
    }


def _afficher_humain(resultats: list[dict], action: str) -> None:
    """Affiche les résultats en mode texte lisible."""
    if action == "chercher":
        for r in resultats:
            print(f"ID {r['id']} : {r['extrait']}")
            if r["metadonnees"]:
                print(f"  Métadonnées : {json.dumps(r['metadonnees'], ensure_ascii=False)}")
    elif action == "perimees":
        for r in resultats:
            etat = r["etat"]
            if etat == VALIDE:
                print(f"ID {r['id']} : VALIDE")
            elif etat == PERIMEE:
                print(f"ID {r['id']} : PÉRIMÉE")
                for mod in r["fichiers_modifies"]:
                    fichier = mod["fichier"]
                    diff = mod["diff"]
                    print(f"  Fichier : {fichier}")
                    if diff is None:
                        print("    (impossible d'afficher le diff : contenu binaire ou indisponible)")
                    else:
                        for line in diff.splitlines():
                            print(f"    {line}")
            elif etat == ORPHELINE:
                manquants = [mod["fichier"] for mod in r["fichiers_modifies"]]
                print(f"ID {r['id']} : ORPHELINE — fichiers manquants : {', '.join(manquants)}")
            elif etat == NON_VERIFIABLE:
                print(f"ID {r['id']} : NON VÉRIFIABLE (aucun fichier déclaré — ne peut pas être validé)")


def main() -> int:
    """Point d'entrée principal."""
    parser = argparse.ArgumentParser(
        description="Mémoire de projet scellée à son contexte (FTS5 + zlib + sha256).",
        epilog="Exemple : python outils/memoire_projet.py --racine . noter 'Décision : utiliser FTS5' --fichiers src/main.py",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Racine du projet (défaut : répertoire de l'outil)",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON sur stdout")
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("memoire.db"),
        help="Chemin de la base SQLite (défaut : memoire.db)",
    )

    sous_parsers = parser.add_subparsers(dest="commande", required=True)

    # Commande noter
    parser_noter = sous_parsers.add_parser("noter", help="Ajouter une entrée")
    parser_noter.add_argument("texte", help="Texte de l'entrée")
    parser_noter.add_argument(
        "--fichiers", nargs="*", default=[], help="Fichiers relatifs à la racine"
    )
    parser_noter.add_argument(
        "--metadonnees",
        type=json.loads,
        default={},
        help='Métadonnées JSON (ex: \'{"type":"decision"}\')',
    )

    # Commande chercher
    parser_chercher = sous_parsers.add_parser("chercher", help="Rechercher un terme")
    parser_chercher.add_argument("terme", help="Terme à rechercher")
    parser_chercher.add_argument(
        "--limite", type=int, default=10, help="Nombre maximal de résultats (défaut : 10)"
    )

    # Commande perimees
    sous_parsers.add_parser("perimees", help="Vérifier les entrées périmées")

    # Commande oublier
    parser_oublier = sous_parsers.add_parser("oublier", help="Supprimer une entrée")
    parser_oublier.add_argument("id", type=int, help="Identifiant de l'entrée")

    args = parser.parse_args()

    # Initialisation de la mémoire
    try:
        memoire = Memoire(args.base, racine=args.racine)
    except Exception as e:
        print(f"Erreur d'initialisation : {e}", file=sys.stderr)
        return 2

    if args.commande == "noter":
        id_entree = memoire.noter(args.texte, fichiers=args.fichiers, metadonnees=args.metadonnees)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "id": id_entree,
                        "denominateur": 1,
                        "contrat": _construire_contrat(),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Entrée notée avec l'ID {id_entree}.")
        memoire.fermer()
        return 0

    if args.commande == "chercher":
        resultats = memoire.chercher(args.terme, limite=args.limite)
        denominateur = len(resultats)
        if args.json:
            print(
                json.dumps(
                    {
                        "denominateur": denominateur,
                        "resultats": resultats,
                        "contrat": _construire_contrat(),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            _afficher_humain(resultats, "chercher")
        memoire.fermer()
        return 0 if resultats else 1

    if args.commande == "perimees":
        resultats = memoire.perimees()
        denominateur = len(resultats)
        if denominateur == 0:
            print(
                "Aucune entrée dans la mémoire — impossible de conclure.",
                file=sys.stderr,
            )
            memoire.fermer()
            return 3
        defauts = [r for r in resultats if r["etat"] != VALIDE]
        if args.json:
            print(
                json.dumps(
                    {
                        "denominateur": denominateur,
                        "resultats": resultats,
                        "defauts": len(defauts),
                        "contrat": _construire_contrat(),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            _afficher_humain(resultats, "perimees")
            print(f"\n{len(defauts)} entrée(s) non valide(s) sur {denominateur}.")
        memoire.fermer()
        return 0 if not defauts else 1

    if args.commande == "oublier":
        memoire.oublier(args.id)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "id": args.id,
                        "denominateur": 1,
                        "contrat": _construire_contrat(),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Entrée {args.id} oubliée.")
        memoire.fermer()
        return 0

    parser.print_help()
    memoire.fermer()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())