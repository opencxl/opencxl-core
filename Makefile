CONFIG_DIR = configs
CONFIG ?= 1vcs_4sld.yaml

ifeq ($(shell uname), Darwin)
	NPROC = $(shell sysctl -n hw.logicalcpu)
else
	NPROC = $$(nproc)
endif

test:
	poetry run pytest --cov --cov-report=term-missing -n $(NPROC)
	rm -f mem*.bin

lint:
	poetry run pylint opencxl
	poetry run pylint tests

clean:
	rm -rf *.bin logs *.log *.pcap
	find . | grep -E "(/__pycache__$$|\.pyc$$|\.pyo$$)" | xargs rm -rf
