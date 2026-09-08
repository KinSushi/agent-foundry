"""
QUESTION      Cette donnée est-elle conforme au schéma attendu ?
MESURE        Validation déclarative, coercition de types, messages d'erreur riches.
HYPOTHESES    Le schéma est exprimé en Python natif (dataclasses, typing) ou via pydantic.
              La donnée est un objet Python sérialisable (dict, list, primitive, dataclass, etc.).
LIMITES       Sans pydantic, la validation est moins expressive et les messages d'erreur moins riches.
              Ne gère pas les références circulaires dans les données.
CONTRE-EXEMPLES Un schéma pydantic avec des validateurs personnalisés non déclarés dans les annotations.
INVOCATION
    {outil} '{"x": 1}' --schema 'dict' --module valide --json
DOMAINE       Données structurées en Python (API, configuration, messages inter-services).
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import sys
import typing
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

T = TypeVar("T")

__all__ = [
    "SchemaValidateur",
    "validateur_standard",
    "valider_donnee",
    "ResultatValidation",
    "main",
]

class SchemaValidateur(ABC):
    @abstractmethod
    def valider(self, donnee: Any, schema: Type[T]) -> T:
        """Valide une donnée par rapport à un schéma et retourne la donnée validée."""
        ...

@dataclasses.dataclass
class ResultatValidation:
    valide: bool
    donnee: Optional[Any] = None
    erreurs: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    denominateur: int = 1
    examines: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    examines_tronques: bool = False

def validateur_standard() -> SchemaValidateur:
    """Retourne un validateur utilisant la bibliothèque standard (dataclasses, typing)."""

    class ValidateurStandard(SchemaValidateur):
        def valider(self, donnee: Any, schema: Type[T]) -> T:
            if dataclasses.is_dataclass(schema):
                return self._valider_dataclass(donnee, schema)
            elif typing.get_origin(schema) is dict and typing.get_args(schema):
                return self._valider_dict(donnee, schema)
            elif typing.get_origin(schema) is list and typing.get_args(schema):
                return self._valider_list(donnee, schema)
            elif schema is typing.Any:
                return donnee
            elif isinstance(donnee, schema):
                return donnee
            else:
                raise TypeError(f"Donnée de type {type(donnee)} incompatible avec le schéma {schema}")

        def _valider_dataclass(self, donnee: Any, schema: Type[T]) -> T:
            if not isinstance(donnee, dict):
                raise TypeError(f"Donnée doit être un dict pour une dataclass, reçu {type(donnee)}")

            erreurs = []
            champs = {f.name: f.type for f in dataclasses.fields(schema)}
            valeurs = {}

            for nom, type_champ in champs.items():
                if nom not in donnee:
                    if dataclasses.MISSING == getattr(schema, nom, dataclasses.MISSING):
                        erreurs.append({
                            "champ": nom,
                            "message": f"Champ obligatoire '{nom}' manquant",
                            "type": "missing"
                        })
                    continue

                try:
                    valeur = donnee[nom]
                    if type_champ is not Any:
                        self.valider(valeur, type_champ)
                    valeurs[nom] = valeur
                except TypeError as e:
                    erreurs.append({
                        "champ": nom,
                        "message": str(e),
                        "type": "type_error"
                    })

            if erreurs:
                raise TypeError(f"Validation échouée: {erreurs}")

            try:
                return schema(**valeurs)
            except TypeError as e:
                raise TypeError(f"Échec de la création de la dataclass: {e}") from e

        def _valider_dict(self, donnee: Any, schema: Type[T]) -> T:
            if not isinstance(donnee, dict):
                raise TypeError(f"Donnée doit être un dict, reçu {type(donnee)}")

            key_type, value_type = typing.get_args(schema)
            erreurs = []

            for k, v in donnee.items():
                try:
                    self.valider(k, key_type)
                except TypeError as e:
                    erreurs.append({
                        "champ": f"clé[{k}]",
                        "message": str(e),
                        "type": "type_error"
                    })
                try:
                    self.valider(v, value_type)
                except TypeError as e:
                    erreurs.append({
                        "champ": f"valeur[{k}]",
                        "message": str(e),
                        "type": "type_error"
                    })

            if erreurs:
                raise TypeError(f"Validation du dict échouée: {erreurs}")

            return donnee

        def _valider_list(self, donnee: Any, schema: Type[T]) -> T:
            if not isinstance(donnee, list):
                raise TypeError(f"Donnée doit être une list, reçu {type(donnee)}")

            item_type = typing.get_args(schema)[0]
            erreurs = []

            for i, item in enumerate(donnee):
                try:
                    self.valider(item, item_type)
                except TypeError as e:
                    erreurs.append({
                        "champ": f"[{i}]",
                        "message": str(e),
                        "type": "type_error"
                    })

            if erreurs:
                raise TypeError(f"Validation de la list échouée: {erreurs}")

            return donnee

    return ValidateurStandard()

def valider_donnee(
    donnee: Any,
    schema: Type[T],
    *,
    utiliser_pydantic: bool = False
) -> ResultatValidation:
    """Valide une donnée par rapport à un schéma et retourne le résultat de validation."""
    denominateur = 1
    examines = [{"nom": "donnee", "valeur": str(donnee)[:100]}]
    examines_tronques = len(examines) > 200

    try:
        if utiliser_pydantic:
            try:
                import pydantic
                if hasattr(schema, "model_validate"):
                    donnee_validee = schema.model_validate(donnee)
                else:
                    adapter = pydantic.TypeAdapter(schema)
                    donnee_validee = adapter.validate_python(donnee)
                return ResultatValidation(
                    valide=True,
                    donnee=donnee_validee,
                    denominateur=denominateur,
                    examines=examines,
                    examines_tronques=examines_tronques
                )
            except ImportError:
                sys.stderr.write("⚠️ pydantic non disponible, utilisation du validateur standard\n")
                validateur = validateur_standard()
        else:
            validateur = validateur_standard()

        donnee_validee = validateur.valider(donnee, schema)
        return ResultatValidation(
            valide=True,
            donnee=donnee_validee,
            denominateur=denominateur,
            examines=examines,
            examines_tronques=examines_tronques
        )
    except (TypeError, ImportError) as e:
        erreurs = []
        if isinstance(e, TypeError):
            if "Validation échouée:" in str(e):
                try:
                    erreurs = json.loads(str(e).split("Validation échouée:")[1].strip())
                except json.JSONDecodeError:
                    erreurs = [{"champ": "global", "message": str(e), "type": "type_error"}]
            else:
                erreurs = [{"champ": "global", "message": str(e), "type": "type_error"}]
        return ResultatValidation(
            valide=False,
            erreurs=erreurs,
            denominateur=denominateur,
            examines=examines,
            examines_tronques=examines_tronques
        )

def analyser_args() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Valide une donnée JSON par rapport à un schéma Python.",
        epilog="Exemple: valider_donnees.py '{\"x\": 1, \"y\": 2}' --schema 'Point' --module exemple.schemas",
    )
    parser.add_argument(
        "donnee",
        type=str,
        help="Donnée JSON à valider.",
    )
    parser.add_argument(
        "--schema",
        type=str,
        required=True,
        help="Nom du schéma (classe, dataclass, etc.) à utiliser pour la validation.",
    )
    parser.add_argument(
        "--module",
        type=str,
        required=True,
        help="Module Python contenant le schéma.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour l'import du module. Par défaut: répertoire du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON.",
    )
    parser.add_argument(
        "--pydantic",
        action="store_true",
        help="Utiliser pydantic pour la validation si disponible.",
    )
    return parser.parse_args()

def importer_schema(module: str, schema: str, racine: Path) -> Type[Any]:
    """Importe et retourne le schéma depuis le module spécifié."""
    if racine not in sys.path:
        sys.path.insert(0, str(racine))

    try:
        imported_module = __import__(module, fromlist=[schema])
        return getattr(imported_module, schema)
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Échec de l'import du schéma {schema} depuis {module}: {e}") from e

def formater_sortie_json(resultat: ResultatValidation) -> str:
    """Formate le résultat en JSON valide avec toutes les clés requises."""
    sortie = {
        "denominateur": resultat.denominateur,
        "examines": resultat.examines,
        "examines_tronques": resultat.examines_tronques,
        "contrat": {
            "QUESTION": "Cette donnée est-elle conforme au schéma attendu ?",
            "MESURE": "Validation déclarative, coercition de types, messages d'erreur riches.",
            "HYPOTHESES": "Le schéma est exprimé en Python natif (dataclasses, typing) ou via pydantic. "
                         "La donnée est un objet Python sérialisable.",
            "LIMITES": "Sans pydantic, la validation est moins expressive et les messages d'erreur moins riches. "
                      "Ne gère pas les références circulaires dans les données.",
            "CONTRE-EXEMPLES": "Un schéma pydantic avec des validateurs personnalisés non déclarés dans les annotations.",
            "DOMAINE": "Données structurées en Python (API, configuration, messages inter-services).",
        }
    }

    if resultat.valide:
        sortie["valide"] = True
        sortie["donnee"] = resultat.donnee
    else:
        sortie["valide"] = False
        sortie["erreurs"] = resultat.erreurs

    return json.dumps(sortie, default=str, ensure_ascii=False)

def main() -> int:
    """Point d'entrée principal."""
    args = analyser_args()

    try:
        donnee = json.loads(args.donnee)
    except json.JSONDecodeError as e:
        if args.json:
            print(formater_sortie_json(ResultatValidation(
                valide=False,
                erreurs=[{"champ": "json", "message": f"Erreur de décodage JSON: {e}", "type": "json_error"}],
                denominateur=1
            )))
        else:
            print(f"Erreur de décodage JSON: {e}", file=sys.stderr)
        return 1

    try:
        schema = importer_schema(args.module, args.schema, args.racine)
    except ImportError as e:
        if args.json:
            print(formater_sortie_json(ResultatValidation(
                valide=False,
                erreurs=[{"champ": "import", "message": f"Erreur d'import: {e}", "type": "import_error"}],
                denominateur=1
            )))
        else:
            print(f"Erreur d'import: {e}", file=sys.stderr)
        return 2

    resultat = valider_donnee(donnee, schema, utiliser_pydantic=args.pydantic)

    if args.json:
        print(formater_sortie_json(resultat))
    else:
        if resultat.denominateur == 0:
            print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
            return 3

        if resultat.valide:
            print("✅ La donnée est conforme au schéma.")
        else:
            print("❌ La donnée n'est pas conforme au schéma:", file=sys.stderr)
            for erreur in resultat.erreurs:
                print(f"  - {erreur['champ']}: {erreur['message']}", file=sys.stderr)
        return 0 if resultat.valide else 4

    return 0 if resultat.valide else 4

if __name__ == "__main__":
    raise SystemExit(main())