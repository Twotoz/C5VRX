.PHONY: test

test:
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_stream.c tests/stream_test.c -o /tmp/c5vrx-stream-test
	/tmp/c5vrx-stream-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_capabilities.c tests/capabilities_test.c -o /tmp/c5vrx-capabilities-test
	/tmp/c5vrx-capabilities-test
	@if [ -n "$(IDF_PATH)" ]; then python3 tools/audit_rf_dump_producer.py --idf "$(IDF_PATH)"; else echo "SKIP producer audit: IDF_PATH unset"; fi
