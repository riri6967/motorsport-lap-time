# Motorsport lap-time simulator -- everything runs locally and free.
#
# Nothing here needs an API key or an account. The only network calls are to
# two public endpoints: a circuit-geometry repository and the MediaWiki API.
#
#   make            what you can run
#   make all        the full pipeline, unattended
#   make watch      follow whatever is running, from another terminal
#
# Long targets log to logs/ and print progress live, so `make all` can be
# left alone and checked on with `make watch`.

PY      := python3
TOOLS   := tools
LOGS    := logs

.PHONY: help all setup tracks references test validate validate-quick \
        sensitivity calibrate plots stint endurance watch clean-lines \
        clean-logs clean status

help:
	@echo "motorsport lap-time simulator"
	@echo
	@echo "  make setup         fetch circuit geometry and lap records"
	@echo "  make test          run the test suite"
	@echo "  make validate      simulated vs published laps (slow: optimises a line per circuit)"
	@echo "  make validate-quick  same, minimum-curvature line only (seconds)"
	@echo "  make sensitivity   what each estimated parameter is worth per circuit"
	@echo "  make calibrate     fit the estimated coefficients to the references"
	@echo "  make plots         regenerate the figures in out/"
	@echo "  make stint         tyre degradation over a race stint"
	@echo "  make endurance     the multi-class endurance scenario"
	@echo "  make all           setup, test, validate, sensitivity, plots"
	@echo
	@echo "  make watch         tail the running log"
	@echo "  make status        what is cached and what is running"
	@echo "  make clean-lines   drop cached racing lines (forces a re-solve)"
	@echo

all: setup test validate sensitivity plots
	@echo
	@echo "pipeline finished -- see $(LOGS)/ and out/"

setup: tracks references

tracks:
	$(PY) $(TOOLS)/fetch_tracks.py

references:
	$(PY) $(TOOLS)/fetch_references.py

test:
	$(PY) -m pytest -q

validate:
	$(PY) $(TOOLS)/validate.py

validate-quick:
	$(PY) $(TOOLS)/validate.py --quick

sensitivity:
	$(PY) $(TOOLS)/sensitivity.py

calibrate:
	$(PY) $(TOOLS)/calibrate.py

plots:
	@mkdir -p out
	@for track in Monza Spa Silverstone Sakhir Catalunya; do \
	    $(PY) $(TOOLS)/plot_lap.py --track $$track --out out/$$track.png || exit 1; \
	done

stint:
	$(PY) $(TOOLS)/run_stint.py

endurance:
	$(PY) scenarios/lemans24h.py

watch:
	@tail -f $(LOGS)/latest.log

status:
	@echo "cached circuits:"; ls tracks/real/*.csv 2>/dev/null | wc -l
	@echo "cached racing lines:"; ls lines/*.npz 2>/dev/null | wc -l
	@echo "running:"; pgrep -af "python3 .*(tools/|scenarios/)" || echo "  nothing"

clean-lines:
	rm -f lines/*.npz

clean-logs:
	rm -f $(LOGS)/*.log

clean: clean-lines clean-logs
	rm -rf out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
