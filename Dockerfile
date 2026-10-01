FROM python@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6
WORKDIR /agent-foundry
COPY . /agent-foundry
RUN python -c "import sys; assert sys.version_info[:3] == (3, 14, 7), sys.version"
RUN python mesures/eprouver_instruments.py
RUN python mesures/porte_qualite.py outils/*.py --racine /agent-foundry
RUN python mesures/test_isolation.py outils --racine /agent-foundry
CMD ["python", "-c", "import pathlib; print(chr(10).join(sorted(p.stem for p in pathlib.Path('outils').glob('*.py'))))"]

