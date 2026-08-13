.PHONY: test

test:
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_stream.c tests/stream_test.c -o /tmp/c5vrx-stream-test
	/tmp/c5vrx-stream-test
