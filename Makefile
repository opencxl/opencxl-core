CONFIG_DIR = configs
CONFIG ?= 1vcs_4sld.yaml

test:
	poetry run pytest --cov --cov-report=term-missing -n $$(nproc)

lint:
	poetry run pylint opencxl
	poetry run pylint tests

clean:
	rm -rf *.bin logs *.log *.pcap
	find . | grep -E "(/__pycache__$$|\.pyc$$|\.pyo$$)" | xargs rm -rf
