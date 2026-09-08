"""atelier_document.py
Outil d'indexation et de recherche de texte positionné dans les PDF.

CONTRAT DE MESURE
QUESTION       que dit ce document, et où exactement le dit-il ?
MESURE         pymupdf pour le texte ET les coordonnées ; FTS5 pour retrouver ;
               zstd pour l'extrait ; sha256 pour dater la source
HYPOTHÈSES    le PDF porte une couche texte ; pymupdf charge sur cette machine
LIMITES        un PDF scanné rend 0 caractère — aucun OCR disponible ici ;
               la détection de tables est limitée, la bibliothèque le dit
               elle‑même ; pymupdf porte un binaire, donc NON embarquable
CONTRE-EXEMPLE un extrait sans position ne se vérifie pas — c'est la faute que
               cet outil corrige, mesurée à 37 citations fausses sur 39
INVOCATION
    {outil} indexer --racine {dossier} --db {dossier}/index.db --json
DOMAINE        PDF avec couche texte, sur cette machine
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Importations optionnelles
# ------------------------------------------------------------
try:
    import fitz  # pymupdf
except ImportError as exc:  # pragma: no cover
    fitz = None
    _FITZ_IMPORT_ERROR = exc
else:
    _FITZ_IMPORT_ERROR = None

try:
    import fsspec
except ImportError:  # pragma: no cover
    fsspec = None

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
CODE_BLOQUE = 3          # pymupdf indisponible ou dénominateur nul
CODE_DEFECT = 1          # défaut trouvé
CODE_OK = 0

# ------------------------------------------------------------
# Fonctions du cœur (sans argparse, sans I/O)
# ------------------------------------------------------------

def _hash_fichier(chemin: Path) -> str:
    """Retourne le SHA‑256 hex du fichier."""
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for bloc in iter(lambda: f.read(8192), b""):
            h.update(bloc)
    return h.hexdigest()

def _compresser(texte: str) -> bytes:
    """Compresse le texte avec zstandard si disponible, sinon renvoie les octets UTF‑8."""
    data = texte.encode("utf-8")
    if zstd:
        return zstd.compress(data)
    return data

def _decompresser(blob: bytes) -> str:
    """Décompresse un blob produit par _compresser."""
    if zstd:
        return zstd.decompress(blob).decode("utf-8")
    return blob.decode("utf-8")

def _ouvrir_pdf(chemin: Path) -> Any:
    """Ouvre un PDF avec pymupdf (ou fsspec si disponible). Lève RuntimeError si impossible."""
    if not fitz:
        raise RuntimeError("pymupdf non disponible")
    if fsspec:
        with fsspec.open(str(chemin), "rb") as f:
            data = f.read()
        return fitz.open(stream=data, filetype="pdf")
    return fitz.open(str(chemin))

def extraire_blocs(chemin: Path) -> Tuple[List[Dict[str, Any]], bool, int]:
    """
    Extrait, pour chaque page du PDF, les blocs de texte avec leurs coordonnées.
    Retourne un tuple (blocs, sans_texte, nb_tables) :
    - blocs : liste de dict contenant doc, page, bloc, x0, y0, x1, y1, texte, sha256, data
    - sans_texte : True si aucun texte n'a été trouvé (PDF scanné)
    - nb_tables : nombre total de tables détectées
    """
    doc = _ouvrir_pdf(chemin)
    sha = _hash_fichier(chemin)
    resultats: List[Dict[str, Any]] = []
    nb_tables = 0
    for num_page, page in enumerate(doc, start=1):
        try:
            finder = page.find_tables()
            nb_tables += len(finder) if finder else 0
        except Exception:
            pass
        blocs = page.get_text("blocks")
        if not blocs:
            continue
        for idx, bloc in enumerate(blocs, start=1):
            x0, y0, x1, y1, texte = bloc[:5]
            texte = texte.strip()
            if not texte:
                continue
            comp = _compresser(texte)
            resultats.append(
                {
                    "doc": str(chemin),
                    "page": num_page,
                    "bloc": idx,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "texte": texte,
                    "sha256": sha,
                    "data": comp,
                }
            )
    sans_texte = len(resultats) == 0
    return resultats, sans_texte, nb_tables

def _creer_schema(conn: sqlite3.Connection) -> None:
    """Crée les tables nécessaires (extraits + FTS5) si elles n'existent pas."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS extraits (
            id INTEGER PRIMARY KEY,
            doc TEXT NOT NULL,
            page INTEGER NOT NULL,
            bloc INTEGER NOT NULL,
            x0 REAL NOT NULL,
            y0 REAL NOT NULL,
            x1 REAL NOT NULL,
            y1 REAL NOT NULL,
            sha256 TEXT NOT NULL,
            data BLOB NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS extraits_fts
        USING fts5(texte, content='', content_rowid='id')
        """
    )
    conn.commit()

def _inserer_extraits(conn: sqlite3.Connection, blocs: Iterable[Dict[str, Any]]) -> None:
    """Insère les blocs dans la table extraits et dans l'index FTS5."""
    cur = conn.cursor()
    for b in blocs:
        cur.execute(
            """
            INSERT INTO extraits (doc, page, bloc, x0, y0, x1, y1, sha256, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                b["doc"],
                b["page"],
                b["bloc"],
                b["x0"],
                b["y0"],
                b["x1"],
                b["y1"],
                b["sha256"],
                b["data"],
            ),
        )
        rowid = cur.lastrowid
        texte = _decompresser(b["data"])
        cur.execute(
            "INSERT INTO extraits_fts(rowid, texte) VALUES (?, ?)",
            (rowid, texte),
        )
    conn.commit()

def indexer(racine: Path, db_path: Path) -> Dict[str, Any]:
    """
    Parcourt récursivement *racine* à la recherche de fichiers *.pdf*,
    extrait les blocs et les stocke dans la base SQLite *db_path*.
    Retourne un dict avec : total_blocs, scannes (liste de chemins), tables (nombre), pdf_count.
    """
    if not racine.exists():
        raise FileNotFoundError(f"Le chemin racine {racine} n'existe pas")
    if not racine.is_dir():
        raise ValueError(f"Le chemin racine {racine} n'est pas un répertoire")

    pdf_count = sum(1 for f in racine.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf")
    if pdf_count == 0:
        return {"total_blocs": 0, "scannes": [], "tables": 0, "pdf_count": 0}

    conn = sqlite3.connect(db_path)
    _creer_schema(conn)

    total = 0
    nb_tables = 0
    scannes: List[str] = []
    erreurs: List[Tuple[str, OSError]] = []

    def _onerror(err: OSError) -> None:
        erreurs.append((err.filename, err))

    for dossier, _, fichiers in os.walk(racine, onerror=_onerror):
        for f in sorted(fichiers):
            if not f.lower().endswith(".pdf"):
                continue
            chemin = Path(dossier) / f
            try:
                blocs, sans_texte, tables = extraire_blocs(chemin)
                nb_tables += tables
                if sans_texte:
                    scannes.append(str(chemin))
                    continue
                if blocs:
                    _inserer_extraits(conn, blocs)
                    total += len(blocs)
            except Exception as exc:  # pragma: no cover
                print(f"Erreur lors de l'indexation de {chemin}: {exc}", file=sys.stderr)

    conn.close()
    if erreurs:
        raise RuntimeError(f"{len(erreurs)} chemins non lus pendant la marche")
    return {"total_blocs": total, "scannes": sorted(scannes), "tables": nb_tables, "pdf_count": pdf_count}

def chercher(db_path: Path, terme: str) -> List[Dict[str, Any]]:
    """
    Recherche *terme* dans l'index FTS5 et renvoie la liste des extraits
    contenant le terme, avec leurs coordonnées.
    Chaque dict contient : doc, page, bloc, x0, y0, x1, y1, texte.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Base de données {db_path} introuvable")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT e.doc, e.page, e.bloc, e.x0, e.y0, e.x1, e.y1, e.data
            FROM extraits_fts f
            JOIN extraits e ON e.id = f.rowid
            WHERE f.texte MATCH ?
            """,
            (terme,),
        )
        rows = cur.fetchall()
        conn.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Base de données {db_path} illisible : {exc}")

    resultats: List[Dict[str, Any]] = []
    for doc, page, bloc, x0, y0, x1, y1, data in rows:
        texte = _decompresser(data)
        resultats.append(
            {
                "doc": doc,
                "page": page,
                "bloc": bloc,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "texte": texte,
            }
        )
    return resultats

def _count_indexed_blocks(db_path: Path) -> int:
    """Nombre total de blocs indexés (table extraits)."""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM extraits")
        cnt = cur.fetchone()[0]
        conn.close()
        return cnt
    except sqlite3.DatabaseError:
        return 0

def verifier_perimees(racine: Path, db_path: Path) -> Tuple[Dict[str, str], int]:
    """
    Compare le SHA‑256 stocké avec le SHA‑256 actuel de chaque PDF indexé.
    Retourne un tuple (statut, denominateur) où statut ∈ {VALIDE, PÉRIMÉE,
    ORPHELINE, NON VÉRIFIABLE} et denominateur = nombre de PDF uniques vérifiés.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Base de données {db_path} introuvable")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT doc, sha256 FROM extraits")
        indexés = {Path(row[0]): row[1] for row in cur.fetchall()}
        conn.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Base de données {db_path} illisible : {exc}")

    statut: Dict[str, str] = {}
    for chemin in sorted(indexés):
        if not chemin.exists():
            statut[str(chemin)] = "ORPHELINE"
            continue
        try:
            actuel = _hash_fichier(chemin)
            statut[str(chemin)] = "VALIDE" if actuel == indexés[chemin] else "PÉRIMÉE"
        except Exception:  # pragma: no cover
            statut[str(chemin)] = "NON VÉRIFIABLE"
    return statut, len(indexés)

