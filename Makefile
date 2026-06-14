PYTHON ?= python

.PHONY: run run20 run40 clean test residuals postprocess

run:
	bash scripts/run_cavity.sh

run20:
	MESH_RESOLUTION=20 bash scripts/run_cavity.sh

run40:
	MESH_RESOLUTION=40 bash scripts/run_cavity.sh

clean:
	bash scripts/clean_case.sh

test:
	$(PYTHON) -m pytest

residuals:
	$(PYTHON) scripts/plot_residuals.py --log results/logs/icoFoam.log --output figures/cavity_residuals.png --csv results/residuals.csv

postprocess:
	$(PYTHON) scripts/postprocess_cavity.py --case cases/lid_driven_cavity --results results --figures figures
