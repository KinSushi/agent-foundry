# transporter_travail

> ce travail peut-il être exécuté ailleurs, et qu'est-ce qui traverse réellement ?

## Comment s'en servir

```
python outils/transporter_travail.py classer lambda --json
```

Invocation déclarée par l'outil lui-même, et rejouée par le juge à chaque mesure.

## Toutes les options

```
usage: transporter_travail.py [-h] [--racine RACINE] [--json] [--compact]
                              {classer,envoyer,recevoir} ...

Transporte du travail Python entre processus ou interpréteurs.

positional arguments:
  {classer,envoyer,recevoir}
    classer             classer comment un objet traverse
    envoyer             envoyer une fonction et récupérer le résultat
    recevoir            recevoir un flux sérialisé et enchaîner les étages de
                        sûreté

options:
  -h, --help            show this help message and exit
  --racine RACINE       répertoire racine du projet
  --json                rendre un objet JSON unique sur stdout
  --compact             utiliser dill à la place de cloudpickle

Exemple : python outils/transporter_travail.py classer closure
```

## Ce qu'il rend

Avec `--json`, un objet JSON portant `denominateur` — le nombre d'éléments réellement examinés — et `examines`, leurs noms.

| code de sortie | ce qu'il signifie |
|---|---|
| 0 | rien à signaler |
| non nul | un défaut a été trouvé, ou l'appel était invalide |
| 3 | rien à examiner : l'outil refuse de conclure |

## Ce qu'il ne fait pas

un verrou, un générateur en cours ne traversent PAS ; les modules traversent par RÉFÉRENCE sans erreur ; ce qui est capturé est COPIÉ, pas partagé

## Contre‑exemples

cloudpickle.dumps(sys.stdout) rend 98 octets et « réussit » — le receveur obtient SON stdout. Plausible et faux.

## Ce qu'il lui faut

RestrictedPython, cloudpickle, dill, test — absentes, l'outil travaille en mode dégradé et le dit sur stderr

---
[← retour à la liste](../README.md)
