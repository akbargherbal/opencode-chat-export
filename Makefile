.PHONY: test doctor export clean

test:
	python3 -m pytest

doctor:
	./bin/opencode-chat-export doctor

export:
	./bin/opencode-chat-export export -o opencode-chats.zip

clean:
	rm -rf build dist .pytest_cache src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