def _sortie_json_base(denominateur: int) -> Dict[str, Any]:
    """Retourne un dictionnaire de base pour toute sortie JSON."""
    return {
        "denominateur": denominateur,
        "contrat": _contrat()
    }

def _refus_legitime(message: str) -> int:
    """Gère un refus légitime selon le protocole F4(b)."""
    print(f"Denominateur nul : {message}", file=sys.stderr)
    return CODE_BLOQUE

# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def _afficher_humain_index(resultat: Dict[str, Any]) -> None:
    for doc in resultat.get("scannes", []):
        print(f"{doc} → SCANNÉ — AUCUN TEXTE")
    tables = resultat.get("tables", 0)
    print(
        f"tables détectées : {tables} — avertissement : "
        f"la détection de tables est limitée, pymupdf recommande pymupdf_layout"
    )
    total = resultat["total_blocs"]
    if total == 0:
        if resultat.get("scannes"):
            print("Aucun bloc de texte indexé (PDF scannés).")
        else:
            print("Aucun bloc indexé.")
    else:
        print(f"{total} blocs de texte positionné indexés avec succès.")

def _afficher_humain_chercher(résultats: List[Dict[str, Any]]) -> None:
    if not résultats:
        print("Aucun résultat trouvé.")
        return
    for r in résultats:
        print(
            f"{r['doc']}  page {r['page']}  bloc {r['bloc']}  "
            f"({r['x0']:.1f},{r['y0']:.1f},{r['x1']:.1f},{r['y1']:.1f})\n"
            f"    « {r['texte']} »"
        )

