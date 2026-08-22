# SPDX-License-Identifier: GPL-3.0-only

.PHONY: test

test:
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_stream.c tests/stream_test.c -o /tmp/c5vrx-stream-test
	/tmp/c5vrx-stream-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_capabilities.c tests/capabilities_test.c -o /tmp/c5vrx-capabilities-test
	/tmp/c5vrx-capabilities-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_cvbs_sync.c tests/cvbs_sync_test.c -o /tmp/c5vrx-cvbs-sync-test
	/tmp/c5vrx-cvbs-sync-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_sample_ring.c tests/sample_ring_test.c -o /tmp/c5vrx-sample-ring-test
	/tmp/c5vrx-sample-ring-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_preview_geometry.c tests/preview_geometry_test.c -o /tmp/c5vrx-preview-geometry-test
	/tmp/c5vrx-preview-geometry-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_video_timing.c tests/video_timing_test.c -o /tmp/c5vrx-video-timing-test
	/tmp/c5vrx-video-timing-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_cvbs_levels.c tests/cvbs_levels_test.c -o /tmp/c5vrx-cvbs-levels-test
	/tmp/c5vrx-cvbs-levels-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_clock_bridge.c tests/clock_bridge_test.c -o /tmp/c5vrx-clock-bridge-test -lm
	/tmp/c5vrx-clock-bridge-test
	$(CC) -std=c11 -Wall -Wextra -Werror -pedantic -Imain \
		main/c5vrx_chroma.c tests/cvbs_chroma_test.c -o /tmp/c5vrx-cvbs-chroma-test -lm
	/tmp/c5vrx-cvbs-chroma-test
	python3 tools/c5vrx_usb_protocol.py --self-test
	python3 tools/c5vrx_lab.py self-test
	python3 tools/validate_firmware_profiles.py
	@if [ -n "$(IDF_PATH)" ]; then python3 tools/audit_rf_dump_producer.py --idf "$(IDF_PATH)"; else echo "SKIP producer audit: IDF_PATH unset"; fi
