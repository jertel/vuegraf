.PHONY: all production test clean

COMPOSE = $(shell if docker compose version >/dev/null 2>&1; then echo " compose"; else echo "-compose"; fi)

all: production

production:
	@true

dev: $(LOCAL_CONFIG_DIR) $(LOGS_DIR) install-hooks

install-hooks:
	pre-commit install -f --install-hooks

test:
	tox -c tox.ini

test-docker:
	$(shell echo docker$(COMPOSE)) -f docker-compose.yaml.test --project-name vuegraf build tox
	$(shell echo docker$(COMPOSE)) -f docker-compose.yaml.test --project-name vuegraf run --rm tox \
		tox -c src/tox.ini -- $(filter-out $@,$(MAKECMDGOALS))

clean:
	make -C clean
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -delete
	rm -rf virtualenv_run src/.tox src/.coverage *.egg-info

%:
	@:
