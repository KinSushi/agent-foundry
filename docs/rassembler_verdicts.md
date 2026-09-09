# rassembler_verdicts

> Que disent, ensemble, tous les outils passés sur ce dépôt ?

## Comment s'en servir

```
python outils/rassembler_verdicts.py emettre dummy 0 --verdicts R note "msg" exemple.py --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: rassembler_verdicts.py [-h] [--racine RACINE]
                              {emettre,rassembler,trier} ...

Agrège les verdicts SARIF de plusieurs outils.

positional arguments:
  {emettre,rassembler,trier}
    emettre             Émettre un SARIF à partir de Verdicts.
    rassembler          Fusionner plusieurs fichiers SARIF.
    trier               Filtrer un SARIF existant.

options:
  -h, --help            show this help message and exit
  --racine RACINE       Répertoire racine du projet (défaut : répertoire du
                        script).

Exemple : python -m rassembler_verdicts rassembler *.sarif --json
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

SARIF décrit des constats localisables : un verdict global sans fichier (ex. « maintenabilité 13,7 ») doit être attaché à la ligne 1, ce qui est une convention, pas une vérité. Le format ne dit rien de la gravité réelle au‑delà des niveaux « none », « note », « warning », « error ».

## Contre‑exemples

Un `uri` contenant un chemin Windows (`outils\x.py`) est refusé par les consommateurs stricts ; il doit être converti en chemin POSIX.

## Ce qu'il lui faut

Bibliothèque standard seule

---
[← retour à la liste](../README.md)

