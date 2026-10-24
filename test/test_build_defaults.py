#!/usr/bin/env python3
"""C5VRX/Twotoz: configure the real root CMake with an IDF boundary stub.

Checks defaults selection only; does not compile firmware or run Kconfig.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildDefaults(unittest.TestCase):
    def test_root_preserves_explicit_defaults(self):
        cmake = shutil.which('cmake')
        self.assertIsNotNone(cmake, 'cmake is required for the defaults regression')
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            idf = temp / 'idf'
            include = idf / 'tools/cmake'
            include.mkdir(parents=True)
            # Stub just the IDF entry point; root CMake evaluates normally.
            # Avoid compiler/toolchain discovery in this configuration test.
            (include / 'project.cmake').write_text('''
macro(project)
    file(WRITE "${CMAKE_BINARY_DIR}/captured-defaults.txt" "${SDKCONFIG_DEFAULTS}")
endmacro()
''')
            env = dict(os.environ, IDF_PATH=str(idf))
            standard = str(ROOT / 'sdkconfig.defaults')
            override = standard + ';' + str(temp / 'clock.defaults')
            for index, explicit in enumerate((None, override, '')):
                with self.subTest(defaults=explicit):
                    build = temp / f'build-{index}'
                    args = [cmake, '-S', str(ROOT), '-B', str(build)]
                    if explicit is not None:
                        args += ['-DSDKCONFIG_DEFAULTS=' + explicit]
                    result = subprocess.run(args, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual((build / 'captured-defaults.txt').read_text(),
                                     standard if explicit is None else explicit)


if __name__ == '__main__':
    unittest.main()
