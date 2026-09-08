"""
QUESTION      Quelles fonctions consomment le plus de CPU ?
MESURE        Nombre d'instructions bytecode, de boucles (JUMP_BACKWARD, FOR_ITER) et d'appels (CALL, CALL_KW) par fonction, via dis, apres import reel du module.
HYPOTHESES    Le cout CPU d'une fonction est proportionnel a sa complexite statique (instructions, boucles, appels) et les fonctions sont accessibles par introspection du module importe.
LIMITES       Ne mesure pas le temps d'execution reel, ignore les entrees/sorties bloquantes et les appels systeme. Les fonctions generees dynamiquement non presentes dans l'espace de noms du module sont ignorees.
CONTRE-EXEMPLES Une fonction avec une seule instruction `time.sleep(10)` aura un cout statique faible mais un cout temps reel eleve. Une fonction recursive aura un cout statique sous-estime.
DOMAINE       Fichiers source Python valides, compilables et importables sans erreur d'execution au niveau module.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import dis
import importlib.util
import json
import types
from pathlib import Path

RACINE = Path(__file__).resolve().parent

__all__ = ["importer_module", "extraire_fonctions_de_module", "calculer_cout_fonction", "extraire_contrat"]


def importer_module(chemin: Path) -> types.ModuleType:
    nom_module = chemin.stem
    spec = importlib.util.spec_from_file_location(nom_module, chemin)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de creer le spec pour {chemin}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom_module] = module
    spec.loader.exec_module(module)
    return module


def extraire_fonctions_de_module(module: types.ModuleType) -> list[tuple[str, types.FunctionType]]:
    fonctions: list[tuple[str, types.FunctionType]] = []
    vus: set[int] = set()
    
    def visiter(obj: object, prefixe: str) -> None:
        if id(obj) in vus:
            return
        vus.add(id(obj))
        if isinstance(obj, types.FunctionType):
            if getattr(obj, "__module__", None) == module.__name__:
                fonctions.append((prefixe, obj))
        elif isinstance(obj, type):
            if getattr(obj, "__module__", None) == module.__name__:
                for nom, membre in vars(obj).items():
                    visiter(membre, f"{prefixe}.{nom}")
    
    for nom, obj in vars(module).items():
        visiter(obj, f"{module.__name__}.{nom}")
        
    return fonctions


def calculer_cout_fonction(func: types.FunctionType) -> dict[str, int]:
    instructions = 0
    boucles = 0
    appels = 0
    
    def analyser_code(code: types.CodeType) -> None:
        nonlocal instructions, boucles, appels
        for instr in dis.get_instructions(code):
            instructions += 1
            if instr.opname in ("JUMP_BACKWARD", "FOR_ITER"):
                boucles += 1
            if instr.opname in ("CALL", "CALL_KW", "CALL_FUNCTION_EX"):
                appels += 1
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                analyser_code(const)

    analyser_code(func.__code__)
    
    cout = instructions + (boucles * 100) + (appels * 10)
    return {
        "instructions": instructions,
        "boucles": boucles,
        "appels": appels,
        "cout": cout,
    }


def extraire_contrat() -> dict[str, str]:
    doc = sys.modules[__name__].__doc__ or ""
    sections: dict[str, str] = {}
    current_key: str | None = None
    for line in doc.splitlines():
        line = line.strip()
        if line in ("QUESTION", "MESURE", "HYPOTHESES", "LIMITES", "CONTRE-EXEMPLES", "DOMAINE"):
            current_key = line
            sections[current_key] = ""
        elif current_key:
            sections[current_key] += line + " "
    return {k: v.strip() for k, v in sections.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Identifie les fonctions qui consomment le plus de CPU par analyse statique du bytecode apres import reel.",
        epilog="Exemple: python optimiseur_cpu.py mon_script.py --json"
    )
    parser.add_argument("cible", help="Chemin du fichier Python a analyser")
    parser.add_argument("--racine", help="Repertoire racine a inserer en tete de sys.path pour l'import")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    
    args = parser.parse_args()
    
    racine = Path(args.racine).resolve() if args.racine else RACINE
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))
        
    cible = Path(args.cible)
    if not cible.is_file():
        print(f"Erreur: la cible {cible} n'existe pas ou n'est pas un fichier.", file=sys.stderr)
        return 2
        
    try:
        source = cible.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Erreur de lecture de {cible}: {e}", file=sys.stderr)
        return 2
        
    try:
        compile(source, str(cible), "exec")
    except SyntaxError as e:
        print(f"Erreur de syntaxe dans {cible}: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Erreur de compilation dans {cible}: {e}", file=sys.stderr)
        return 2
        
    try:
        module = importer_module(cible)
    except Exception as e:
        print(f"Erreur lors de l'import reel de {cible}: {e}", file=sys.stderr)
        return 2
        
    fonctions = extraire_fonctions_de_module(module)
    resultats = []
    for nom_fct, func in fonctions:
        cout = calculer_cout_fonction(func)
        resultats.append({
            "fonction": nom_fct,
            **cout
        })
        
    resultats.sort(key=lambda x: x["cout"], reverse=True)
    
    denominateur = len(resultats)
    examines = resultats[:200]
    examines_tronques = denominateur > 200
    
    if denominateur == 0:
        print("Aucune fonction trouvee dans la cible. Refus de conclure.", file=sys.stderr)
        return 3
        
    if args.json:
        sortie = {
            "contrat": extraire_contrat(),
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
        }
        print(json.dumps(sortie, indent=2, ensure_ascii=False))
    else:
        print("Fonctions par cout CPU estime (descendant) :")
        for r in examines:
            print(f"  {r['fonction']}: cout={r['cout']} (instr={r['instructions']}, boucles={r['boucles']}, appels={r['appels']})")
        if examines_tronques:
            print(f"  ... et {denominateur - 200} autres fonctions tronquees.")
            
    return 1

if __name__ == "__main__":
    raise SystemExit(main())