def _afficher_humain_perimees(statut: Dict[str, str]) -> None:
    for doc, st in sorted(statut.items()):
        print(f"{doc} → {st}")

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Indexe et recherche du texte positionné dans les PDF.",
        epilog="Exemple : python atelier_document.py indexer --racine ./docs --db ./index.db --json",
    )
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--json", action="store_true", help="Produit une sortie JSON unique sur stdout.")
    parent_parser.add_argument("--racine", type=Path, default=argparse.SUPPRESS, help="Répertoire racine à parcourir.")

    subparsers = parser.add_subparsers(dest="commande", required=True)

    sp_index = subparsers.add_parser("indexer", parents=[parent_parser], help="Construire ou mettre à jour l'index.")
    sp_index.add_argument("--db", type=Path, default=Path(__file__).with_name("atelier_document.db"), help="Fichier SQLite contenant l'index.")
    sp_index.add_argument("racine", type=Path, help="Répertoire racine à indexer.")

    sp_search = subparsers.add_parser("chercher", parents=[parent_parser], help="Rechercher un terme dans l'index.")
    sp_search.add_argument("--db", type=Path, default=Path(__file__).with_name("atelier_document.db"), help="Fichier SQLite contenant l'index.")
    sp_search.add_argument("terme", help="Terme à rechercher.")
    sp_search.add_argument("racine", type=Path, nargs="?", default=None, help="Répertoire racine à utiliser pour l'indexation préalable (optionnel).")

    sp_perimees = subparsers.add_parser("perimees", parents=[parent_parser], help="Vérifier la validité des PDF déjà indexés.")
    sp_perimees.add_argument("--db", type=Path, default=Path(__file__).with_name("atelier_document.db"), help="Fichier SQLite contenant l'index.")
    sp_perimees.add_argument("racine", type=Path, help="Répertoire racine à vérifier.")

    args = parser.parse_args()

    # Gestion du blocage de pymupdf
    if not fitz:
        if args.json:
            sortie = _sortie_json_base(0)
            sortie.update({"etat": "BLOQUÉ", "raison": str(_FITZ_IMPORT_ERROR)})
            json.dump(sortie, sys.stdout, ensure_ascii=False)
        return _refus_legitime("pymupdf indisponible")

    try:
        if args.commande == "indexer":
            if not args.racine.exists():
                print(f"Erreur : la racine {args.racine} n'existe pas.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "indexer", "erreur": f"la racine {args.racine} n'existe pas"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("rien à examiner, racine inexistante")
            if not args.racine.is_dir():
                print(f"Erreur : la racine {args.racine} n'est pas un répertoire.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "indexer", "erreur": f"la racine {args.racine} n'est pas un répertoire"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("rien à examiner, racine n'est pas un répertoire")

            resultat = indexer(args.racine, args.db)
            denominateur = resultat["pdf_count"]
            if denominateur == 0:
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "indexer", "erreur": "aucun PDF trouvé"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("aucun PDF trouvé")
            if args.json:
                sortie = _sortie_json_base(denominateur)
                sortie.update({
                    "action": "indexer",
                    "total_blocs": resultat["total_blocs"],
                    "scannes": resultat["scannes"],
                    "tables": resultat["tables"]
                })
                json.dump(sortie, sys.stdout, ensure_ascii=False)
            else:
                _afficher_humain_index(resultat)
            return CODE_OK if resultat["total_blocs"] > 0 or resultat["scannes"] else CODE_DEFECT

        if args.commande == "chercher":
            racine = args.racine if args.racine is not None else Path(__file__).resolve().parent
            db_path = args.db

            if not db_path.exists():
                print(f"Base de données {db_path} introuvable, tentative d'indexation...", file=sys.stderr)
                try:
                    indexer(racine, db_path)
                except Exception as exc:
                    print(f"Échec de l'indexation : {exc}", file=sys.stderr)
                    if args.json:
                        sortie = _sortie_json_base(0)
                        sortie.update({"action": "chercher", "erreur": f"échec de l'indexation : {exc}"})
                        json.dump(sortie, sys.stdout, ensure_ascii=False)
                    return CODE_DEFECT

            try:
                résultats = chercher(db_path, args.terme)
            except Exception as exc:
                print(f"Erreur : {exc}", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "chercher", "erreur": str(exc)})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return CODE_DEFECT

            denominateur = _count_indexed_blocks(db_path)
            if denominateur == 0:
                print("Aucun bloc indexé dans la base.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "chercher", "erreur": "Aucun bloc indexé"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("aucun bloc indexé")
            if args.json:
                sortie = _sortie_json_base(denominateur)
                sortie.update({
                    "action": "chercher",
                    "terme": args.terme,
                    "résultats": résultats
                })
                json.dump(sortie, sys.stdout, ensure_ascii=False)
            else:
                _afficher_humain_chercher(résultats)
            return CODE_OK if résultats else CODE_DEFECT

        if args.commande == "perimees":
            if not args.racine.exists():
                print(f"Erreur : la racine {args.racine} n'existe pas.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "perimees", "erreur": f"la racine {args.racine} n'existe pas"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("rien à examiner, racine inexistante")
            if not args.racine.is_dir():
                print(f"Erreur : la racine {args.racine} n'est pas un répertoire.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "perimees", "erreur": f"la racine {args.racine} n'est pas un répertoire"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("rien à examiner, racine n'est pas un répertoire")

            if not args.db.exists():
                print(f"Erreur : la base de données {args.db} n'existe pas.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "perimees", "erreur": f"la base de données {args.db} n'existe pas"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("base de données inexistante")
            try:
                statut, denominateur = verifier_perimees(args.racine, args.db)
            except Exception as exc:
                print(f"Erreur : {exc}", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "perimees", "erreur": str(exc)})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return CODE_DEFECT

            if denominateur == 0:
                print("Aucun PDF indexé à vérifier.", file=sys.stderr)
                if args.json:
                    sortie = _sortie_json_base(0)
                    sortie.update({"action": "perimees", "erreur": "Aucun PDF indexé"})
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                return _refus_legitime("aucun PDF indexé")
            if args.json:
                sortie = _sortie_json_base(denominateur)
                sortie.update({
                    "action": "perimees",
                    "statut": statut
                })
                json.dump(sortie, sys.stdout, ensure_ascii=False)
            else:
                _afficher_humain_perimees(statut)
            return CODE_OK

    except RuntimeError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        if args.json:
            sortie = _sortie_json_base(0)
            sortie.update({"erreur": str(exc)})
            json.dump(sortie, sys.stdout, ensure_ascii=False)
        return CODE_DEFECT
    except Exception:  # pragma: no cover
        traceback.print_exc(file=sys.stderr)
        if args.json:
            sortie = _sortie_json_base(0)
            sortie.update({"erreur": "exception inattendue"})
            json.dump(sortie, sys.stdout, ensure_ascii=False)
        return CODE_DEFECT

    return CODE_OK  # jamais atteint

def _contrat() -> Dict[str, str]:
    """Renvoie le contrat de mesure sous forme de dictionnaire."""
    return {
        "QUESTION": "que dit ce document, et où exactement le dit-il ?",
        "MESURE": "pymupdf pour le texte ET les coordonnées ; FTS5 pour retrouver ; zstd pour l'extrait ; sha256 pour dater la source",
        "HYPOTHÈSES": "le PDF porte une couche texte ; pymupdf charge sur cette machine",
        "LIMITES": "un PDF scanné rend 0 caractère — aucun OCR disponible ici ; la détection de tables est limitée, la bibliothèque le dit elle‑même ; pymupdf porte un binaire, donc NON embarquable",
        "CONTRE-EXEMPLE": "un extrait sans position ne se vérifie pas — c'est la faute que cet outil corrige, mesurée à 37 citations fausses sur 39",
        "DOMAINE": "PDF avec couche texte, sur cette machine",
    }

if __name__ == "__main__":
    raise SystemExit(main())